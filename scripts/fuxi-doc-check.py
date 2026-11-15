#!/usr/bin/env python3
"""Check local inline links in active entry/operation documents, without downloads."""
import argparse
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
ACTIVE = (
    "README.md", "AGENTS.md", "CLAUDE.md", "TASKS.md", "kernel/README.md", "packaging/README.md",
    "apps/README.md", "kernel/experiments/camera/README.md",
    "docs/内核维护与独立升级.md", "docs/内核持续维护与发布.md",
    "docs/基础相机目标.md", "docs/主摄接通计划-20261115.md",
    "images/README.md", "docs/项目精简记录-20261116.md",
)


def broken_links(document):
    text = document.read_text()
    text = re.sub(r"^\s*(`{3,}|~{3,}).*?^\s*\1\s*$", "", text,
                  flags=re.MULTILINE | re.DOTALL)
    errors = []
    for match in re.finditer(r"\[[^\]\n]+\]\((<[^>]+>|[^\s)]+)(?:\s+\"[^\"]*\")?\)", text):
        target = match.group(1).strip("<>")
        url = urlsplit(target)
        if url.scheme or url.netloc or not url.path:
            continue
        path = unquote(url.path)
        resolved = ROOT / path.lstrip("/") if path.startswith("/") else document.parent / path
        if not resolved.exists():
            errors.append(f"{document}: missing local link {target}")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("documents", nargs="*", type=Path)
    args = parser.parse_args()
    documents = args.documents or [ROOT / name for name in ACTIVE]
    errors = []
    for document in documents:
        try:
            errors.extend(broken_links(document))
        except (OSError, ValueError) as error:
            errors.append(f"{document}: {error}")
    subprocess.run(["git", "diff", "--check", "--", ".", ":!*.patch"], cwd=ROOT, check=True)
    if errors:
        parser.exit(1, "\n".join(errors) + "\n")
    print(f"Documentation: {len(documents)} files, local links and diff passed")


if __name__ == "__main__":
    main()
