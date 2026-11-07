#!/usr/bin/env bash
# 构建 fuxi 设备包并生成本地 apt 源（主机上运行，不需要 debhelper / sudo）。
#   scripts/fuxi-build-pkgs.sh [版本]       版本默认 0.<日期>.<时分>，例如 0.20261107.0930
# 产物：work/apt/
#   fuxi-device_<版本>_arm64.deb      packaging/fuxi-device（引导工具、服务、udev、UCM…）
#   fuxi-firmware_<版本>_all.deb       work/firmware-pkg（scripts/fuxi-firmware-pull.sh 拉取；专有，不进 git）
#   linux-image-*-fuxi-*.deb          work/kbuild/*/ 里已有的内核包（硬链接）
#   Packages Packages.gz Release SHA256SUMS fuxi.sources fuxi-pin（Release 无签名，fuxi.sources 里 Trusted: yes）
# 手机上：rsync work/apt/ → /srv/fuxi-apt/，fuxi.sources → /etc/apt/sources.list.d/，fuxi-pin → /etc/apt/preferences.d/fuxi
set -euo pipefail
B="$(cd "$(dirname "$0")/.." && pwd)"
V="${1:-0.$(date +%Y%m%d.%H%M)}"
OUT="$B/work/apt"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$OUT"

# 统一属主 root:root、权限规范化；时间戳固定为提交时间，保证可复现
SDE=$(git -C "$B" log -1 --format=%ct)
build() { # build <包目录> <输出 deb>
	local d="$1" deb="$2"
	find "$d" -exec touch -h -d "@$SDE" {} +
	fakeroot sh -c "chown -R 0:0 '$d' && dpkg-deb --root-owner-group -Zzstd -z9 --build '$d' '$deb'" >/dev/null
	echo "   $(basename "$deb")  $(du -h "$deb" | cut -f1)"
}

echo "== fuxi-device $V"
P="$TMP/fuxi-device"
mkdir -p "$P"
cp -a "$B/packaging/fuxi-device/root/." "$P/"
cp -a "$B/packaging/fuxi-device/DEBIAN" "$P/DEBIAN"
# hexagonrpcd 补丁版（scripts/fuxi-build-hexagonrpc.sh 构建，静态链接）
HX="$B/work/kbuild/hexagonrpc/hexagonrpcd-fuxi"
[ -x "$HX" ] || { echo "缺 $HX，先跑 scripts/fuxi-build-hexagonrpc.sh" >&2; exit 1; }
install -m755 "$HX" "$P/usr/libexec/fuxi/hexagonrpcd-fuxi"
# 迁移清单内嵌进 preinst/postinst
for s in preinst postinst; do
	python3 - "$P/DEBIAN/$s" "$B/packaging/fuxi-device/migrate.list" <<'EOF'
import sys
p, m = sys.argv[1], open(sys.argv[2]).read().rstrip("\n")
s = open(p).read()
assert "@MIGRATE_LIST@" in s
open(p, "w").write(s.replace("@MIGRATE_LIST@", m))
EOF
done
sed -i "s/@VERSION@/$V/" "$P/DEBIAN/control"
rm -rf "$P"/usr/sbin/__pycache__
chmod 755 "$P"/DEBIAN/{preinst,postinst,postrm} "$P"/usr/sbin/* "$P"/usr/libexec/fuxi/* "$P"/etc/kernel/post*.d/zz-fuxi "$P"/etc/initramfs/post-update.d/zz-fuxi
find "$P" -type d -exec chmod 755 {} +
find "$P" -type f ! -perm -u+x -exec chmod 644 {} +
# 包内不能还有 /usr/local
if grep -rIl "/usr/local/" "$P" --exclude-dir=DEBIAN; then echo "包内仍引用 /usr/local（见上）" >&2; exit 1; fi
( cd "$P" && find . -path ./DEBIAN -prune -o -type f -print | sed 's|^\./||' | LC_ALL=C sort | xargs -d '\n' md5sum ) > "$P/DEBIAN/md5sums"
build "$P" "$OUT/fuxi-device_${V}_arm64.deb"

echo "== fuxi-firmware $V"
FW="$B/work/firmware-pkg"
if [ -d "$FW/root/usr/lib/firmware" ]; then
	P="$TMP/fuxi-firmware"
	mkdir -p "$P/usr/share/fuxi-firmware"  # 不放 /usr/share/doc：Ubuntu dpkg.cfg.d/excludes 会丢掉
	cp -a "$FW/root/." "$P/"
	cp "$FW/PROVENANCE.txt" "$P/usr/share/fuxi-firmware/"
	cp -a "$B/packaging/fuxi-firmware/DEBIAN" "$P/DEBIAN"
	sed -i "s/@VERSION@/$V/" "$P/DEBIAN/control"
	find "$P" -type d -exec chmod 755 {} +
	find "$P" -type f -exec chmod 644 {} +
	( cd "$P" && find . -path ./DEBIAN -prune -o -type f -print | sed 's|^\./||' | LC_ALL=C sort | xargs -d '\n' md5sum ) > "$P/DEBIAN/md5sums"
	build "$P" "$OUT/fuxi-firmware_${V}_all.deb"
else
	echo "   跳过：没有 $FW（先跑 scripts/fuxi-firmware-pull.sh）"
fi

echo "== 内核包"
for k in "$B"/work/kbuild/*/linux-image-*-fuxi-*_arm64.deb; do
	[ -e "$k" ] || continue
	[ -e "$OUT/$(basename "$k")" ] || ln "$k" "$OUT/" 2>/dev/null || cp "$k" "$OUT/"
	echo "   $(basename "$k")"
done

echo "== 索引"
cp "$B/packaging/apt/fuxi.sources" "$B/packaging/apt/fuxi-pin" "$OUT/"
( cd "$OUT" && dpkg-scanpackages --multiversion . /dev/null 2>/dev/null > Packages && gzip -9nkf Packages \
	&& { echo "Origin: fuxi"; echo "Label: fuxi"; echo "Suite: fuxi"; echo "Codename: fuxi"; echo "Architectures: arm64 all";
	     echo "Date: $(LC_ALL=C date -Ru)"; echo "SHA256:";
	     for f in Packages Packages.gz; do printf ' %s %s %s\n' "$(sha256sum < $f | cut -d' ' -f1)" "$(stat -c%s $f)" "$f"; done; } > Release \
	&& sha256sum *.deb Packages > SHA256SUMS )
echo "   $(grep -c '^Package:' "$OUT/Packages") 个包 → $OUT"
