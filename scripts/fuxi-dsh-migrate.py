#!/usr/bin/env python3
"""Inventory, back up, and verify DeepSeek Harness user data.

The default backup deliberately excludes credentials and browser state.  DSH
session data, attachments, storages, settings, selected runtime helpers, and
explicitly named workspaces are stored under separate archive roots.  Secret
files can be included only with ``--include-secrets`` and an explicit
``--acknowledge-secrets`` acknowledgement.

This tool works on a local directory or a mounted/staged copy of a device.
It never invokes SSH, systemd, npm, or boot management commands.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import os
import posixpath
from pathlib import Path, PurePosixPath
import stat
import sys
import tarfile
import tempfile
from typing import Iterable, Iterator


SCHEMA_VERSION = 1
DATA_DIRS = {"sessions", "attachments", "storages"}
OMITTED_DSH_DIRS = {"cache", "logs", "runtimes", "tmp"}
SAFE_RUNTIME_FILES = (
    ".config/systemd/user/dsh-web.service",
    ".config/systemd/user/dsh-web-idle.service",
    ".config/systemd/user/dsh-web-idle.timer",
    ".config/systemd/user/dsh-app-window.service",
    ".local/bin/dsh-web-launcher",
    ".local/bin/dsh-web-idle-check",
    ".local/bin/dsh-app-window-run",
    ".local/state/dsh-web/last-activity",
)
SECRET_RUNTIME_FILES = (
    ".config/dsh-web.env",
    ".config/dsh-app-chromium",
    ".local/state/dsh-web/url.txt",
)
SECRET_NAMES = {
    ".credentials",
    ".credentials.json",
    ".credentials.yaml",
    ".credentials.yml",
    ".env",
    ".env.local",
    "auth.json",
    "credentials.json",
    "token.json",
    "tokens.json",
    "url.txt",
    "dsh-web.env",
    "dsh-app-chromium",
}
SECRET_MARKERS = ("credential", "secret", "api-key", "api_key", "apikey", "access-token", "access_token", "cookie")


@dataclass(frozen=True)
class ArchiveEntry:
    archive_path: str
    source_root: str
    source_path: str
    category: str
    kind: str
    mode: int
    size: int
    sha256: str | None = None
    link_target: str | None = None


@dataclass(frozen=True)
class ExcludedEntry:
    source_root: str
    source_path: str
    category: str
    reason: str
    kind: str
    size: int


@dataclass
class Inventory:
    dsh_home: Path
    user_home: Path | None
    agents_home: Path | None
    workspaces: list[Path]
    include_secrets: bool
    entries: list[ArchiveEntry]
    excluded: list[ExcludedEntry]
    missing: list[dict[str, str]]
    omitted: list[dict[str, str]]

    def manifest(self) -> dict:
        categories: dict[str, dict[str, int]] = {}
        for entry in self.entries:
            summary = categories.setdefault(entry.category, {"files": 0, "directories": 0, "symlinks": 0, "bytes": 0})
            summary["bytes"] += entry.size
            summary[entry.kind + "s"] = summary.get(entry.kind + "s", 0) + 1
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "fuxi-dsh-migration-backup",
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "include_secrets": self.include_secrets,
            "source_roots": {
                "dsh_home": display_path(self.dsh_home),
                **({"user_home": display_path(self.user_home)} if self.user_home else {}),
                **({"agents_home": display_path(self.agents_home)} if self.agents_home else {}),
                "workspaces": [display_path(path) for path in self.workspaces],
            },
            "categories": categories,
            "entries": [asdict(entry) for entry in sorted(self.entries, key=lambda item: item.archive_path)],
            "excluded": [asdict(item) for item in sorted(self.excluded, key=lambda item: (item.source_root, item.source_path))],
            "missing": sorted(self.missing, key=lambda item: (item["source_root"], item["source_path"])),
            "omitted": sorted(self.omitted, key=lambda item: (item["source_root"], item["source_path"])),
        }


def display_path(path: Path | None) -> str:
    if path is None:
        return ""
    resolved = str(path)
    home = str(Path.home())
    if resolved == home:
        return "~"
    if resolved.startswith(home + os.sep):
        return "~" + resolved[len(home):]
    return resolved


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def kind_and_size(path: Path, st: os.stat_result | None = None) -> tuple[str, int]:
    info = st or path.lstat()
    if stat.S_ISREG(info.st_mode):
        return "file", info.st_size
    if stat.S_ISDIR(info.st_mode):
        return "directory", 0
    if stat.S_ISLNK(info.st_mode):
        return "symlink", 0
    raise ValueError(f"unsupported filesystem object: {path}")


def is_sensitive_path(relative: str) -> bool:
    parts = [part.lower() for part in PurePosixPath(relative).parts]
    for part in parts:
        if part in SECRET_NAMES or part.endswith(".env") or part.startswith(".env.") or any(marker in part for marker in SECRET_MARKERS):
            return True
    return False


def safe_archive_path(path: str) -> str:
    if not path or "\x00" in path or "\\" in path:
        raise ValueError(f"unsafe archive path: {path}")
    normalized = PurePosixPath(path)
    if normalized.is_absolute() or ".." in normalized.parts or str(normalized) in ("", ".") or normalized.as_posix() != path:
        raise ValueError(f"unsafe archive path: {path}")
    return normalized.as_posix()


def iter_children(path: Path) -> Iterator[Path]:
    if not path.is_dir():
        return
    yield from sorted(path.iterdir(), key=lambda item: item.name)


def add_excluded(inventory: Inventory, path: Path, source_root: str, source_path: str, category: str, reason: str) -> None:
    kind, size = kind_and_size(path)
    inventory.excluded.append(ExcludedEntry(source_root, source_path, category, reason, kind, size))


def collect_tree(
    inventory: Inventory,
    path: Path,
    *,
    archive_path: str,
    source_root: str,
    source_path: str,
    category: str,
    filter_sensitive: bool,
    portable_root: Path,
) -> None:
    """Collect a tree without following symlinks or reading excluded files."""
    if not path.exists() and not path.is_symlink():
        inventory.missing.append({"source_root": source_root, "source_path": source_path})
        return
    if filter_sensitive and is_sensitive_path(source_path):
        add_excluded(inventory, path, source_root, source_path, category, "credential-or-secret")
        return
    archive_path = safe_archive_path(archive_path)
    kind, size = kind_and_size(path)
    if kind == "symlink":
        target_path = posixpath.normpath(posixpath.join(PurePosixPath(source_path).parent.as_posix(), os.readlink(path)))
        if (not portable_symlink(path, portable_root)
                or (filter_sensitive and is_sensitive_path(target_path))):
            add_excluded(inventory, path, source_root, source_path, category, "unsafe-symlink-target")
            return
    link_target = os.readlink(path) if kind == "symlink" else None
    digest = sha256_file(path) if kind == "file" else None
    inventory.entries.append(ArchiveEntry(
        archive_path, source_root, source_path, category, kind,
        stat.S_IMODE(path.lstat().st_mode), size, digest, link_target,
    ))
    if kind == "directory":
        for child in iter_children(path):
            collect_tree(
                inventory,
                child,
                archive_path=f"{archive_path}/{child.name}",
                source_root=source_root,
                source_path=child.name if source_path in ("", ".") else f"{source_path}/{child.name}",
                category=category,
                filter_sensitive=filter_sensitive,
                portable_root=portable_root,
            )


def collect_optional(
    inventory: Inventory,
    path: Path,
    *,
    archive_path: str,
    source_root: str,
    source_path: str,
    category: str,
    filter_sensitive: bool,
) -> None:
    portable_root = path.resolve() if path.is_dir() and not path.is_symlink() else path.parent.resolve()
    collect_tree(
        inventory,
        path,
        archive_path=archive_path,
        source_root=source_root,
        source_path=source_path,
        category=category,
        filter_sensitive=filter_sensitive,
        portable_root=portable_root,
    )


def portable_symlink(path: Path, portable_root: Path) -> bool:
    """Only preserve relative links whose resolved target stays in this tree."""
    target = os.readlink(path)
    if os.path.isabs(target) or "\\" in target:
        return False
    try:
        resolved_target = (path.parent / target).resolve(strict=False)
        resolved_target.relative_to(portable_root)
    except (OSError, RuntimeError, ValueError):
        return False
    return True


def build_inventory(
    dsh_home: Path,
    *,
    user_home: Path | None = None,
    agents_home: Path | None = None,
    workspaces: Iterable[Path] = (),
    include_secrets: bool = False,
) -> Inventory:
    dsh_home = dsh_home.expanduser().resolve()
    if not dsh_home.is_dir():
        raise ValueError(f"DSH_HOME is not a directory: {dsh_home}")
    user_home = user_home.expanduser().resolve() if user_home else None
    agents_home = agents_home.expanduser().resolve() if agents_home else None
    workspace_paths = [path.expanduser().resolve() for path in workspaces]
    inventory = Inventory(dsh_home, user_home, agents_home, workspace_paths, include_secrets, [], [], [], [])

    for name, category in (("sessions", "sessions"), ("attachments", "attachments"), ("storages", "storages")):
        collect_optional(
            inventory, dsh_home / name, archive_path=f"dsh_home/{name}", source_root="dsh_home",
            source_path=name, category=category, filter_sensitive=not include_secrets,
        )

    for child in iter_children(dsh_home):
        if child.name in DATA_DIRS:
            continue
        if child.name in OMITTED_DSH_DIRS:
            inventory.omitted.append({"source_root": "dsh_home", "source_path": child.name, "reason": "rebuildable-runtime-data"})
            continue
        collect_tree(
            inventory, child, archive_path=f"dsh_home/settings/{child.name}", source_root="dsh_home",
            source_path=child.name, category="settings", filter_sensitive=not include_secrets,
            portable_root=child.resolve() if child.is_dir() and not child.is_symlink() else child.parent.resolve(),
        )

    if agents_home is not None:
        collect_optional(
            inventory, agents_home, archive_path="agents_home", source_root="agents_home", source_path=".",
            category="agent-settings", filter_sensitive=not include_secrets,
        )

    if user_home is not None:
        for relative in SAFE_RUNTIME_FILES:
            collect_optional(
                inventory, user_home / relative, archive_path=f"user_config/{relative}", source_root="user_home",
                source_path=relative, category="runtime-settings", filter_sensitive=False,
            )
        for relative in SECRET_RUNTIME_FILES:
            collect_optional(
                inventory, user_home / relative, archive_path=f"credentials/user_home/{relative}", source_root="user_home",
                source_path=relative, category="credentials", filter_sensitive=not include_secrets,
            )

    for index, workspace in enumerate(workspace_paths):
        if not workspace.is_dir():
            raise ValueError(f"workspace is not a directory: {workspace}")
        collect_tree(
            inventory, workspace, archive_path=f"workspaces/{index}", source_root=f"workspace_{index}",
            source_path=".", category="workspace", filter_sensitive=not include_secrets,
            portable_root=workspace.resolve(),
        )
    return inventory


def archive_digest(path: Path) -> str:
    return sha256_file(path)


def tar_info_for(tar: tarfile.TarFile, entry: ArchiveEntry, source: Path) -> tarfile.TarInfo:
    info = tar.gettarinfo(str(source), arcname=entry.archive_path)
    # Ownership is intentionally neutral: extraction must not recreate the
    # source user's numeric UID/GID on a different device.
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def write_backup(inventory: Inventory, output: Path) -> dict:
    if "\n" in output.name or "\r" in output.name:
        raise ValueError("backup filename must not contain a newline")
    manifest_path = Path(str(output) + ".manifest.json")
    checksum_path = Path(str(output) + ".sha256")
    if any(os.path.lexists(path) for path in (output, manifest_path, checksum_path)):
        if os.path.lexists(output):
            raise FileExistsError(f"refusing to overwrite existing backup: {output}")
        raise FileExistsError(f"backup sidecar already exists beside: {output}")
    manifest = inventory.manifest()
    output.parent.mkdir(parents=True, exist_ok=True)
    archive_fd, archive_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    os.close(archive_fd)
    temporary = Path(archive_name)
    temporary_paths = [temporary]
    published: list[tuple[Path, Path]] = []
    try:
        with tarfile.open(temporary, mode="w:gz") as tar:
            for entry in sorted(inventory.entries, key=lambda item: item.archive_path):
                source = resolve_entry_source(inventory, entry)
                info = tar_info_for(tar, entry, source)
                if entry.kind == "file":
                    with source.open("rb") as stream:
                        tar.addfile(info, stream)
                else:
                    tar.addfile(info)
            raw = json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
            info = tarfile.TarInfo("manifest.json")
            info.mode = 0o600
            info.uid = info.gid = 0
            info.size = len(raw)
            tar.addfile(info, fileobj=io.BytesIO(raw))
        verification = verify_backup(temporary)
        if verification["status"] != "ok":
            raise ValueError("temporary backup failed self-verification: " + "; ".join(verification["errors"]))
        digest = archive_digest(temporary)
        sidecar = dict(manifest)
        sidecar["archive"] = {"file": output.name, "sha256": digest}
        checksum_temporary = write_private_temp(
            output.parent, f".{checksum_path.name}.", f"{digest}  {output.name}\n".encode("ascii"),
        )
        temporary_paths.append(checksum_temporary)
        manifest_temporary = write_private_temp(
            output.parent, f".{manifest_path.name}.",
            (json.dumps(sidecar, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"),
        )
        temporary_paths.append(manifest_temporary)

        # Publish the sidecars first; the archive path appears only after the
        # complete set is staged and verified. Hard links fail if a name exists.
        for source, destination in (
            (checksum_temporary, checksum_path),
            (manifest_temporary, manifest_path),
            (temporary, output),
        ):
            os.link(source, destination, follow_symlinks=False)
            published.append((source, destination))
        directory_fd = os.open(output.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        for source, destination in reversed(published):
            try:
                destination_info = destination.lstat()
                source_info = source.stat()
                if destination_info.st_dev == source_info.st_dev and destination_info.st_ino == source_info.st_ino:
                    destination.unlink()
            except FileNotFoundError:
                pass
        raise
    finally:
        for path in temporary_paths:
            path.unlink(missing_ok=True)

    return {"status": "ok", "archive": str(output), "manifest": str(manifest_path), "checksum": str(checksum_path), "archive_sha256": digest, "entries": len(inventory.entries), "excluded": len(inventory.excluded), "include_secrets": inventory.include_secrets}


def write_private_temp(directory: Path, prefix: str, data: bytes) -> Path:
    file_descriptor, name = tempfile.mkstemp(prefix=prefix, dir=directory)
    temporary_path = Path(name)
    try:
        with os.fdopen(file_descriptor, "wb") as temporary:
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    return temporary_path


def resolve_entry_source(inventory: Inventory, entry: ArchiveEntry) -> Path:
    if entry.source_root == "dsh_home":
        return inventory.dsh_home / entry.source_path
    if entry.source_root == "agents_home":
        if inventory.agents_home is None:
            raise ValueError("agents_home entry has no source root")
        return inventory.agents_home / ("" if entry.source_path == "." else entry.source_path)
    if entry.source_root == "user_home":
        if inventory.user_home is None:
            raise ValueError("user_home entry has no source root")
        return inventory.user_home / entry.source_path
    if entry.source_root.startswith("workspace_"):
        index = int(entry.source_root.removeprefix("workspace_"))
        base = inventory.workspaces[index]
        return base / ("" if entry.source_path == "." else entry.source_path)
    raise ValueError(f"unknown source root: {entry.source_root}")


def read_member_bytes(tar: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    stream = tar.extractfile(member)
    if stream is None:
        raise ValueError(f"member is not readable: {member.name}")
    return stream.read()


def verify_backup(archive: Path) -> dict:
    errors: list[str] = []
    digest = archive_digest(archive)
    checksum_path = Path(str(archive) + ".sha256")
    manifest: dict = {}
    entries: list = []
    try:
        if os.path.lexists(checksum_path):
            if checksum_path.is_symlink() or not checksum_path.is_file():
                errors.append("sidecar SHA256 is not a regular file")
            else:
                checksum_lines = checksum_path.read_text(encoding="ascii").splitlines()
                checksum_line = checksum_lines[0] if len(checksum_lines) == 1 else ""
                if (len(checksum_line) < 66 or checksum_line[64:66] != "  "
                        or checksum_line[:64] != digest or checksum_line[66:] != archive.name):
                    errors.append("sidecar SHA256 does not match archive")
        with tarfile.open(archive, mode="r:gz") as tar:
            members = tar.getmembers()
            by_name: dict[str, tarfile.TarInfo] = {}
            for member in members:
                try:
                    safe_archive_path(member.name)
                except ValueError as exc:
                    errors.append(str(exc))
                    continue
                if member.name in by_name:
                    errors.append(f"duplicate archive member: {member.name}")
                by_name[member.name] = member
                if not (member.isfile() or member.isdir() or member.issym()):
                    errors.append(f"unsupported archive member type: {member.name}")
            manifest_member = by_name.get("manifest.json")
            if manifest_member is None or not manifest_member.isfile():
                errors.append("manifest.json is missing")
                return {"status": "invalid", "archive": str(archive), "archive_sha256": digest, "errors": errors}
            manifest = json.loads(read_member_bytes(tar, manifest_member))
            if not isinstance(manifest, dict):
                errors.append("manifest is not an object")
                manifest = {}
            if manifest.get("kind") != "fuxi-dsh-migration-backup" or manifest.get("schema_version") != SCHEMA_VERSION:
                errors.append("unsupported manifest kind or schema version")
            entries = manifest.get("entries", [])
            if not isinstance(entries, list):
                errors.append("manifest entries is not a list")
                entries = []
            expected_names = {"manifest.json"}
            seen_names: set[str] = set()
            for raw_entry in entries:
                if not isinstance(raw_entry, dict):
                    errors.append("manifest entry is not an object")
                    continue
                name = raw_entry.get("archive_path")
                if not isinstance(name, str):
                    errors.append("manifest entry has no string archive_path")
                    continue
                try:
                    safe_archive_path(name)
                except ValueError as exc:
                    errors.append(str(exc))
                    continue
                if name == "manifest.json":
                    errors.append("manifest entry must not replace manifest.json")
                    continue
                if name in seen_names:
                    errors.append(f"duplicate manifest entry: {name}")
                seen_names.add(name)
                expected_names.add(name)
                member = by_name.get(name)
                if member is None:
                    errors.append(f"manifest entry is missing from archive: {name}")
                    continue
                kind = raw_entry.get("kind")
                if kind == "file":
                    if not member.isfile():
                        errors.append(f"member type mismatch: {name}")
                        continue
                    if member.size != raw_entry.get("size"):
                        errors.append(f"file size mismatch: {name}")
                    actual = hashlib.sha256(read_member_bytes(tar, member)).hexdigest()
                    if actual != raw_entry.get("sha256"):
                        errors.append(f"file digest mismatch: {name}")
                elif kind == "directory":
                    if not member.isdir():
                        errors.append(f"member type mismatch: {name}")
                elif kind == "symlink":
                    if not member.issym() or not portable_archive_link(name, member.linkname) or member.linkname != raw_entry.get("link_target"):
                        errors.append(f"symlink mismatch: {name}")
                else:
                    errors.append(f"unsupported manifest entry type: {name}")
            extras = sorted(set(by_name) - expected_names)
            errors.extend(f"unlisted archive member: {name}" for name in extras)
        manifest_path = Path(str(archive) + ".manifest.json")
        if os.path.lexists(manifest_path):
            if manifest_path.is_symlink() or not manifest_path.is_file():
                errors.append("sidecar manifest is not a regular file")
            else:
                sidecar = json.loads(manifest_path.read_text(encoding="utf-8"))
                if not isinstance(sidecar, dict):
                    errors.append("sidecar manifest is not an object")
                else:
                    sidecar_archive = sidecar.pop("archive", None)
                    if sidecar_archive != {"file": archive.name, "sha256": digest} or sidecar != manifest:
                        errors.append("sidecar manifest does not match archive manifest")
    except (OSError, tarfile.TarError, json.JSONDecodeError, UnicodeError, ValueError) as exc:
        errors.append(f"cannot read backup: {exc}")
        return {"status": "invalid", "archive": str(archive), "archive_sha256": digest, "errors": errors}
    excluded = manifest.get("excluded", [])
    if not isinstance(excluded, list):
        errors.append("manifest excluded is not a list")
        excluded = []
    return {"status": "ok" if not errors else "invalid", "archive": str(archive), "archive_sha256": digest, "entries": len(entries), "excluded": len(excluded), "include_secrets": bool(manifest.get("include_secrets")), "errors": errors}


def portable_archive_link(name: str, target: str) -> bool:
    if not target or "\\" in target or PurePosixPath(target).is_absolute():
        return False
    candidate = posixpath.normpath(posixpath.join(PurePosixPath(name).parent.as_posix(), target))
    return candidate not in ("..", ".") and not candidate.startswith("../")


def audit_report(inventory: Inventory) -> dict:
    manifest = inventory.manifest()
    return {
        "status": "ok",
        "mode": "audit",
        "dsh_home": manifest["source_roots"]["dsh_home"],
        "include_secrets": inventory.include_secrets,
        "categories": manifest["categories"],
        "entries": len(inventory.entries),
        "excluded": manifest["excluded"],
        "missing": manifest["missing"],
        "omitted": manifest["omitted"],
        "source_roots": manifest["source_roots"],
    }


def path_arg(value: str) -> Path:
    return Path(value).expanduser()


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dsh-home", required=True, type=path_arg, help="source DSH_HOME directory")
    parser.add_argument("--user-home", type=path_arg, help="source user home containing .config and .local")
    parser.add_argument("--agents-home", type=path_arg, help="source shared agent configuration root")
    parser.add_argument("--workspace", action="append", default=[], type=path_arg, help="workspace to include; repeatable")
    parser.add_argument("--include-secrets", action="store_true", help="include credential files and browser state")


def make_inventory(args: argparse.Namespace) -> Inventory:
    dsh_home = args.dsh_home
    user_home = args.user_home
    if user_home is None and dsh_home.name == ".dsh":
        user_home = dsh_home.parent
    agents_home = args.agents_home
    if agents_home is None and user_home is not None:
        agents_home = user_home / ".agents"
    return build_inventory(
        dsh_home,
        user_home=user_home,
        agents_home=agents_home,
        workspaces=args.workspace,
        include_secrets=args.include_secrets,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit = subparsers.add_parser("audit", help="print a non-destructive data inventory")
    add_common_arguments(audit)

    backup = subparsers.add_parser("backup", help="create a verified tar.gz backup")
    add_common_arguments(backup)
    backup.add_argument("--output", required=True, type=path_arg, help="new .tar.gz output path")
    backup.add_argument("--acknowledge-secrets", action="store_true", help="confirm that included secrets are intentional")

    verify = subparsers.add_parser("verify", help="verify archive members and content digests")
    verify.add_argument("--archive", required=True, type=path_arg)

    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            result = verify_backup(args.archive)
        else:
            if args.command == "backup" and args.include_secrets and not args.acknowledge_secrets:
                parser.error("--include-secrets requires --acknowledge-secrets")
            inventory = make_inventory(args)
            if args.command == "audit":
                result = audit_report(inventory)
            else:
                result = write_backup(inventory, args.output)
    except (OSError, ValueError, tarfile.TarError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
