#!/usr/bin/python3
# 卡 2（订阅 1）发一条短信（SMS-SUBMIT，经 IMS）。会产生短信费用，只在用户确认后运行。
# 用法（手机上，root）：fuxi-qmi-sub2-send.py [--dry-run] [--no-ims] <号码> <正文>
#   --dry-run  只组包，打印长度，不碰基带；--no-ims 不设 sms_on_ims（走 CS/NAS 路径，排障用）
# 号码和正文不写进输出；只输出 PDU 长度、传输注册状态、基带返回码。
# 绑定只对本进程的 client 生效，所以用独立进程新开 WMS client 并绑到订阅 1，不影响 fuxi-sub2-msg.service。
import re
import sys
import time
import gi

SUB = 1
WMS_BIND, WMS_GET_BINDING = 0x004C, 0x0058
GSM7_SAFE = re.compile(r"^[A-Za-z0-9 .,!?:;\-+/()%*=#&\"']*$")  # 这些字符在 GSM 7 位默认字母表里与 ASCII 同码


def encode_number(num):
    toa = 0x91 if num.startswith("+") else 0x81
    digits = num.lstrip("+")
    if len(digits) % 2:
        digits += "F"
    bcd = bytes(int(digits[i + 1] + digits[i], 16) for i in range(0, len(digits), 2))
    return toa, bcd


def pack_gsm7(text):
    bits, nbits, out = 0, 0, bytearray()
    for ch in text:
        bits |= (ord(ch) & 0x7F) << nbits
        nbits += 7
        while nbits >= 8:
            out.append(bits & 0xFF)
            bits >>= 8
            nbits -= 8
    if nbits:
        out.append(bits & 0xFF)
    return bytes(out)


def build_pdu(number, text):
    if not re.fullmatch(r"\+?[0-9]{3,15}", number):
        raise ValueError("bad number")
    toa, bcd = encode_number(number)
    ndigits = len(number.lstrip("+"))
    if GSM7_SAFE.match(text):
        dcs, ud, udl = 0x00, pack_gsm7(text), len(text)
        if udl > 160:
            raise ValueError("text too long for one SMS (160 chars)")
    else:
        ud = text.encode("utf-16-be")
        dcs, udl = 0x08, len(ud)
        if udl > 140:
            raise ValueError("text too long for one SMS (70 UCS2 chars)")
    # SMSC 长度 0 = 用 SIM 里的默认；首字节 0x01 = SMS-SUBMIT，无有效期；MR 0（基带会改写）
    return bytes([0x00, 0x01, 0x00, ndigits, toa]) + bcd + bytes([0x00, dcs, udl]) + ud


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if len(args) != 2 or flags - {"--dry-run", "--no-ims"}:
        sys.exit(__doc__)
    pdu = build_pdu(args[0], args[1])
    print(f"pdu_len={len(pdu)}", flush=True)
    if "--dry-run" in flags:
        return
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
    wms = dev.allocate_client_finish(wait(dev.allocate_client, Qmi.Service.WMS, Qmi.CID_NONE, 10, None)[1])
    try:
        m = Qmi.message_new(wms.get_service(), wms.get_cid(), wms.get_next_transaction_id(), WMS_BIND)
        off = Qmi.message_tlv_write_init(m, 0x01)
        Qmi.message_tlv_write_guint8(m, SUB)
        Qmi.message_tlv_write_complete(m, off)
        dev.command_full_finish(wait(dev.command_full, m, Qmi.MessageContext.new(), 10, None)[1])
        print(f"{time.strftime('%T')} WMS bound to sub {SUB}", flush=True)
        try:
            st = wms.get_transport_network_registration_status_finish(
                wait(wms.get_transport_network_registration_status,
                     Qmi.MessageWmsGetTransportNetworkRegistrationStatusInput(), 10, None)[1])
            print(f"transport registration: {st.get_registration_status()}", flush=True)
        except GLib.Error as e:
            print(f"transport registration query failed: {e.message}", flush=True)
        inp = Qmi.MessageWmsRawSendInput()
        inp.set_raw_message_data(Qmi.WmsMessageFormat.GSM_WCDMA_POINT_TO_POINT, list(pdu))
        if "--no-ims" not in flags:
            inp.set_sms_on_ims(True)
        print(f"{time.strftime('%T')} sending (ims={'--no-ims' not in flags})", flush=True)
        try:
            out = wms.raw_send_finish(wait(wms.raw_send, inp, 60, None)[1])
            try:
                out.get_result()
                print(f"{time.strftime('%T')} send OK, message_id={out.get_message_id()}", flush=True)
            except GLib.Error as e:
                print(f"{time.strftime('%T')} send FAILED: {e.message}", flush=True)
                for getter in ("get_message_delivery_failure_type", "get_gsm_wcdma_cause_info"):
                    try:
                        print(f"  {getter}: {getattr(out, getter)()}", flush=True)
                    except Exception:
                        pass
                sys.exit(1)
        except GLib.Error as e:
            print(f"{time.strftime('%T')} raw_send error: {e.message}", flush=True)
            sys.exit(1)
    finally:
        wait(dev.release_client, wms, Qmi.DeviceReleaseClientFlags.RELEASE_CID, 10, None)


main()
