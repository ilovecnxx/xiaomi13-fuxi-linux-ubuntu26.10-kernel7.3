#!/usr/bin/env python3
"""
Clash Fuxi — native GTK4 / libadwaita GUI for the Mihomo (Clash.Meta) core.

Mobile-first redesign (iOS/Android VPN-app style) for the Xiaomi 13 mainline-Linux
+ Phosh phone (1080x2400, phoc scale 2.5 -> 432x960 logical).

Design notes:
  * No desktop window controls / no Adw.HeaderBar title buttons.
  * maximize() (NOT fullscreen) so Phosh's status bar is not overlapped.
  * Big focal connect button, iOS-style segmented mode control, Material-style
    switch rows, generous rounded cards, vertical fill.
"""
import os
import sys
import json
import signal
import time
import subprocess
import threading
import urllib.request

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gio  # noqa: E402

APP_ID = "org.fuxi.ClashFuxi"
APP_NAME = "Clash Fuxi"
MIHOMO = "/usr/local/bin/mihomo"
HOME = os.path.expanduser("~")
CONF_DIR = os.path.join(HOME, ".config", "mihomo")
CONF_FILE = os.path.join(CONF_DIR, "config.yaml")
RUN_DIR = os.path.join(HOME, ".local", "state", "clash-fuxi")
PID_FILE = os.path.join(RUN_DIR, "mihomo.pid")
LOG_FILE = os.path.join(RUN_DIR, "mihomo.log")
SUB_FILE = os.path.join(CONF_DIR, "subscription.url")

API_HOST = "127.0.0.1"
API_PORT = 9090
API_SECRET = "clashfuxi"

DEFAULT_CONFIG = f"""\
mixed-port: 7890
allow-lan: false
mode: rule
log-level: info
geodata-mode: true
geo-auto-update: false
external-controller: {API_HOST}:{API_PORT}
secret: "{API_SECRET}"
dns:
  enable: true
  ipv6: false
  enhanced-mode: fake-ip
  fake-ip-range: 198.18.0.1/16
  nameserver:
    - 223.5.5.5
    - 119.29.29.29
  fallback:
    - 8.8.8.8
    - 1.1.1.1
tun:
  enable: false
  stack: system
  auto-route: true
  auto-detect-interface: true
  dns-hijack:
    - any:53
proxies: []
proxy-groups:
  - name: PROXY
    type: select
    proxies: [DIRECT]
rules:
  - MATCH,PROXY
"""

# ---- mobile palette / design tokens (iOS-ish blue accent, soft surfaces) ----
CSS = """
.hero-card {
  border-radius: 28px;
  background: alpha(@accent_bg_color, 0.10);
  padding: 10px;
}
.connect-btn {
  min-width: 168px; min-height: 168px;
  border-radius: 84px;
  background: alpha(@accent_bg_color, 0.14);
  border: 4px solid alpha(@accent_color, 0.30);
  color: @accent_color;
  transition: all 200ms ease;
}
.connect-btn.on {
  background: @accent_bg_color;
  border: 4px solid @accent_bg_color;
  color: @accent_fg_color;
  box-shadow: 0 0 0 10px alpha(@accent_bg_color, 0.18);
}
.connect-btn:active { transform: scale(0.97); }
.big-state { font-size: 22px; font-weight: 800; }
.sub-state { font-size: 12px; opacity: 0.6; }
.stat-card {
  border-radius: 20px;
  background: alpha(@window_fg_color, 0.06);
  padding: 12px 8px;
}
.stat-arrow { opacity: 0.55; }
.stat-num { font-size: 17px; font-weight: 800; }
.stat-cap { font-size: 10px; opacity: 0.55; letter-spacing: 1px; }
.section-title { font-size: 12px; font-weight: 700; opacity: 0.55; letter-spacing: 1px; }
.row-card { border-radius: 18px; background: alpha(@window_fg_color, 0.05); }
.log-pill { border-radius: 999px; }
"""


def ensure_dirs():
    os.makedirs(CONF_DIR, exist_ok=True)
    os.makedirs(RUN_DIR, exist_ok=True)
    if not os.path.exists(CONF_FILE) or os.path.getsize(CONF_FILE) == 0:
        with open(CONF_FILE, "w") as f:
            f.write(DEFAULT_CONFIG)
    for name, min_size in (("geoip.metadb", 1024 * 1024), ("Country.mmdb", 1024 * 1024)):
        p = os.path.join(CONF_DIR, name)
        try:
            if os.path.exists(p) and os.path.getsize(p) < min_size:
                os.remove(p)
        except Exception:
            pass


def read_pid():
    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return pid
    except Exception:
        return None


def api_request(path, method="GET", data=None, timeout=4):
    url = f"http://{API_HOST}:{API_PORT}{path}"
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {API_SECRET}")
    if body:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


def fmt_speed(n):
    n = float(n)
    for u in ("B", "K", "M", "G"):
        if n < 1024:
            return f"{n:.0f}{u}" if u == "B" else f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}T"


class ClashFuxiApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.proc = None
        self.win = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        self._css = Gtk.CssProvider()
        self._css.load_from_data(CSS.encode())

    def do_activate(self):
        if not self.win:
            self.win = MainWindow(self)
        disp = self.win.get_display()
        if disp and not getattr(self, "_css_added", False):
            Gtk.StyleContext.add_provider_for_display(
                disp, self._css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            self._css_added = True
        self.win.present()


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME)
        self.app = app
        self.set_default_size(432, 960)
        self._prev_sample = None
        self._prev_t = 0.0
        self._busy = False
        self._tun_supported = None   # 未知；探测后 0/1
        ensure_dirs()

        # maximize (not fullscreen) -> Phosh status bar stays visible above us
        self.connect("realize", lambda w: GLib.idle_add(self._fit_window))

        self.toast_overlay = Adw.ToastOverlay()
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        root.set_margin_top(14)
        root.set_margin_bottom(18)
        root.set_margin_start(18)
        root.set_margin_end(18)
        self.toast_overlay.set_child(root)
        self.set_content(self.toast_overlay)

        root.append(self._build_topbar())
        root.append(self._build_hero())          # vexpand big focal area
        root.append(self._build_stats())
        root.append(self._build_mode())
        root.append(self._build_switches())
        root.append(self._build_subscription())

        self._sync_state()
        GLib.timeout_add_seconds(1, self._tick)

    def _fit_window(self):
        # 手机上是 maximize（保留系统顶栏）。若个别环境不认，退回默认尺寸。
        self.maximize()
        GLib.timeout_add(600, self._log_size)
        return False

    def _log_size(self):
        try:
            with open(LOG_FILE, "a") as f:
                f.write(f"[ui] window {self.get_width()}x{self.get_height()} "
                        f"maximized={self.is_maximized()} fullscreened={self.is_fullscreen()}\n")
        except Exception:
            pass
        return False

    # ---------------- top bar (custom, no window controls) ----------------
    def _build_topbar(self):
        bar = Gtk.CenterBox()
        bar.set_margin_start(4)
        bar.set_margin_end(4)
        left = Gtk.Label(label=APP_NAME, xalign=0)
        left.add_css_class("title-3")
        left.add_css_class("heading")
        bar.set_start_widget(left)
        logb = Gtk.Button(icon_name="view-list-symbolic", tooltip_text="内核日志")
        logb.add_css_class("flat")
        logb.add_css_class("circular")
        logb.connect("clicked", self._show_log)
        bar.set_end_widget(logb)
        return bar

    # ---------------- hero: big connect button ----------------
    def _build_hero(self):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        card.add_css_class("hero-card")
        card.set_vexpand(True)
        card.set_valign(Gtk.Align.FILL)
        card.set_halign(Gtk.Align.FILL)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        inner.set_valign(Gtk.Align.CENTER)
        inner.set_vexpand(True)

        self.connect_btn = Gtk.Button()
        self.connect_btn.add_css_class("connect-btn")
        self.connect_btn.set_halign(Gtk.Align.CENTER)
        self.connect_btn.connect("clicked", self._on_power_clicked)
        pbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        pbox.set_valign(Gtk.Align.CENTER)
        self.power_icon = Gtk.Image.new_from_icon_name("network-vpn-symbolic")
        self.power_icon.set_pixel_size(56)
        self.power_icon.set_halign(Gtk.Align.CENTER)
        self.state_lbl = Gtk.Label(label="未连接")
        self.state_lbl.add_css_class("big-state")
        self.state_lbl.set_halign(Gtk.Align.CENTER)
        self.sub_lbl = Gtk.Label(label="点按启动")
        self.sub_lbl.add_css_class("sub-state")
        self.sub_lbl.set_halign(Gtk.Align.CENTER)
        pbox.append(self.power_icon)
        pbox.append(self.state_lbl)
        pbox.append(self.sub_lbl)
        self.connect_btn.set_child(pbox)

        inner.append(self.connect_btn)
        card.append(inner)
        return card

    def _build_stats(self):
        grid = Gtk.Grid()
        grid.set_column_homogeneous(True)
        grid.set_column_spacing(12)

        self.down_num = self._stat_card(grid, 0, "go-next-symbolic",
                                        "0B/s", "下载 DOWNLOAD")
        self.up_num = self._stat_card(grid, 1, "go-previous-symbolic",
                                      "0B/s", "上传 UPLOAD")
        return grid

    def _stat_card(self, grid, col, icon, val, cap):
        c = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        c.add_css_class("stat-card")
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.set_halign(Gtk.Align.CENTER)
        ic = Gtk.Image.new_from_icon_name(icon)
        ic.set_pixel_size(14)
        ic.add_css_class("stat-arrow")
        num = Gtk.Label(label=val)
        num.add_css_class("stat-num")
        row.append(ic)
        row.append(num)
        cap_lbl = Gtk.Label(label=cap)
        cap_lbl.add_css_class("stat-cap")
        c.append(row)
        c.append(cap_lbl)
        grid.attach(c, col, 0, 1, 1)
        return num

    def _section(self, title):
        lbl = Gtk.Label(label=title, xalign=0)
        lbl.add_css_class("section-title")
        lbl.set_margin_start(6)
        return lbl

    def _build_mode(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(self._section("代理模式"))
        self.mode_group = Adw.ToggleGroup()
        self.mode_group.add(Adw.Toggle(name="rule", label="规则"))
        self.mode_group.add(Adw.Toggle(name="global", label="全局"))
        self.mode_group.add(Adw.Toggle(name="direct", label="直连"))
        self.mode_group.set_active_name("rule")
        self.mode_group.connect("notify::active-name", self._on_mode_seg)
        box.append(self.mode_group)
        return box

    def _build_switches(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(self._section("代理开关"))

        row1 = self._switch_row("network-transmit-receive-symbolic", "TUN 模式",
                                "透明代理全部流量")
        self.tun_sw = row1[1]
        self.tun_sw.connect("state-set", self._on_tun_switch)
        box.append(row1[0])

        row2 = self._switch_row("preferences-system-network-symbolic", "系统代理",
                                "设置全局 HTTP/SOCKS 127.0.0.1:7890")
        self.sys_sw = row2[1]
        self.sys_sw.connect("state-set", self._on_sys_switch)
        box.append(row2[0])
        return box

    def _switch_row(self, icon, title, subtitle):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.add_css_class("row-card")
        row.set_margin_top(2)
        row.set_margin_bottom(2)
        row.set_margin_start(14)
        row.set_margin_end(14)
        row.set_size_request(-1, 60)
        ic = Gtk.Image.new_from_icon_name(icon)
        ic.set_pixel_size(20)
        ic.set_valign(Gtk.Align.CENTER)
        ic.set_opacity(0.8)
        txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        txt.set_valign(Gtk.Align.CENTER)
        txt.set_hexpand(True)
        t = Gtk.Label(label=title, xalign=0)
        t.add_css_class("heading")
        s = Gtk.Label(label=subtitle, xalign=0)
        s.add_css_class("caption")
        s.set_opacity(0.6)
        txt.append(t)
        txt.append(s)
        sw = Gtk.Switch(valign=Gtk.Align.CENTER)
        row.append(ic)
        row.append(txt)
        row.append(sw)
        return row, sw

    def _build_subscription(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(self._section("订阅链接"))
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.sub_entry = Gtk.Entry(hexpand=True)
        self.sub_entry.set_placeholder_text("粘贴 Clash 订阅 URL…")
        try:
            if os.path.exists(SUB_FILE):
                with open(SUB_FILE) as f:
                    self.sub_entry.set_text(f.read().strip())
        except Exception:
            pass
        self.import_btn = Gtk.Button(label="导入")
        self.import_btn.add_css_class("suggested-action")
        self.import_btn.add_css_class("pill")
        self.import_btn.connect("clicked", self._on_import)
        row.append(self.sub_entry)
        row.append(self.import_btn)
        box.append(row)
        return box

    def toast(self, msg):
        self.toast_overlay.add_toast(Adw.Toast.new(msg))

    # ---------------- state ----------------
    def _set_running_ui(self, running, pid=None):
        if running:
            self.connect_btn.add_css_class("on")
            self.power_icon.set_from_icon_name("network-vpn-symbolic")
            self.state_lbl.set_text("已连接")
            self.sub_lbl.set_text(f"PID {pid}" if pid else "运行中")
        else:
            self.connect_btn.remove_css_class("on")
            self.power_icon.set_from_icon_name("network-vpn-symbolic")
            self.state_lbl.set_text("未连接")
            self.sub_lbl.set_text("点按启动")
            self.down_num.set_text("0B/s")
            self.up_num.set_text("0B/s")

    def _sync_state(self):
        pid = read_pid()
        self._set_running_ui(pid is not None, pid)
        if pid:
            self._load_remote_state()

    def _load_remote_state(self):
        try:
            cfg = api_request("/configs")
            mode = cfg.get("mode", "rule")
            self.mode_group.handler_block_by_func(self._on_mode_seg)
            if mode in ("rule", "global", "direct"):
                self.mode_group.set_active_name(mode)
            self.mode_group.handler_unblock_by_func(self._on_mode_seg)
            tun = bool(cfg.get("tun", {}).get("enable", False))
            self.tun_sw.handler_block_by_func(self._on_tun_switch)
            self.tun_sw.set_active(tun)
            self.tun_sw.handler_unblock_by_func(self._on_tun_switch)
        except Exception:
            pass

    # ---------------- core control ----------------
    def _start_core(self):
        try:
            with open(LOG_FILE, "a") as log:
                log.write(f"\n=== start {time.ctime()} ===\n"); log.flush()
                self.app.proc = subprocess.Popen(
                    [MIHOMO, "-d", CONF_DIR], stdout=log, stderr=log, start_new_session=True)
            with open(PID_FILE, "w") as f:
                f.write(str(self.app.proc.pid))
            for _ in range(50):
                try:
                    api_request("/version", timeout=1)
                    return True
                except Exception:
                    time.sleep(0.15)
            return True
        except Exception as e:
            self.toast(f"启动失败：{e}")
            return False

    def _stop_core(self):
        pid = read_pid()
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
                for _ in range(20):
                    try:
                        os.kill(pid, 0); time.sleep(0.1)
                    except Exception:
                        break
                else:
                    os.kill(pid, signal.SIGKILL)
            except Exception:
                pass
        try:
            os.remove(PID_FILE)
        except Exception:
            pass

    def _on_power_clicked(self, _):
        if self._busy:
            return
        if read_pid() is not None:
            self._stop_core()
            self._set_running_ui(False)
            self.toast("已断开")
        else:
            self._busy = True
            self.sub_lbl.set_text("启动中…")
            def work():
                ok = self._start_core()
                GLib.idle_add(self._after_start, ok)
            threading.Thread(target=work, daemon=True).start()

    def _after_start(self, ok):
        self._busy = False
        if ok:
            self._set_running_ui(True, read_pid())
            self.toast("已连接")
            self._load_remote_state()
        else:
            self._set_running_ui(False)
        return False

    # ---------------- subscription ----------------
    def _on_import(self, btn):
        url = self.sub_entry.get_text().strip()
        if not url:
            self.toast("请先填写订阅链接")
            return
        btn.set_sensitive(False)
        btn.set_label("…")
        def work():
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": "ClashforWindows/0.20.39 mihomo/1.19"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    text = r.read().decode("utf-8", "replace")
                if "proxies" not in text and "proxy-providers" not in text:
                    GLib.idle_add(self._import_done, False, "内容不像 Clash 配置")
                    return
                if "external-controller" not in text:
                    text += f"\nexternal-controller: {API_HOST}:{API_PORT}\nsecret: \"{API_SECRET}\"\n"
                with open(CONF_FILE, "w") as f:
                    f.write(text)
                with open(SUB_FILE, "w") as f:
                    f.write(url + "\n")
                GLib.idle_add(self._import_done, True, "订阅已保存")
            except Exception as e:
                GLib.idle_add(self._import_done, False, f"下载失败：{e}")
        threading.Thread(target=work, daemon=True).start()

    def _import_done(self, ok, msg):
        self.toast(msg)
        self.import_btn.set_sensitive(True)
        self.import_btn.set_label("导入")
        if ok and read_pid():
            try:
                api_request("/configs", "PUT", {"path": CONF_FILE})
            except Exception:
                pass
        return False

    # ---------------- switches ----------------
    def _on_tun_switch(self, sw, state):
        try:
            api_request("/configs", "PATCH", {"tun": {"enable": state}})
        except Exception:
            self.toast("切换 TUN 失败（内核未运行？）")
            self._revert(sw, self._on_tun_switch)
            return False
        GLib.timeout_add(900, self._verify_tun, state)
        return False

    def _verify_tun(self, want):
        try:
            actual = bool(api_request("/configs", timeout=2).get("tun", {}).get("enable", False))
        except Exception:
            actual = want
        if want and not actual:
            self.toast("TUN 失败：内核缺策略路由/nft（需 FUXI_NET=1 镜像）")
            self._revert(self.tun_sw, self._on_tun_switch)
        elif want:
            self.toast("TUN 已开启")
        else:
            self.toast("TUN 已关闭")
        return False

    def _revert(self, sw, handler):
        sw.handler_block_by_func(handler)
        sw.set_active(not sw.get_active())
        sw.handler_unblock_by_func(handler)

    def _on_sys_switch(self, sw, state):
        try:
            if state:
                subprocess.run(["gsettings", "set", "org.gnome.system.proxy", "mode", "manual"], check=False)
                subprocess.run(["gsettings", "set", "org.gnome.system.proxy.http", "host", "127.0.0.1"], check=False)
                subprocess.run(["gsettings", "set", "org.gnome.system.proxy.http", "port", "7890"], check=False)
                subprocess.run(["gsettings", "set", "org.gnome.system.proxy.https", "host", "127.0.0.1"], check=False)
                subprocess.run(["gsettings", "set", "org.gnome.system.proxy.https", "port", "7890"], check=False)
                subprocess.run(["gsettings", "set", "org.gnome.system.proxy", "ignore-hosts",
                                "['localhost','127.0.0.0/8','::1']"], check=False)
            else:
                subprocess.run(["gsettings", "set", "org.gnome.system.proxy", "mode", "none"], check=False)
            self.toast(f"系统代理已{'开启' if state else '关闭'}")
        except Exception as e:
            self.toast(f"设置系统代理失败：{e}")
        return False

    # ---------------- mode ----------------
    def _on_mode_seg(self, group, _):
        name = group.get_active_name()
        try:
            api_request("/configs", "PATCH", {"mode": name})
            label = {"rule": "规则", "global": "全局", "direct": "直连"}.get(name, name)
            self.toast(f"模式：{label}")
        except Exception:
            self.toast("切换模式失败（内核未运行？）")

    def _show_log(self, _=None):
        try:
            with open(LOG_FILE) as f:
                tail = f.read()[-4000:]
        except Exception:
            tail = "（暂无日志）"
        dlg = Adw.Dialog()
        dlg.set_title("内核日志")
        dlg.set_content_width(380); dlg.set_content_height(600)
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        sw = Gtk.ScrolledWindow(vexpand=True)
        lbl = Gtk.Label(label=tail, xalign=0, yalign=0, wrap=True, selectable=True)
        for s in ("top", "bottom", "start", "end"):
            getattr(lbl, f"set_margin_{s}")(8)
        sw.set_child(lbl); tv.set_content(sw); dlg.set_child(tv)
        dlg.present(self)

    # ---------------- tick ----------------
    def _tick(self):
        if read_pid():
            try:
                conns = api_request("/connections", timeout=2).get("connections") or []
                up_total = sum((x.get("upload") or 0) for x in conns)
                down_total = sum((x.get("download") or 0) for x in conns)
                now = time.monotonic()
                if self._prev_sample is not None:
                    dt = max(now - self._prev_t, 0.001)
                    self.up_num.set_text(fmt_speed(max(0, up_total - self._prev_sample[0]) / dt) + "/s")
                    self.down_num.set_text(fmt_speed(max(0, down_total - self._prev_sample[1]) / dt) + "/s")
                self._prev_sample = (up_total, down_total)
                self._prev_t = now
            except Exception:
                pass
        else:
            self._prev_sample = None
        return True


def main():
    ensure_dirs()
    app = ClashFuxiApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
