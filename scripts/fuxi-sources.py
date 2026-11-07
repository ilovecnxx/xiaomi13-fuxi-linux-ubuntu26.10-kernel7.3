#!/usr/bin/env python3
"""Fetch content-pinned dependencies into disposable trees, never from a phone."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "packaging/sources/manifest.json"
spec = importlib.util.spec_from_file_location("kernel_source", ROOT / "scripts/fuxi-kernel-source.py")
kernel_source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kernel_source)


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fetch(url, digest, cache):
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / digest
    if not destination.exists():
        fd, temporary = tempfile.mkstemp(dir=cache)
        os.close(fd)
        try:
            with urllib.request.urlopen(url, timeout=60) as response, open(temporary, "wb") as output:
                shutil.copyfileobj(response, output)
            if sha256(Path(temporary)) != digest:
                raise ValueError(f"download hash mismatch: {url}")
            os.replace(temporary, destination)
        finally:
            Path(temporary).unlink(missing_ok=True)
    if sha256(destination) != digest:
        raise ValueError(f"cache hash mismatch: {destination}")
    return destination


def tree_digest(tree):
    digest = hashlib.sha256()
    for path in sorted(tree.rglob("*")):
        relative = path.relative_to(tree)
        if relative.parts[0] == ".git" or relative.as_posix() == ".fuxi-source.json":
            continue
        if path.is_symlink():
            value = b"link:" + os.fsencode(os.readlink(path))
        elif path.is_file():
            value = f"{path.stat().st_mode & 0o111}:{sha256(path)}".encode()
        else:
            continue
        digest.update(os.fsencode(relative.as_posix()) + b"\0" + value + b"\0")
    return digest.hexdigest()


def recipe_digest(recipe):
    return hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()


def check(name, tree, recipe):
    record = json.loads((tree / ".fuxi-source.json").read_text())
    if record["recipe_sha256"] != recipe_digest(recipe) or record["tree_sha256"] != tree_digest(tree):
        raise ValueError(f"modified or obsolete dependency tree: {name}: {tree}")
    for patch in recipe.get("patches", []):
        if sha256(ROOT / patch["path"]) != patch["sha256"]:
            raise ValueError(f"patch hash mismatch: {patch['path']}")


def prepare(name, tree, recipe, cache):
    if tree.exists():
        check(name, tree, recipe)
        return
    tree.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{name}-", dir=tree.parent) as temporary:
        staging = Path(temporary)
        if "archive" in recipe:
            source = fetch(recipe["archive"], recipe["sha256"], cache)
            source_tree = kernel_source.extract_archive(source, staging, recipe["root"])
        else:
            source_tree = staging / "files"
            source_tree.mkdir()
            for asset in recipe["files"]:
                shutil.copyfile(fetch(asset["url"], asset["sha256"], cache), source_tree / asset["name"])
        for patch in recipe.get("patches", []):
            path = ROOT / patch["path"]
            if sha256(path) != patch["sha256"]:
                raise ValueError(f"patch hash mismatch: {path}")
            subprocess.run(["patch", "--batch", "--forward", "--fuzz=0", "-p1", "-i", str(path)],
                           cwd=source_tree, check=True)
        record = {"name": name, "recipe_sha256": recipe_digest(recipe),
                  "tree_sha256": tree_digest(source_tree), "upstream": recipe.get("revision", recipe.get("archive"))}
        (source_tree / ".fuxi-source.json").write_text(json.dumps(record, indent=2) + "\n")
        os.rename(source_tree, tree)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "check"))
    parser.add_argument("names", nargs="*")
    parser.add_argument("--output", type=Path, default=ROOT / "build/sources")
    parser.add_argument("--cache", type=Path, default=ROOT / "build/source-cache")
    args = parser.parse_args()
    recipes = json.loads(MANIFEST.read_text())
    for name in args.names or recipes:
        if name not in recipes:
            parser.error(f"unknown source {name}")
        if args.command == "prepare":
            prepare(name, args.output / name, recipes[name], args.cache)
        check(name, args.output / name, recipes[name])
        print(f"verified: {name}")


if __name__ == "__main__":
    main()
