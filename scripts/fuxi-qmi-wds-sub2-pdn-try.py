#!/usr/bin/python3
# 一次性试验：卡 2 IMS PDN 用不同的 Start Network 参数组合，看拿到哪段地址（240e:469 = ctnet，240e:569 = IMS）和有没有 P-CSCF。
# 每个组合：分配 WDS → Bind Mux Data Port(0xE2) → Bind Subscription → Start Network → Get Current Settings → Stop Network → 释放。不绑 mux，不改 profile。
# 地址只打印前两段。
# 用法（手机上，root）：fuxi-qmi-wds-sub2-pdn-try.py <sub> <apn|-> <profile|0> <ipfam 4|6|0> [<apn> <profile> <ipfam> ...]
import sys
import gi
gi.require_version("Qmi", "1.0")
gi.require_version("Qrtr", "1.0")
from gi.repository import GLib, Qmi, Qrtr

sub = int(sys.argv[1])
rest = sys.argv[2:]
if not rest or len(rest) % 3:
    sys.exit("usage: <sub> <apn|-> <profile|0> <ipfam 4|6|0> [...]")
CASES = [(rest[k], int(rest[k + 1]), int(rest[k + 2])) for k in range(0, len(rest), 3)]
FAMS = {4: Qmi.WdsIpFamily.IPV4, 6: Qmi.WdsIpFamily.IPV6}
RS = Qmi.WdsRequestedSettings
# 81voltd 卡 2 用 0xF2，MM 用 1、2；这里用之前探针用过的 0xE2，避免和它们冲突
MUX = 0xE2
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
    return out


def val(out, g):
    try:
        v = getattr(out, g)()
    except (GLib.Error, TypeError, AttributeError):
        return None
    if isinstance(v, tuple) and len(v) > 1 and v[0] is True:
        v = v[1:] if len(v) > 2 else v[1]
    return v


def prefix6(v):
    # get_ipv6_address 给 (地址数组, 前缀长度)
    try:
        a = v[0] if isinstance(v, tuple) else v
        return "%x:%x:…/%d" % (a[0], a[1], v[1])
    except Exception:
        return "n/a"


def try_case(dev, apn, profile, fam):
    label = f"apn={apn} profile={profile} fam={fam}"
    wds = call("allocate_client", dev, Qmi.Service.WDS, Qmi.CID_NONE, 10, None)
    try:
        # 必须先 Bind Mux 再 Bind Subscription：不绑 mux 时 Start Network 回 InvalidOperation，
        # 而 Bind Mux 会把订阅重置回主卡（docs/交接-新会话-20261101-1406.md 等记录的 v1/v2 结论）。
        i = Qmi.MessageWdsBindMuxDataPortInput.new()
        i.set_endpoint_info(Qmi.DataEndpointType.EMBEDDED, 1)
        i.set_mux_id(MUX)
        call("bind_mux_data_port", wds, i, 10, None).get_result()
        i = Qmi.MessageWdsBindSubscriptionInput.new()
        i.set_subscription_id(sub)
        call("bind_subscription", wds, i, 10, None).get_result()
        i = Qmi.MessageWdsStartNetworkInput.new()
        if apn != "-":
            i.set_apn(apn)
        if profile:
            i.set_profile_index_3gpp(profile)
        if fam in FAMS:
            i.set_ip_family_preference(FAMS[fam])
        out = call("start_network", wds, i, 60, None)
        try:
            out.get_result()
        except GLib.Error as e:
            vr = val(out, "get_verbose_call_end_reason")
            print(f"{label}: START FAIL {e.message} call_end={val(out, 'get_call_end_reason')} verbose={vr}")
            return
        pdh = val(out, "get_packet_data_handle")
        i = Qmi.MessageWdsGetCurrentSettingsInput.new()
        i.set_requested_settings(RS.IP_ADDRESS | RS.IP_FAMILY | RS.APN_NAME | RS.PROFILE_ID | RS.IMCN_FLAG
                                 | RS.PCSCF_ADDRESS | RS.PCSCF_SERVER_ADDRESS_LIST | RS.PCSCF_DOMAIN_NAME_LIST)
        s = call("get_current_settings", wds, i, 10, None)
        try:
            s.get_result()
            fam_got = val(s, "get_ip_family")
            pl = val(s, "get_pcscf_server_address_list")
            dl = val(s, "get_pcscf_domain_name_list")
            print(f"{label}: ok ip_family={getattr(fam_got, 'value_nick', fam_got)} "
                  f"v6={prefix6(val(s, 'get_ipv6_address'))} v4={'present' if val(s, 'get_ipv4_address') else 'none'} "
                  f"apn={val(s, 'get_apn_name')!r} profile={val(s, 'get_profile_id')} imcn={val(s, 'get_imcn_flag')} "
                  f"pcscf_pco={val(s, 'get_pcscf_address_using_pco')} pcscf_v4_list={len(pl) if pl else 0} "
                  f"pcscf_domains={len(dl) if dl else 0}")
        except GLib.Error as e:
            print(f"{label}: ok, get current settings FAIL {e.message}")
        i = Qmi.MessageWdsStopNetworkInput.new()
        i.set_packet_data_handle(pdh)
        try:
            call("stop_network", wds, i, 10, None).get_result()
            print(f"  stop network: ok")
        except GLib.Error as e:
            print(f"  stop network: FAIL {e.message}")
    except GLib.Error as e:
        print(f"{label}: FAIL {e.message}")
    finally:
        wait(dev.release_client, wds, Qmi.DeviceReleaseClientFlags.RELEASE_CID, 10, None)


bus = Qrtr.Bus.new_finish(wait(Qrtr.Bus.new, 1000, None)[1])
dev = Qmi.Device.new_from_node_finish(wait(Qmi.Device.new_from_node, bus.peek_node(0), None)[1])
wait(dev.open, Qmi.DeviceOpenFlags.PROXY, 10, None)
for apn, profile, fam in CASES:
    try_case(dev, apn, profile, fam)
    GLib.usleep(2000000)
