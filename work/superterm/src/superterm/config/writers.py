"""Materialize SuperTerm provider config into the CLIs' own config formats.

Two strategies:
1. Per-process environment (preferred for isolation): the driver builds a child env
   with the token/base_url so the token never touches disk or argv.
2. Atomic write-through files (codex `~/.codex/config.toml`, claude `settings.json`)
   preserving unrelated keys, with a backup per revision.

Both are provided; the controller uses env where the CLI supports it (claude fully;
codex supports OPENAI_API_KEY env + model via -m), and write-through for codex's
base_url/wire_api which must live in config.toml.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional

from .. import paths


def _atomic_write(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".stw-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _backup(path: Path) -> Optional[Path]:
    if not path.exists():
        return None
    paths.backups_dir().mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time()*1e6)%1000000:06d}"
    dst = paths.backups_dir() / (path.name + ".bak." + ts)
    shutil.copy2(path, dst)
    return dst


# ---------- environment builders (token off disk/argv) ----------

def codex_env(base_url: str, token: str, wire_api: str = "responses") -> dict[str, str]:
    env = {"OPENAI_API_KEY": token, "OPENAI_BASE_URL": base_url}
    return env


def claude_env(base_url: str, token: str, model: Optional[str] = None) -> dict[str, str]:
    env = {"ANTHROPIC_BASE_URL": base_url, "ANTHROPIC_AUTH_TOKEN": token}
    if model:
        env["ANTHROPIC_MODEL"] = model
    return env


def dsh_env(base_url: str, token: str, model: Optional[str] = None) -> dict[str, str]:
    env = {}
    if base_url:
        env["DEEPSEEK_BASE_URL"] = base_url
    if token:
        env["DEEPSEEK_API_KEY"] = token
    if model:
        env["DSH_MODEL"] = model
    return env


# ---------- codex config.toml write-through ----------

def write_codex_config(profile_name: str, base_url: str, wire_api: str, model: Optional[str],
                       requires_openai_auth: bool = True, config_path: Optional[Path] = None) -> Path:
    """Merge a custom model_provider block into ~/.codex/config.toml, preserving other keys.

    We only manage: top-level `model`, `model_provider`, and `[model_providers.custom]`.
    Unrelated tables/settings are preserved verbatim.
    """
    path = Path(config_path or (Path.home() / ".codex" / "config.toml"))
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    _backup(path)

    lines = existing.splitlines()
    # Strip our managed keys/blocks: top-level `model`/`model_provider` ONLY (before the
    # first [table] header) and the whole [model_providers.custom*] block. `model = ...`
    # inside unrelated tables (e.g. [other]) must be preserved verbatim.
    out: list[str] = []
    skip_table = False
    in_table = False  # becomes True after we pass the first table header
    for ln in lines:
        stripped = ln.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_table = True
            skip_table = stripped in ("[model_providers.custom]", "[model_providers]") or \
                         stripped.startswith("[model_providers.custom.")
            if skip_table:
                continue
            out.append(ln)
            continue
        if skip_table:
            continue
        if not in_table:
            # only top-level scalars are ours
            key = stripped.split("=")[0].strip()
            if key in ("model", "model_provider"):
                continue
        out.append(ln)

    header = []
    if model:
        header.append(f'model = {json.dumps(model)}')
    header.append('model_provider = "custom"')
    block = [
        "",
        "[model_providers.custom]",
        f'name = {json.dumps(profile_name)}',
        f'base_url = {json.dumps(base_url)}',
        f'wire_api = {json.dumps(wire_api or "responses")}',
        f'requires_openai_auth = {"true" if requires_openai_auth else "false"}',
    ]
    new_content = "\n".join(header + out + block).strip() + "\n"
    _atomic_write(path, new_content, 0o600)
    return path


# ---------- claude settings.json write-through ----------

def write_claude_settings(base_url: str, token: str, model: Optional[str] = None,
                          settings_path: Optional[Path] = None) -> Path:
    """Merge env vars into ~/.claude/settings.json preserving all other keys."""
    path = Path(settings_path or (Path.home() / ".claude" / "settings.json"))
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        _backup(path)
    env = data.get("env") if isinstance(data.get("env"), dict) else {}
    env["ANTHROPIC_BASE_URL"] = base_url
    env["ANTHROPIC_AUTH_TOKEN"] = token
    if model:
        env["ANTHROPIC_MODEL"] = model
    data["env"] = env
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2), 0o600)
    return path


# ---------- dsh env file ----------

def write_dsh_env(base_url: str, token: str, model: Optional[str] = None,
                  env_path: Optional[Path] = None) -> Path:
    path = Path(env_path or (paths.config_dir() / "dsh.env"))
    lines = []
    if base_url:
        lines.append(f"DEEPSEEK_BASE_URL={base_url}")
    if token:
        lines.append(f"DEEPSEEK_API_KEY={token}")
    if model:
        lines.append(f"DSH_MODEL={model}")
    _atomic_write(path, "\n".join(lines) + "\n", 0o600)
    return path
