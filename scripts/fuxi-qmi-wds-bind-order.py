#!/usr/bin/python3
# 只读探针：WDS 客户端先后做 Bind Subscription / Bind Mux Data Port 后，列出 3GPP profile 的 APN，
# 用来判断订阅绑定在绑 mux 之后是否还有效（不建 PDN，不改 profile）。
# 用法（手机上，root）：fuxi-qmi-wds-bind-order.py [mux_id，默认 0xE2]
import sys
import gi
gi.require_version("Qmi", "1.0")
gi.require_version("Qrtr", "1.0")
from gi.repository import GLib, Qmi, Qrtr

MUX = int(sys.argv[1], 0) if len(sys.argv) > 1 else 0xE2
ctx = GLib.MainContext.default()


def wait(start, *args):
    box = []
    start(*args, lambda src, res: box.append((src, res)))
    while not box:
        ctx.iteration(True)
    return box[0]


def call(fn_name, obj, *args):
    src, res = wait(getattr(obj, fn_name), *args)
    out = getattr(obj, fn_name + "_finish")(res)
    out.get_result()
    return out


def bind_sub(wds, sub):
    i = Qmi.MessageWdsBindSubscriptionInput.new()
    i.set_subscription_id(sub)
    call("bind_subscription", wds, i, 10, None)


def bind_mux(wds):
    i = Qmi.MessageWdsBindMuxDataPortInput.new()
    i.set_endpoint_info(Qmi.DataEndpointType.EMBEDDED, 1)
    i.set_mux_id(MUX)
    call("bind_mux_data_port", wds, i, 10, None)


def apns(wds):
    i = Qmi.MessageWdsGetProfileListInput.new()
    i.set_profile_type(getattr(Qmi.WdsProfileType, "3GPP"))
    out = call("get_profile_list", wds, i, 10, None)
    names = []
    for p in out.get_profile_list():
        si = Qmi.MessageWdsGetProfileSettingsInput.new()
        si.set_profile_id(getattr(Qmi.WdsProfileType, "3GPP"), p.profile_index)
        try:
            s = call("get_profile_settings", wds, si, 10, None)
            names.append(f"{p.profile_index}:{s.get_apn_name()}")
        except GLib.Error as e:
            names.append(f"{p.profile_index}:?({e.message})")
    return " ".join(names)


bus = Qrtr.Bus.new_finish(wait(Qrtr.Bus.new, 1000, None)[1])
dev = Qmi.Device.new_from_node_finish(wait(Qmi.Device.new_from_node, bus.peek_node(0), None)[1])
wait(dev.open, Qmi.DeviceOpenFlags.PROXY, 10, None)

CASES = [
    ("unbound", []),
    ("sub2", [lambda w: bind_sub(w, 2)]),
    ("sub2 -> mux", [lambda w: bind_sub(w, 2), bind_mux]),
    ("mux -> sub2", [bind_mux, lambda w: bind_sub(w, 2)]),
    ("sub1 -> mux", [lambda w: bind_sub(w, 1), bind_mux]),
]
for label, steps in CASES:
    src, res = wait(dev.allocate_client, Qmi.Service.WDS, Qmi.CID_NONE, 10, None)
    wds = dev.allocate_client_finish(res)
    try:
        for s in steps:
            s(wds)
        print(f"{label:12s} cid {wds.get_cid()}: {apns(wds)}")
    except GLib.Error as e:
        print(f"{label:12s}: FAIL {e.message}")
    finally:
        wait(dev.release_client, wds, Qmi.DeviceReleaseClientFlags.RELEASE_CID, 10, None)
