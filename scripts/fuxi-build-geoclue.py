#!/usr/bin/env python3
"""Build and test the pinned GeoClue daemon, then create an uninstalled ARM64 deb."""

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sources", ROOT / "scripts/fuxi-sources.py")
sources = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sources)
OPTIONS = [
    "--prefix=/usr", "--sysconfdir=/etc", "--localstatedir=/var",
    "--libexecdir=/usr/libexec", "--buildtype=release", "-Dintrospection=false",
    "-Dvapi=false", "-Dgtk-doc=false", "-Ddemo-agent=false", "-Dlibgeoclue=false",
    "-Ddbus-srv-user=geoclue", "-Dsystemd-system-unit-dir=/usr/lib/systemd/system",
    "-Ddefault-wifi-url=https://api.beacondb.net/v1/geolocate",
]


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def output(*args, **kwargs):
    return run(*args, stdout=subprocess.PIPE, text=True, **kwargs).stdout.strip()


def stage_package(destination, source, binary, environment, version, depends, epoch, options):
    template = ROOT / "packaging/fuxi-geoclue"
    shutil.copytree(template / "root", destination, dirs_exist_ok=True)
    shutil.copytree(template / "DEBIAN", destination / "DEBIAN")
    control = destination / "DEBIAN/control"
    control.write_text(control.read_text().replace("@VERSION@", version).replace("@DEPENDS@", depends))
    installed = destination / "usr/libexec/fuxi/geoclue"
    installed.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(binary, installed)
    records = destination / "usr/share/fuxi-geoclue"
    records.mkdir(parents=True)
    shutil.copyfile(source / ".fuxi-source.json", records / "source.json")
    shutil.copyfile(source / "COPYING", records / "COPYING")
    shutil.copyfile(environment, records / "build-environment.lock")
    (records / "build.json").write_text(json.dumps({
        "version": version, "architecture": "arm64", "source_date_epoch": epoch,
        "meson_options": options, "binary_sha256": sources.sha256(binary),
        "environment_sha256": sources.sha256(environment),
    }, indent=2) + "\n")
    files = sorted(p for p in destination.rglob("*") if p.is_file())
    (destination / "DEBIAN/md5sums").write_text("".join(
        f"{hashlib.md5(p.read_bytes()).hexdigest()}  {p.relative_to(destination)}\n"
        for p in files if "DEBIAN" not in p.relative_to(destination).parts
    ))
    for path in [destination, *destination.rglob("*")]:
        executable = path == installed or path.name in ("postinst", "postrm")
        path.chmod(0o755 if path.is_dir() or executable else 0o644)
        os.utime(path, (epoch, epoch))


def build(args):
    if output("dpkg", "--print-architecture") != "arm64":
        raise ValueError("Run in a native arm64 build environment")
    run("dpkg", "--validate-version", args.version)
    epoch_text = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch_text is None:
        epoch_text = output("git", "-C", str(ROOT), "log", "-1", "--format=%ct")
    epoch = int(epoch_text)
    if epoch <= 0:
        raise ValueError("SOURCE_DATE_EPOCH must be positive")
    environment = args.environment.resolve()
    inventory = output("dpkg-query", "-W", "-f=${binary:Package}=${Version}\n")
    if sorted(environment.read_text().splitlines()) != sorted(inventory.splitlines()):
        raise ValueError("Build environment record differs from the current dpkg inventory")
    source = args.sources.resolve() / "geoclue"
    recipe = json.loads(sources.MANIFEST.read_text())["geoclue"]
    sources.check("geoclue", source, recipe)
    directory = ROOT / "work/kbuild/geoclue"
    directory.mkdir(parents=True, exist_ok=True)
    args.output.mkdir(parents=True, exist_ok=True)
    package = args.output.resolve() / f"fuxi-geoclue_{args.version}_arm64.deb"
    with (directory / ".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if package.exists():
            raise ValueError(f"Refusing to overwrite {package}")
        env = dict(os.environ, SOURCE_DATE_EPOCH=str(epoch), LC_ALL="C.UTF-8",
                   DEB_BUILD_MAINT_OPTIONS="hardening=+all")
        cflags = shlex.split(output("dpkg-buildflags", "--get", "CPPFLAGS", env=env))
        cflags += shlex.split(output("dpkg-buildflags", "--get", "CFLAGS", env=env))
        cflags.append(f"-ffile-prefix-map={ROOT}=/usr/src/fuxi")
        ldflags = shlex.split(output("dpkg-buildflags", "--get", "LDFLAGS", env=env))
        # Explicit options also update flags when reusing a Meson build directory.
        options = [*OPTIONS, "-Dc_args=" + shlex.join(cflags),
                   "-Dc_link_args=" + shlex.join(ldflags)]
        builddir = directory / "build"
        setup = ["meson", "setup"]
        if (builddir / "meson-private/coredata.dat").exists():
            setup.append("--reconfigure")
        run(*setup, str(builddir), str(source), *options, env=env)
        run("ninja", "-j4", "-C", str(builddir), env=env)
        run("meson", "test", "-C", str(builddir), "--print-errorlogs", env=env)
        sources.check("geoclue", source, recipe)
        if inventory != output("dpkg-query", "-W", "-f=${binary:Package}=${Version}\n"):
            raise ValueError("Build environment changed while compiling")
        binary = builddir / "src/geoclue"
        with tempfile.TemporaryDirectory(prefix=".geoclue-", dir=args.output) as temporary:
            staging = Path(temporary)
            (staging / "debian").mkdir()
            (staging / "debian/control").write_text(
                "Source: fuxi-geoclue\n\nPackage: fuxi-geoclue\nArchitecture: arm64\n"
                "Description: fuxi GeoClue daemon\n")
            shlibs = output("dpkg-shlibdeps", "-O", "-e" + str(binary), cwd=staging, env=env)
            if not shlibs.startswith("shlibs:Depends=") or "\n" in shlibs:
                raise ValueError("Unexpected dpkg-shlibdeps output")
            depends = shlibs.removeprefix("shlibs:Depends=")
            if not depends:
                raise ValueError("Missing shared library dependencies")
            stage_package(staging / "root", source, binary, environment,
                          args.version, depends, epoch, options)
            artifact = staging / package.name
            run("dpkg-deb", "--root-owner-group", "-Zzstd", "-z9", "--build",
                str(staging / "root"), str(artifact), env=env)
            os.link(artifact, package)
    print(f"{sources.sha256(package)}  {package}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("--sources", type=Path, default=ROOT / "build/sources")
    parser.add_argument("--output", type=Path, default=ROOT / "work/geoclue-pkgs")
    parser.add_argument("--environment", type=Path,
                        default=Path(os.environ.get("FUXI_BUILD_ENV_LOCK", "/build-environment.lock")))
    args = parser.parse_args()
    try:
        build(args)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"fuxi-build-geoclue: {error}\n")


if __name__ == "__main__":
    main()
