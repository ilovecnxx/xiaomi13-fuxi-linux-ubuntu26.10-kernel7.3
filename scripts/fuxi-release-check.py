#!/usr/bin/env python3
"""Require explicit ownership decisions before publishing a public release."""

import json
import os
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def main():
    policy = json.loads((ROOT / "packaging/release-policy.json").read_text())
    missing = [key for key in ("project_license_approved", "third_party_redistribution_reviewed", "public_release_approved")
               if policy.get(key) is not True]
    expected_key = policy.get("release_key_fingerprint") or ""
    if not re.fullmatch(r"(?:[A-Fa-f0-9]{40}|[A-Fa-f0-9]{64})", expected_key):
        missing.append("release_key_fingerprint")
    selected_key = os.environ.get("FUXI_SIGNING_KEY", "")
    if selected_key:
        result = subprocess.run(["gpg", "--batch", "--with-colons", "--fingerprint", selected_key],
                                capture_output=True, text=True, check=True)
        fingerprint = next((line.split(":")[9] for line in result.stdout.splitlines()
                            if line.startswith("fpr:")), "")
        if fingerprint.upper() != expected_key.upper():
            missing.append("selected signing key matches approved fingerprint")
    else:
        missing.append("FUXI_SIGNING_KEY")
    dirty = subprocess.check_output(["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no"], text=True)
    if dirty:
        missing.append("clean tracked worktree")
    if missing:
        raise SystemExit("Public release blocked: " + ", ".join(missing))
    print("public release ownership and clean-tree checks passed")


if __name__ == "__main__":
    main()
