#!/usr/bin/python3
# 只读查询基带 IMS 状态：IMSA 注册/服务状态、IMS Settings 服务开关、PDC 当前选中配置。
# qmicli 走 qrtr 时每个进程是独立端口，bind 后 CID 不能跨进程用，所以 bind 和查询必须在同一进程里做。
# 用法（手机上，root）：fuxi-qmi-ims.py [binding]   binding 默认 0（主卡订阅）
import sys
import gi
gi.require_version("Qmi", "1.0")
gi.require_version("Qrtr", "1.0")
from gi.repository import GLib, Qmi, Qrtr

binding = int(sys.argv[1]) if len(sys.argv) > 1 else 0
ctx = GLib.MainContext.default()


def wait(start, *args):
    # 把 libqmi 的异步调用变成同步：start(*args, cb)，迭代主循环直到回调到达
    box = []
    start(*args, lambda src, res: box.append((src, res)))
    while not box:
        ctx.iteration(True)
    return box[0]


def call(fn_name, obj, finish_obj=None, *args):
    src, res = wait(getattr(obj, fn_name), *args)
    return getattr(finish_obj or obj, fn_name + "_finish")(res)


def show(out, names):
    for n in names:
        try:
            v = getattr(out, "get_" + n)()
            if isinstance(v, tuple) and len(v) == 2 and v[0] is True:
                v = v[1]
            if hasattr(v, "value_nick"):
                v = v.value_nick
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
call("open", dev, None, Qmi.DeviceOpenFlags.PROXY, 10, None)
clients = []


def client(service):
    c = call("allocate_client", dev, None, service, Qmi.CID_NONE, 10, None)
    clients.append(c)
    return c


def imsa():
    c = client(Qmi.Service.IMSA)
    inp = Qmi.MessageImsaBindInput.new()
    inp.set_binding(binding)
    call("bind", c, None, inp, 10, None).get_result()
    out = call("get_ims_registration_status", c, None, None, 10, None)
    out.get_result()
    show(out, ["ims_registration_status", "ims_registration_error_code",
               "ims_registration_error_message", "ims_registration_technology"])
    out = call("get_ims_services_status", c, None, None, 10, None)
    out.get_result()
    show(out, ["ims_sms_service_status", "ims_sms_service_registration_technology",
               "ims_voice_service_status", "ims_voice_service_registration_technology",
               "ims_video_telephony_service_status", "ims_ue_to_tas_service_status"])


def ims():
    c = client(Qmi.Service.IMS)
    inp = Qmi.MessageImsBindInput.new()
    inp.set_binding(binding)
    call("bind", c, None, inp, 10, None).get_result()
    out = call("get_ims_services_enabled_setting", c, None, None, 10, None)
    out.get_result()
    show(out, ["ims_registration_service_enabled", "ims_voice_service_enabled",
               "ims_voice_wifi_service_enabled", "ims_sms_service_enabled",
               "ims_ussd_service_enabled", "ims_ut_service_enabled"])


query(f"IMSA (binding {binding})", imsa)
query(f"IMS settings (binding {binding})", ims)

for c in clients:
    try:
        call("release_client", dev, None, c, Qmi.DeviceReleaseClientFlags.RELEASE_CID, 10, None)
    except GLib.Error:
        pass
