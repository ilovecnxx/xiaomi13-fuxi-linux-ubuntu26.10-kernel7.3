#!/usr/bin/env python3
"""Produce review drafts and checks, without claiming authorship or sending mail."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def git(tree, *args, **kwargs):
    return subprocess.check_output(["git", "-C", str(tree), *args], **kwargs)


def export(tree, output):
    subprocess.run(["python3", str(ROOT / "scripts/fuxi-kernel-source.py"), "check", "--tree", str(tree)], check=True)
    manifest = json.loads((ROOT / "kernel/manifest.json").read_text())
    base = manifest["base_tree"]
    paths = git(tree, "diff", "--name-only", base, "HEAD", text=True).splitlines()
    output.mkdir(parents=True, exist_ok=False)
    records = []
    with tempfile.TemporaryDirectory() as temporary:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(temporary) / "index"))
        git(tree, "read-tree", base, env=env)
        for number, path in enumerate(paths, 1):
            patch = output / f"{number:04d}-{Path(path).name.replace(',', '_')}.patch"
            patch.write_bytes(git(tree, "diff", "--binary", base, "HEAD", "--", path))
            subprocess.run(["git", "-C", str(tree), "apply", "--cached", str(patch)], env=env, check=True)
            report = subprocess.run(["perl", str(tree / "scripts/checkpatch.pl"), "--no-tree", "--terse", str(patch)],
                                    capture_output=True, text=True)
            patch.with_suffix(".checkpatch.txt").write_text(report.stdout + report.stderr)
            maintainers = subprocess.run(["perl", str(tree / "scripts/get_maintainer.pl"), "--no-git", "--no-git-fallback", str(patch)],
                                         cwd=tree, capture_output=True, text=True)
            records.append({"path": path, "patch": patch.name, "checkpatch_exit": report.returncode,
                            "maintainer_candidates": maintainers.stdout.splitlines(),
                            "author_signoff": None, "semantic_split_reviewed": False,
                            "binding_reviewed": False, "maintainers_reviewed": False})
        reconstructed = git(tree, "write-tree", env=env, text=True).strip()
        expected = git(tree, "rev-parse", "HEAD^{tree}", text=True).strip()
        if reconstructed != expected:
            raise ValueError("drafts do not reconstruct the maintained tree")
    (output / "review.json").write_text(json.dumps({"source_tree": expected, "reconstructed_tree": reconstructed,
                                                   "drafts": records}, indent=2) + "\n")
    (output / "README.txt").write_text(
        "File-level review drafts, NOT signed email patches. Reconstruction was verified.\n"
        "Multi-purpose hunks still need semantic splitting and independent validation.\n"
        "Local diagnostic/config patches must not be proposed unchanged.\n"
        "No maintainer was contacted. Consult kernel/upstream.md and history.json.\n")
    print(f"exported and reconstruction-checked {len(records)} review drafts: {output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--tree", type=Path, default=ROOT / "build/linux-standard")
    args = parser.parse_args()
    export(args.tree.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
