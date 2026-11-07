"""XDG paths, directory layout, and permission helpers for SuperTerm.

Everything lives under the running user's XDG dirs; nothing requires root.
State (db, logs, secrets) -> $XDG_STATE_HOME/superterm (default ~/.local/state/superterm)
Config                       -> $XDG_CONFIG_HOME/superterm (default ~/.config/superterm)
"""
from __future__ import annotations

import os
import stat
from pathlib import Path


def _xdg(env: str, default: str) -> Path:
    v = os.environ.get(env)
    if v:
        return Path(v)
    return Path.home() / default


def state_dir() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state") / "superterm"


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "superterm"


def db_path() -> Path:
    return state_dir() / "superterm.db"


def logs_dir() -> Path:
    return state_dir() / "logs"


def secrets_dir() -> Path:
    return state_dir() / "secrets"


def backups_dir() -> Path:
    return state_dir() / "backups"


def ensure_dirs() -> None:
    for d in (state_dir(), config_dir(), logs_dir()):
        d.mkdir(parents=True, exist_ok=True)
    for d in (secrets_dir(), backups_dir()):
        d.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass


def restrict_file(path: str | Path, mode: int = 0o600) -> None:
    """Best-effort chmod on a file; ignore on platforms without support."""
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def is_within(root: Path, candidate: Path) -> bool:
    """True if candidate's realpath is inside root's realpath (symlink-safe)."""
    try:
        root_r = root.resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    try:
        cand_r = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        # Non-existent target: resolve parent then append name.
        try:
            cand_r = candidate.parent.resolve(strict=True) / candidate.name
        except (OSError, RuntimeError):
            return False
    try:
        cand_r.relative_to(root_r)
        return True
    except ValueError:
        return False


def safe_file_mode(path: str | Path) -> int:
    try:
        return stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        return -1
