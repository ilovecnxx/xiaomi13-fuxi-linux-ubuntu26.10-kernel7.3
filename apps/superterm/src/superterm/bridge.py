"""Strict UI bridge: validates request/response envelopes and dispatches to App.

Every request: {protocol_version, request_id, method, params}.
Every response: {request_id, ok, result | error}.
Server push:    {protocol_version, kind, cursor, payload}.

Only the bundled app origin is allowed to talk to the bridge (WebKit loads it via a
custom scheme).  Unknown methods / oversized payloads are rejected.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import os
from typing import Any, Awaitable, Callable, Optional

from . import models as M
from .app import App

MAX_REQUEST_BYTES = 2 * 1024 * 1024  # 2 MiB


class BridgeError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class Bridge:
    def __init__(self, app: App, push: Optional[Callable[[dict], Awaitable[None]]] = None) -> None:
        self.app = app
        self._push = push
        app.controller.set_push(self._on_event)

    async def _on_event(self, msg: dict[str, Any]) -> None:
        if self._push is not None:
            await self._push(msg)

    # ---------- request handling ----------
    async def handle(self, raw: str) -> str:
        try:
            if len(raw.encode("utf-8")) > MAX_REQUEST_BYTES:
                raise BridgeError("too_large", "request exceeds size limit")
            req = json.loads(raw)
            if not isinstance(req, dict):
                raise BridgeError("bad_request", "request must be an object")
            if req.get("protocol_version") != M.PROTOCOL_VERSION:
                raise BridgeError("bad_protocol", f"expected protocol_version {M.PROTOCOL_VERSION}")
            rid = req.get("request_id")
            method = req.get("method")
            params = req.get("params") or {}
            if not rid or not isinstance(rid, str):
                raise BridgeError("bad_request", "missing request_id")
            if not method or not isinstance(method, str):
                raise BridgeError("bad_request", "missing method")
            result = await self._dispatch(method, params)
            return json.dumps({"request_id": rid, "ok": True, "result": result}, ensure_ascii=False)
        except BridgeError as e:
            return json.dumps({"request_id": self._rid(raw), "ok": False,
                               "error": {"code": e.code, "message": e.message}}, ensure_ascii=False)
        except Exception as e:  # noqa: BLE001
            return json.dumps({"request_id": self._rid(raw), "ok": False,
                               "error": {"code": "internal", "message": str(e)}}, ensure_ascii=False)

    @staticmethod
    def _rid(raw: str) -> Any:
        try:
            return json.loads(raw).get("request_id")
        except Exception:  # noqa: BLE001
            return None

    async def _dispatch(self, method: str, p: dict[str, Any]) -> Any:
        attr = "m_" + method.replace(".", "_")
        fn = getattr(self, attr, None)
        if fn is None:
            raise BridgeError("unknown_method", f"no method {method}")
        return await fn(p) if asyncio.iscoroutinefunction(fn) else fn(p)

    # ---------- methods ----------
    def m_app_get_boot_state(self, p: dict) -> dict:
        profiles = [pr.summary(self.app.config.secrets) for pr in self.app.config.list_profiles()]
        bindings = {a: self.app.config.get_binding(a) for a in ("codex", "claude", "dsh")}
        return {"protocol_version": M.PROTOCOL_VERSION, "profiles": profiles,
                "bindings": bindings, "app_version": "0.1.0"}

    def m_session_list(self, p: dict) -> dict:
        limit = int(p.get("limit", 100)); offset = int(p.get("offset", 0))
        rows = self.app.list_sessions(limit, offset)
        return {"sessions": [{"id": r.id, "provider": r.provider, "title": r.title,
                              "workspace": r.workspace, "status": r.status,
                              "provider_session_id": r.provider_session_id,
                              "created_at": r.created_at, "updated_at": r.updated_at} for r in rows]}

    @staticmethod
    def _allowed_ws_roots():
        """Directories in which workspaces / listings are permitted: the user's home and
        the system temp dir. Prevents the bridge from enumerating arbitrary paths."""
        from pathlib import Path
        roots = [Path(os.path.expanduser("~")).resolve()]
        tmp = Path(tempfile.gettempdir()).resolve()
        if tmp not in roots:
            roots.append(tmp)
        return roots

    def _ws_ok(self, resolved) -> bool:
        from .paths import is_within
        return any(is_within(r, resolved) or resolved == r for r in self._allowed_ws_roots())

    def m_session_create(self, p: dict) -> dict:
        from pathlib import Path
        ws_raw = p.get("workspace") or str(Path(os.path.expanduser("~")))
        ws = Path(ws_raw).expanduser()
        try:
            ws_res = ws.resolve(strict=True)
        except OSError:
            raise BridgeError("io", f"workspace does not exist: {ws_raw}")
        if not ws_res.is_dir():
            raise BridgeError("io", f"workspace is not a directory: {ws_raw}")
        if not self._ws_ok(ws_res):
            raise BridgeError("forbidden", "workspace outside allowed roots (home/tmp)")
        s = self.app.create_session(p["provider"], str(ws_res),
                                    p.get("title", ""), p.get("model"))
        return {"session": {"id": s.id, "provider": s.provider, "workspace": s.workspace,
                            "title": s.title, "status": s.status}}

    def m_session_open(self, p: dict) -> dict:
        sess, events = self.app.open_session(p["session_id"], int(p.get("after", 0)))
        return {"session": {"id": sess.id, "provider": sess.provider, "workspace": sess.workspace,
                            "title": sess.title, "status": sess.status},
                "events": events}

    async def m_turn_start(self, p: dict) -> dict:
        to = p.get("timeout_s")
        tid = await self.app.start_turn(p["session_id"], p["prompt"],
                                        model=p.get("model"), resume=bool(p.get("resume")),
                                        timeout_s=float(to) if to is not None else None)
        return {"turn_id": tid, "running": True}

    async def m_turn_cancel(self, p: dict) -> dict:
        ok = await self.app.cancel(p["session_id"])
        return {"cancelled": ok}

    # config
    def m_config_list_profiles(self, p: dict) -> dict:
        appt = p.get("app_type")
        profiles = [pr.summary(self.app.config.secrets) for pr in self.app.config.list_profiles(appt)]
        return {"profiles": profiles,
                "bindings": {a: self.app.config.get_binding(a) for a in ("codex", "claude", "dsh")}}

    def m_config_save_profile(self, p: dict) -> dict:
        from .config.profiles import Profile
        prof = Profile(id=p.get("id") or f"{p.get('app_type','x')}-{M.new_id()[:8]}",
                       display_name=p["display_name"], app_type=p["app_type"],
                       base_url=p.get("base_url", ""), wire_api=p.get("wire_api", ""),
                       enabled=bool(p.get("enabled", True)), source="local")
        self.app.config.upsert_profile(prof, token=p.get("token"))
        if p.get("models"):
            self.app.config.set_models(prof.id, p["models"])
        self.app.refresh_redactor()  # new token must be scrubbed from now on
        return {"profile": self.app.config.get_profile(prof.id).summary(self.app.config.secrets)}

    def m_config_delete_profile(self, p: dict) -> dict:
        self.app.config.delete_profile(p["id"])
        return {"deleted": p["id"]}

    def m_config_set_binding(self, p: dict) -> dict:
        self.app.config.set_binding(p["agent"], p["provider_id"], p.get("model_id"))
        res = self.app.apply_binding(p["agent"])
        return {"binding": self.app.config.get_binding(p["agent"]), "materialize": res}

    def m_config_apply(self, p: dict) -> dict:
        return self.app.apply_binding(p["agent"])

    def m_config_health_check(self, p: dict) -> dict:
        return self.app.health_check(p["provider_id"])

    def m_config_preview_cc_switch(self, p: dict) -> dict:
        from .config import import_cc_switch
        dbp = p.get("db") or os.path.expanduser("~/.cc-switch/cc-switch.db")
        try:
            return import_cc_switch.preview_import(dbp)
        except FileNotFoundError:
            raise BridgeError("not_found", f"cc-switch db not found: {dbp}")

    def m_config_import_cc_switch(self, p: dict) -> dict:
        from .config import import_cc_switch
        dbp = p.get("db") or os.path.expanduser("~/.cc-switch/cc-switch.db")
        try:
            res = import_cc_switch.do_import(dbp, self.app.config, only_ids=p.get("ids"),
                                             set_current=bool(p.get("set_current", True)))
            self.app.refresh_redactor()  # imported tokens must be scrubbed from now on
            return res
        except FileNotFoundError:
            raise BridgeError("not_found", f"cc-switch db not found: {dbp}")

    # logs
    def m_logs_query(self, p: dict) -> dict:
        sid = p["session_id"]
        after = int(p.get("after", 0))
        limit = int(p.get("limit", 200))
        kind = p.get("kind")
        events = self.app.store.events_after(sid, after, limit)
        if kind:
            events = [e for e in events if e.get("kind") == kind]
        return {"events": events}

    def m_workspace_list(self, p: dict) -> dict:
        from pathlib import Path
        default_root = str(Path(os.path.expanduser("~")))
        requested = Path(p.get("root") or default_root).expanduser()
        try:
            root = requested.resolve(strict=True)
        except OSError as e:
            raise BridgeError("io", f"cannot resolve root: {e}")
        if not self._ws_ok(root):
            raise BridgeError("forbidden", "workspace list root outside allowed roots (home/tmp)")
        if not root.is_dir():
            raise BridgeError("io", "not a directory")
        try:
            entries = sorted(os.listdir(root))
        except OSError as e:
            raise BridgeError("io", str(e))
        dirs = [d for d in entries if os.path.isdir(os.path.join(root, d)) and not d.startswith(".")]
        return {"root": str(root), "dirs": dirs[:200]}

    def m_workspace_read_file(self, p: dict) -> dict:
        from .paths import is_within
        from pathlib import Path
        sess = self.app.store.get_session(p["session_id"])
        if not sess:
            raise BridgeError("not_found", "no session")
        root = Path(sess.workspace)
        target = root / p["path"]
        if not is_within(root, target):
            raise BridgeError("forbidden", "path escapes workspace")
        try:
            data = target.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            raise BridgeError("io", str(e))
        maxb = int(p.get("max_bytes", 200000))
        truncated = len(data) > maxb
        return {"path": p["path"], "content": data[:maxb], "truncated": truncated}
