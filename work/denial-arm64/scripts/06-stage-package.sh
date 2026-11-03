#!/bin/bash
# Phase 4: assemble ARM64 install tree (engine + denial packages), debs, tarball, checksums
set -euo pipefail
set -x
source /home/fuxi/denial-logs/env.sh
SR=/home/fuxi/denial-src
OUT=/home/fuxi/denial-out
CACHE=/home/fuxi/denial-cache
WORK=$OUT/work
PKGVER=0.5.1
PKGREL=1
GLIBC_MAX=2.44
ENGINE_DIR=$CACHE/flutter-engine
ENGINE_OUT=$ENGINE_DIR/build/out/denial_host_release_arm64
BUNDLE=$SR/dart_shell/build/linux/arm64/release/bundle
SETTINGS=$SR/settings_app/build/linux/arm64/release/bundle
RUST=$CACHE/pc-build/rust/release
X64META=$SR/prebuilt/flutter-engine/linux-x64-release

date -Is
echo "=== verify inputs ==="
for f in "$ENGINE_OUT/libflutter_engine.so" "$ENGINE_OUT/icudtl.dat" "$ENGINE_OUT/args.gn" \
         "$BUNDLE/lib/libapp.so" "$BUNDLE/lib/libflutter_engine.so" "$BUNDLE/data/icudtl.dat" \
         "$BUNDLE/data/flutter_assets/AssetManifest.bin" \
         "$SETTINGS/denial-settings" "$SETTINGS/lib/libapp.so" "$SETTINGS/lib/libflutter_engine.so" \
         "$RUST/deniald" "$RUST/denialctl" "$RUST/denial-portal"; do
  [[ -f "$f" ]] || { echo "MISSING $f"; exit 1; }
done
file "$ENGINE_OUT/libflutter_engine.so" "$ENGINE_OUT/icudtl.dat" "$BUNDLE/lib/libapp.so" \
     "$SETTINGS/lib/libapp.so" "$SETTINGS/denial-settings" \
     "$RUST/deniald" "$RUST/denialctl" "$RUST/denial-portal"

rm -rf --one-file-system "$WORK"
mkdir -p "$WORK/engine" "$WORK/denial"
engine_root=$WORK/engine
denial_root=$WORK/denial

echo "=== engine package root ==="
install -Dm755 "$ENGINE_OUT/libflutter_engine.so" "$engine_root/usr/lib/denial/flutter/lib/libflutter_engine.so"
install -Dm644 "$ENGINE_OUT/icudtl.dat" "$engine_root/usr/lib/denial/flutter/data/icudtl.dat"
install -Dm644 "$SR/prebuilt/flutter-engine/SOURCE_LOCK.json" "$engine_root/usr/share/denial/flutter-engine/SOURCE_LOCK.json"
install -Dm644 "$ENGINE_OUT/args.gn" "$engine_root/usr/share/denial/flutter-engine/args.gn"
install -Dm644 "$X64META/ENGINE_REVISION" "$engine_root/usr/share/denial/flutter-engine/ENGINE_REVISION"
install -Dm644 "$X64META/FLUTTER_REVISION" "$engine_root/usr/share/denial/flutter-engine/FLUTTER_REVISION"
install -Dm644 "$X64META/LICENSE.flutter" "$engine_root/usr/share/licenses/denial-flutter-engine/LICENSE.flutter"
install -Dm644 "$X64META/LICENSE.third_party" "$engine_root/usr/share/licenses/denial-flutter-engine/LICENSE.third_party"
engine_sha=$(sha256sum "$ENGINE_OUT/libflutter_engine.so" | cut -d' ' -f1)
args_sha=$(sha256sum "$ENGINE_OUT/args.gn" | cut -d' ' -f1)
icu_sha=$(sha256sum "$ENGINE_OUT/icudtl.dat" | cut -d' ' -f1)
printf '%s  libflutter_engine.so\n' "$engine_sha" > "$engine_root/usr/share/denial/flutter-engine/libflutter_engine.so.sha256"
lock_sha=$(sha256sum "$SR/prebuilt/flutter-engine/SOURCE_LOCK.json" | cut -d' ' -f1)
dart_ver=$(awk -F'"' '/^dart_version = /{print $2}' "$ENGINE_OUT/args.gn")
jq -n --arg sha "$engine_sha" --arg args_sha "$args_sha" --arg icu "$icu_sha" \
      --arg lock_sha "$lock_sha" --arg dart_ver "$dart_ver" --arg build_id "$(readelf -n "$ENGINE_OUT/libflutter_engine.so" | awk '/Build ID/{print $3}')" '
{
  schema_version: 1,
  stage: "device-local-beta",
  generation: ("flutter-3.47.5-engine-af7e796e-denial-r1-arm64-native"),
  package: { name: "denial-flutter-engine", version_source: "denial-release-tag", epoch: 1, architecture: "aarch64" },
  compatibility: { flutter_engine_abi: "3.47.5.denial1" },
  sources: {
    flutter_version: "3.47.5",
    flutter_revision: "6a19cca56475dbfba1478ee68d7bd0c2ef891da1",
    engine_artifact_revision: "af7e796e161ae0bb1ff0758c71a7105418bd9ded",
    dart_version: $dart_ver,
    flutter_repository: "https://github.com/denialwm/flutter.git",
    flutter_fork_revision: "d9c0f91faefa7850131473a5c51d68950eeb40f2",
    skia_repository: "https://github.com/denialwm/skia.git",
    skia_fork_revision: "5b495e3e15a59ee76af882d83316d351654c1e88"
  },
  source_lock: { path: "prebuilt/flutter-engine/SOURCE_LOCK.json", sha256: $lock_sha },
  artifacts: { gn_args_sha256: $args_sha, libflutter_engine_sha256: $sha, libflutter_engine_build_id: $build_id, icudtl_sha256: $icu },
  assurances: {
    source_tree_equality_verified: true,
    source_build_verified: true,
    unit_tests_verified: false,
    hardware_verified: false,
    offline_source_closure: false,
    reproducible_package: false,
    signed_generation_tags: false
  }
}' > "$engine_root/usr/share/denial/flutter-engine/manifest.json"
{
  mkdir -p "$engine_root/usr/share/doc/denial-flutter-engine"
  echo "# Flutter Engine - Linux arm64 (aarch64 native) release"
  echo
  echo "Device-local source build on Xiaomi 13 (fuxi), Ubuntu 26.10 arm64, clang/bundled"
  echo "Flutter toolchain from the Denial Flutter fork. This is **not** a Denial"
  echo "first-party release artifact: ARM64 release metadata/validation is not"
  echo "published upstream, and this tree is not signed."
  echo
  echo "## Source identity"
  echo
  echo "SOURCE_LOCK.json: \`d9c0f91faefa7850131473a5c51d68950eeb40f2\` (Flutter fork),"
  echo "\`5b495e3e15a59ee76af882d83316d351654c1e88\` (Skia fork), depot_tools"
  echo "\`580b4ff3f5cd0dcaa2eacda28cefe0f45320e8f7\`."
  echo
  echo "## Build"
  echo
  echo '    tools/denial-flutter-engine prepare-app-build   # native aarch64'
  echo
  echo "GN arguments: args.gn beside this file (host_cpu=arm64, target_cpu=arm64,"
  echo "dart_target_arch=arm64, is_official_build=true, slimpeller=true, fontconfig)."
  echo
  echo "## Artifacts"
  echo
  echo "libflutter_engine.so sha256: \`$engine_sha\`"
  echo "icudtl.dat sha256: \`$icu_sha\`"
  echo "args.gn sha256: \`$args_sha\`"
} > "$engine_root/usr/share/doc/denial-flutter-engine/BUILD_INFO.md"

echo "=== denial package root ==="
install -Dm755 "$RUST/deniald" "$denial_root/usr/bin/deniald"
install -Dm755 "$RUST/denialctl" "$denial_root/usr/bin/denialctl"
install -Dm755 "$RUST/denial-portal" "$denial_root/usr/bin/denial-portal"
install -Dm755 "$SR/packaging/arch/denial-session" "$denial_root/usr/bin/denial-session"
install -Dm644 "$SR/packaging/denial-session.target" "$denial_root/usr/lib/systemd/user/denial-session.target"
install -Dm644 "$SR/packaging/arch/denial-portal.service" "$denial_root/usr/lib/systemd/user/denial-portal.service"
install -Dm755 "$SR/packaging/denial-suspend-mode" "$denial_root/usr/lib/systemd/system-sleep/denial-suspend-mode"
install -Dm755 "$SR/packaging/denial-suspend-mode" "$denial_root/usr/lib/elogind/system-sleep/denial-suspend-mode"
install -d -m 0755 "$denial_root/usr/lib/denial/flutter/data"
cp -a "$BUNDLE/data/flutter_assets" "$denial_root/usr/lib/denial/flutter/data/flutter_assets"
chmod -R u=rwX,go=rX "$denial_root/usr/lib/denial/flutter/data/flutter_assets"
install -Dm755 "$BUNDLE/lib/libapp.so" "$denial_root/usr/lib/denial/flutter/lib/libapp.so"
install -d -m 0755 "$denial_root/usr/lib/denial/settings"
cp -a "$SETTINGS/." "$denial_root/usr/lib/denial/settings/"
chmod -R u=rwX,go=rX "$denial_root/usr/lib/denial/settings"
ln -s ../lib/denial/settings/denial-settings "$denial_root/usr/bin/denial-settings"
install -Dm644 "$SR/packaging/arch/denial.desktop" "$denial_root/usr/share/wayland-sessions/denial.desktop"
install -Dm644 "$SR/packaging/arch/dev.denial.Settings.desktop" "$denial_root/usr/share/applications/dev.denial.Settings.desktop"
install -Dm644 "$SR/packaging/dev.denial.Welcome.desktop" "$denial_root/usr/share/applications/dev.denial.Welcome.desktop"
install -Dm644 "$SR/packaging/arch/denial-portals.conf" "$denial_root/usr/share/xdg-desktop-portal/denial-portals.conf"
install -Dm644 "$SR/packaging/arch/denial.portal" "$denial_root/usr/share/xdg-desktop-portal/portals/denial.portal"
install -Dm644 "$SR/packaging/arch/org.freedesktop.impl.portal.desktop.denial.service" "$denial_root/usr/share/dbus-1/services/org.freedesktop.impl.portal.desktop.denial.service"
install -Dm644 "$SR/packaging/arch/xdg-desktop-portal-wlr-Denial" "$denial_root/etc/xdg/xdg-desktop-portal-wlr/Denial"
install -Dm644 "$SR/packaging/arch/outputs.conf" "$denial_root/etc/denial/outputs.conf"
install -Dm644 "$SR/packaging/arch/session.conf" "$denial_root/etc/denial/session.conf"
printf '\n# Device default for Xiaomi 13 fuxi: phone shell\nDENIAL_SHELL_PROFILE=mobile\n' >> "$denial_root/etc/denial/session.conf"
install -Dm644 "$SR/README.md" "$denial_root/usr/share/doc/denial/README.md"
install -d -m 0755 "$denial_root/usr/share/man/man1"
for m in denialctl deniald denial-session denial-portal; do
  gzip -n -9 --stdout "$SR/docs/man/$m.1" > "$denial_root/usr/share/man/man1/$m.1.gz"
  chmod 0644 "$denial_root/usr/share/man/man1/$m.1.gz"
done
install -Dm644 "$SR/packages/denial_flutter_sdk/assets/wallpapers/ATTRIBUTION.md" "$denial_root/usr/share/doc/denial/WALLPAPERS.md"
install -Dm644 "$SR/packages/denial_flutter_sdk/assets/cursors/BIBATA_MODERN_ICE.md" "$denial_root/usr/share/doc/denial/CURSORS.md"
install -Dm644 "$SR/packages/denial_flutter_sdk/assets/cursors/bibata_modern_ice.sha256" "$denial_root/usr/share/doc/denial/BIBATA_MODERN_ICE.sha256"
install -Dm644 "$SR/packages/denial_flutter_sdk/assets/fonts/README.md" "$denial_root/usr/share/doc/denial/FONTS.md"
install -Dm644 "$SR/packages/denial_flutter_sdk/assets/fonts/JetBrainsMono.sha256" "$denial_root/usr/share/doc/denial/JETBRAINS_MONO.sha256"
install -Dm644 "$SR/LICENSES/CC-BY-SA-4.0.txt" "$denial_root/usr/share/licenses/denial/CC-BY-SA-4.0.txt"
install -Dm644 "$SR/LICENSES/GPL-3.0-only.txt" "$denial_root/usr/share/licenses/denial/GPL-3.0-only.txt"
install -Dm644 "$SR/packages/denial_flutter_sdk/assets/fonts/OFL.txt" "$denial_root/usr/share/licenses/denial/OFL-1.1.txt"
install -Dm644 "$SR/LICENSE" "$denial_root/usr/share/licenses/denial/LICENSE"

echo "=== ELF architecture verification ==="
bad=0
while IFS= read -r -d '' f; do
  if [[ "$(od -An -N4 -tx1 -- "$f" | tr -d ' \n')" == 7f454c46 ]]; then
    file "$f" | grep -q 'ARM aarch64' || { echo "NOT_AARCH64: $(file -b "$f") $f"; bad=1; }
  fi
done < <(find "$engine_root" "$denial_root" -type f -print0)
[[ $bad -eq 0 ]] || exit 1

max_glibc_of() {
  local root="$1" max=0 v
  while IFS= read -r -d '' f; do
    [[ "$(od -An -N4 -tx1 -- "$f" | tr -d ' \n')" == 7f454c46 ]] || continue
    while IFS= read -r v; do
      [[ -n "$v" ]] || continue
      if [[ "$(printf '%s\n%s\n' "$max" "$v" | sort -V | tail -1)" == "$v" ]]; then max=$v; fi
    done < <(objdump -T "$f" 2>/dev/null | sed -n 's/.*(GLIBC_\([^)]*\)).*/\1/p' | sort -Vu)
  done < <(find "$root" -type f -print0)
  printf '%s\n' "$max"
}
engine_glibc=$(max_glibc_of "$engine_root"); denial_glibc=$(max_glibc_of "$denial_root")
echo "engine requires GLIBC_$engine_glibc; denial requires GLIBC_$denial_glibc"

echo "=== manifests ==="
( cd "$engine_root" && find . -type f -print0 | sort -z | xargs -0 sha256sum > "$WORK/denial-flutter-engine.sha256" )
( cd "$denial_root" && find . -type f -print0 | sort -z | xargs -0 sha256sum > "$WORK/denial.sha256" )
( cd "$engine_root" && find . -mindepth 1 \( -type l -printf '%m %y %p -> %l\n' -o -printf '%m %y %p\n' \) | sort > "$WORK/denial-flutter-engine.inventory" )
( cd "$denial_root" && find . -mindepth 1 \( -type l -printf '%m %y %p -> %l\n' -o -printf '%m %y %p\n' \) | sort > "$WORK/denial.inventory" )

install -d "$WORK/pkg"
build_deb() {
  local name="$1" root="$2" body="$3" extra="$4" out="$5"
  local pkgroot="$WORK/pkg/$name"
  rm -rf "$pkgroot"; install -d "$pkgroot/DEBIAN"
  cp -a "$root/." "$pkgroot/"
  local size; size=$(du -sk --apparent-size "$root" | cut -f1)
  { printf 'Package: %s\nVersion: %s-%s\nArchitecture: arm64\nMaintainer: Doctor Logix <doctor.logix@gmail.com>\nInstalled-Size: %s\n'
    printf '%s\n' "$body"; } > "$pkgroot/DEBIAN/control"
  if [[ -n "$extra" ]]; then eval "$extra"; fi
  find "$pkgroot/DEBIAN" -print0 | xargs -0 touch --no-dereference --date="@$SOURCE_DATE_EPOCH"
  env SOURCE_DATE_EPOCH=$SOURCE_DATE_EPOCH dpkg-deb --root-owner-group --uniform-compression -Zxz --build "$pkgroot" "$out" >/dev/null
  echo "built $out"
}

engine_control="Section: libs
Priority: optional
Depends: libc6 (>= $engine_glibc), libfontconfig1
Provides: denial-flutter-engine-abi (= 3.47.5.denial1)
Conflicts: denial-flutter-engine-git
Homepage: https://github.com/denialwm/denial
Description: pinned Flutter Engine runtime for Denial (arm64 source build)
 Source-built Flutter Engine generation coupled to Denial's embedder ABI."
denial_control="Section: x11
Priority: optional
Depends: bash, coreutils, git, dbus, denial-flutter-engine (= $PKGVER-$PKGREL), init-system-helpers (>= 1.66~), libc6 (>= $denial_glibc), libegl1, libgbm1, libgles2, libwayland-client0, libgcc-s1, libgl1, libinput10, libpam0g, libpolkit-agent-1-0, polkitd | policykit-1, libpulse0, libseat1, libudev1, libxkbcommon0, rtkit, x11-xkb-utils, xkb-data, xserver-xorg-core, xwayland
Recommends: fonts-noto-cjk, network-manager, pipewire-pulse, power-profiles-daemon, upower, xdg-desktop-portal, xdg-desktop-portal-gtk, xdg-desktop-portal-wlr, zenity
Suggests: ddcutil
Conflicts: denial-git
Homepage: https://github.com/denialwm/denial
Description: Flutter-native Wayland compositor and desktop shell (arm64 source build)
 Denial owns the Wayland desktop scene, shell, motion, and composition using
 Flutter as part of the compositor foundation."

dest=$OUT/artifacts; rm -rf "$dest"; mkdir -p "$dest"
build_deb denial-flutter-engine "$engine_root" "$engine_control" "" \
  "$dest/denial-flutter-engine_${PKGVER}-${PKGREL}_arm64.deb"
rel_here=$(pwd)
build_deb denial "$denial_root" "$denial_control" \
  'printf "%s\n" /etc/denial/outputs.conf /etc/denial/session.conf /etc/xdg/xdg-desktop-portal-wlr/Denial > "$pkgroot/DEBIAN/conffiles"; install -m755 "$SR/packaging/debian/postinst" "$pkgroot/DEBIAN/postinst"; install -m755 "$SR/packaging/debian/postrm" "$pkgroot/DEBIAN/postrm"' \
  "$dest/denial_${PKGVER}-${PKGREL}_arm64.deb"

echo "=== merged install root tarball ==="
merge=$WORK/install-root
mkdir -p "$merge"
cp -a "$engine_root/." "$merge/"
cp -a "$denial_root/." "$merge/"
( cd "$merge" && find . -type f -print0 | sort -z | xargs -0 sha256sum > "$WORK/install-root.sha256" )
( cd "$merge" && find . -mindepth 1 \( -type l -printf '%m %y %p -> %l\n' -o -printf '%m %y %p\n' \) | sort > "$WORK/install-root.inventory" )
tar -C "$merge" --exclude='./usr/share/doc/denial/sources' -czf "$dest/denial-arm64-${PKGVER}-${PKGREL}-install-root.tar.gz" .
cp "$WORK/denial-flutter-engine.sha256" "$WORK/denial.sha256" "$WORK/install-root.sha256" \
   "$WORK/denial-flutter-engine.inventory" "$WORK/denial.inventory" "$WORK/install-root.inventory" "$dest/"
cp "$WORK/engine/usr/share/denial/flutter-engine/args.gn" "$dest/engine-args.gn"
{ echo "denial v0.5.1 (c96375a190a200b6343868afc42d4492b6badb9d) ARM64 source build on Xiaomi 13 fuxi"
  echo "engine glibc max: $engine_glibc"
  echo "denial glibc max: $denial_glibc"
  echo "built: $(date -Is)"
} > "$dest/PROVENANCE.txt"
( cd "$dest" && sha256sum * > SHA256SUMS.txt )
echo "=== artifacts ==="
ls -l "$dest"
date -Is
