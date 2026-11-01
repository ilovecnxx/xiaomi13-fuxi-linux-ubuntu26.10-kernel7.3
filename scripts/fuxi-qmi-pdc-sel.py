#!/usr/bin/python3
# 只读查询每个订阅当前选中的 MBN（PDC Get Selected Config，0x0022，带 TLV 0x11 subscription_id）。
# libqmi 的类型化 API 没有 subscription_id，这里发原始消息；结果通过 PDC 0x0022 指示返回。
# 用法（手机上，root）：fuxi-qmi-pdc-sel.py [sub ...]   默认查 0 1（PDC 订阅从 0 开始：0 主、1 副）
import sys
import gi
gi.require_version("Qmi", "1.0")
gi.require_version("Qrtr", "1.0")
from gi.repository import GLib, Qmi, Qrtr

subs = [int(a) for a in sys.argv[1:]] or [0, 1]
ctx = GLib.MainContext.default()
PDC_GET_SELECTED_CONFIG = 0x0022
PDC_GET_CONFIG_INFO = 0x0029
CONFIG_SOFTWARE = 1


def wait(start, *args, timeout=0):
    box = []
    start(*args, lambda src, res: box.append((src, res)))
    while not box:
        ctx.iteration(True)
    return box[0]


def call(fn_name, obj, *args):
    src, res = wait(getattr(obj, fn_name), *args)
    return getattr(obj, fn_name + "_finish")(res)


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


def u32(v):
    return v.to_bytes(4, "little")


bus = Qrtr.Bus.new_finish(wait(Qrtr.Bus.new, 1000, None)[1])
node = bus.peek_node(0)
if node is None:
    sys.exit("qrtr node 0 not found")
dev = Qmi.Device.new_from_node_finish(wait(Qmi.Device.new_from_node, node, None)[1])
call("open", dev, Qmi.DeviceOpenFlags.PROXY, 10, None)
c = call("allocate_client", dev, Qmi.Service.PDC, Qmi.CID_NONE, 10, None)

inds = []
dev.connect("indication", lambda _d, msg: inds.append(msg))

inp = Qmi.MessagePdcRegisterInput.new()
inp.set_enable_reporting(True)
call("register", c, inp, 10, None).get_result()


def raw(msg_id, tlvs):
    m = Qmi.message_new(Qmi.Service.PDC, c.get_cid(), c.get_next_transaction_id(), msg_id)
    for t, data in tlvs:
        off = Qmi.message_tlv_write_init(m, t)
        for b in data:
            Qmi.message_tlv_write_guint8(m, b)
        Qmi.message_tlv_write_complete(m, off)
    src, res = wait(dev.command_full, m, Qmi.MessageContext.new(), 10, None)
    r = tlv_bytes(dev.command_full_finish(res), 0x02)
    status, err = int.from_bytes(r[0:2], "little"), int.from_bytes(r[2:4], "little")
    if status != 0:
        raise GLib.Error(f"msg 0x{msg_id:04x}: QMI error {err}")


def parse_ind(raw_msg):
    # Device "indication" 信号给的是原始 QMUX 字节：marker 1 + len 2 + flags 1 + service 1 + cid 1，
    # 之后服务头 flags 1 + txn 2 + msg_id 2 + tlv_len 2，再是 TLV（类型 1 + 长度 2 + 值）
    b = bytes(raw_msg)
    service, msg_id, tlv_len = b[4], int.from_bytes(b[9:11], "little"), int.from_bytes(b[11:13], "little")
    tlvs, o = {}, 13
    while o + 3 <= 13 + tlv_len:
        t, ln = b[o], int.from_bytes(b[o + 1:o + 3], "little")
        tlvs[t] = b[o + 3:o + 3 + ln]
        o += 3 + ln
    return service, msg_id, tlvs


def wait_ind(msg_id, token, secs=10):
    end = GLib.get_monotonic_time() + secs * 1000000
    while GLib.get_monotonic_time() < end:
        for m in list(inds):
            service, mid, tlvs = parse_ind(m)
            if service == int(Qmi.Service.PDC) and mid == msg_id and tlvs.get(0x10) == u32(token):
                inds.remove(m)
                return tlvs
        ctx.iteration(False) or GLib.usleep(20000)
    return None


def config_desc(cid):
    tok = 0x7000 + len(cid)
    try:
        raw(PDC_GET_CONFIG_INFO, [(0x01, u32(CONFIG_SOFTWARE) + bytes([len(cid)]) + cid), (0x10, u32(tok))])
    except GLib.Error as e:
        return f"? ({e.message})"
    m = wait_ind(PDC_GET_CONFIG_INFO, tok)
    if m is None:
        return "? (no indication)"
    d = m.get(0x12)
    return d[1:1 + d[0]].decode(errors="replace") if d else "?"


for i, sub in enumerate(subs):
    tok = 0x5000 + i
    print(f"subscription {sub}")
    try:
        raw(PDC_GET_SELECTED_CONFIG, [(0x01, u32(CONFIG_SOFTWARE)), (0x10, u32(tok)), (0x11, u32(sub))])
    except GLib.Error as e:
        print(f"  error: {e.message}")
        continue
    m = wait_ind(PDC_GET_SELECTED_CONFIG, tok)
    if m is None:
        print("  no indication in 10 s")
        continue
    err = m.get(0x02)
    print(f"  indication error: {int.from_bytes(err, 'little') if err else 'n/a'}")
    for t, name in ((0x11, "active"), (0x12, "pending")):
        b = m.get(t)
        if b:
            cid = b[1:1 + b[0]]
            print(f"  {name}: {cid.hex(':').upper()}  {config_desc(cid)}")
        else:
            print(f"  {name}: none")

try:
    call("release_client", dev, c, Qmi.DeviceReleaseClientFlags.RELEASE_CID, 10, None)
except GLib.Error:
    pass
