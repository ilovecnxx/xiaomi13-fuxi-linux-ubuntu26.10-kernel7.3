#!/usr/bin/python3
# 卡 2（订阅 1）的 VOICE/WMS 常驻观察工具 v1：绑定订阅、注册事件、把收到的指示记成日志。
# 不拨号、不发短信、不应答来电、不删短信。收到传输路由短信时：原始 PDU 先追加到 SMS_FILE（0600），成功后再发 WMS Send Ack（用户 11-01 批准）。
# 日志不含号码和短信正文，只有服务/消息 ID/TLV 类型/长度。
# 绑定只对本进程的 client 生效，所以必须常驻（见 docs/调研-双卡QMI-20261101.md §5）。
# 用法（手机上，root）：fuxi-qmi-sub2-msg.py [秒数]   秒数默认 0 = 一直跑（作服务用）；VOICE/WMS 枚举 0 主 1 副。
import fcntl
import json
import os
import signal
import stat
import sys
import time
from contextlib import ExitStack

SUB = 1
SMS_FILE = "/var/log/fuxi-sim2-sms.jsonl"  # 原始 PDU（含号码和正文），只给 root 读写
WMS_CLIENT = None
STORED_READS = {}
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


def parse_frame(b):
    # QMUX 头 6 字节（标记、长度、控制标志、服务、CID）+ QMI 头 7 字节（标志、事务号 2、消息 ID 2、TLV 总长 2）
    if len(b) < 13 or b[0] != 0x01:
        return None
    if int.from_bytes(b[1:3], "little") + 1 != len(b):
        return None
    svc, cid, txn = b[4], b[5], int.from_bytes(b[7:9], "little")
    msg_id, tlv_len = int.from_bytes(b[9:11], "little"), int.from_bytes(b[11:13], "little")
    if tlv_len != len(b) - 13:
        return None
    tlvs, o = {}, 13
    while o < len(b):
        if len(b) - o < 3:
            return None
        t, ln = b[o], int.from_bytes(b[o + 1:o + 3], "little")
        if ln > len(b) - o - 3 or t in tlvs:
            return None
        tlvs[t] = b[o + 3:o + 3 + ln]
        o += 3 + ln
    return svc, cid, txn, msg_id, tlvs


def describe(svc, msg_id, tlvs):
    # 只解码不含号码和正文的字段
    out = []
    if svc == 0x05 and msg_id == 0x0001:
        if tlvs.get(0x10):
            out.append(f"stored:storage={tlvs[0x10][0]}")
        d = tlvs.get(0x11)
        if d and len(d) >= 8:
            out.append(f"transfer-route:ack_ind={d[0]},format={d[5]},pdu_len={int.from_bytes(d[6:8], 'little')}")
    return "; ".join(out)


def ack_cb(src, res):
    try:
        src.send_ack_finish(res).get_result()
        log("sent WMS ack")
    except GLib.Error as e:
        log(f"WMS ack failed: {e.message}")


def persist_sms(txn, fmt, pdu, storage=None):
    row = {"t": time.strftime("%F %T"), "txn": txn, "format": fmt, "pdu": pdu.hex()}
    flags = os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open(SMS_FILE, flags | (os.O_WRONLY if storage is None else os.O_RDWR), 0o600)
    with os.fdopen(fd, "ab" if storage is None else "a+b") as stream:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise OSError("SMS log must be private")
        fcntl.flock(fd, fcntl.LOCK_EX)
        duplicate = False
        if storage is not None:
            row["storage"] = storage
            stream.seek(0)
            terminated = True
            for line in stream:
                terminated = line.endswith(b"\n")
                try:
                    previous = json.loads(line)
                except (ValueError, RecursionError):
                    continue
                if isinstance(previous, dict) and previous.get("storage") == storage and previous.get("txn") == txn:
                    duplicate = previous.get("format") == fmt and previous.get("pdu") == row["pdu"]
            if not terminated:
                stream.write(b"\n")
        if not duplicate:
            stream.write(json.dumps(row).encode("ascii") + b"\n")
        stream.flush()
        os.fsync(fd)
        directory_fd = os.open(os.path.dirname(SMS_FILE) or ".", os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


def stored_tpdu(data):
    raw_pdu = bytes(data)
    if not raw_pdu:
        raise ValueError("empty stored message")
    tpdu = raw_pdu[1 + raw_pdu[0]:]
    if len(tpdu) < 3 or tpdu[0] & 3 or not 1 <= tpdu[1] <= 20 or not tpdu[2] & 0x80:
        raise ValueError("invalid SMS-DELIVER header")
    header_len = 3 + (tpdu[1] + 1) // 2 + 10
    if not header_len <= len(tpdu) <= header_len + 140:
        raise ValueError("invalid SMS-DELIVER size")
    dcs, udl = tpdu[header_len - 9], tpdu[header_len - 1]
    if dcs < 0x80 and not dcs & 0x20:
        alphabet = (dcs >> 2) & 3
    elif 0xc0 <= dcs < 0xf0:
        alphabet = 2 if dcs >= 0xe0 else 0
    elif 0xf0 <= dcs < 0xf8:
        alphabet = 1 if dcs & 4 else 0
    else:
        alphabet = 3
    if alphabet != 3 and len(tpdu) - header_len != ((udl * 7 + 7) // 8 if alphabet == 0 else udl):
        raise ValueError("invalid SMS-DELIVER user data length")
    return tpdu


def read_stored(client, storage, index):
    key = (client, storage, index)
    if key in STORED_READS:
        STORED_READS[key] = True
        return
    STORED_READS[key] = False

    def complete(source, res):
        try:
            output = source.raw_read_finish(res)
            if source != WMS_CLIENT or output.get_result() is False:
                return
            tag, fmt, data = output.get_raw_message_data()
            if tag in (Qmi.WmsMessageTagType.MT_READ, Qmi.WmsMessageTagType.MT_NOT_READ) and (
                    fmt == Qmi.WmsMessageFormat.GSM_WCDMA_POINT_TO_POINT):
                persist_sms(index, int(fmt), stored_tpdu(data), storage)
        except (GLib.Error, OSError, ValueError):
            log("stored MT read or persistence failed")
        finally:
            if STORED_READS.pop(key) and source == WMS_CLIENT:
                read_stored(client, storage, index)

    try:
        request = Qmi.MessageWmsRawReadInput()
        request.set_message_memory_storage_id(Qmi.WmsStorageType(storage), index)
        request.set_message_mode(Qmi.WmsMessageMode.GSM_WCDMA)
        client.raw_read(request, 10, None, complete)
    except GLib.Error:
        STORED_READS.pop(key)
        log("stored MT read unavailable")


def handle_transfer_route(d):
    # TLV 0x11：ack 指示 1B（0=要应答）、transaction id 4B、格式 1B、长度 2B、PDU（与 libqmi 的 getter 一致）。
    # 先把 PDU 落盘再应答：应答后网络不会重发，没存下就丢了。和 MM 一样，格式 CDMA 以外一律按 WCDMA 应答。
    if len(d) < 8:
        log("invalid transfer-route header; not saving or acking")
        return
    pdu_len = int.from_bytes(d[6:8], "little")
    if d[0] not in (0, 1) or not pdu_len or pdu_len != len(d) - 8:
        log("invalid transfer-route length or ack indicator; not saving or acking")
        return
    ack_ind, txn, fmt = d[0], int.from_bytes(d[1:5], "little"), d[5]
    pdu = d[8:]
    try:
        persist_sms(txn, fmt, pdu)
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
            if len(a) > 4 and a[4] == 0x09:
                return
            f = parse_frame(bytes(a))
            if f is None:
                log(f"indication unparsed len={len(a)}")
                return
            svc, cid, txn, msg_id, tlvs = f
            if svc == 0x05 and (WMS_CLIENT is None or cid != WMS_CLIENT.get_cid()):
                return
            shape = " ".join(f"{t:02x}:{len(v)}" for t, v in sorted(tlvs.items()))
            log(f"{SVC_NAMES.get(svc, svc)} cid={cid} txn={txn} msg=0x{msg_id:04x} tlvs[{shape}] {describe(svc, msg_id, tlvs)}")
            if svc == 0x05 and msg_id == 0x0001 and a[6] == 4:
                if 0x11 in tlvs:
                    handle_transfer_route(tlvs[0x11])
                elif 0x10 in tlvs and tlvs.get(0x12, b"\x01") == b"\x01":
                    stored = tlvs[0x10]
                    if len(stored) == 5 and stored[0] in (0, 1):
                        read_stored(WMS_CLIENT, stored[0], int.from_bytes(stored[1:], "little"))
            return
    log("indication args:", [type(a).__name__ for a in args])


def listen(watchdog, duration, glib, unix):
    loop = glib.MainLoop()
    sources = []
    failed = False

    def stop(*_):
        loop.quit()
        return False

    def check():
        nonlocal failed
        try:
            watchdog()
        except Exception as error:
            log(f"watchdog failed ({error}); exiting for restart")
            failed = True
            return stop()
        return True

    try:
        signal_add = getattr(unix, "signal_add", None) or glib.unix_signal_add
        for sig in (signal.SIGTERM, signal.SIGINT):
            sources.append(signal_add(glib.PRIORITY_DEFAULT, sig, stop))
        # 基带或 QRTR 重启后 client 会失效；失败退出后由 systemd 重新绑定。
        sources.append(glib.timeout_add_seconds(30, check))
        if duration:
            sources.append(glib.timeout_add_seconds(duration, stop))
        loop.run()
    finally:
        context = glib.MainContext.default()
        for source in sources:
            if context.find_source_by_id(source) is not None:
                glib.source_remove(source)
    return 1 if failed else 0


def check_wms_binding(dev, client):
    binding = tlv_bytes(raw(dev, client, WMS_GET_BINDING), 0x10)
    if binding != bytes([SUB]):
        raise RuntimeError("WMS secondary subscription binding not confirmed")


def register_voice_listener(resources, dev, voice, unix):
    from fuxi_qmi_bind import bind_subscription
    from fuxi_sub2_call_cache import CallCache, serve
    from fuxi_sub2_control import _observe_calls, _register_calls

    cache = None
    if os.environ.get('RUNTIME_DIRECTORY') == '/run/fuxi-sub2-msg':
        cache = CallCache(os.environ.get('INVOCATION_ID'))

    removed = False

    def device_removed(*_):
        nonlocal removed
        removed = True
        if cache is not None:
            cache.observe(None)

    def receive(calls):
        if removed:
            return
        if cache is not None:
            cache.observe(calls)
        log('VOICE calls', list(calls.values()) if calls is not None else 'unknown')

    resources.callback(dev.disconnect, dev.connect('device-removed', device_removed))
    bind_subscription(Qmi, wait, dev, voice, VOICE_BIND, SUB)
    _observe_calls(resources, Qmi, dev, voice, receive)
    _register_calls(Qmi, wait, voice, 10)
    if removed:
        raise RuntimeError('VOICE device removed during registration')
    if cache is not None:
        serve(resources, unix, GLib, cache)


def main():
    run_s = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    if run_s < 0:
        sys.exit("duration must be nonnegative")
    import gi
    gi.require_version("Qmi", "1.0")
    gi.require_version("Qrtr", "1.0")
    gi.require_version("GLibUnix", "2.0")
    global GLib, Qmi, Qrtr, ctx
    from gi.repository import GLib, GLibUnix, Qmi, Qrtr
    ctx = GLib.MainContext.default()
    bus = Qrtr.Bus.new_finish(wait(Qrtr.Bus.new, 1000, None)[1])
    node = bus.peek_node(0)
    if node is None:
        sys.exit("qrtr node 0 not found")
    dev = Qmi.Device.new_from_node_finish(wait(Qmi.Device.new_from_node, node, None)[1])
    dev.open_finish(wait(dev.open, Qmi.DeviceOpenFlags.PROXY, 10, None)[1])
    def release(client):
        dev.release_client_finish(wait(dev.release_client, client, Qmi.DeviceReleaseClientFlags.RELEASE_CID, 10, None)[1])

    def forget_wms():
        global WMS_CLIENT
        WMS_CLIENT = None

    global WMS_CLIENT
    with ExitStack() as resources:
        resources.callback(lambda: dev.close_finish(wait(dev.close_async, 10, None)[1]))
        voice = dev.allocate_client_finish(wait(dev.allocate_client, Qmi.Service.VOICE, Qmi.CID_NONE, 10, None)[1])
        resources.callback(release, voice)
        wms = dev.allocate_client_finish(wait(dev.allocate_client, Qmi.Service.WMS, Qmi.CID_NONE, 10, None)[1])
        resources.callback(release, wms)
        raw(dev, wms, WMS_BIND, [(0x01, bytes([SUB]))])
        check_wms_binding(dev, wms)
        WMS_CLIENT = wms
        resources.callback(forget_wms)
        resources.callback(dev.disconnect, dev.connect("indication", on_indication))
        inp = Qmi.MessageWmsSetEventReportInput()
        inp.set_new_mt_message_indicator(True)
        wms.set_event_report_finish(wait(wms.set_event_report, inp, 10, None)[1]).get_result()
        register_voice_listener(resources, dev, voice, GLibUnix)
        log(f"registered WMS MT-message and VOICE call notifications for sub {SUB}")
        return listen(lambda: check_wms_binding(dev, wms), run_s, GLib, GLibUnix)


if __name__ == "__main__":
    sys.exit(main())
