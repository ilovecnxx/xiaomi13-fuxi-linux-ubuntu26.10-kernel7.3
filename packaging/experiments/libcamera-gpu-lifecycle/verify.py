#!/usr/bin/env python3
"""Verify the pinned libcamera archive and replay the ordered GPU patch stack."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    recipe = Path(__file__).resolve().parent
    manifest = json.loads((recipe / "manifest.json").read_text())
    patches = [recipe / item["file"] for item in manifest["patches"]]
    checks = [(args.archive, manifest["source"]["git_archive_sha256"])]
    checks.extend((path, item["sha256"]) for path, item in zip(patches, manifest["patches"]))
    for path, expected in checks:
        if digest(path) != expected:
            parser.error(f"SHA256 mismatch: {path}")
    with args.archive.open("rb") as stream:
        commit = subprocess.run(["git", "get-tar-commit-id"], stdin=stream,
                                capture_output=True, text=True, check=True).stdout.strip()
    if commit != manifest["source"]["commit"]:
        parser.error("archive commit does not match manifest")
    targets = ("src/ipa/simple/soft_simple.cpp", "src/libcamera/software_isp/software_isp.cpp",
               "include/libcamera/internal/egl.h", "src/libcamera/egl.cpp",
               "src/libcamera/software_isp/debayer_egl.cpp", "src/libcamera/pipeline/simple/simple.cpp")
    with tempfile.TemporaryDirectory(prefix="fuxi-egl-check-") as temporary:
        root = Path(temporary)
        with tarfile.open(args.archive) as archive:
            for name in targets:
                member = archive.getmember(name)
                if not member.isfile():
                    parser.error(f"not a regular source file: {name}")
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as stream:
                    target.write_bytes(stream.read())
        for patch in patches:
            subprocess.run(["git", "apply", "--check", str(patch)], cwd=root, check=True)
            subprocess.run(["git", "apply", str(patch)], cwd=root, check=True)
        hashes = {name: digest(root / name) for name in targets}
        for patch in reversed(patches):
            subprocess.run(["git", "apply", "--reverse", str(patch)], cwd=root, check=True)
        print(json.dumps({"source_commit": commit, "archive_verified": True,
                          "patch_stack_replay": "passed", "patched_files": hashes,
                          "compiled": False, "device_validated": False}, indent=2))


if __name__ == "__main__":
    main()
