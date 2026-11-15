#!/usr/bin/env python3
"""Choose independent CI checks from changed paths; unknown inputs run all."""
import argparse
import json
import os
from pathlib import Path
import subprocess


CHECKS = ("docs", "scripts", "apps", "source", "third_party", "kernel", "userspace")


def classify(paths, full=False):
    selected = {name: full for name in CHECKS}
    selected["docs"] = True
    for path in paths:
        if (path.startswith((".github/", "packaging/build/"))
                or path in ("scripts/fuxi-ci.sh", "scripts/fuxi-ci-plan.py",
                            "scripts/fuxi-build-env.sh")):
            return dict.fromkeys(CHECKS, True)
        if ((path.endswith(".md") and not path.startswith(("scripts/tests/", "apps/superterm/tests/")))
                or path.startswith("docs/")):
            continue
        if path.startswith("scripts/tests/kernel/"):
            selected["source"] = True
        elif path.startswith(("scripts/", "packaging/")):
            selected["scripts"] = True
            if path.startswith("packaging/sources/") or path == "scripts/fuxi-sources.py":
                selected["third_party"] = selected["userspace"] = True
            elif path.startswith("packaging/") and not path.endswith(".md"):
                selected["userspace"] = True
            if path in ("scripts/fuxi-build-deb.sh", "scripts/fuxi-source-state.py",
                        "scripts/fuxi-kernel-source.py", "scripts/fuxi-kernel-benchmark.py"):
                selected["source"] = selected["kernel"] = True
        elif path.startswith("apps/superterm/"):
            selected["apps"] = True
        elif path.startswith("kernel/experiments/"):
            selected["scripts"] = True
        elif path.startswith("kernel/"):
            selected["scripts"] = selected["source"] = selected["kernel"] = True
        else:
            return dict.fromkeys(CHECKS, True)
    return selected


def event_plan(event_name, event, root):
    if event_name in ("schedule", "workflow_dispatch"):
        return classify([], full=True)
    try:
        if event_name == "pull_request":
            base = event["pull_request"]["base"]["sha"]
            head = event["pull_request"]["head"]["sha"]
            revision = f"{base}...{head}"
        elif event_name == "push":
            base, head = event["before"], event["after"]
            revision = f"{base}..{head}"
        else:
            return classify([], full=True)
        if any(len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha)
               or sha == "0" * 40 for sha in (base, head)):
            return classify([], full=True)
        changed = subprocess.check_output(
            ["git", "diff", "--no-renames", "--name-only", "-z", revision, "--"],
            cwd=root, stderr=subprocess.PIPE,
        )
    except (KeyError, TypeError, OSError, subprocess.CalledProcessError):
        return classify([], full=True)
    return classify([os.fsdecode(path) for path in changed.split(b"\0") if path])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", type=Path, default=os.environ.get("GITHUB_EVENT_PATH"))
    parser.add_argument("--event-name", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    parser.add_argument("--output", type=Path, default=os.environ.get("GITHUB_OUTPUT"))
    args = parser.parse_args()
    try:
        event = json.loads(args.event.read_text()) if args.event else {}
    except (OSError, ValueError):
        event = {}
    plan = event_plan(args.event_name, event, Path(__file__).resolve().parents[1])
    if args.output:
        with args.output.open("a") as output:
            for key, value in plan.items():
                output.write(f"{key}={str(value).lower()}\n")
    print(json.dumps(plan, sort_keys=True))


if __name__ == "__main__":
    main()
