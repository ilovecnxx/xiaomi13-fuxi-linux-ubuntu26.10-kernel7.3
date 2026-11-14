#!/usr/bin/env python3
"""Verify the pinned archive and replay the candidate in a temporary directory."""

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
    patch = recipe / manifest["patch"]["file"]
    for path, expected in ((args.archive, manifest["source"]["git_archive_sha256"]),
                           (patch, manifest["patch"]["sha256"])):
        if digest(path) != expected:
            parser.error(f"SHA256 mismatch: {path}")
    with args.archive.open("rb") as stream:
        commit = subprocess.run(["git", "get-tar-commit-id"], stdin=stream,
                                capture_output=True, text=True, check=True).stdout.strip()
    if commit != manifest["source"]["commit"]:
        parser.error("archive commit does not match manifest")
    targets = ("src/ipa/simple/soft_simple.cpp", "src/libcamera/software_isp/software_isp.cpp")
    with tempfile.TemporaryDirectory(prefix="fuxi-libcamera-check-") as temporary:
        root = Path(temporary)
        # Extract only the two fixed regular files; never unpack archive links.
        with tarfile.open(args.archive) as archive:
            for name in targets:
                member = archive.getmember(name)
                if not member.isfile():
                    parser.error(f"not a regular source file: {name}")
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as stream:
                    target.write_bytes(stream.read())
        for options in (["--check"], [], ["--reverse", "--check"]):
            subprocess.run(["git", "apply", *options, str(patch)], cwd=root, check=True)
        print(json.dumps({"source_commit": commit, "archive_verified": True,
                          "patch_verified": True, "patch_replay": "passed",
                          "patched_files": {name: digest(root / name) for name in targets},
                          "compiled": False, "device_validated": False}, indent=2))


if __name__ == "__main__":
    main()
