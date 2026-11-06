#!/usr/bin/python3
# fuxi 基带守护：通过 D-Bus 监视 ModemManager。
# 1. 每出现一个新的 modem 对象（开机、SSR、reprobe），把 NAS usage preference 设为 data-centric
#    （调用 fuxi-qmi-usage.py，change duration = power-cycle，不写 NV）。
# 2. 收到网络拒绝后 REJECT_GRACE 秒内没重新注册，或连续 STALL_LIMIT 秒没注册，做一次射频循环：
#    Enable(false) -> SetPowerState(low) -> SetPowerState(on) -> Enable(true)，然后重设 data-centric。
#    循环之间按 MIN_GAP 起步、失败翻倍、最多 MAX_GAP 退避。
# 只在 modem 处于 enabled/searching 时动作；被人为 disable 的 modem 不碰。
import os
import subprocess
import sys
import time

from gi.repository import Gio, GLib

MM = "org.freedesktop.ModemManager1"
IF_MODEM = MM + ".Modem"
IF_3GPP = MM + ".Modem.Modem3gpp"
USAGE = os.environ.get("FUXI_USAGE_TOOL", "/usr/local/sbin/fuxi-qmi-usage.py")
POLL = 5
REJECT_GRACE = int(os.environ.get("FUXI_REJECT_GRACE", "30"))
STALL_LIMIT = int(os.environ.get("FUXI_STALL_LIMIT", "300"))
MIN_GAP = 120
MAX_GAP = 1800
STABLE_RESET = 600
# 3. 81voltd 在跑时，注册后 IMS_LIMIT 秒卡 1 IMSA 仍未注册：第 1 次重启 81voltd，第 2 次射频循环（每个 modem / 每次换上网卡后最多 IMS_MAX_CYCLES 次）
IMS_TOOL = os.environ.get("FUXI_IMS_TOOL", "/usr/local/sbin/fuxi-qmi-ims.py")
IMS_LIMIT = int(os.environ.get("FUXI_IMS_LIMIT", "180"))
IMS_POLL = 30
IMS_MAX_CYCLES = 2
# 射频循环会同时打断卡 2 的 IMS PDN 重试（2102 交接 §7.2）：开机 BOOT_HOLDOFF 秒内一律不循环；
# 81voltd 最近 SUB2_QUIET 秒内还在重试卡 2 时，不为"卡 1 IMS 未注册"做循环
BOOT_HOLDOFF = int(os.environ.get("FUXI_BOOT_HOLDOFF", "300"))
SUB2_QUIET = int(os.environ.get("FUXI_SUB2_QUIET", "180"))

# MMModemState
ST_DISABLED, ST_ENABLED, ST_SEARCHING = 3, 6, 7
# MMModem3gppRegistrationState：home、roaming、sms-only、csfb-not-preferred
REGISTERED = {1, 5, 6, 7, 9, 10}
# MMModemPowerState
PWR_LOW, PWR_ON = 2, 3


def uptime():
    with open("/proc/uptime") as f:
        return float(f.read().split()[0])


def log(msg):
    print(f"[{uptime():.2f}] {msg}", flush=True)


def datacard():
    try:
        with open("/var/lib/fuxi/datacard") as f:
            return f.read().strip()
    except OSError:
        return "1"


def sub2_retrying():
    try:
        out = subprocess.run(["journalctl", "-u", "81voltd", f"--since=-{SUB2_QUIET}s", "-q", "--no-pager"],
                             capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "retrying start network" in out.stdout or "wrong PDN" in out.stdout


class Keeper:
    def __init__(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        self.path = None
        self.unreg_since = None
        self.reject_at = None
        self.last_cycle = 0.0
        self.gap = MIN_GAP
        self.reg_since = None
        self.busy = False
        self.ims_ok = False
        self.ims_cycles = 0
        self.ims_checked = 0.0
        self.ims2_checked = 0.0
        self.ims2_last = None
        self.ims_held = False
        self.datacard = datacard()
        self.bus.signal_subscribe(MM, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                                  None, None, Gio.DBusSignalFlags.NONE, self.on_props)

    def call(self, path, iface, method, args=None, timeout=60000):
        return self.bus.call_sync(MM, path, iface, method, args, None,
                                  Gio.DBusCallFlags.NONE, timeout, None)

    def modems(self):
        try:
            r = self.call("/org/freedesktop/ModemManager1", "org.freedesktop.DBus.ObjectManager",
                          "GetManagedObjects", timeout=10000)
        except GLib.Error:
            return {}
        return {p: ifs for p, ifs in r.unpack()[0].items() if IF_MODEM in ifs}

    def on_props(self, _c, _s, path, _i, _sig, params):
        iface, changed, _inv = params.unpack()
        if iface != IF_3GPP or path != self.path or "NetworkRejection" not in changed:
            return
        rej = changed["NetworkRejection"]
        if rej:
            self.reject_at = time.monotonic()
            log(f"network rejection: {dict(rej)}")

    def set_data_centric(self):
        try:
            out = subprocess.run([USAGE, "data"], capture_output=True, text=True, timeout=40)
            text = " ".join(out.stdout.split())
            log(f"usage data: rc={out.returncode} {text}")
        except (OSError, subprocess.TimeoutExpired) as e:
            log(f"usage data failed: {e}")

    def modem_state(self):
        try:
            r = self.bus.call_sync(MM, self.path, "org.freedesktop.DBus.Properties", "Get",
                                   GLib.Variant("(ss)", (IF_MODEM, "State")), None,
                                   Gio.DBusCallFlags.NONE, 5000, None)
            return r.unpack()[0]
        except GLib.Error:
            return None

    def cycle(self):
        # 先关 NM 的 WWAN：否则 Enable(false) 之后 NM 的 fuxi-mobile-ct 自动连接会在 1 ms 内 simple-connect
        # 把 modem 重新 enable，SetPowerState(low) 报 WrongState，循环只做了一半（11-06 23:03:58 实测）
        log(f"radio cycle start (gap {self.gap}s)")
        self.busy = True
        voltd = subprocess.run(["systemctl", "is-active", "--quiet", "81voltd"]).returncode == 0
        if voltd:
            # 按 81voltd → MM → 81voltd 的顺序恢复，和 fuxi-simctl reconnect 一样
            subprocess.run(["systemctl", "stop", "81voltd"], timeout=60)
        try:
            subprocess.run(["nmcli", "radio", "wwan", "off"], timeout=20)
            for _ in range(20):
                st = self.modem_state()
                if st is not None and st <= ST_DISABLED:
                    break
                time.sleep(0.5)
            if (self.modem_state() or 0) > ST_DISABLED:
                self.call(self.path, IF_MODEM, "Enable", GLib.Variant("(b)", (False,)))
            self.call(self.path, IF_MODEM, "SetPowerState", GLib.Variant("(u)", (PWR_LOW,)))
            time.sleep(5)
            log("radio cycle: power low done")
        except (GLib.Error, OSError, subprocess.TimeoutExpired) as e:
            log(f"radio cycle error: {getattr(e, 'message', e)}")
        finally:
            subprocess.run(["nmcli", "radio", "wwan", "on"], timeout=20)
        try:
            # Enable 自己会上电；NM 打开 WWAN 后可能已经在 enable，WrongState 忽略
            if (self.modem_state() or 0) <= ST_DISABLED:
                self.call(self.path, IF_MODEM, "Enable", GLib.Variant("(b)", (True,)))
            log("radio cycle done")
        except GLib.Error as e:
            log(f"radio cycle enable: {e.message}")
        if voltd:
            subprocess.run(["systemctl", "start", "81voltd"], timeout=60)
        self.busy = False
        self.set_data_centric()
        self.last_cycle = time.monotonic()
        self.unreg_since = self.last_cycle
        self.reject_at = self.reg_since = None

    def ims_registered(self):
        try:
            out = subprocess.run([IMS_TOOL, "0"], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return None
        for line in out.stdout.splitlines():
            key, _, value = line.strip().partition(": ")
            if key == "ims_registration_status":
                return value == "registered"
        return None

    def ims2_status(self):
        try:
            out = subprocess.run([IMS_TOOL, "1"], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return None
        for line in out.stdout.splitlines():
            key, _, value = line.strip().partition(": ")
            if key == "ims_registration_status":
                return value
        return None

    def check_ims2(self, now):
        # 卡 2 只监测、只记日志；不自动恢复（射频循环会断卡 1，恢复要按 81voltd → MM → 81voltd 的顺序，交给人）
        if self.busy or now - self.ims2_checked < IMS_POLL:
            return
        self.ims2_checked = now
        if subprocess.run(["systemctl", "is-active", "--quiet", "81voltd"]).returncode != 0:
            return
        st = self.ims2_status()
        if st is not None and st != self.ims2_last:
            log(f"sim2 ims: {self.ims2_last or 'unknown'} -> {st}")
            self.ims2_last = st

    def check_ims(self, now):
        if now - self.ims_checked < IMS_POLL:
            return
        self.ims_checked = now
        dc = datacard()
        if dc != self.datacard:
            # 换上网卡会断掉卡 1 的 IMS PDN（ims_ok 不能一直算数），VoLTE 计时从切换时刻重新算
            log(f"data card {self.datacard} -> {dc}: restart ims timer")
            self.datacard = dc
            self.reg_since, self.ims_ok, self.ims_cycles, self.ims_held = now, False, 0, False
            return
        if self.ims_ok or self.ims_cycles >= IMS_MAX_CYCLES:
            return
        if subprocess.run(["systemctl", "is-active", "--quiet", "81voltd"]).returncode != 0:
            return
        if dc == "2":
            # 卡槽 2 上网（DDS=2）时 modem 不给卡 1（电信）起 IMS PDN，循环射频没用，只会把卡 2 的数据和 IMS 一起打断
            if not self.ims_held:
                log("ims trigger held: data card is slot 2 (sim1 IMS unavailable while DDS=2)")
                self.ims_held = True
            return
        ok = self.ims_registered()
        if ok:
            self.ims_ok = True
            log(f"ims registered ({int(now - self.reg_since)}s after network registration)")
            return
        if ok is None or now - self.reg_since < IMS_LIMIT:
            return
        if uptime() < BOOT_HOLDOFF or sub2_retrying():
            if not self.ims_held:
                log("ims trigger held: boot holdoff or 81voltd retrying sim2")
                self.ims_held = True
            return
        self.ims_held = False
        self.ims_cycles += 1
        log(f"trigger: ims not registered {int(now - self.reg_since)}s after network registration")
        if self.ims_cycles == 1:
            # 11-06 23:19 实测：DDS 1→2→1 之后 modem IMS 栈不再向 81voltd 要卡 1 的连接，射频循环也没用；
            # 只重启 81voltd，30 s 内两张卡都注册
            log("restart 81voltd")
            subprocess.run(["systemctl", "restart", "81voltd"], timeout=60)
            self.reg_since = time.monotonic()
            return
        self.cycle()

    def tick(self):
        if self.busy:
            return True
        mods = self.modems()
        if self.path not in mods:
            self.path = next(iter(sorted(mods)), None)
            self.unreg_since = self.reject_at = self.reg_since = None
            self.ims_ok, self.ims_cycles = False, 0
            if self.path:
                log(f"new modem {self.path}")
                self.set_data_centric()
            return True
        ifs = mods[self.path]
        state = ifs[IF_MODEM].get("State", 0)
        reg = ifs.get(IF_3GPP, {}).get("RegistrationState", 4)
        now = time.monotonic()
        if reg in REGISTERED or state > ST_SEARCHING:
            if self.unreg_since is not None:
                log(f"registered (state {state}, reg {reg})")
            self.unreg_since = self.reject_at = None
            self.reg_since = self.reg_since or now
            if now - self.reg_since > STABLE_RESET:
                self.gap = MIN_GAP
            self.check_ims(now)
            self.check_ims2(now)
            return True
        self.reg_since = None
        if state not in (ST_ENABLED, ST_SEARCHING):
            self.unreg_since = None
            return True
        if self.unreg_since is None:
            self.unreg_since = now
            log(f"not registered (state {state}, reg {reg})")
        lost = now - self.unreg_since
        rejected = self.reject_at is not None and now - self.reject_at >= REJECT_GRACE
        if (rejected and lost >= REJECT_GRACE) or lost >= STALL_LIMIT:
            if now - self.last_cycle < self.gap or uptime() < BOOT_HOLDOFF:
                return True
            if self.last_cycle:
                self.gap = min(self.gap * 2, MAX_GAP)
            log(f"trigger: {'rejection' if rejected else 'stall'}, unregistered {int(lost)}s")
            self.cycle()
        return True


def main():
    k = Keeper()
    log(f"start: reject_grace={REJECT_GRACE}s stall_limit={STALL_LIMIT}s "
        f"boot_holdoff={BOOT_HOLDOFF}s sub2_quiet={SUB2_QUIET}s")
    GLib.timeout_add_seconds(POLL, k.tick)
    GLib.MainLoop().run()


if __name__ == "__main__":
    sys.exit(main())
