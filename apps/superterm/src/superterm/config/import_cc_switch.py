"""Read-only importer for a CCSwitch database (host ~/.cc-switch/cc-switch.db).

Never mutates the source.  Introspects schema, maps providers for app_type codex /
claude into SuperTerm Profiles, masks tokens in any preview.  The token values are
read only to be written into SuperTerm's SecretStore (0600), never logged.
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Optional

from .profiles import ConfigStore, Profile, SecretStore

# map cc-switch app_type -> SuperTerm agent/app_type
APP_MAP = {"codex": "codex", "claude": "claude"}


def _safe_open(db_path: str) -> sqlite3.Connection:
    if not os.path.exists(db_path):
        raise FileNotFoundError(db_path)
    # read-only URI
    uri = f"file:{os.path.abspath(db_path)}?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True)


def introspect(db_path: str) -> dict[str, Any]:
    conn = _safe_open(db_path)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        counts = {}
        for t in tables:
            try:
                counts[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.Error:
                counts[t] = -1
        cols = {}
        if "providers" in tables:
            cols["providers"] = [c[1] for c in conn.execute("PRAGMA table_info(providers)")]
        return {"tables": tables, "counts": counts, "providers_columns": cols.get("providers", [])}
    finally:
        conn.close()


def _extract_codex(settings: dict) -> dict[str, Any]:
    """Parse cc-switch codex settings_config -> {base_url, wire_api, model, token, name}."""
    auth = settings.get("auth") or {}
    cfg = settings.get("config") or ""
    token = auth.get("OPENAI_API_KEY", "")
    base_url, wire_api, model = "", "responses", None
    # naive but robust TOML scan for our fields
    for line in str(cfg).splitlines():
        s = line.strip()
        if s.startswith("base_url"):
            base_url = s.split("=", 1)[1].strip().strip('"')
        elif s.startswith("wire_api"):
            wire_api = s.split("=", 1)[1].strip().strip('"')
        elif s.startswith("model =") or s.startswith("model="):
            model = s.split("=", 1)[1].strip().strip('"')
    return {"base_url": base_url, "wire_api": wire_api, "model": model, "token": token}


def _extract_claude(settings: dict) -> dict[str, Any]:
    env = settings.get("env") or {}
    return {
        "base_url": env.get("ANTHROPIC_BASE_URL", ""),
        "wire_api": "anthropic",
        "model": env.get("ANTHROPIC_MODEL"),
        "token": env.get("ANTHROPIC_AUTH_TOKEN", ""),
    }


def preview_import(db_path: str) -> dict[str, Any]:
    """Return a masked preview of what would be imported (no writes)."""
    conn = _safe_open(db_path)
    try:
        rows = conn.execute(
            "SELECT id,name,app_type,settings_config,is_current FROM providers").fetchall()
    finally:
        conn.close()
    out = []
    for pid, name, app_type, sc_raw, is_current in rows:
        at = APP_MAP.get(app_type)
        if not at:
            continue
        try:
            sc = json.loads(sc_raw) if sc_raw else {}
        except json.JSONDecodeError:
            sc = {}
        info = _extract_codex(sc) if at == "codex" else _extract_claude(sc)
        from ..redaction import mask_token
        out.append({
            "source_id": pid, "name": name, "app_type": at,
            "base_url": info["base_url"], "wire_api": info["wire_api"],
            "model": info["model"], "is_current": bool(is_current),
            "has_token": bool(info["token"]), "token_masked": mask_token(info["token"]),
        })
    return {"providers": out}


def do_import(db_path: str, store: ConfigStore, only_ids: Optional[list[str]] = None,
              set_current: bool = True) -> dict[str, Any]:
    """Import selected providers into SuperTerm store. Returns a report."""
    conn = _safe_open(db_path)
    try:
        rows = conn.execute(
            "SELECT id,name,app_type,settings_config,is_current FROM providers").fetchall()
    finally:
        conn.close()
    imported, skipped = [], []
    for pid, name, app_type, sc_raw, is_current in rows:
        at = APP_MAP.get(app_type)
        if not at:
            skipped.append({"name": name, "reason": f"unsupported app_type {app_type}"})
            continue
        if only_ids and pid not in only_ids:
            continue
        try:
            sc = json.loads(sc_raw) if sc_raw else {}
        except json.JSONDecodeError:
            skipped.append({"name": name, "reason": "bad settings_config json"})
            continue
        info = _extract_codex(sc) if at == "codex" else _extract_claude(sc)
        sid = f"ccsw-{at}-{pid}"
        p = Profile(id=sid, display_name=name, app_type=at, base_url=info["base_url"],
                    wire_api=info["wire_api"], enabled=True, source="cc-switch")
        store.upsert_profile(p, token=info["token"] if info["token"] else None)
        if info.get("model"):
            store.set_models(sid, [info["model"]])
        imported.append({"id": sid, "name": name, "app_type": at})
        if set_current and is_current:
            store.set_binding(at, sid, info.get("model"))
    return {"imported": imported, "skipped": skipped}
