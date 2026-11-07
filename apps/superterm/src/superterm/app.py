"""Application service: the single facade the UI (or headless CLI) uses.

Wraps Controller + ConfigStore, resolves per-turn env from the active agent binding,
and materializes CLI config when the user switches providers.  Keeps token handling
in one place.
"""
from __future__ import annotations

from typing import Any, Optional

from . import models as M
from .config.profiles import ConfigStore
from .config import writers
from .drivers.base import AgentSpec
from .sessions.controller import Controller
from .sessions.store import Store
from .redaction import Redactor


class App:
    def __init__(self, store: Optional[Store] = None, config: Optional[ConfigStore] = None) -> None:
        self.store = store or Store()
        self.config = config or ConfigStore()
        # build redactor with all known secrets so raw logs never leak them
        secrets = []
        for p in self.config.list_profiles():
            t = self.config.secrets.get(p.id)
            if t:
                secrets.append(t)
        self.redactor = Redactor(secrets)
        self.store.set_redactor(self.redactor)
        # Reconcile turns/sessions left 'running' by a previous process (or by a finalize
        # that could not be written to disk). Guarantees nothing stays permanently 'running'.
        try:
            self.recovered = self.store.recover_orphans()
        except Exception:  # noqa: BLE001
            self.recovered = 0
        self.controller = Controller(self.store, self.redactor)

    def refresh_redactor(self) -> None:
        """Reload configured secrets into the redactor (call after adding/importing tokens)."""
        secrets = []
        for p in self.config.list_profiles():
            t = self.config.secrets.get(p.id)
            if t:
                secrets.append(t)
        self.redactor.set_secrets(secrets)
        self.store.set_redactor(self.redactor)

    # ---------- sessions ----------
    def create_session(self, provider: str, workspace: str, title: str = "", model: Optional[str] = None):
        return self.controller.create_session(provider, workspace, title, model)

    def list_sessions(self, limit: int = 100, offset: int = 0):
        return self.controller.list_sessions(limit, offset)

    def open_session(self, sid: str, after_seq: int = 0):
        return self.controller.open_session(sid, after_seq)

    # ---------- env resolution ----------
    def _env_for(self, provider: str, model: Optional[str]) -> dict[str, str]:
        eff = self.config.effective(provider)
        if not eff:
            return {}
        p = eff["profile"]
        token = eff["token"] or ""
        if provider == "claude":
            return writers.claude_env(p.base_url, token, model or eff.get("model"))
        if provider == "codex":
            return writers.codex_env(p.base_url, token, p.wire_api or "responses")
        if provider == "dsh":
            return writers.dsh_env(p.base_url, token, model or eff.get("model"))
        return {}

    async def start_turn(self, sid: str, prompt: str, model: Optional[str] = None,
                         resume: bool = False, timeout_s: Optional[float] = None):
        sess = self.store.get_session(sid)
        if not sess:
            raise KeyError(sid)
        env = self._env_for(sess.provider, model or sess.provider_model)
        return await self.controller.start_turn(sid, prompt, model=model, env=env,
                                                resume=resume, timeout_s=timeout_s)

    async def cancel(self, sid: str) -> bool:
        return await self.controller.cancel(sid)

    # ---------- config materialization ----------
    def apply_binding(self, agent: str) -> dict[str, Any]:
        """Write the active binding into the CLI's config file (codex/claude/dsh)."""
        eff = self.config.effective(agent)
        if not eff:
            return {"ok": False, "error": f"no binding for {agent}"}
        p = eff["profile"]
        token = eff["token"] or ""
        model = eff.get("model")
        try:
            if agent == "codex":
                path = writers.write_codex_config(p.display_name, p.base_url, p.wire_api,
                                                  model or None)
            elif agent == "claude":
                path = writers.write_claude_settings(p.base_url, token, model)
            elif agent == "dsh":
                path = writers.write_dsh_env(p.base_url, token, model)
            else:
                return {"ok": False, "error": f"unknown agent {agent}"}
            return {"ok": True, "path": str(path)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def health_check(self, provider_id: str) -> dict[str, Any]:
        """Lightweight reachability check of a provider base_url (no token billed)."""
        p = self.config.get_profile(provider_id)
        if not p:
            return {"status": "unknown", "error": "no profile"}
        if not p.base_url:
            return {"status": "no_base_url"}
        import urllib.parse
        u = urllib.parse.urlparse(p.base_url)
        if u.scheme not in ("http", "https"):
            return {"status": "bad_url"}
        return {"status": "configured", "base_url": p.base_url}

    def close(self):
        self.store.close()
        self.config.close()
