#!/usr/bin/python3
# 双卡偏好：拨号卡（NAS 默认语音订阅）和上网卡（DSD 默认数据订阅 DDS）。
# 原始 QMI 消息（libqmi 1.38 没有这些方法），定义来自高通 nas/dsd IDL：
#   NAS Get Dual Standby Pref 0x005C：0x10 standby、0x11 priority、0x12 active_subs、
#       0x13 默认数据、0x14 默认语音（u8，0 = 卡槽 1，1 = 卡槽 2），0x15 激活订阅掩码（u64）
#   NAS Set Dual Standby Pref 0x004B：0x10 standby、0x11 priority、0x12 默认数据、
#       0x13 默认语音、0x14 掩码（比 Get 少 active_subs，号码错开一位）；只带 0x13 即可，
#       带 0x10 反而 InvalidArgument
#   DSD Get Current DDS 0x004F：响应 0x10 u32 DDS（1 = 卡槽 1，2 = 卡槽 2），0x11 u32 类型
#   DSD Switch DDS 0x004E：0x01 u32 订阅，0x10 u32 类型（0 永久，1 临时）
# 用法（手机上，root）：
#   fuxi-sim-pref.py get
#   fuxi-sim-pref.py voice 1|2            设拨号卡（卡槽号）：NAS default_voice + /var/lib/fuxi/dialcard
#                                         （打了订阅绑定补丁的 MM 按这个文件选拨出的 VOICE 客户端，每次拨号时读）
#   fuxi-sim-pref.py data 1|2 [temp]      设上网卡（卡槽号），默认永久
import os
import struct
import sys
import time
import gi
gi.require_version("Qmi", "1.0")
gi.require_version("Qrtr", "1.0")
from gi.repository import GLib, Qmi, Qrtr

ctx = GLib.MainContext.default()
DIALCARD = "/var/lib/fuxi/dialcard"


def wait(start, *args):
    box = []
    start(*args, lambda src, res: box.append((src, res)))
    while not box:
        ctx.iteration(True)
    return box[0]


def call(fn_name, obj, *args):
    return getattr(obj, fn_name + "_finish")(wait(getattr(obj, fn_name), *args)[1])


bus = Qrtr.Bus.new_finish(wait(Qrtr.Bus.new, 1000, None)[1])
node = bus.peek_node(0)
if node is None:
    sys.exit("qrtr node 0 not found")
dev = Qmi.Device.new_from_node_finish(wait(Qmi.Device.new_from_node, node, None)[1])
call("open", dev, Qmi.DeviceOpenFlags.PROXY, 10, None)
clients = {}


def client(service):
    if service not in clients:
        clients[service] = call("allocate_client", dev, service, Qmi.CID_NONE, 10, None)
    return clients[service]


def tlvs(msg):
    # GI 里 message_get_raw 只返回指针，按 TLV 号逐个读：tlv_read_init 返回 (offset, length)
    out = {}
    for t in [0x02] + list(range(0x10, 0x20)):
        try:
            off, ln = Qmi.message_tlv_read_init(msg, t)
        except GLib.Error:
            continue
        b, o = bytearray(), 0
        for _ in range(ln):
            _, o, v = Qmi.message_tlv_read_guint8(msg, off, o)
            b.append(v)
        out[t] = bytes(b)
    return out


def send(service, msg_id, fields=()):
    # fields: [(tlv, fmt, value)]，fmt 为 struct 格式（B / I / Q）
    c = client(service)
    msg = Qmi.message_new(service, c.get_cid(), c.get_next_transaction_id(), msg_id)
    for t, fmt, v in fields:
        off = Qmi.message_tlv_write_init(msg, t)
        for b in struct.pack("<" + fmt, v):
            Qmi.message_tlv_write_guint8(msg, b)
        Qmi.message_tlv_write_complete(msg, off)
    resp = dev.command_full_finish(wait(dev.command_full, msg, Qmi.MessageContext.new(), 10, None)[1])
    t = tlvs(resp)
    result, error = struct.unpack_from("<HH", t.get(0x02, b"\0\0\0\0"))
    if result:
        try:
            name = Qmi.ProtocolError(error).value_nick
        except ValueError:
            name = f"0x{error:04x}"
        raise RuntimeError(f"{service.value_nick} 0x{msg_id:04x}: {name}")
    return t


def u(b):
    return int.from_bytes(b, "little") if b else None


def get():
    n = send(Qmi.Service.NAS, 0x005C)
    d = send(Qmi.Service.DSD, 0x004F)
    voice, data, mask = u(n.get(0x14)), u(n.get(0x13)), u(n.get(0x15))
    dds, ddst = u(d.get(0x10)), u(d.get(0x11))
    print(f"拨号卡 (NAS default_voice) : 卡槽 {voice + 1 if voice is not None else '?'}  [raw {voice}]")
    mm = open(DIALCARD).read().strip() if os.path.exists(DIALCARD) else "1（无文件）"
    print(f"拨号卡 (MM, {DIALCARD}) : 卡槽 {mm}")
    print(f"NAS default_data           : 卡槽 {data + 1 if data is not None else '?'}  [raw {data}]")
    print(f"上网卡 (DSD DDS)           : 卡槽 {dds if dds else '?'}  type={ddst}")
    print(f"standby={u(n.get(0x10))} priority={u(n.get(0x11))} active_subs={u(n.get(0x12))} active_mask=0x{mask or 0:x}")
    return voice, dds


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "get"
    if cmd == "get":
        get()
    elif cmd == "voice":
        slot = int(sys.argv[2])
        assert slot in (1, 2)
        send(Qmi.Service.NAS, 0x004B, [(0x13, "B", slot - 1)])
        os.makedirs(os.path.dirname(DIALCARD), exist_ok=True)
        with open(DIALCARD, "w") as f:
            f.write(f"{slot}\n")
        time.sleep(0.3)
        voice, _ = get()
        sys.exit(0 if voice == slot - 1 else 1)
    elif cmd == "data":
        slot = int(sys.argv[2])
        assert slot in (1, 2)
        temp = len(sys.argv) > 3 and sys.argv[3] == "temp"
        send(Qmi.Service.DSD, 0x004E, [(0x01, "I", slot), (0x10, "I", 1 if temp else 0)])
        for _ in range(20):
            time.sleep(0.5)
            if u(send(Qmi.Service.DSD, 0x004F).get(0x10)) == slot:
                break
        _, dds = get()
        sys.exit(0 if dds == slot else 1)
    else:
        sys.exit(__doc__ or "usage: get | voice 1|2 | data 1|2 [temp]")


try:
    main()
finally:
    for c in clients.values():
        try:
            call("release_client", dev, c, Qmi.DeviceReleaseClientFlags.RELEASE_CID, 10, None)
        except GLib.Error:
            pass
