#!/usr/bin/python3
# 只读探针：按订阅列出 WDS 的 LTE 附着 PDN 列表、附着参数、默认 profile 和各 3GPP profile 的 APN/PDP 类型。
# 用来判断卡 2 开机附着的是哪个 APN（ctnet 还是 IMS）。只发 Get 和 Bind Subscription，不建 PDN、不改 profile。
# 用法（手机上，root）：fuxi-qmi-wds-attach.py [sub ...]（WDS 订阅号，1 主 2 副；默认 1 2）
import sys
import gi
gi.require_version("Qmi", "1.0")
gi.require_version("Qrtr", "1.0")
from gi.repository import GLib, Qmi, Qrtr

SUBS = [int(a) for a in sys.argv[1:]] or [1, 2]
P3GPP = getattr(Qmi.WdsProfileType, "3GPP")
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


def get(out, name):
    try:
        v = getattr(out, "get_" + name)()
    except (GLib.Error, AttributeError) as e:
        return f"n/a ({getattr(e, 'message', e)})"
    if isinstance(v, (list, tuple)):
        v = [x.value_nick if hasattr(x, "value_nick") else x for x in v]
    return v.value_nick if hasattr(v, "value_nick") else v


def step(label, fn):
    try:
        fn()
    except GLib.Error as e:
        print(f"  {label}: FAIL {e.message}")


def probe(wds):
    def attach_list():
        out = call("get_lte_attach_pdn_list", wds, None, 10, None)
        print(f"  lte attach pdn list: current={get(out, 'current_list')} pending={get(out, 'pending_list')}")

    def attach_params():
        out = call("get_lte_attach_parameters", wds, None, 10, None)
        print(f"  lte attach params: apn={get(out, 'apn')!r} ip={get(out, 'ip_support')} "
              f"ota_attach={get(out, 'ota_attach_performed')}")

    def default_profile():
        i = Qmi.MessageWdsGetDefaultProfileNumberInput.new()
        i.set_profile_type(P3GPP, Qmi.WdsProfileFamily.EMBEDDED)
        out = call("get_default_profile_number", wds, i, 10, None)
        print(f"  default profile (3gpp/embedded): {get(out, 'index')}")

    def profiles():
        i = Qmi.MessageWdsGetProfileListInput.new()
        i.set_profile_type(P3GPP)
        for p in call("get_profile_list", wds, i, 10, None).get_profile_list():
            si = Qmi.MessageWdsGetProfileSettingsInput.new()
            si.set_profile_id(P3GPP, p.profile_index)
            try:
                s = call("get_profile_settings", wds, si, 10, None)
                print(f"  profile {p.profile_index}: apn={get(s, 'apn_name')!r} pdp={get(s, 'pdp_type')} "
                      f"apn_type_mask={get(s, 'apn_type_mask')}")
            except GLib.Error as e:
                print(f"  profile {p.profile_index}: FAIL {e.message}")

    step("get lte attach pdn list", attach_list)
    step("get lte attach parameters", attach_params)
    step("get default profile number", default_profile)
    step("get profile list/settings", profiles)


bus = Qrtr.Bus.new_finish(wait(Qrtr.Bus.new, 1000, None)[1])
dev = Qmi.Device.new_from_node_finish(wait(Qmi.Device.new_from_node, bus.peek_node(0), None)[1])
wait(dev.open, Qmi.DeviceOpenFlags.PROXY, 10, None)

for sub in SUBS:
    src, res = wait(dev.allocate_client, Qmi.Service.WDS, Qmi.CID_NONE, 10, None)
    wds = dev.allocate_client_finish(res)
    print(f"WDS sub {sub} (cid {wds.get_cid()})")
    try:
        i = Qmi.MessageWdsBindSubscriptionInput.new()
        i.set_subscription_id(sub)
        call("bind_subscription", wds, i, 10, None)
        probe(wds)
    except GLib.Error as e:
        print(f"  bind subscription: FAIL {e.message}")
    finally:
        wait(dev.release_client, wds, Qmi.DeviceReleaseClientFlags.RELEASE_CID, 10, None)
