#!/usr/bin/python3
# 一次性试验：副卡 WDS Start Network 是否需要先 Bind Mux Data Port。
# 步骤：分配 WDS → Bind Subscription → [Bind Mux Data Port] → Set IP Family 6 → Start Network → Get Current Settings → Stop Network → 释放。
# 用法（手机上，root）：fuxi-qmi-wds-sub2-test.py <sub(1主2副)> <mux_id|0 不绑> [profile|0] [apn|-] [ipfam 4|6|0]
import sys
import gi
gi.require_version("Qmi", "1.0")
gi.require_version("Qrtr", "1.0")
from gi.repository import GLib, Qmi, Qrtr

sub = int(sys.argv[1])
mux = int(sys.argv[2])
profile = int(sys.argv[3]) if len(sys.argv) > 3 else 2
apn = sys.argv[4] if len(sys.argv) > 4 else "IMS"
apn = "" if apn == "-" else apn
fam = int(sys.argv[5]) if len(sys.argv) > 5 else 6
FAM = {4: Qmi.WdsIpFamily.IPV4, 6: Qmi.WdsIpFamily.IPV6}.get(fam)
ctx = GLib.MainContext.default()


def wait(start, *args):
    box = []
    start(*args, lambda src, res: box.append((src, res)))
    while not box:
        ctx.iteration(True)
    return box[0]


def call(fn_name, obj, *args):
    src, res = wait(getattr(obj, fn_name), *args)
    return getattr(obj, fn_name + "_finish")(res)


def step(label, fn):
    try:
        out = fn()
        out.get_result()
        print(f"{label}: ok")
        return out
    except GLib.Error as e:
        print(f"{label}: FAIL {e.message}")
        return None


bus = Qrtr.Bus.new_finish(wait(Qrtr.Bus.new, 1000, None)[1])
dev = Qmi.Device.new_from_node_finish(wait(Qmi.Device.new_from_node, bus.peek_node(0), None)[1])
call("open", dev, Qmi.DeviceOpenFlags.PROXY, 10, None)
wds = call("allocate_client", dev, Qmi.Service.WDS, Qmi.CID_NONE, 10, None)
print(f"wds cid {wds.get_cid()}")

try:
    i = Qmi.MessageWdsBindSubscriptionInput.new()
    i.set_subscription_id(Qmi.WdsClientSubscriptionId(sub) if hasattr(Qmi, "WdsClientSubscriptionId") else sub)
    if not step("bind subscription", lambda: call("bind_subscription", wds, i, 10, None)):
        raise SystemExit(1)
    if mux:
        i = Qmi.MessageWdsBindMuxDataPortInput.new()
        i.set_endpoint_info(Qmi.DataEndpointType.EMBEDDED, 1)
        i.set_mux_id(mux)
        step(f"bind mux data port (embedded/1/mux {mux})", lambda: call("bind_mux_data_port", wds, i, 10, None))
    if FAM is not None:
        i = Qmi.MessageWdsSetIpFamilyInput.new()
        i.set_preference(FAM)
        step(f"set ip family {fam}", lambda: call("set_ip_family", wds, i, 10, None))

    i = Qmi.MessageWdsStartNetworkInput.new()
    if apn:
        i.set_apn(apn)
    if profile:
        i.set_profile_index_3gpp(profile)
    if FAM is not None:
        i.set_ip_family_preference(FAM)
    try:
        out = call("start_network", wds, i, 60, None)
        out.get_result()
        pdh = out.get_packet_data_handle()
        pdh = pdh[1] if isinstance(pdh, tuple) else pdh
        print(f"start network apn={apn} profile={profile} fam={fam}: ok pdh=0x{pdh:x}")
    except GLib.Error as e:
        print(f"start network apn={apn} profile={profile} fam={fam}: FAIL {e.message}")
        for g in ("get_call_end_reason", "get_verbose_call_end_reason"):
            try:
                print(f"  {g}:", out.__getattribute__(g)())
            except Exception as e2:
                print(f"  {g}: n/a ({e2})")
        try:
            vt, vr = out.get_verbose_call_end_reason()[1:]
            print("  verbose:", vt.value_nick, Qmi.WdsVerboseCallEndReason3gpp(vr).value_nick if vt == Qmi.WdsVerboseCallEndReasonType.THREEGPP else vr)
        except Exception:
            pass
        pdh = None
    if pdh is not None:
        i = Qmi.MessageWdsGetCurrentSettingsInput.new()
        i.set_requested_settings(Qmi.WdsRequestedSettings.IP_ADDRESS
                                 | Qmi.WdsRequestedSettings.IP_FAMILY)
        s = step("get current settings", lambda: call("get_current_settings", wds, i, 10, None))
        if s:
            for g in ("get_ip_family", "get_ipv6_address", "get_ipv4_address"):
                try:
                    v = getattr(s, g)()
                    v = v[1:] if isinstance(v, tuple) and v and v[0] is True else v
                    if g == "get_ip_family":
                        print("  ip family:", getattr(v, "value_nick", v))
                    else:
                        print(f"  {g}: present (地址不打印)")
                except (GLib.Error, TypeError) as e:
                    print(f"  {g}: n/a")
        i = Qmi.MessageWdsStopNetworkInput.new()
        i.set_packet_data_handle(pdh)
        step("stop network", lambda: call("stop_network", wds, i, 10, None))
finally:
    call("release_client", dev, wds, Qmi.DeviceReleaseClientFlags.RELEASE_CID, 10, None)
    print("released")
