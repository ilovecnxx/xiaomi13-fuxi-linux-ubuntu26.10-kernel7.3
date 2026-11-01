#!/usr/bin/python3
# 卡 2（订阅 1）的 VOICE/WMS 常驻观察工具 v1：绑定订阅、注册事件、把收到的指示记成日志。
# 不拨号、不发短信、不应答来电、不删短信。收到传输路由短信时：原始 PDU 先追加到 SMS_FILE（0600），成功后再发 WMS Send Ack（用户 11-01 批准）。
# 日志不含号码和短信正文，只有服务/消息 ID/TLV 类型/长度。
# 绑定只对本进程的 client 生效，所以必须常驻（见 docs/调研-双卡QMI-20261101.md §5）。
# 用法（手机上，root）：fuxi-qmi-sub2-msg.py [秒数]   秒数默认 0 = 一直跑（作服务用）；VOICE/WMS 枚举 0 主 1 副。
import json
import os
import signal
import sys
import time
import gi
gi.require_version("Qmi", "1.0")
gi.require_version("Qrtr", "1.0")
from gi.repository import GLib, Qmi, Qrtr

SUB = 1
run_s = int(sys.argv[1]) if len(sys.argv) > 1 else 0
ctx = GLib.MainContext.default()
SMS_FILE = "/var/log/fuxi-sim2-sms.jsonl"  # 原始 PDU（含号码和正文），只给 root 读写
WMS_CLIENT = None
VOICE_BIND, WMS_BIND, WMS_GET_BINDING = 0x0044, 0x004C, 0x0058
SVC_NAMES = {0x05: "WMS", 0x09: "VOICE"}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def wait(start, *args):
    box = []
    start(*args, lambda src, res: box.append((src, res)))
    while not box:
        ctx.iteration(True)
    return box[0]


def tlv_bytes(msg, t):
    try:
        off, ln = Qmi.message_tlv_read_init(msg, t)
    except GLib.Error:
        return None
    o, buf = 0, []
    for _ in range(ln):
        _, o, b = Qmi.message_tlv_read_guint8(msg, off, o)
        buf.append(b)
    return bytes(buf)


def raw(dev, c, msg_id, tlvs=()):
    m = Qmi.message_new(c.get_service(), c.get_cid(), c.get_next_transaction_id(), msg_id)
    for t, data in tlvs:
        off = Qmi.message_tlv_write_init(m, t)
        for b in data:
            Qmi.message_tlv_write_guint8(m, b)
        Qmi.message_tlv_write_complete(m, off)
    resp = dev.command_full_finish(wait(dev.command_full, m, Qmi.MessageContext.new(), 10, None)[1])
    r = tlv_bytes(resp, 0x02)
    if r is None or len(r) < 4:
        raise RuntimeError(f"msg 0x{msg_id:04x}: no result TLV")
    if int.from_bytes(r[0:2], "little") != 0:
        raise RuntimeError(f"msg 0x{msg_id:04x}: QMI error {int.from_bytes(r[2:4], 'little')}")
    return resp


CALL_STATES = {1: "origination", 2: "incoming", 3: "conversation", 4: "cc-in-progress", 5: "alerting",
               6: "hold", 7: "waiting", 8: "disconnecting", 9: "end", 10: "setup"}


def parse_frame(b):
    # QMUX 头 6 字节（标记、长度、控制标志、服务、CID）+ QMI 头 7 字节（标志、事务号 2、消息 ID 2、TLV 总长 2）
    if len(b) < 13 or b[0] != 0x01:
        return None
    svc, cid, txn = b[4], b[5], int.from_bytes(b[7:9], "little")
    msg_id, tlv_len = int.from_bytes(b[9:11], "little"), int.from_bytes(b[11:13], "little")
    tlvs, o = {}, 13
    while o + 3 <= len(b) and o < 13 + tlv_len:
        t, ln = b[o], int.from_bytes(b[o + 1:o + 3], "little")
        tlvs[t] = b[o + 3:o + 3 + ln]
        o += 3 + ln
    return svc, cid, txn, msg_id, tlvs


def describe(svc, msg_id, tlvs):
    # 只解码不含号码和正文的字段
    out = []
    if svc == 0x09 and msg_id == 0x002E and 0x01 in tlvs:
        d = tlvs[0x01]
        n = d[0]
        for i in range(n):
            c = d[1 + 7 * i:8 + 7 * i]
            if len(c) == 7:
                out.append(f"call{c[0]}:{CALL_STATES.get(c[1], c[1])},type={c[2]},dir={c[3]},mode={c[4]}")
    elif svc == 0x05 and msg_id == 0x0001:
        if 0x10 in tlvs:
            out.append(f"stored:storage={tlvs[0x10][0]}")
        d = tlvs.get(0x11)
        if d and len(d) >= 8:
            out.append(f"transfer-route:ack_ind={d[0]},format={d[5]},pdu_len={int.from_bytes(d[6:8], 'little')}")
    return "; ".join(out)


def ack_cb(src, res):
    try:
        src.send_ack_finish(res)
        log("sent WMS ack")
    except GLib.Error as e:
        log(f"WMS ack failed: {e.message}")


def handle_transfer_route(d):
    # TLV 0x11：ack 指示 1B（0=要应答）、transaction id 4B、格式 1B、长度 2B、PDU（与 libqmi 的 getter 一致）。
    # 先把 PDU 落盘再应答：应答后网络不会重发，没存下就丢了。和 MM 一样，格式 CDMA 以外一律按 WCDMA 应答。
    ack_ind, txn, fmt = d[0], int.from_bytes(d[1:5], "little"), d[5]
    pdu = d[8:8 + int.from_bytes(d[6:8], "little")]
    try:
        fd = os.open(SMS_FILE, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.strftime("%F %T"), "txn": txn, "format": fmt, "pdu": pdu.hex()}) + "\n")
    except OSError as e:
        log(f"cannot save SMS txn={txn} ({e.strerror}); not acking")
        return
    log(f"saved SMS txn={txn} format={fmt} pdu_len={len(pdu)} ack_ind={ack_ind}")
    if ack_ind != 0:
        return
    ack = Qmi.MessageWmsSendAckInput()
    ack.set_information(txn, Qmi.WmsMessageProtocol.CDMA if fmt == 0 else Qmi.WmsMessageProtocol.WCDMA, True)
    WMS_CLIENT.send_ack(ack, 10, None, ack_cb)


def on_indication(dev, *args):
    # GI 给的指示是原始字节（已实测）；只记 TLV 类型/长度和上面 describe 解码出的安全字段
    for a in args:
        if isinstance(a, (bytes, bytearray)):
            f = parse_frame(bytes(a))
            if f is None:
                log(f"indication unparsed len={len(a)}")
                return
            svc, cid, txn, msg_id, tlvs = f
            shape = " ".join(f"{t:02x}:{len(v)}" for t, v in sorted(tlvs.items()))
            log(f"{SVC_NAMES.get(svc, svc)} cid={cid} txn={txn} msg=0x{msg_id:04x} tlvs[{shape}] {describe(svc, msg_id, tlvs)}")
            if svc == 0x05 and msg_id == 0x0001 and len(tlvs.get(0x11, b"")) >= 8:
                handle_transfer_route(tlvs[0x11])
            return
    log("indication args:", [type(a).__name__ for a in args])


def main():
    bus = Qrtr.Bus.new_finish(wait(Qrtr.Bus.new, 1000, None)[1])
    node = bus.peek_node(0)
    if node is None:
        sys.exit("qrtr node 0 not found")
    dev = Qmi.Device.new_from_node_finish(wait(Qmi.Device.new_from_node, node, None)[1])
    dev.open_finish(wait(dev.open, Qmi.DeviceOpenFlags.PROXY, 10, None)[1])
    dev.connect("indication", on_indication)
    global WMS_CLIENT
    voice = dev.allocate_client_finish(wait(dev.allocate_client, Qmi.Service.VOICE, Qmi.CID_NONE, 10, None)[1])
    wms = dev.allocate_client_finish(wait(dev.allocate_client, Qmi.Service.WMS, Qmi.CID_NONE, 10, None)[1])
    WMS_CLIENT = wms
    raw(dev, voice, VOICE_BIND, [(0x01, bytes([SUB]))])
    raw(dev, wms, WMS_BIND, [(0x01, bytes([SUB]))])
    got = tlv_bytes(raw(dev, wms, WMS_GET_BINDING), 0x10)
    log(f"bound VOICE+WMS to sub {SUB}; WMS binding readback={got.hex() if got else None}")
    # 注册事件：WMS 新 MT 短信；VOICE 通话通知
    inp = Qmi.MessageWmsSetEventReportInput()
    inp.set_new_mt_message_indicator(True)
    wms.set_event_report_finish(wait(wms.set_event_report, inp, 10, None)[1])
    vin = Qmi.MessageVoiceIndicationRegisterInput()
    vin.set_call_notification_events(True)
    voice.indication_register_finish(wait(voice.indication_register, vin, 10, None)[1])
    log("registered WMS MT-message event + VOICE call-notification events; waiting")
    end = time.time() + run_s if run_s else None
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    signal.signal(signal.SIGINT, lambda *_: stop.append(1))
    next_check = time.time() + 30
    while not stop and (end is None or time.time() < end):
        ctx.iteration(False)
        time.sleep(0.05)
        if time.time() >= next_check:
            # 看门狗：基带或 QRTR 重启后 client 会失效，读一次绑定确认；失败就退出，由外部拉起并重新绑定
            next_check = time.time() + 30
            try:
                raw(dev, wms, WMS_GET_BINDING)
            except Exception as e:
                log(f"watchdog failed ({e}); exiting for restart")
                sys.exit(1)
    for c in (voice, wms):
        dev.release_client_finish(wait(dev.release_client, c, Qmi.DeviceReleaseClientFlags.RELEASE_CID, 10, None)[1])
    log("released clients, exit")


main()
