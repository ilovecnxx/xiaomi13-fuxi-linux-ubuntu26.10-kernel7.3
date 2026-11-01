#!/usr/bin/python3
# 只读查询副卡（第二订阅）的 NAS 状态：双卡待机偏好、订阅信息、模式偏好、注册/系统信息/信号。
# libqmi 1.38 没有 NAS Bind Subscription 等消息，这里手工组原始 QMI 消息（ID 见 docs/调研-双卡QMI-20261101.md §1）。
# 绑定只对当前进程里的这个 client 生效，不影响 ModemManager 的 client；进程退出即失效。
# 用法（手机上，root）：fuxi-qmi-sub2.py [nas_sub]   nas_sub 默认 1（NAS 枚举：0 主卡、1 副卡）
import sys
import gi
gi.require_version("Qmi", "1.0")
gi.require_version("Qrtr", "1.0")
from gi.repository import GLib, Qmi, Qrtr

nas_sub = int(sys.argv[1]) if len(sys.argv) > 1 else 1
ctx = GLib.MainContext.default()
LE = Qmi.Endian.LITTLE

NAS_BIND_SUBSCRIPTION = 0x0045
NAS_GET_MODE_PREF = 0x0049
NAS_GET_DUAL_STANDBY_PREF = 0x005C
NAS_GET_SUBSCRIPTION_INFO = 0x007C


def wait(start, *args):
    # 把 libqmi 的异步调用变成同步：start(*args, cb)，迭代主循环直到回调到达
    box = []
    start(*args, lambda src, res: box.append((src, res)))
    while not box:
        ctx.iteration(True)
    return box[0]


def call(fn_name, obj, *args):
    src, res = wait(getattr(obj, fn_name), *args)
    return getattr(obj, fn_name + "_finish")(res)


def show(out, names):
    for n in names:
        try:
            v = getattr(out, "get_" + n)()
            if isinstance(v, tuple) and v and v[0] is True:
                v = v[1:] if len(v) > 2 else v[1]
            if hasattr(v, "value_nick"):
                v = v.value_nick
            elif isinstance(v, tuple):
                v = tuple(x.value_nick if hasattr(x, "value_nick") else x for x in v)
            print(f"  {n}: {v}")
        except (GLib.Error, AttributeError, TypeError) as e:
            print(f"  {n}: n/a ({e})")


def query(label, fn):
    print(label)
    try:
        fn()
    except GLib.Error as e:
        print(f"  error: {e.message}")


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


def tlv_bytes(msg, t):
    # 读整个 TLV 的原始字节；不存在返回 None
    try:
        off, ln = Qmi.message_tlv_read_init(msg, t)
    except GLib.Error:
        return None
    o, buf = 0, []
    for _ in range(ln):
        _, o, b = Qmi.message_tlv_read_guint8(msg, off, o)
        buf.append(b)
    return bytes(buf)


def raw(c, msg_id, tlvs=()):
    # 发原始请求；tlvs = [(类型, bytes)]；返回响应，QMI 结果非成功时抛 GLib.Error
    m = Qmi.message_new(c.get_service(), c.get_cid(), c.get_next_transaction_id(), msg_id)
    for t, data in tlvs:
        off = Qmi.message_tlv_write_init(m, t)
        for b in data:
            Qmi.message_tlv_write_guint8(m, b)
        Qmi.message_tlv_write_complete(m, off)
    src, res = wait(dev.command_full, m, Qmi.MessageContext.new(), 10, None)
    resp = dev.command_full_finish(res)
    r = tlv_bytes(resp, 0x02)
    if r is None or len(r) < 4:
        raise GLib.Error(f"msg 0x{msg_id:04x}: no result TLV")
    status, err = int.from_bytes(r[0:2], "little"), int.from_bytes(r[2:4], "little")
    if status != 0:
        name = Qmi.ProtocolError(err).value_nick if err in Qmi.ProtocolError.__enum_values__ else "?"
        raise GLib.Error(f"msg 0x{msg_id:04x}: QMI error {err} ({name})")
    return resp


def dump(resp, names):
    # 按 {类型: 名称} 打印可选 TLV 的十六进制原值
    for t in range(0x10, 0x20):
        b = tlv_bytes(resp, t)
        if b is not None:
            print(f"  0x{t:02x} {names.get(t, '?')}: {b.hex(' ')}")


STANDBY_NAMES = {0x10: "standby_pref (1 单待/2,4 双待/5,6 自动/7 三待)", 0x11: "priority_subs",
                 0x12: "active_subs (单待时有效)", 0x13: "default_data_subs", 0x14: "default_voice_subs",
                 0x15: "active_subs_mask (bit0 主 bit1 副)"}
SUBINFO_NAMES = {0x10: "is_priority", 0x11: "is_active", 0x12: "is_dds (废弃)", 0x13: "voice_system_id",
                 0x14: "lte_voice_system_id", 0x15: "wlan_voice_system_id", 0x16: "dds_type",
                 0x17: "nr5g_voice_system_id"}
MODE_NAMES = {0x10: "mode_pref idx0 (bit4 LTE bit6 NR5G)", 0x11: "mode_pref idx1", 0x12: "mode_pref idx2"}


def nas_unbound():
    c = client(Qmi.Service.NAS)
    print(" get dual standby pref (0x005C)")
    dump(raw(c, NAS_GET_DUAL_STANDBY_PREF), STANDBY_NAMES)
    print(" get mode pref (0x0049)")
    dump(raw(c, NAS_GET_MODE_PREF), MODE_NAMES)
    print(" get subscription info (0x007C, 未绑定)")
    dump(raw(c, NAS_GET_SUBSCRIPTION_INFO), SUBINFO_NAMES)


def nas_bound():
    c = client(Qmi.Service.NAS)
    raw(c, NAS_BIND_SUBSCRIPTION, [(0x01, bytes([nas_sub]))])
    print(f" bind subscription {nas_sub}: ok")
    print(" get subscription info (0x007C)")
    dump(raw(c, NAS_GET_SUBSCRIPTION_INFO), SUBINFO_NAMES)
    print(" serving system")
    out = call("get_serving_system", c, None, 10, None)
    out.get_result()
    show(out, ["serving_system", "roaming_indicator", "current_plmn", "data_service_capability"])
    print(" system info")
    out = call("get_system_info", c, None, 10, None)
    out.get_result()
    show(out, ["gsm_service_status", "wcdma_service_status", "lte_service_status",
               "nr5g_service_status_info", "lte_system_info_v2", "nr5g_system_info"])
    print(" signal info")
    out = call("get_signal_info", c, None, 10, None)
    out.get_result()
    show(out, ["lte_signal_strength", "5g_signal_strength", "5g_signal_strength_extended"])


query("NAS (未绑定)", nas_unbound)
query(f"NAS (bind subscription {nas_sub})", nas_bound)

for c in clients:
    try:
        call("release_client", dev, c, Qmi.DeviceReleaseClientFlags.RELEASE_CID, 10, None)
    except GLib.Error:
        pass
