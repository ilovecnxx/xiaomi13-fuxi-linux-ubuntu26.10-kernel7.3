"""GTK4/libadwaita + WebKitGTK window hosting the SuperTerm local UI.

The UI HTML/CSS/JS is bundled under src/superterm/ui and served from a custom
`superterm://` scheme (offline, no loopback server).  A WebKit user-content script
bridge connects JS `window.superterm.call(...)` to the Python Bridge.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    # WebKit 6.0 is the GTK4-native bindings; 4.1 is GTK3-based and incompatible with
    # libadwaita.  Prefer 6.0 and fall back to 4.1 only if 6.0 is unavailable.
    _WEBKIT_NS, _WEBKIT_VER = "WebKit", "6.0"
    try:
        gi.require_version("WebKit", "6.0")
    except ValueError:
        _WEBKIT_NS, _WEBKIT_VER = "WebKit2", "4.1"
        gi.require_version("WebKit2", "4.1")
    from gi.repository import Gtk, Adw, GLib, Gio  # noqa: E402
    WebKit2 = __import__("gi.repository", fromlist=[_WEBKIT_NS]).__dict__[_WEBKIT_NS]
    _HAVE_GI = True
except Exception:  # noqa: BLE001
    _HAVE_GI = False

from .app import App
from .bridge import Bridge

UI_DIR = Path(__file__).parent / "ui"

# JS injected before page load: defines the call/bridge glue.
_PRELOAD_JS = r"""
(function(){
  function post(msg){
    try { window.webkit.messageHandlers.superterm.postMessage(msg); } catch(e) {}
  }
  const pending = new Map();
  let counter = 0;
  window.superterm = {
    protocolVersion: 1,
    call: function(method, params){
      return new Promise(function(resolve, reject){
        const id = 'r' + (++counter);
        pending.set(id, {resolve: resolve, reject: reject});
        post(JSON.stringify({protocol_version: 1, request_id: id, method: method, params: params || {}}));
      });
    },
    _resolve: function(resp){
      const p = pending.get(resp.request_id);
      if(!p) return;
      pending.delete(resp.request_id);
      if(resp.ok) p.resolve(resp.result);
      else p.reject(resp.error || {code:'error', message:'unknown'});
    },
    _event: function(msg){
      if(window.supertermOnEvent) window.supertermOnEvent(msg);
    }
  };
})();
"""


class SuperTermWindow(Adw.ApplicationWindow if _HAVE_GI else object):
    def __init__(self, app: "SuperTermApp", **kwargs) -> None:
        kwargs.setdefault("application", app)
        super().__init__(**kwargs)
        self._kt_app: App = app.superterm_app
        self._bridge = Bridge(self._kt_app)
        self._loop = asyncio.new_event_loop()
        # Run the asyncio loop in a background thread so bridge coroutines actually execute.
        import threading
        self._loop_thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._loop_thread.start()

        self.set_default_size(420, 860)
        self.set_title("SuperTerm")

        # WebKit web view
        self._web = WebKit2.WebView()
        settings = self._web.get_settings()
        settings.set_enable_developer_extras(True)
        # Allow the bundled file:// UI to use the local resources it needs.
        try:
            settings.set_allow_file_access_from_file_urls(True)
            settings.set_allow_universal_access_from_file_urls(True)
        except Exception:  # noqa: BLE001
            pass

        ucm = self._web.get_user_content_manager()
        ucm.add_script(WebKit2.UserScript.new(
            _PRELOAD_JS, WebKit2.UserContentInjectedFrames.TOP_FRAME,
            WebKit2.UserScriptInjectionTime.START, None, None))
        ucm.connect("script-message-received::superterm", self._on_js_message)
        ucm.register_script_message_handler("superterm")

        self._web.connect("load-changed", self._on_load)
        self._web.connect("load-failed", self._on_load_failed)
        # Offline: load the bundled UI straight from the filesystem (no server, no scheme).
        self._web.load_uri((UI_DIR / "index.html").as_uri())

        # libadwaita header
        header = Adw.HeaderBar()
        title = Gtk.Label(label="SuperTerm")
        header.set_title_widget(title)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(self._web)
        self.set_content(toolbar)

    # ---- custom scheme (serve bundled files) ----
    def _on_scheme(self, request):
        path = request.get_path() or "/index.html"
        rel = path.lstrip("/")
        target = (UI_DIR / rel).resolve()
        try:
            target.relative_to(UI_DIR.resolve())
        except ValueError:
            request.finish_error(GLib.Error.new_literal(GLib.FileError, GLib.FileError.NOT_FOUND, "bad path"))
            return
        if not target.exists():
            # default document when hitting the scheme root
            target = UI_DIR / "index.html"
        mime = {".html": "text/html", ".js": "text/javascript", ".css": "text/css",
                ".svg": "image/svg+xml", ".json": "application/json"}.get(target.suffix, "text/plain")
        try:
            data = target.read_bytes()
        except OSError:
            data = b"not found"
        stream = WebKit2.MemoryInputStream.new_from_data(data, None)
        request.finish(stream, len(data), mime)

    # ---- JS -> Python ----
    def _on_js_message(self, manager, js_result):
        # WebKit2 4.1 gives a JavascriptResult (.get_js_value()); WebKit 6.0 gives a
        # JSCValue directly. Support both.
        try:
            raw = js_result.get_js_value().to_string()
        except AttributeError:
            raw = js_result.to_string()
        fut = asyncio.run_coroutine_threadsafe(self._bridge.handle(raw), self._loop)
        fut.add_done_callback(lambda f: GLib.idle_add(self._deliver_response, f))

    def _deliver_response(self, fut):
        try:
            resp = fut.result()
        except Exception as e:  # noqa: BLE001
            resp = json.dumps({"request_id": None, "ok": False,
                               "error": {"code": "internal", "message": str(e)}})
        self._send_to_js(f"window.superterm._resolve({resp});")

    def _send_to_js(self, js: str) -> None:
        self._web.evaluate_javascript(js, -1, None, None, None, None, None)

    async def _push_to_js(self, msg: dict) -> None:
        payload = json.dumps(msg, ensure_ascii=False)
        GLib.idle_add(self._send_to_js, f"window.superterm._event({payload});")

    def _on_load(self, webview, event):
        if os.environ.get("SUPERTERM_SELFTEST"):
            sys.stdout.write(f"LOAD_EVENT={event}\n"); sys.stdout.flush()
        if event == WebKit2.LoadEvent.FINISHED:
            self._kt_app.controller.set_push(self._push_to_js)
            # selftest: exercise the JS->Python->JS bridge round-trip and the UI DOM.
            if os.environ.get("SUPERTERM_SELFTEST"):
                self._run_selftest()
            elif os.environ.get("SUPERTERM_E2E"):
                self._run_e2e()

    def _on_load_failed(self, webview, event, uri, error):
        sys.stdout.write(f"LOAD_FAILED uri={uri} err={error}\n"); sys.stdout.flush()
        return False

    def _run_selftest(self):
        # Step 1 (async, fire-and-forget): call the bridge and stash the result on window.
        kick = """
        window.__st_boot = null; window.__st_err = null;
        window.superterm.call('app_get_boot_state', {}).then(function(b){ window.__st_boot = b; })
          .catch(function(e){ window.__st_err = String(e && e.message || e); });
        "kicked";
        """
        self._web.evaluate_javascript(kick, -1, None, None, None, None, None)
        # Step 2 (after a short delay): read DOM + stashed result synchronously.
        GLib.timeout_add(700, self._selftest_read)

    def _selftest_read(self):
        read = r"""
        (function(){
          var b = window.__st_boot;
          return JSON.stringify({
            ok: !!(b && b.profiles !== undefined),
            err: window.__st_err,
            title: (document.querySelector('#title')||{}).textContent || '',
            hasComposer: !!document.querySelector('#input'),
            hasMessages: !!document.querySelector('#messages'),
            hasSettings: !!document.querySelector('#settings'),
            css: getComputedStyle(document.body).backgroundColor,
            profiles: b ? (b.profiles||[]).length : -1,
            protocol: b ? b.protocol_version : -1
          });
        })();
        """

        def cb(wv, result, *args):
            try:
                jsval = wv.evaluate_javascript_finish(result)
                out = jsval.to_string() if jsval else "(null)"
            except Exception as e:  # noqa: BLE001
                out = "ERROR:" + str(e)
            sys.stdout.write("SELFTEST_RESULT=" + out + "\n")
            sys.stdout.flush()
            self.get_application().quit()
            return False

        self._web.evaluate_javascript(read, -1, None, None, None, cb, None)
        return False

    def _run_e2e(self):
        """Drive a real turn through the JS UI, then report DOM stats. Env: SUPERTERM_E2E=provider::prompt"""
        spec = os.environ.get("SUPERTERM_E2E", "claude::hi")
        provider, _, prompt = spec.partition("::")
        prompt = prompt or "reply OK"
        ws = os.environ.get("SUPERTERM_E2E_WS", "/tmp/fuxi-ws")
        kick = """
        (async function(){
          try {
            var s = await window.superterm.call('session_create', {provider: %s, workspace: %s, title: %s});
            window.__e2e_sid = s.session.id;
            await window.superterm.call('turn_start', {session_id: s.session.id, prompt: %s});
            window.__e2e_started = true;
          } catch(e){ window.__e2e_err = String(e && e.message || e); }
        })();
        "kicked";
        """ % (json.dumps(provider), json.dumps(ws), json.dumps(prompt[:40]), json.dumps(prompt))
        self._web.evaluate_javascript(kick, -1, None, None, None, None, None)
        GLib.timeout_add(1500, self._e2e_poll, [0])

    def _e2e_poll(self, count):
        read = r"""
        (function(){
          return JSON.stringify({
            started: !!window.__e2e_started,
            err: window.__e2e_err || null,
            bubbles: document.querySelectorAll('#messages .bubble').length,
            toolcards: document.querySelectorAll('#messages .tool-card').length,
            assistantText: Array.from(document.querySelectorAll('#messages .msg.assistant .bubble')).map(function(e){return e.textContent;}).join('|').slice(0,200),
            running: !!document.querySelector('#btn-stop') && !document.querySelector('#btn-stop').classList.contains('hidden')
          });
        })();
        """

        def cb(wv, result, *args):
            try:
                out = wv.evaluate_javascript_finish(result).to_string()
            except Exception as e:  # noqa: BLE001
                out = "ERR:" + str(e)
            sys.stdout.write("E2E_POLL=" + out + "\n"); sys.stdout.flush()
            try:
                info = json.loads(out)
            except Exception:  # noqa: BLE001
                info = {"running": False}
            if not info.get("running") and info.get("bubbles", 0) > 0:
                sys.stdout.write("E2E_DONE\n"); sys.stdout.flush()
                self.get_application().quit()
                return False
            count[0] += 1
            if count[0] > 40:
                sys.stdout.write("E2E_TIMEOUT\n"); sys.stdout.flush()
                self.get_application().quit()
                return False
            GLib.timeout_add(1500, self._e2e_poll, count)
            return False

        self._web.evaluate_javascript(read, -1, None, None, None, cb, None)
        return False


class SuperTermApp(Adw.Application if _HAVE_GI else object):
    def __init__(self) -> None:
        super().__init__(application_id="org.superterm.app",
                         flags=Gio.ApplicationFlags.FLAGS_NONE if _HAVE_GI else 0)
        self.superterm_app = App()
        self.connect("activate", self._on_activate)
        self.connect("shutdown", self._on_shutdown)

    def _on_activate(self, app):
        try:
            win = SuperTermWindow(app)
            win.present()
        except Exception as e:  # noqa: BLE001
            import traceback
            sys.stderr.write("ACTIVATE ERROR: " + str(e) + "\n")
            traceback.print_exc()
            sys.stderr.flush()
            raise

    def _on_shutdown(self, app):
        self.superterm_app.close()


def main(argv=None):
    if not _HAVE_GI:
        sys.stderr.write("SuperTerm GUI requires GTK4/libadwaita/WebKitGTK (python3-gi). "
                         "Use `superterm-headless` for CLI mode.\n")
        return 3
    app = SuperTermApp()
    # Pass an empty argv: GIO would otherwise try to parse '-m' (from `python -m`)
    # as an application option and exit immediately.
    return app.run([] if argv is None else argv)


if __name__ == "__main__":
    sys.exit(main())
