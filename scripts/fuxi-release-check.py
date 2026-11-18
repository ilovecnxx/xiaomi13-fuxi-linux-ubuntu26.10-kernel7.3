#!/usr/bin/env python3
"""Require explicit ownership decisions before publishing a public release."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def policy_requirements(policy, unsigned_prerelease):
    missing = [key for key in ("project_license_approved", "third_party_redistribution_reviewed", "public_release_approved")
               if policy.get(key) is not True]
    if unsigned_prerelease:
        if policy.get("unsigned_prerelease_approved") is not True:
            missing.append("unsigned_prerelease_approved")
    else:
        expected_key = policy.get("release_key_fingerprint") or ""
        if not re.fullmatch(r"(?:[A-Fa-f0-9]{40}|[A-Fa-f0-9]{64})", expected_key):
            missing.append("release_key_fingerprint")
        if not os.environ.get("FUXI_SIGNING_KEY"):
            missing.append("FUXI_SIGNING_KEY")
    return missing


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unsigned-prerelease", action="store_true",
                        help="allow an explicitly approved, unsigned GitHub prerelease")
    args = parser.parse_args()
    policy = json.loads((ROOT / "packaging/release-policy.json").read_text())
    missing = policy_requirements(policy, args.unsigned_prerelease)
    if not args.unsigned_prerelease:
        expected_key = policy.get("release_key_fingerprint") or ""
        selected_key = os.environ.get("FUXI_SIGNING_KEY", "")
        if selected_key:
            result = subprocess.run(["gpg", "--batch", "--with-colons", "--fingerprint", selected_key],
                                    capture_output=True, text=True, check=True)
            fingerprint = next((line.split(":")[9] for line in result.stdout.splitlines()
                                if line.startswith("fpr:")), "")
            if fingerprint.upper() != expected_key.upper():
                missing.append("selected signing key matches approved fingerprint")
    dirty = subprocess.check_output(["git", "-C", str(ROOT), "status", "--porcelain"], text=True)
    if dirty:
        missing.append("clean worktree")
    if missing:
        raise SystemExit("Public release blocked: " + ", ".join(missing))
    print("unsigned prerelease approved and clean-tree checks passed" if args.unsigned_prerelease
          else "signed public release ownership and clean-tree checks passed")


if __name__ == "__main__":
    main()
