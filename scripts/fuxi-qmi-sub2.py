#!/usr/bin/python3
# 卡槽 2（副卡订阅）短信/语音工具。ModemManager 只管卡槽 1，副卡的短信和通话从这里走。
# libqmi 1.38 只有 WDS/QoS 有 bind-subscription，WMS/VOICE 没有，这里发原始 QMI 消息：
#   VOICE bind subscription 0x0044，TLV 0x01 u8（0 主 / 1 副 / 2 第三），来源 wroomd voice.rs
#   WMS   bind subscription 0x004C，TLV 0x01 u8（同上），未公开文档，probe 子命令实测
# qrtr 下每个进程是独立端口，bind 和后续请求必须在同一进程里。
# 用法（手机上，root）：
#   fuxi-qmi-sub2.py probe                     只做 bind，打印结果
#   fuxi-qmi-sub2.py sms NUMBER TEXT [WAIT]    发短信（SMS over IMS），再等 WAIT 秒收短信
#   fuxi-qmi-sub2.py listen [WAIT]             只收短信
#   fuxi-qmi-sub2.py dial NUMBER [SECONDS]     拨号，接通后保持 SECONDS 秒再挂断
#   fuxi-qmi-sub2.py watch [SECS] [HOLD]       收短信 + 来电 3 s 后自动接听，HOLD 秒后挂断
# 环境变量 FUXI_SUB=0|1 选订阅（默认 1 = 卡槽 2）。号码只在屏幕上打码显示。
import os
import struct
import sys
import time
import gi
gi.require_version("Qmi", "1.0")
gi.require_version("Qrtr", "1.0")
from gi.repository import GLib, Qmi, Qrtr

SUB = int(os.environ.get("FUXI_SUB", "1"))
ctx = GLib.MainContext.default()


def mask(num):
    return num if len(num) <= 5 else num[:3] + "*" * (len(num) - 5) + num[-2:]


def wait(start, *args):
    box = []
    start(*args, lambda src, res: box.append((src, res)))
    while not box:
        ctx.iteration(True)
    return box[0]


def call(fn_name, obj, *args):
    src, res = wait(getattr(obj, fn_name), *args)
    return getattr(obj, fn_name + "_finish")(res)


def pump(seconds, done=lambda: False):
    end = time.monotonic() + seconds
    while time.monotonic() < end and not done():
        ctx.iteration(False) or time.sleep(0.05)


bus = Qrtr.Bus.new_finish(wait(Qrtr.Bus.new, 1000, None)[1])
node = bus.peek_node(0)
if node is None:
    sys.exit("qrtr node 0 not found")
dev = Qmi.Device.new_from_node_finish(wait(Qmi.Device.new_from_node, node, None)[1])
call("open", dev, Qmi.DeviceOpenFlags.PROXY, 10, None)
clients = []


def client(service):
    c = call("allocate_client", dev, service, Qmi.CID_NONE, 10, None)
    clients.append(c)
    return c


def raw_bind(c, service, msg_id):
    # GI 里 qmi_message_add_raw_tlv 的 buffer 参数注解坏了，用 tlv_write_* 逐字节写
    msg = Qmi.message_new(service, c.get_cid(), c.get_next_transaction_id(), msg_id)
    off = Qmi.message_tlv_write_init(msg, 0x01)
    Qmi.message_tlv_write_guint8(msg, SUB)
    Qmi.message_tlv_write_complete(msg, off)
    src, res = wait(dev.command_full, msg, Qmi.MessageContext.new(), 10, None)
    resp = dev.command_full_finish(res)
    toff = Qmi.message_tlv_read_init(resp, 0x02)[0]
    _, o, result = Qmi.message_tlv_read_guint16(resp, toff, 0, Qmi.Endian.LITTLE)
    _, o, error = Qmi.message_tlv_read_guint16(resp, toff, o, Qmi.Endian.LITTLE)
    name = Qmi.ProtocolError(error).value_nick if result else "ok"
    print(f"bind {service.value_nick} 0x{msg_id:04x} sub={SUB}: {name}")
    return result == 0


def gv(r, n=1):
    # libqmi GI 的 getter 有的返回 (ok, v...)，有的直接返回 v...：统一取最后 n 个
    r = r if isinstance(r, tuple) else (r,)
    return r[-1] if n == 1 else r[-n:]


def cleanup():
    for c in clients:
        try:
            call("release_client", dev, c, Qmi.DeviceReleaseClientFlags.RELEASE_CID, 10, None)
        except GLib.Error:
            pass


# ---------- 短信 PDU ----------
GSM7 = ("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
        "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")


def bcd(num):
    d = num + ("F" if len(num) % 2 else "")
    return bytes(int(d[i + 1] + d[i], 16) for i in range(0, len(d), 2))


def unbcd(data, ndigits):
    s = "".join(f"{b & 0xF:X}{b >> 4:X}" for b in data)
    return s[:ndigits]


def pack7(septets):
    out, acc, bits = bytearray(), 0, 0
    for s in septets:
        acc |= s << bits
        bits += 7
        while bits >= 8:
            out.append(acc & 0xFF)
            acc >>= 8
            bits -= 8
    if bits:
        out.append(acc & 0xFF)
    return bytes(out)


def unpack7(data, count, skip_bits=0):
    acc = int.from_bytes(data, "little") >> skip_bits
    return "".join(GSM7[(acc >> (7 * i)) & 0x7F] if ((acc >> (7 * i)) & 0x7F) < len(GSM7) else "?"
                   for i in range(count))


def submit_pdu(number, text):
    toa = 0x91 if number.startswith("+") else 0x81
    digits = number.lstrip("+")
    if all(ch in GSM7 and ch != "\x1b" for ch in text):
        dcs, ud = 0x00, pack7([GSM7.index(ch) for ch in text])
        udl = len(text)
    else:
        dcs, ud = 0x08, text.encode("utf-16-be")
        udl = len(ud)
    return (bytes([0x00, 0x01, 0x00, len(digits), toa]) + bcd(digits)
            + bytes([0x00, dcs, udl]) + ud)


def parse_deliver(pdu, smsc=True):
    # transfer-route 上报的是裸 TPDU（实测没有 SMSC 前缀），raw_read 读存储的带 SMSC
    pdu = bytes(pdu)
    i = 1 + pdu[0] if smsc else 0
    fo = pdu[i]; i += 1
    if fo & 0x03 != 0:
        return None, f"(非 SMS-DELIVER，首字节 0x{fo:02x})"
    n = pdu[i]; toa = pdu[i + 1]; i += 2
    if toa & 0x70 == 0x50:
        sender = unpack7(pdu[i:i + (n + 1) // 2], n * 4 // 7)
    else:
        sender = unbcd(pdu[i:i + (n + 1) // 2], n)
    i += (n + 1) // 2
    i += 1                              # PID
    dcs = pdu[i]; i += 1
    i += 7                              # SCTS
    udl = pdu[i]; i += 1
    ud = pdu[i:]
    hdr, part = 0, None
    if fo & 0x40:
        hdr = ud[0] + 1
        h = ud[1:hdr]
        if len(h) >= 5 and h[0] == 0x00:
            part = (h[2], h[3], h[4])          # (ref, total, seq)
        elif len(h) >= 6 and h[0] == 0x08:
            part = ((h[2] << 8) | h[3], h[4], h[5])
    if dcs & 0x0C == 0x08:
        text = ud[hdr:udl].decode("utf-16-be", "replace")
    elif dcs & 0x0C == 0x04:
        text = ud[hdr:udl].decode("latin-1")
    else:
        skip = hdr * 8
        fill = (7 - skip % 7) % 7
        text = unpack7(ud, udl, 0)[(skip + fill) // 7:] if hdr else unpack7(ud, udl)
    return sender, text, part


# ---------- 短信 ----------
def wms_client():
    c = client(Qmi.Service.WMS)
    if not raw_bind(c, Qmi.Service.WMS, 0x004C):
        raise SystemExit("WMS bind failed")
    return c


def send_sms(c, number, text):
    pdu = submit_pdu(number, text)
    inp = Qmi.MessageWmsRawSendInput.new()
    inp.set_raw_message_data(Qmi.WmsMessageFormat.GSM_WCDMA_POINT_TO_POINT, list(pdu))
    inp.set_sms_on_ims(True)
    try:
        out = call("raw_send", c, inp, 60, None)
        out.get_result()
        ref = gv(out.get_message_id())
        print(f"sms sent to {mask(number)}: message ref {ref}")
        return True
    except GLib.Error as e:
        print(f"sms send failed: {e.message}")
        try:
            print(f"  cause: {out.get_cause_code()} {out.get_error_class()}")
        except Exception:
            pass
        return False


def listen_sms(c, seconds):
    got = []
    inp = Qmi.MessageWmsSetEventReportInput.new()
    inp.set_new_mt_message_indicator(True)
    call("set_event_report", c, inp, 10, None).get_result()

    parts = {}

    def show(raw, fmt=None, smsc=True):
        if os.environ.get("FUXI_DEBUG"):
            print(f"    fmt={getattr(fmt, 'value_nick', fmt)} len={len(raw)} hex={bytes(raw)[:40].hex()}")
        try:
            sender, text, part = parse_deliver(raw, smsc)
        except (IndexError, ValueError) as e:
            sender, text, part = None, f"(解析失败 {e!r})", None
        if part:
            ref, total, seq = part
            buf = parts.setdefault((sender, ref), {})
            buf[seq] = text
            print(f"  part {seq}/{total} from {mask(sender or '?')}")
            if len(buf) < total:
                return
            text = "".join(buf[k] for k in sorted(buf))
            del parts[(sender, ref)]
        print(f"sms from {mask(sender or '?')}: {text}")
        got.append(text)

    def on_report(_c, out):
        print(f"  [{time.strftime('%H:%M:%S')}] wms event-report")
        try:
            ack, tid, fmt, raw = gv(out.get_transfer_route_mt_message(), 4)
            if raw:
                show(raw, fmt, smsc=False)
                if ack == Qmi.WmsAckIndicator.SEND:
                    a = Qmi.MessageWmsSendAckInput.new()
                    a.set_information(tid, Qmi.WmsMessageProtocol.WCDMA, True)
                    a.set_sms_on_ims(True)
                    c.send_ack(a, 10, None, None)
                return
        except (GLib.Error, TypeError, ValueError) as e:
            if os.environ.get("FUXI_DEBUG"):
                print(f"    transfer-route: {e!r}")
        try:
            storage, index = gv(out.get_mt_message(), 2)
        except (GLib.Error, TypeError, ValueError) as e:
            if os.environ.get("FUXI_DEBUG"):
                print(f"    mt-message: {e!r}")
            return
        r = Qmi.MessageWmsRawReadInput.new()
        r.set_message_memory_storage_id(storage, index)
        r.set_message_mode(Qmi.WmsMessageMode.GSM_WCDMA)
        c.raw_read(r, 10, None, lambda cl, res: on_read(cl, res, storage, index))

    def on_read(cl, res, storage, index):
        try:
            o = cl.raw_read_finish(res)
            o.get_result()
            tag, fmt, raw = gv(o.get_raw_message_data(), 3)
            print(f"  (stored {storage.value_nick} #{index})")
            show(raw)
        except GLib.Error as e:
            print(f"raw read failed: {e.message}")

    c.connect("event-report", on_report)
    if seconds:
        print(f"listening {seconds}s ...")
        pump(seconds)
    return got


def list_stored(c):
    for storage in (Qmi.WmsStorageType.UIM, Qmi.WmsStorageType.NV):
        inp = Qmi.MessageWmsListMessagesInput.new()
        inp.set_storage_type(storage)
        inp.set_message_mode(Qmi.WmsMessageMode.GSM_WCDMA)
        try:
            out = call("list_messages", c, inp, 10, None)
            out.get_result()
            lst = gv(out.get_message_list())
            print(f"stored {storage.value_nick}: {len(lst)} messages")
        except GLib.Error as e:
            print(f"stored {storage.value_nick}: {e.message}")


# ---------- 语音 ----------
def voice_client():
    c = client(Qmi.Service.VOICE)
    if not raw_bind(c, Qmi.Service.VOICE, 0x0044):
        raise SystemExit("VOICE bind failed")
    inp = Qmi.MessageVoiceIndicationRegisterInput.new()
    inp.set_call_notification_events(True)
    call("indication_register", c, inp, 10, None).get_result()
    return c


def dial(c, number, hold):
    states = []

    def on_status(_c, out):
        try:
            info = gv(out.get_call_information())
        except (GLib.Error, TypeError, ValueError):
            return
        for ci in info or []:
            states.append((ci.id, ci.state))
            nick = lambda v: getattr(v, "value_nick", v)
            ctype = getattr(ci, "type", getattr(ci, "call_type", "?"))
            s = f"id {ci.id} {nick(ci.state)} {nick(ctype)} {nick(ci.direction)}"
            print(f"  [{time.strftime('%H:%M:%S')}] {s}")

    c.connect("all-call-status", on_status)
    inp = Qmi.MessageVoiceDialCallInput.new()
    inp.set_calling_number(number)
    try:
        out = call("dial_call", c, inp, 30, None)
        out.get_result()
        cid = gv(out.get_call_id())
    except GLib.Error as e:
        print(f"dial failed: {e.message}")
        return
    print(f"dialing {mask(number)}: call id {cid}")
    active = lambda: any(i == cid and s == Qmi.VoiceCallState.CONVERSATION for i, s in states)
    ended = lambda: any(i == cid and s in (Qmi.VoiceCallState.END, Qmi.VoiceCallState.DISCONNECTING)
                        for i, s in states)
    pump(40, lambda: active() or ended())
    if active():
        print(f"active, holding {hold}s")
        pump(hold, ended)
    if not ended():
        e = Qmi.MessageVoiceEndCallInput.new()
        e.set_call_id(cid)
        try:
            call("end_call", c, e, 10, None).get_result()
            print("hung up")
        except GLib.Error as err:
            print(f"end failed: {err.message}")
        pump(3, ended)


def auto_answer(c, hold):
    """来电（mt incoming）自动接听，HOLD 秒后挂断；号码打码。"""
    seen = {}
    nick = lambda v: getattr(v, "value_nick", v)

    def end(cid):
        e = Qmi.MessageVoiceEndCallInput.new()
        e.set_call_id(cid)
        c.end_call(e, 10, None, None)
        print(f"  [{time.strftime('%H:%M:%S')}] hang up id {cid}")
        return False

    def on_answer(cl, res, cid):
        try:
            cl.answer_call_finish(res).get_result()
            print(f"  [{time.strftime('%H:%M:%S')}] answered id {cid}, hold {hold}s")
            GLib.timeout_add_seconds(hold, end, cid)
        except GLib.Error as e:
            print(f"  answer failed: {e.message}")

    def on_status(_c, out):
        try:
            info = gv(out.get_call_information())
        except (GLib.Error, TypeError, ValueError):
            return
        nums = {}
        try:
            for r in gv(out.get_remote_party_number()) or []:
                nums[r.id] = getattr(r, "number", "") or "?"
        except (GLib.Error, TypeError, ValueError, AttributeError):
            pass
        for ci in info or []:
            ctype = getattr(ci, "type", getattr(ci, "call_type", "?"))
            if seen.get(ci.id) != ci.state:
                seen[ci.id] = ci.state
                who = mask(nums[ci.id]) if ci.id in nums else ""
                print(f"  [{time.strftime('%H:%M:%S')}] id {ci.id} {nick(ci.state)} "
                      f"{nick(ctype)} {nick(ci.direction)} {who}", flush=True)
            if ci.state == Qmi.VoiceCallState.INCOMING and ("ans", ci.id) not in seen:
                seen[("ans", ci.id)] = True
                a = Qmi.MessageVoiceAnswerCallInput.new()
                a.set_call_id(ci.id)
                GLib.timeout_add_seconds(3, lambda cid=ci.id, a=a: (
                    c.answer_call(a, 10, None, lambda cl, res: on_answer(cl, res, cid)), False)[1])

    c.connect("all-call-status", on_status)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "probe"
    try:
        if cmd == "probe":
            raw_bind(client(Qmi.Service.WMS), Qmi.Service.WMS, 0x004C)
            raw_bind(client(Qmi.Service.VOICE), Qmi.Service.VOICE, 0x0044)
        elif cmd == "sms":
            c = wms_client()
            if send_sms(c, sys.argv[2], sys.argv[3]):
                listen_sms(c, int(sys.argv[4]) if len(sys.argv) > 4 else 60)
        elif cmd == "listen":
            c = wms_client()
            list_stored(c)
            listen_sms(c, int(sys.argv[2]) if len(sys.argv) > 2 else 60)
        elif cmd == "watch":
            secs = int(sys.argv[2]) if len(sys.argv) > 2 else 600
            listen_sms(wms_client(), 0)
            auto_answer(voice_client(), int(sys.argv[3]) if len(sys.argv) > 3 else 20)
            print(f"watching sms + calls {secs}s ...", flush=True)
            pump(secs)
        elif cmd == "dial":
            dial(voice_client(), sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 5)
        else:
            sys.exit(__doc__ or "usage: probe | sms N T [W] | listen [W] | dial N [S] | watch [SECS] [HOLD]")
    finally:
        cleanup()


if __name__ == "__main__":
    main()
