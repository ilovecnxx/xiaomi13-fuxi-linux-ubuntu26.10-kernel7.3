#!/usr/bin/env python3
"""Export and reconstruct the pinned fuxi kernel without local worktree state."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "kernel"


def git(tree, *args, **kwargs):
    return subprocess.check_output(["git", "-C", str(tree), *args], **kwargs)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n")


def load_manifest():
    manifest = json.loads((CATALOG / "manifest.json").read_text())
    seen = set()
    for patch in manifest["patches"]:
        name = patch["file"]
        if Path(name).name != name or not name.endswith(".patch") or name in seen:
            raise ValueError(f"Invalid patch name: {name}")
        seen.add(name)
        if digest(CATALOG / "patches" / name) != patch["sha256"]:
            raise ValueError(f"Patch checksum mismatch: {name}")
    return manifest


def check_tree(tree, manifest):
    if git(tree, "status", "--porcelain", "--untracked-files=normal").strip():
        raise ValueError(f"Source tree is dirty: {tree}")
    actual = git(tree, "rev-parse", "HEAD^{tree}").decode().strip()
    if actual != manifest["tree"]:
        raise ValueError(f"Source tree mismatch: {actual} != {manifest['tree']}")
    print(f"Verified source tree {actual}: {tree}")


def export(args):
    tree = args.tree.resolve()
    if git(tree, "status", "--porcelain").strip():
        raise ValueError("Commit source changes before exporting")
    manifest = json.loads((CATALOG / "manifest.json").read_text())
    groups = json.loads((CATALOG / "series.json").read_text())
    changed = set(git(tree, "diff", "--name-only", args.base, "HEAD").decode().splitlines())
    covered = set()
    patches = []
    output = CATALOG / "patches"
    output.mkdir(exist_ok=True)
    generated = []
    for index, group in enumerate(groups, 1):
        paths = set(git(tree, "diff", "--name-only", args.base, "HEAD", "--",
                        *group["paths"]).decode().splitlines())
        if covered & paths:
            raise ValueError(f"Overlapping patch group: {group['name']}")
        covered.update(paths)
        if not paths:
            continue
        name = f"{index:04d}-{group['name']}.patch"
        content = git(tree, "diff", "--binary", "--full-index", args.base, "HEAD",
                      "--", *group["paths"])
        generated.append((name, content))
        patches.append({"file": name, "sha256": hashlib.sha256(content).hexdigest(),
                        "status": group["status"], "paths": sorted(paths)})
    if changed != covered:
        raise ValueError(f"Unassigned source changes: {sorted(changed - covered)}")
    # Only replace generated files once every changed path has been assigned.
    for old in output.glob("*.patch"):
        old.unlink()
    for name, content in generated:
        (output / name).write_bytes(content)
    manifest.update(base_tree=git(tree, "rev-parse", f"{args.base}^{{tree}}").decode().strip(),
                    tree=git(tree, "rev-parse", "HEAD^{tree}").decode().strip(),
                    source_commit=git(tree, "rev-parse", "HEAD").decode().strip(),
                    source_date_epoch=int(git(tree, "show", "-s", "--format=%ct", "HEAD")),
                    patches=patches)
    write_json(CATALOG / "manifest.json", manifest)
    history = []
    for commit in git(tree, "rev-list", "--reverse", f"{args.base}..HEAD").decode().splitlines():
        fields = git(tree, "show", "-s", "--format=%H%x00%an%x00%ae%x00%B", commit).decode().split("\0", 3)
        history.append(dict(zip(("commit", "author", "email", "message"), fields)))
    write_json(CATALOG / "history.json", history)
    print(f"Exported {len(patches)} patches, {len(covered)} paths, tree {manifest['tree']}")


def extract_archive(archive, destination, expected_root):
    def preserve_link_text(member, target):
        filtered = tarfile.data_filter(member, target)
        # Python 3.14 normalizes symlink text, changing the upstream Git tree.
        # Retain exact text only after checking its original resolved target.
        if filtered is not None and member.issym():
            resolved = (Path(target) / Path(member.name).parent / member.linkname).resolve()
            if not resolved.is_relative_to(Path(target).resolve()):
                raise ValueError(f"Unsafe archive link: {member.name}")
            filtered = filtered.replace(linkname=member.linkname)
        return filtered

    with tarfile.open(archive) as source:
        for member in source.getmembers():
            if Path(member.name).parts[0] != expected_root:
                raise ValueError(f"Unexpected archive path: {member.name}")
        source.extractall(destination, filter=preserve_link_text)
    return destination / expected_root


def prepare(args):
    manifest = load_manifest()
    target = args.tree.resolve()
    if target.exists():
        check_tree(target, manifest)
        return
    archive = args.archive
    if archive is None:
        cache = ROOT / "work" / "sources"
        cache.mkdir(parents=True, exist_ok=True)
        archive = cache / manifest["archive"]
        if not archive.exists():
            with tempfile.NamedTemporaryFile(dir=cache, delete=False) as stream:
                temporary_archive = Path(stream.name)
                try:
                    print(f"Downloading {manifest['url']}", flush=True)
                    with urllib.request.urlopen(manifest["url"], timeout=60) as response:
                        shutil.copyfileobj(response, stream)
                    stream.flush()
                    if digest(temporary_archive) != manifest["archive_sha256"]:
                        raise ValueError("Downloaded archive checksum mismatch")
                    temporary_archive.replace(archive)
                finally:
                    temporary_archive.unlink(missing_ok=True)
    if digest(archive) != manifest["archive_sha256"]:
        raise ValueError(f"Archive checksum mismatch: {archive}")
    target.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "GIT_AUTHOR_NAME": "fuxi source reconstruction",
           "GIT_AUTHOR_EMAIL": "builder@localhost", "GIT_COMMITTER_NAME": "fuxi source reconstruction",
           "GIT_COMMITTER_EMAIL": "builder@localhost",
           "GIT_AUTHOR_DATE": f"{manifest['source_date_epoch']} +0000",
           "GIT_COMMITTER_DATE": f"{manifest['source_date_epoch']} +0000"}
    with tempfile.TemporaryDirectory(prefix=".fuxi-source-", dir=target.parent) as temporary:
        source = extract_archive(archive, Path(temporary), manifest["archive_root"])
        git(source, "init", "-q")
        git(source, "add", "--force", ".")
        base_tree = git(source, "write-tree").decode().strip()
        if base_tree != manifest["base_tree"]:
            raise ValueError(f"Archive tree mismatch: {base_tree} != {manifest['base_tree']}")
        git(source, "commit", "-q", "-m", "Import pinned upstream release tarball", env=env)
        for patch in manifest["patches"]:
            git(source, "apply", "--index", str(CATALOG / "patches" / patch["file"]))
            git(source, "commit", "-q", "-m", f"Apply {patch['file']} (see kernel/history.json)", env=env)
        check_tree(source, manifest)
        source.rename(target)
    print(f"Prepared {target}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--archive", type=Path)
    prepare_parser.add_argument("--tree", type=Path, default=ROOT / "build/linux-standard")
    check_parser = commands.add_parser("check")
    check_parser.add_argument("--tree", type=Path, default=ROOT / "build/linux-standard")
    export_parser = commands.add_parser("export")
    export_parser.add_argument("--tree", type=Path, required=True)
    export_parser.add_argument("--base", required=True)
    args = parser.parse_args()
    try:
        if args.command == "export":
            export(args)
        elif args.command == "check":
            check_tree(args.tree, load_manifest())
        else:
            prepare(args)
    except (ValueError, OSError, subprocess.CalledProcessError, tarfile.TarError) as error:
        parser.exit(1, f"fuxi-kernel-source: {error}\n")


if __name__ == "__main__":
    main()
