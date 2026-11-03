"""Provider configuration store (CCSwitch-style) in its own SQLite database.

Stores provider profiles (base_url, wire_api, token), model catalogs, and per-agent
bindings.  Secrets are stored in a separate 0600 file, never in the main DB or UI
state.  Materialization into CLI config files is handled by config.writers.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .. import paths
from ..redaction import mask_token

CONFIG_SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_profiles (
  id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  app_type TEXT NOT NULL,        -- codex | claude | dsh
  base_url TEXT,
  wire_api TEXT,
  enabled INTEGER DEFAULT 1,
  source TEXT DEFAULT 'local',
  extra_json TEXT,
  created_at REAL,
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS provider_models (
  provider_id TEXT NOT NULL,
  model_id TEXT NOT NULL,
  display_name TEXT,
  enabled INTEGER DEFAULT 1,
  PRIMARY KEY (provider_id, model_id)
);
CREATE TABLE IF NOT EXISTS agent_bindings (
  agent TEXT PRIMARY KEY,        -- codex | claude | dsh
  provider_id TEXT,
  model_id TEXT,
  extra_json TEXT,
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS config_revisions (
  revision INTEGER PRIMARY KEY AUTOINCREMENT,
  summary TEXT,
  actor TEXT,
  changed_at REAL
);
"""


@dataclass
class Profile:
    id: str
    display_name: str
    app_type: str
    base_url: str = ""
    wire_api: str = ""
    enabled: bool = True
    source: str = "local"
    extra: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0

    def summary(self, secrets: "SecretStore") -> dict[str, Any]:
        """Redacted view for the UI (never includes the token)."""
        tok = secrets.get(self.id) or ""
        return {
            "id": self.id, "display_name": self.display_name, "app_type": self.app_type,
            "base_url": self.base_url, "wire_api": self.wire_api, "enabled": self.enabled,
            "source": self.source, "has_token": bool(tok), "token_masked": mask_token(tok),
            "extra": {k: v for k, v in self.extra.items() if k not in ("token", "api_key")},
        }


class SecretStore:
    """Per-provider token stored in a 0600 JSON file, dir 0700."""

    def __init__(self, path: Optional[Path] = None) -> None:
        paths.ensure_dirs()
        self.path = Path(path or (paths.secrets_dir() / "tokens.json"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        self._data: dict[str, str] = {}
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def _flush(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self._data, f)
        os.replace(tmp, self.path)
        paths.restrict_file(self.path, 0o600)

    def set(self, provider_id: str, token: str) -> None:
        self._data[provider_id] = token
        self._flush()

    def get(self, provider_id: str) -> Optional[str]:
        return self._data.get(provider_id)

    def delete(self, provider_id: str) -> None:
        self._data.pop(provider_id, None)
        self._flush()


class ConfigStore:
    def __init__(self, db_path: Optional[Path] = None, secrets: Optional[SecretStore] = None) -> None:
        paths.ensure_dirs()
        self.db_path = str(db_path or (paths.config_dir() / "config.db"))
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.executescript(CONFIG_SCHEMA)
        self._conn.commit()
        paths.restrict_file(self.db_path, 0o600)
        self.secrets = secrets or SecretStore()

    # ---------- profiles ----------
    def upsert_profile(self, p: Profile, token: Optional[str] = None) -> str:
        now = time.time()
        if not p.created_at:
            p.created_at = now
        p.updated_at = now
        self._conn.execute(
            "INSERT INTO provider_profiles(id,display_name,app_type,base_url,wire_api,enabled,source,"
            "extra_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET display_name=excluded.display_name,base_url=excluded.base_url,"
            "wire_api=excluded.wire_api,enabled=excluded.enabled,extra_json=excluded.extra_json,"
            "updated_at=excluded.updated_at",
            (p.id, p.display_name, p.app_type, p.base_url, p.wire_api, int(p.enabled), p.source,
             json.dumps(p.extra, ensure_ascii=False), p.created_at, p.updated_at))
        if token is not None:
            self.secrets.set(p.id, token)
        self._conn.commit()
        self._revise(f"upsert profile {p.display_name}")
        return p.id

    def get_profile(self, pid: str) -> Optional[Profile]:
        r = self._conn.execute(
            "SELECT id,display_name,app_type,base_url,wire_api,enabled,source,extra_json,"
            "created_at,updated_at FROM provider_profiles WHERE id=?", (pid,)).fetchone()
        if not r:
            return None
        return Profile(r[0], r[1], r[2], r[3] or "", r[4] or "", bool(r[5]), r[6],
                       json.loads(r[7] or "{}"), r[8], r[9])

    def list_profiles(self, app_type: Optional[str] = None) -> list[Profile]:
        q = ("SELECT id,display_name,app_type,base_url,wire_api,enabled,source,extra_json,"
             "created_at,updated_at FROM provider_profiles")
        args: tuple = ()
        if app_type:
            q += " WHERE app_type=?"
            args = (app_type,)
        q += " ORDER BY updated_at DESC"
        return [Profile(r[0], r[1], r[2], r[3] or "", r[4] or "", bool(r[5]), r[6],
                        json.loads(r[7] or "{}"), r[8], r[9])
                for r in self._conn.execute(q, args).fetchall()]

    def delete_profile(self, pid: str) -> None:
        self._conn.execute("DELETE FROM provider_profiles WHERE id=?", (pid,))
        self._conn.execute("DELETE FROM provider_models WHERE provider_id=?", (pid,))
        self._conn.commit()
        self.secrets.delete(pid)
        self._revise(f"delete profile {pid}")

    # ---------- models ----------
    def set_models(self, provider_id: str, models: list[str]) -> None:
        self._conn.execute("DELETE FROM provider_models WHERE provider_id=?", (provider_id,))
        for m in models:
            self._conn.execute("INSERT OR IGNORE INTO provider_models(provider_id,model_id) VALUES(?,?)",
                               (provider_id, m))
        self._conn.commit()

    def get_models(self, provider_id: str) -> list[str]:
        return [r[0] for r in self._conn.execute(
            "SELECT model_id FROM provider_models WHERE provider_id=? AND enabled=1", (provider_id,)).fetchall()]

    # ---------- bindings ----------
    def set_binding(self, agent: str, provider_id: str, model_id: Optional[str] = None,
                    extra: Optional[dict] = None) -> None:
        self._conn.execute(
            "INSERT INTO agent_bindings(agent,provider_id,model_id,extra_json,updated_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(agent) DO UPDATE SET provider_id=excluded.provider_id,model_id=excluded.model_id,"
            "extra_json=excluded.extra_json,updated_at=excluded.updated_at",
            (agent, provider_id, model_id, json.dumps(extra or {}), time.time()))
        self._conn.commit()
        self._revise(f"bind {agent} -> {provider_id}")

    def get_binding(self, agent: str) -> Optional[dict]:
        r = self._conn.execute("SELECT agent,provider_id,model_id,extra_json FROM agent_bindings WHERE agent=?",
                               (agent,)).fetchone()
        if not r:
            return None
        return {"agent": r[0], "provider_id": r[1], "model_id": r[2], "extra": json.loads(r[3] or "{}")}

    def effective(self, agent: str) -> Optional[dict]:
        """Return binding + resolved provider + token for an agent (token used only to build env)."""
        b = self.get_binding(agent)
        if not b:
            return None
        p = self.get_profile(b["provider_id"])
        if not p:
            return None
        return {"binding": b, "profile": p, "token": self.secrets.get(p.id),
                "model": b.get("model_id")}

    def _revise(self, summary: str, actor: str = "local") -> None:
        self._conn.execute("INSERT INTO config_revisions(summary,actor,changed_at) VALUES(?,?,?)",
                           (summary, actor, time.time()))
        self._conn.commit()

    def revisions(self, limit: int = 50) -> list[dict]:
        return [{"revision": r[0], "summary": r[1], "actor": r[2], "changed_at": r[3]}
                for r in self._conn.execute(
                    "SELECT revision,summary,actor,changed_at FROM config_revisions ORDER BY revision DESC LIMIT ?",
                    (limit,)).fetchall()]

    def close(self) -> None:
        self._conn.close()
