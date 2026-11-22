import json
import re
import secrets
from dataclasses import dataclass

from fuxi_qmi_bind import bind_subscription, check_response_header

SUB = 1
WMS_BIND, WMS_GET_BINDING = 0x004C, 0x0058
MAX_REQUEST_BYTES = 4096
EXIT_CODES = {"submitted": 0, "invalid": 2, "not_submitted": 3, "rejected": 4, "uncertain": 5, "partial": 6}
GSM7_DEFAULT = ("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
                "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")
GSM7_CODES = {char: bytes([code]) for code, char in enumerate(GSM7_DEFAULT) if code != 0x1b}
GSM7_CODES.update({char: bytes([0x1b, code]) for char, code in {
    "\f": 0x0a, "^": 0x14, "{": 0x28, "}": 0x29, "\\": 0x2f,
    "[": 0x3c, "~": 0x3d, "]": 0x3e, "|": 0x40, "€": 0x65,
}.items()})


def encode_number(num):
    toa = 0x91 if num.startswith("+") else 0x81
    digits = num.lstrip("+")
    if len(digits) % 2:
        digits += "F"
    bcd = bytes(int(digits[i + 1] + digits[i], 16) for i in range(0, len(digits), 2))
    return toa, bcd


def pack_gsm7(septets, fill_bits=0):
    bits, nbits, out = 0, fill_bits, bytearray()
    for septet in septets:
        bits |= septet << nbits
        nbits += 7
        while nbits >= 8:
            out.append(bits & 0xFF)
            bits >>= 8
            nbits -= 8
    if nbits:
        out.append(bits & 0xFF)
    return bytes(out)


def build_pdus(number, text):
    if not re.fullmatch(r"\+?[0-9]{3,15}", number):
        raise ValueError("bad number")
    toa, bcd = encode_number(number)
    address = bytes([len(number.lstrip("+")), toa]) + bcd
    gsm = all(char in GSM7_CODES for char in text)
    if gsm:
        units = [GSM7_CODES[char] for char in text]
        dcs, single_limit, segment_limit = 0, 160, 153
    else:
        units = [char.encode("utf-16-be") for char in text]
        dcs, single_limit, segment_limit = 8, 140, 134
    prefix = address + bytes([0, dcs])
    size = sum(map(len, units))
    if size <= single_limit:
        data = b"".join(units)
        return [b"\x00\x01\x00" + prefix + bytes([size]) + (pack_gsm7(data) if gsm else data)]

    parts = [bytearray()]
    for unit in units:
        if len(parts[-1]) + len(unit) > segment_limit:
            if len(parts) == 255:
                raise ValueError("too many SMS segments")
            parts.append(bytearray())
        parts[-1].extend(unit)
    reference = secrets.randbelow(256)
    pdus = []
    for index, part in enumerate(parts, start=1):
        header = bytes([5, 0, 3, reference, len(parts), index])
        udl = len(part) + (7 if gsm else len(header))
        data = pack_gsm7(part, fill_bits=1) if gsm else part
        pdus.append(b"\x00\x41\x00" + prefix + bytes([udl]) + header + data)
    return pdus


def build_pdu(number, text):
    pdus = build_pdus(number, text)
    if len(pdus) != 1:
        raise ValueError("text too long for one SMS")
    return pdus[0]


@dataclass
class Submission:
    status: str = "not_submitted"
    error: str | None = "backend_unavailable"
    cleanup_failed: bool = False
    message_id: int | None = None
    segments_total: int = 0
    segments_submitted: int = 0
    segments_uncertain: int = 0


def verify_binding(qmi, wait, device, client):
    bind_subscription(qmi, wait, device, client, WMS_BIND, SUB)
    request = qmi.message_new(client.get_service(), client.get_cid(),
                              client.get_next_transaction_id(), WMS_GET_BINDING)
    response = device.command_full_finish(wait(device.command_full, request, qmi.MessageContext.new(), 10, None)[1])
    check_response_header(qmi, request, response)
    for tag, expected in ((0x02, b"\x00\x00\x00\x00"), (0x10, bytes([SUB]))):
        offset, length = qmi.message_tlv_read_init(response, tag)
        if length != len(expected):
            raise ValueError("binding response length")
        cursor = 0
        for byte in expected:
            valid, cursor, value = qmi.message_tlv_read_guint8(response, offset, cursor)
            if not valid or value != byte:
                raise ValueError("binding response mismatch")


def _libraries():
    import gi
    gi.require_version("Qmi", "1.0")
    gi.require_version("Qrtr", "1.0")
    from gi.repository import GLib, Qmi, Qrtr
    return GLib, Qmi, Qrtr


def submit_pdu(pdu, ims=True, log=None):
    return submit_pdus([pdu], ims, log)


def submit_pdus(pdus, ims=True, log=None):
    if not pdus:
        return Submission("invalid", "invalid_request")
    result = Submission(segments_total=len(pdus))
    try:
        glib, qmi, qrtr = _libraries()
        context = glib.MainContext.new()
        context.push_thread_default()
        try:
            _submit(context, glib, qmi, qrtr, pdus, ims, log, result)
        finally:
            context.pop_thread_default()
    except Exception:
        return result
    return result


def _submit(context, glib, qmi, qrtr, pdus, ims, log, result):

    def wait(start, *a):
        box = []
        start(*a, lambda src, res: box.append((src, res)))
        while not box:
            context.iteration(True)
        return box[0]

    bus = qrtr.Bus.new_finish(wait(qrtr.Bus.new, 1000, None)[1])
    node = bus.peek_node(0)
    if node is None:
        return
    dev = qmi.Device.new_from_node_finish(wait(qmi.Device.new_from_node, node, None)[1])
    wms = None
    try:
        if not dev.open_finish(wait(dev.open, qmi.DeviceOpenFlags.PROXY, 10, None)[1]):
            return
        wms = dev.allocate_client_finish(wait(dev.allocate_client, qmi.Service.WMS, qmi.CID_NONE, 10, None)[1])
        result.error = "binding_unverified"
        verify_binding(qmi, wait, dev, wms)
        result.error = "prepare_failed"
        if log is not None:
            log(f"WMS bound to sub {SUB}")
            try:
                st = wms.get_transport_network_registration_status_finish(wait(
                    wms.get_transport_network_registration_status,
                    qmi.MessageWmsGetTransportNetworkRegistrationStatusInput(), 10, None)[1])
                log(f"transport registration: {st.get_registration_status()}")
            except glib.Error:
                log("transport registration query failed")
        for pdu in pdus:
            inp = qmi.MessageWmsRawSendInput()
            inp.set_raw_message_data(qmi.WmsMessageFormat.GSM_WCDMA_POINT_TO_POINT, list(pdu))
            if ims:
                inp.set_sms_on_ims(True)
            if log is not None:
                log(f"sending (ims={ims})")
            result.status, result.error = "uncertain", "confirmation_missing"
            result.segments_uncertain = 1
            out = wms.raw_send_finish(wait(wms.raw_send, inp, 60, None)[1])
            try:
                if out.get_result() is False:
                    return
            except glib.Error as exc:
                if exc.matches(qmi.protocol_error_quark(), exc.code):
                    result.status, result.error = "rejected", "modem_rejected"
                    result.segments_uncertain = 0
                return
            message_id = out.get_message_id()
            if type(message_id) is not int or not 0 <= message_id <= 0xffff:
                return
            result.segments_uncertain = 0
            result.segments_submitted += 1
            result.message_id = message_id
            result.status, result.error = "partial", "prepare_failed"
        result.status, result.error = "submitted", None
    finally:
        if wms is not None:
            try:
                if not dev.release_client_finish(wait(dev.release_client, wms,
                        qmi.DeviceReleaseClientFlags.RELEASE_CID, 10, None)[1]):
                    result.cleanup_failed = True
            except Exception:
                result.cleanup_failed = True
        try:
            if not dev.close_finish(wait(dev.close_async, 10, None)[1]):
                result.cleanup_failed = True
        except Exception:
            result.cleanup_failed = True


def _unique_object(pairs):
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("duplicate field")
    return value


def _request_pdus(stream):
    data = stream.read(MAX_REQUEST_BYTES + 1)
    if isinstance(data, str):
        data = data.encode("utf-8")
    if len(data) > MAX_REQUEST_BYTES:
        raise ValueError("request too large")
    request = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
    if not isinstance(request, dict) or set(request) not in ({"number", "text"}, {"version", "number", "text"}):
        raise ValueError("invalid fields")
    version = request.get("version", 1)
    if type(version) is not int or version != 1:
        raise ValueError("invalid version")
    if not isinstance(request["number"], str) or not isinstance(request["text"], str) or not request["text"]:
        raise ValueError("invalid SMS")
    return build_pdus(request["number"], request["text"])


def run_send(stream):
    """Consume one EOF-terminated UTF-8 request and emit one SIM2 submission result."""
    try:
        pdus = _request_pdus(stream)
    except (ValueError, RecursionError, OSError):
        result = Submission("invalid", "invalid_request")
    else:
        result = submit_pdus(pdus)
    reply = {"version": 1, "slot": 2, "status": result.status, "error": result.error,
             "segments_total": result.segments_total, "segments_submitted": result.segments_submitted,
             "segments_uncertain": result.segments_uncertain}
    if result.cleanup_failed:
        reply["warning"] = "cleanup_failed"
    print(json.dumps(reply, separators=(",", ":")), flush=True)
    return EXIT_CODES[result.status]
