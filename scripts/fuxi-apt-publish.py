#!/usr/bin/env python3
"""Validate and atomically publish immutable, optionally signed APT snapshots."""

import argparse
from datetime import datetime, timezone
import fcntl
import gzip
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def publish(source, target, key=None, local=False, adopt=False):
    if not key and not local:
        raise ValueError("a signing key is required; --local permits file: use only")
    target = target.absolute()
    target.parent.mkdir(parents=True, exist_ok=True)
    generations = target.parent / (target.name + ".generations")
    generations.mkdir(exist_ok=True)
    with (target.parent / (target.name + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if target.exists() and not target.is_symlink() and not adopt:
            raise ValueError("existing repository is a directory; use --adopt once to archive it")
        staging = Path(tempfile.mkdtemp(prefix="snapshot-", dir=generations))
        legacy = None
        try:
            packages = sorted(source.glob("*.deb"))
            if not packages:
                raise ValueError("no packages")
            for package in packages:
                name = subprocess.check_output(["dpkg-deb", "-f", str(package), "Package"], text=True).strip()
                if name == "fuxi-firmware" and not local:
                    raise ValueError("firmware redistribution not cleared; use a private local repository")
                run("dpkg-deb", "--info", str(package), stdout=subprocess.DEVNULL)
                shutil.copyfile(package, staging / package.name)
            index = subprocess.check_output(["dpkg-scanpackages", "--multiversion", ".", "/dev/null"], cwd=staging,
                                            stderr=subprocess.DEVNULL)
            (staging / "Packages").write_bytes(index)
            (staging / "Packages.gz").write_bytes(gzip.compress(index, mtime=0))
            # Keep previously advertised URLs available for readers which
            # fetched the old Release before the pointer changed.
            previous_sources = target / "fuxi.sources"
            if target.is_symlink() and previous_sources.exists():
                was_local = "Trusted: yes" in previous_sources.read_text()
                if was_local == local:
                    for old in target.glob("*.deb"):
                        new = staging / old.name
                        if new.exists():
                            if hashlib.sha256(old.read_bytes()).digest() != hashlib.sha256(new.read_bytes()).digest():
                                raise ValueError(f"published package name cannot be reused: {old.name}")
                        else:
                            shutil.copyfile(old, new)
                    if (target / "by-hash").exists():
                        shutil.copytree(target / "by-hash", staging / "by-hash", dirs_exist_ok=True)
            if (source / "fuxi-pin").exists():
                shutil.copyfile(source / "fuxi-pin", staging / "fuxi-pin")
            release = ["Origin: fuxi", "Label: fuxi", "Suite: fuxi", "Codename: fuxi",
                       "Architectures: arm64 all", "Acquire-By-Hash: yes",
                       "Date: " + datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000"), "SHA256:"]
            for name in ("Packages", "Packages.gz"):
                data = (staging / name).read_bytes()
                digest = hashlib.sha256(data).hexdigest()
                release.append(f" {digest} {len(data)} {name}")
                by_hash = staging / "by-hash/SHA256" / digest
                by_hash.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(staging / name, by_hash)
            (staging / "Release").write_text("\n".join(release) + "\n")
            if key:
                run("gpg", "--batch", "--yes", "--local-user", key, "--clearsign", "--output",
                    str(staging / "InRelease"), str(staging / "Release"))
                run("gpg", "--batch", "--yes", "--local-user", key, "--detach-sign", "--output",
                    str(staging / "Release.gpg"), str(staging / "Release"))
                public = subprocess.check_output(["gpg", "--batch", "--export", key])
                (staging / "fuxi-archive-keyring.gpg").write_bytes(public)
                run("gpgv", "--keyring", str(staging / "fuxi-archive-keyring.gpg"), str(staging / "InRelease"))
            fields = "Types: deb\nURIs: file:/srv/fuxi-apt\nSuites: ./\n"
            fields += "Trusted: yes\n" if local else "Signed-By: /usr/share/keyrings/fuxi-archive-keyring.gpg\n"
            (staging / "fuxi.sources").write_text(fields)
            sums = []
            for path in sorted(staging.iterdir()):
                if path.is_file():
                    sums.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n")
            (staging / "SHA256SUMS").write_text("".join(sums))
            # mkdtemp is private; APT's sandbox and HTTP servers need read access.
            staging.chmod(0o755)
            for path in staging.rglob("*"):
                path.chmod(0o755 if path.is_dir() else 0o644)
                if path.is_file():
                    with path.open("rb") as stream:
                        os.fsync(stream.fileno())
            os.sync()
            if target.exists() and not target.is_symlink():
                legacy = generations / ("legacy-" + staging.name)
                os.rename(target, legacy)
            pointer = target.parent / ("." + target.name + ".new")
            pointer.unlink(missing_ok=True)
            pointer.symlink_to(staging.relative_to(target.parent), target_is_directory=True)
            os.replace(pointer, target)
            directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except BaseException:
            if legacy is not None and not target.exists() and not target.is_symlink():
                os.rename(legacy, target)
            if not target.is_symlink() or target.resolve() != staging:
                shutil.rmtree(staging)
            raise
    print(f"published: {target} -> {staging.name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--key", default=os.environ.get("FUXI_SIGNING_KEY"))
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--adopt", action="store_true")
    args = parser.parse_args()
    publish(args.source, args.target, args.key, args.local, args.adopt)


if __name__ == "__main__":
    main()
