#!/usr/bin/python3
# 卡 2（订阅 1）接听一通来电，保持 N 秒后挂断。一次性工具，不是常驻服务；只接听本进程运行期间出现的第一通来电。
# 用法（手机上，root）：fuxi-qmi-sub2-answer.py [等待来电秒数，默认 90] [通话保持秒数，默认 15]
# 没有通话音频通路，只验证接听/挂断。输出只含通话状态，不含号码。退出前一定尝试挂断。
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
    wait_s = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    hold = int(sys.argv[2]) if len(sys.argv) > 2 else 15
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
    seen = []        # (call_id, state 名)
    incoming = []    # 第一通来电的 call_id

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
                            name = CALL_STATES.get(c[1], c[1])
                            if not seen or seen[-1] != (c[0], name):
                                log(f"call{c[0]}: {name} type={c[2]} dir={c[3]}")
                                seen.append((c[0], name))
                            if name == "incoming" and not incoming:
                                incoming.append(c[0])
                o += 3 + ln
    dev.connect("indication", on_ind)

    def ended():
        return incoming and (incoming[0], "end") in seen

    def pump(until, cond=lambda: False):
        while time.time() < until and not cond() and not stop:
            ctx.iteration(False)
            time.sleep(0.05)

    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    signal.signal(signal.SIGINT, lambda *_: stop.append(1))
    answered = False
    try:
        m = Qmi.message_new(voice.get_service(), voice.get_cid(), voice.get_next_transaction_id(), VOICE_BIND)
        off = Qmi.message_tlv_write_init(m, 0x01)
        Qmi.message_tlv_write_guint8(m, SUB)
        Qmi.message_tlv_write_complete(m, off)
        dev.command_full_finish(wait(dev.command_full, m, Qmi.MessageContext.new(), 10, None)[1])
        vin = Qmi.MessageVoiceIndicationRegisterInput()
        vin.set_call_notification_events(True)
        voice.indication_register_finish(wait(voice.indication_register, vin, 10, None)[1])
        log(f"VOICE bound to sub {SUB}; waiting up to {wait_s}s for an incoming call")
        pump(time.time() + wait_s, lambda: bool(incoming))
        if not incoming:
            log("no incoming call; exit")
            return
        inp = Qmi.MessageVoiceAnswerCallInput()
        inp.set_call_id(incoming[0])
        try:
            voice.answer_call_finish(wait(voice.answer_call, inp, 15, None)[1]).get_result()
            answered = True
            log("answer OK")
        except GLib.Error as e:
            log(f"answer FAILED: {e.message}")
            return
        pump(time.time() + hold, ended)
    finally:
        if answered and not ended():
            try:
                inp = Qmi.MessageVoiceEndCallInput()
                inp.set_call_id(incoming[0])
                voice.end_call_finish(wait(voice.end_call, inp, 10, None)[1]).get_result()
                log("end call OK")
            except GLib.Error as e:
                log(f"end call: {e.message}")
            pump(time.time() + 3)
        log("states seen: " + " > ".join(n for _, n in seen))
        try:
            wait(dev.release_client, voice, Qmi.DeviceReleaseClientFlags.RELEASE_CID, 10, None)
        except Exception:
            pass


main()
