#!/usr/bin/env python3
"""Archive exact upstream inputs and tracked build recipes for redistribution."""

import argparse
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sources", ROOT / "scripts/fuxi-sources.py")
sources = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sources)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if subprocess.check_output(["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no"]):
        raise SystemExit("Commit reviewed build recipes before making a corresponding-source bundle")
    commit = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    epoch = int(subprocess.check_output(["git", "-C", str(ROOT), "show", "-s", "--format=%ct", "HEAD"]))
    manifest = json.loads((ROOT / "packaging/sources/manifest.json").read_text())
    inputs = []
    cache = ROOT / "build/source-cache"
    for name, recipe in manifest.items():
        if "archive" in recipe:
            path = cache / recipe["sha256"]
            if not path.exists() or sources.sha256(path) != recipe["sha256"]:
                raise SystemExit(f"missing or corrupt source archive: {name}; prepare sources first")
            inputs.append((path, "upstream/" + name + ".tar" + (".xz" if recipe["archive"].endswith(".xz") else ".gz")))
    kernel = json.loads((ROOT / "kernel/manifest.json").read_text())
    archive = sources.fetch(kernel["url"], kernel["archive_sha256"], cache)
    inputs.append((archive, "upstream/" + kernel["archive"]))
    tools = ROOT / "build/sources/boot-tools"
    sources.check("boot-tools", tools, manifest["boot-tools"])
    inputs.append((tools / "mkbootimg.py", "upstream/mkbootimg.py"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("xb") as stream, tarfile.open(fileobj=stream, mode="w") as bundle:
        project = subprocess.check_output(["git", "-C", str(ROOT), "archive", "--format=tar", "HEAD"])
        info = tarfile.TarInfo("project-recipes.tar")
        info.size = len(project)
        info.mtime = epoch
        bundle.addfile(info, io.BytesIO(project))
        for path, name in inputs:
            info = bundle.gettarinfo(str(path), arcname=name)
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            info.mtime = epoch
            with path.open("rb") as content:
                bundle.addfile(info, content)
        record = json.dumps({"project_commit": commit, "source_date_epoch": epoch,
                             "inputs": [{"path": name, "sha256": sources.sha256(path)} for path, name in inputs],
                             "excluded": ["private firmware", "vendor compatibility DTB: rights review pending"]}, indent=2).encode()
        info = tarfile.TarInfo("provenance.json")
        info.size = len(record)
        info.mtime = epoch
        bundle.addfile(info, io.BytesIO(record))
    digest = sources.sha256(args.output)
    args.output.with_suffix(args.output.suffix + ".sha256").write_text(f"{digest}  {args.output.name}\n")
    print(f"corresponding-source inputs: {args.output}: {digest}")


if __name__ == "__main__":
    main()
