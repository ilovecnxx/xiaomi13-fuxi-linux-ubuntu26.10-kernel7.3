#!/usr/bin/python3
# 卡 2（订阅 1）拨出一通电话，N 秒后挂断。会产生通话费，只在用户确认后运行。没有通话音频通路，只验证呼叫建立/振铃/接通/挂断。
# 用法（手机上，root）：fuxi-qmi-sub2-dial.py <号码> [保持秒数，默认 20]
# 号码不写进输出；输出通话状态变化（origination/alerting/conversation/...）和 End Call 结果。退出前一定尝试挂断。
import re
import signal
import sys
import time
import gi

SUB = 1
VOICE_BIND = 0x0044
CALL_STATES = {1: "origination", 2: "incoming", 3: "conversation", 4: "cc-in-progress", 5: "alerting",
               6: "hold", 7: "waiting", 8: "disconnecting", 9: "end", 10: "setup"}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def main():
    if len(sys.argv) not in (2, 3) or not re.fullmatch(r"\+?[0-9]{3,15}", sys.argv[1]):
        sys.exit(__doc__)
    number, hold = sys.argv[1], int(sys.argv[2]) if len(sys.argv) == 3 else 20
    gi.require_version("Qmi", "1.0")
    gi.require_version("Qrtr", "1.0")
    from gi.repository import GLib, Qmi, Qrtr
    ctx = GLib.MainContext.default()

    def wait(start, *a):
        box = []
        start(*a, lambda src, res: box.append((src, res)))
        while not box:
            ctx.iteration(True)
        return box[0]

    bus = Qrtr.Bus.new_finish(wait(Qrtr.Bus.new, 1000, None)[1])
    node = bus.peek_node(0)
    if node is None:
        sys.exit("qrtr node 0 not found")
    dev = Qmi.Device.new_from_node_finish(wait(Qmi.Device.new_from_node, node, None)[1])
    dev.open_finish(wait(dev.open, Qmi.DeviceOpenFlags.PROXY, 10, None)[1])
    voice = dev.allocate_client_finish(wait(dev.allocate_client, Qmi.Service.VOICE, Qmi.CID_NONE, 10, None)[1])
    call_id = []
    states = []

    def on_ind(_dev, *args):
        for a in args:
            b = bytes(a) if isinstance(a, (bytes, bytearray)) else None
            if b is None or len(b) < 13 or b[0] != 0x01 or b[4] != 0x09 or b[5] != voice.get_cid():
                continue
            if int.from_bytes(b[9:11], "little") != 0x002E:
                continue
            o = 13
            while o + 3 <= len(b):
                t, ln = b[o], int.from_bytes(b[o + 1:o + 3], "little")
                if t == 0x01 and ln >= 1:
                    d = b[o + 3:o + 3 + ln]
                    for i in range(d[0]):
                        c = d[1 + 7 * i:8 + 7 * i]
                        if len(c) == 7:
                            log(f"call{c[0]}: {CALL_STATES.get(c[1], c[1])} type={c[2]} dir={c[3]} mode={c[4]}")
                            states.append(CALL_STATES.get(c[1], c[1]))
                o += 3 + ln
    dev.connect("indication", on_ind)

    def end_call():
        if not call_id:
            return
        try:
            inp = Qmi.MessageVoiceEndCallInput()
            inp.set_call_id(call_id[0])
            out = voice.end_call_finish(wait(voice.end_call, inp, 10, None)[1])
            out.get_result()
            log("end call OK")
        except GLib.Error as e:
            log(f"end call: {e.message}")

    try:
        m = Qmi.message_new(voice.get_service(), voice.get_cid(), voice.get_next_transaction_id(), VOICE_BIND)
        off = Qmi.message_tlv_write_init(m, 0x01)
        Qmi.message_tlv_write_guint8(m, SUB)
        Qmi.message_tlv_write_complete(m, off)
        dev.command_full_finish(wait(dev.command_full, m, Qmi.MessageContext.new(), 10, None)[1])
        log(f"VOICE bound to sub {SUB}")
        vin = Qmi.MessageVoiceIndicationRegisterInput()
        vin.set_call_notification_events(True)
        voice.indication_register_finish(wait(voice.indication_register, vin, 10, None)[1])
        dial = Qmi.MessageVoiceDialCallInput()
        dial.set_calling_number(number)
        log(f"dialing (hold {hold}s)")
        try:
            out = voice.dial_call_finish(wait(voice.dial_call, dial, 30, None)[1])
            out.get_result()
            call_id.append(out.get_call_id())
            log(f"dial accepted, call_id={call_id[0]}")
        except GLib.Error as e:
            log(f"dial FAILED: {e.message}")
            sys.exit(1)
        stop = []
        signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
        signal.signal(signal.SIGINT, lambda *_: stop.append(1))
        deadline = time.time() + hold
        while time.time() < deadline and not stop and "end" not in states:
            ctx.iteration(False)
            time.sleep(0.05)
        if "end" not in states:
            end_call()
        t_end = time.time() + 3
        while time.time() < t_end:
            ctx.iteration(False)
            time.sleep(0.05)
        log("states seen: " + " > ".join(states))
    finally:
        try:
            wait(dev.release_client, voice, Qmi.DeviceReleaseClientFlags.RELEASE_CID, 10, None)
        except Exception:
            pass


main()
