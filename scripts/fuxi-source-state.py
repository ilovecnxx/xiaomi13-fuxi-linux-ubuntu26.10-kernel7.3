#!/usr/bin/env python3
"""Save the actual Git worktree as a binary patch without changing its index."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


def git(tree, *args, env=None):
    return subprocess.check_output(
        ["git", "-C", str(tree), *args], env=env, stderr=subprocess.PIPE,
    )


def source_state(tree):
    tree = Path(tree).resolve()
    root = Path(os.fsdecode(git(tree, "rev-parse", "--show-toplevel")).strip())
    if root.resolve() != tree:
        raise ValueError("source must be the Git worktree root")
    commit = git(tree, "rev-parse", "HEAD").decode().strip()
    base = git(tree, "rev-parse", "HEAD^{tree}").decode().strip()
    with tempfile.TemporaryDirectory(prefix="fuxi-source-index-") as temporary:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(temporary) / "index")}
        git(tree, "read-tree", "HEAD", env=env)
        git(tree, "add", "-A", "--", ".", env=env)
        entries = git(tree, "ls-files", "--stage", "-z", env=env).split(b"\0")
        if any(entry.startswith(b"160000 ") for entry in entries):
            raise ValueError("submodule inputs require a separate source snapshot")
        actual = git(tree, "write-tree", env=env).decode().strip()
    if git(tree, "rev-parse", "HEAD").decode().strip() != commit:
        raise ValueError("source HEAD changed during snapshot")
    return {"source_commit": commit, "source_base_tree": base,
            "source_tree": actual, "source_dirty": actual != base}


def capture(tree, output):
    state = source_state(tree)
    patch = git(tree, "diff-tree", "--no-ext-diff", "--no-textconv", "--binary",
                "--full-index", "--no-commit-id", "-p", state["source_base_tree"],
                state["source_tree"], "--")
    state["source_patch_sha256"] = hashlib.sha256(patch).hexdigest()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with (output / "source.patch").open("xb") as handle:
        handle.write(patch)
    with (output / "source-state.json").open("x") as handle:
        json.dump(state, handle, indent=2)
        handle.write("\n")
    return state


def verify(tree, output):
    output = Path(output)
    saved = json.loads((output / "source-state.json").read_text())
    digest = hashlib.sha256((output / "source.patch").read_bytes()).hexdigest()
    if digest != saved["source_patch_sha256"]:
        raise ValueError("saved source patch changed")
    actual = source_state(tree)
    if any(actual[key] != saved[key] for key in actual):
        raise ValueError("source changed during build; rebuild this candidate")
    return saved


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("capture", "verify"))
    parser.add_argument("--tree", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        state = {"capture": capture, "verify": verify}[args.action](args.tree, args.output)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"source provenance: {error}\n")
    print(f"source {args.action}: {state['source_tree']}")


if __name__ == "__main__":
    main()
