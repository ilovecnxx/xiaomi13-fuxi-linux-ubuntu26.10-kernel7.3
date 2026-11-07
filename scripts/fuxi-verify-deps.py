#!/usr/bin/env python3
"""Reject stale or device-pulled binaries in standard package builds."""

import importlib.util
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sources", ROOT / "scripts/fuxi-sources.py")
sources = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sources)


def main():
    recipes = json.loads((ROOT / "packaging/sources/manifest.json").read_text())
    records = {
        "hexagonrpc": ROOT / "work/kbuild/hexagonrpc/source.json",
        "modemmanager": ROOT / "work/modem-bin/modemmanager-source.json",
        "81voltd": ROOT / "work/modem-bin/81voltd-source.json",
    }
    for name, path in records.items():
        record = json.loads(path.read_text())
        if record["recipe_sha256"] != sources.recipe_digest(recipes[name]):
            raise ValueError(f"stale binary source recipe: {name}")
        expected = json.loads((ROOT / "build/sources" / name / ".fuxi-source.json").read_text())
        if record != expected:
            raise ValueError(f"binary source tree mismatch: {name}")
    for directory in (ROOT / "work/kbuild/hexagonrpc", ROOT / "work/modem-bin"):
        if not (directory / "build-environment.lock").is_file():
            raise ValueError(f"missing fixed build environment record: {directory}")
        subprocess.run(["sha256sum", "--check", "--quiet", "SHA256SUMS"], cwd=directory, check=True)
    binaries = ("work/kbuild/hexagonrpc/hexagonrpcd-fuxi", "work/modem-bin/mm/src/ModemManager",
                "work/modem-bin/81voltd-sub2")
    for binary in binaries:
        header = (ROOT / binary).read_bytes()[:20]
        if header[:5] != b"\x7fELF\x02" or int.from_bytes(header[18:20], "little") != 183:
            raise ValueError(f"not an ARM64 ELF binary: {binary}")
    print("verified ARM64 dependencies and source provenance")


if __name__ == "__main__":
    main()
