#!/usr/bin/env python3
"""Read-only package/service drift report; never invoke boot management tools."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


UNITS = ("fuxi-firmware-devices.service", "fuxi-wifi-init.service", "fuxi-bt-init.service")
INSTALLER = "usr/sbin/fuxi-kernel-install"


def run(argv):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=5)
        return {"returncode": result.returncode, "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip()}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"returncode": None, "stdout": "", "stderr": str(exc)}


def read(root, name):
    try:
        return {"value": (root / name).read_text().strip(), "error": None}
    except OSError as exc:
        return {"value": None, "error": str(exc)}


def collect(expected_installer, root=Path("/"), runner=run):
    report = {"schema_version": 1, "scope": "package-service-drift-only",
              "kernel": read(root, "proc/sys/kernel/osrelease"),
              "boot": {name: read(root, "boot/fuxi/" + name)
                       for name in ("default", "fallback")}}
    reasons = []
    unknown = []
    try:
        actual = hashlib.sha256((root / INSTALLER).read_bytes()).hexdigest()
        installer = {"sha256": actual, "expected_sha256": expected_installer,
                     "matches_repository": actual == expected_installer}
        if actual != expected_installer:
            reasons.append("kernel installer differs from repository; review before upgrading")
    except OSError as exc:
        installer = {"sha256": None, "expected_sha256": expected_installer,
                     "matches_repository": None, "error": str(exc)}
        unknown.append("kernel installer could not be read")
    report["installer"] = installer
    services = {}
    for unit in UNITS:
        result = runner(["systemctl", "show", unit, "--no-pager",
                         "--property=LoadState,ActiveState,SubState,ExecStart"])
        properties = dict(line.split("=", 1) for line in result["stdout"].splitlines() if "=" in line)
        services[unit] = {"properties": properties, "query": result}
        if properties.get("LoadState") == "not-found":
            reasons.append(unit + " missing: legacy combined setup or incomplete installation")
        elif result["returncode"] != 0 or not all(properties.get(key) for key in ("LoadState", "ActiveState")):
            unknown.append(unit + " state unavailable")
        elif properties["LoadState"] != "loaded" or properties["ActiveState"] != "active":
            reasons.append(unit + " is not loaded and active")
    report["services"] = services
    report["packages"] = runner(["dpkg-query", "-W", "-f=${Package}\t${Version}\t${Status}\n",
                                 "fuxi-device", "fuxi-desktop", "fuxi-modem", "fuxi-firmware"])
    if report["packages"]["returncode"] != 0:
        unknown.append("package inventory incomplete")
    if report["kernel"]["error"] or any(item["error"] for item in report["boot"].values()):
        unknown.append("kernel or boot selection unavailable")
    report.update(status="unknown" if unknown else "drift" if reasons else "match",
                  differences=reasons, unknown=unknown)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-installer-sha256", required=True)
    args = parser.parse_args()
    digest = args.expected_installer_sha256
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        parser.error("expected installer digest must be a lowercase SHA256")
    report = collect(digest)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return {"match": 0, "drift": 1, "unknown": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
