#!/usr/bin/env python3
"""DSH 手机端原生窗口（WebKitGTK，Phosh 原生 GTK 组件）。
省电策略：
  - 服务按需启动：打开时若 dsh web 未监听，自动拉起（systemctl --user start）。
  - 闲置自动停：窗口打开期间每分钟刷新活动戳；窗口关闭后由 timer 在闲置阈值后停服。
  - 关闭硬件合成 / 减少动画（可用 DSH_SKIN_LOWPOWER=0 关闭）以降低 GPU/CPU 功耗。
用法: dsh-web-window [URL]   无 URL 时读活动戳同目录 url.txt，再退回 http://127.0.0.1:<PORT>/。
"""
import os
import socket
import subprocess
import sys
import time

PORT = int(os.environ.get("DSH_WEB_PORT", "3080"))
BIND = os.environ.get("DSH_WEB_BIND", "127.0.0.1")
STATE_DIR = os.environ.get("DSH_WEB_STATE", os.path.expanduser("~/.local/state/dsh-web"))
URL_FILE = os.path.join(STATE_DIR, "url.txt")
STAMP_FILE = os.path.join(STATE_DIR, "last-activity")
PROBE_HOST = "127.0.0.1" if BIND in ("0.0.0.0", "", "127.0.0.1") else BIND
BASE = "http://%s:%d/" % (PROBE_HOST, PORT)


def is_port_open(host=PROBE_HOST, port=PORT, timeout=1.0):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def ensure_service():
    """若端口未监听，尝试按需启动用户级服务。"""
    if is_port_open():
        return
    for cmd in (["systemctl", "--user", "start", "dsh-web.service"],
                ["systemctl", "start", "dsh-web.service"]):
        try:
            subprocess.run(cmd, check=False, timeout=15,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            continue
        for _ in range(50):
            if is_port_open():
                return
            time.sleep(0.4)


def touch_stamp():
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(STAMP_FILE, "w") as f:
            f.write(str(int(time.time())))
    except OSError:
        pass


def resolve_url():
    if len(sys.argv) > 1:
        return sys.argv[1]
    try:
        with open(URL_FILE) as f:
            u = f.read().strip()
            if u:
                return u
    except OSError:
        pass
    return BASE


def main():
    # 默认开启硬件加速（有 Adreno/msm GPU，走 GPU 渲染才流畅）。
    # 省电靠“按需启动+闲置停服”，不靠关合成。DSH_SKIN_LOWPOWER=1 时才关（牺牲流畅换功耗）。
    lowpower = os.environ.get("DSH_SKIN_LOWPOWER", "0") == "1"
    if lowpower:
        os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")
        os.environ.setdefault("WEBKIT_DISABLE_DMABUF_RENDERER", "1")

    ensure_service()
    touch_stamp()
    url = resolve_url()

    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("WebKit2", "4.1")
    from gi.repository import Gtk, WebKit2, GLib  # type: ignore

    win = Gtk.Window(title="DeepSeek")
    win.set_default_size(414, 896)

    view = WebKit2.WebView()
    st = view.get_settings()
    st.set_property("enable-javascript", True)
    st.set_property("enable-smooth-scrolling", not lowpower)
    st.set_property("default-font-size", 16)
    # 显式确认属性存在再设置（不同 WebKit 版本差异）
    has_hw = any(p.name == "hardware-acceleration-policy" for p in st.list_properties())
    if has_hw:
        try:
            pol = (WebKit2.HardwareAccelerationPolicy.NEVER if lowpower
                   else WebKit2.HardwareAccelerationPolicy.ALWAYS)
            st.set_property("hardware-acceleration-policy", pol)
        except Exception:
            pass

    view.load_uri(url)
    win.add(view)

    # 窗口打开期间每分钟刷新活动戳，避免闲置 timer 在用户正使用时停服
    def heartbeat():
        touch_stamp()
        return True  # 继续定时

    hb_id = GLib.timeout_add_seconds(60, heartbeat)

    def on_destroy(*_):
        try:
            GLib.source_remove(hb_id)
        except Exception:
            pass
        touch_stamp()
        Gtk.main_quit()

    win.connect("destroy", on_destroy)
    win.show_all()
    win.maximize()
    Gtk.main()


if __name__ == "__main__":
    main()
