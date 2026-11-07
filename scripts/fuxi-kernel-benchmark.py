#!/usr/bin/env python3
"""Build a clean kernel deb in the pinned container and retain comparable timings."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def run_phase(report, output, name, command, env):
    phase = {"name": name, "command": command}
    report["phases"].append(phase)
    started = time.monotonic()
    print(f"[{name}] {' '.join(command)}", flush=True)
    try:
        with (output / f"{name}.log").open("wb") as log:
            result = subprocess.run(command, cwd=ROOT, env=env, stdout=log,
                                    stderr=subprocess.STDOUT, check=False)
        phase["exit_code"] = result.returncode
        if result.returncode:
            raise subprocess.CalledProcessError(result.returncode, command)
    finally:
        phase["seconds"] = round(time.monotonic() - started, 3)
        print(f"[{name}] {phase['seconds']:.3f}s; log: {output / (name + '.log')}", flush=True)


def collect_artifacts(name, output, manifest, commit):
    package_dir = ROOT / "work/kbuild" / name
    record = json.loads((package_dir / "build.json").read_text())
    if record["source_tree"] != manifest["tree"] or record["source_dirty"]:
        raise ValueError("Built kernel does not match the clean pinned source tree")
    if not record["build_environment_sha256"]:
        raise ValueError("Missing pinned build environment provenance")
    subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=package_dir, check=True)
    packages = list(package_dir.glob("*.deb"))
    if len(packages) != 1:
        raise ValueError("Expected exactly one kernel deb")
    fields = subprocess.check_output(
        ["dpkg-deb", "--field", str(packages[0]), "Architecture"], text=True).strip()
    if fields != "arm64":
        raise ValueError(f"Unexpected package architecture: {fields}")
    shutil.copytree(package_dir, output / "package")
    # Include actual upstream input and exact tracked recipes, not just URLs.
    source_dir = output / "corresponding-source"
    source_dir.mkdir()
    archive = ROOT / "work/sources" / manifest["archive"]
    if sha256(archive) != manifest["archive_sha256"]:
        raise ValueError("Upstream kernel archive checksum mismatch")
    shutil.copy2(archive, source_dir / manifest["archive"])
    with (source_dir / "project-recipes.tar").open("wb") as stream:
        subprocess.run(["git", "-C", str(ROOT), "archive", "--format=tar", commit],
                       stdout=stream, check=True)
    (source_dir / "README.txt").write_text(
        f"Project commit: {commit}\n"
        "Extract project-recipes.tar, then from its root run:\n"
        f"python3 scripts/fuxi-kernel-source.py prepare --archive /path/{manifest['archive']}\n"
        "bash scripts/fuxi-build-env.sh image\n"
        f"bash scripts/fuxi-build-env.sh run bash scripts/fuxi-build-deb.sh {name}\n"
        "Container inputs are pinned in packaging/build/Containerfile.\n"
        "See package/build.json and benchmark.json for epoch, compiler and parallelism.\n"
        "See LICENSE.md, THIRD_PARTY.md and kernel/ for existing license terms.\n"
        "CI candidate only: unsigned, not validated on hardware, not a release.\n")
    return record


def save_report(report, output):
    (output / "benchmark.json").write_text(json.dumps(report, indent=2) + "\n")
    lines = ["## Clean Kernel Package Benchmark", "",
             "Unsigned CI candidate; no device validation or automatic installation.", "",
             f"- Result: {report['status']}", f"- Project commit: `{report['project_commit']}`",
             f"- Kernel tree: `{report['kernel_tree']}`", f"- Parallel jobs: {report['jobs']}",
             f"- CPU: {report['cpu']}", "", "| Phase | Seconds |", "|---|---:|"]
    lines.extend(f"| {phase['name']} | {phase['seconds']:.3f} |" for phase in report["phases"])
    lines.extend([f"| Total measured | {report['total_seconds']:.3f} |", "",
                  "Build uses a new output directory without ccache. Source/image caches may be warm.",
                  "Queueing and artifact upload are excluded; use Actions job times for delivery latency.",
                  "Compare build-package separately from environment and source preparation.", ""])
    text = "\n".join(lines)
    (output / "summary.md").write_text(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as stream:
            stream.write(text)
    with (output / "SHA256SUMS").open("w") as stream:
        for path in sorted(output.rglob("*")):
            if path.is_file() and path != output / "SHA256SUMS":
                stream.write(f"{sha256(path)}  {path.relative_to(output)}\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", help="Unique lowercase package name; use the same name on both hosts")
    parser.add_argument("--jobs", type=int, default=0, help="Parallel jobs; 0 uses nproc")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[a-z0-9.]+", args.name) or args.jobs < 0:
        parser.error("name must contain only a-z, 0-9 or dots; jobs must be nonnegative")
    if git("status", "--porcelain", "--untracked-files=no"):
        parser.error("Commit tracked changes before benchmarking exact project recipes")
    output = ROOT / "work/benchmarks" / args.name
    kout = ROOT / "work/kbuild" / f"benchmark-{args.name}-out"
    for path in (output, kout, ROOT / "work/kbuild" / args.name):
        if path.exists():
            parser.error(f"Clean benchmark refuses existing path: {path}")
    if shutil.disk_usage(ROOT).free < 8 * 1024**3:
        parser.error("Need at least 8 GiB free for a clean build and source artifacts")
    jobs = args.jobs or int(subprocess.check_output(["nproc"], text=True))
    manifest = json.loads((ROOT / "kernel/manifest.json").read_text())
    cpu = platform.machine()
    if Path("/proc/cpuinfo").exists():
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    report = {"schema": 1, "status": "running", "name": args.name,
              "project_commit": git("rev-parse", "HEAD"), "kernel_tree": manifest["tree"],
              "jobs": jobs, "cpu": cpu, "host": platform.platform(),
              "source_date_epoch": manifest["source_date_epoch"],
              "runner": os.environ.get("RUNNER_ENVIRONMENT", "local"),
              "github_run_id": os.environ.get("GITHUB_RUN_ID"),
              "clean_output": True, "compiler_cache": False, "phases": []}
    output.mkdir(parents=True)
    env = dict(os.environ)
    env["FUXI_BUILD_ARCH"] = "amd64"
    started = time.monotonic()
    try:
        run_phase(report, output, "prepare-source",
                  [sys.executable, "scripts/fuxi-kernel-source.py", "prepare"], env)
        run_phase(report, output, "prepare-environment",
                  ["bash", "scripts/fuxi-build-env.sh", "image"], env)
        run_phase(report, output, "build-package",
                  ["bash", "scripts/fuxi-build-env.sh", "run", "env",
                   f"FUXI_KOUT=/src/work/kbuild/{kout.name}", f"FUXI_BUILD_JOBS={jobs}",
                   f"SOURCE_DATE_EPOCH={manifest['source_date_epoch']}",
                   "FUXI_KTREE=", "FUXI_EXTRA_CFG=",
                   "bash", "scripts/fuxi-build-deb.sh", args.name], env)
        phase = {"name": "collect-artifacts"}
        report["phases"].append(phase)
        collected = time.monotonic()
        try:
            report["build"] = collect_artifacts(args.name, output, manifest, report["project_commit"])
            phase["exit_code"] = 0
        finally:
            phase["seconds"] = round(time.monotonic() - collected, 3)
        report["status"] = "success"
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        report["status"] = "failure"
        report["error"] = str(error)
        print(error, file=sys.stderr)
    finally:
        report["total_seconds"] = round(time.monotonic() - started, 3)
        compiler_log = ROOT / "work/claude-logs" / f"kbuild-deb-{args.name}.log"
        if compiler_log.exists():
            shutil.copy2(compiler_log, output / "compiler.log")
        save_report(report, output)
    return 0 if report["status"] == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
