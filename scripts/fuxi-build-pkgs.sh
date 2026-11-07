#!/usr/bin/env bash
# 构建 fuxi 设备包并生成本地 apt 源（主机上运行，不需要 debhelper / sudo）。
#   scripts/fuxi-build-pkgs.sh [版本]       版本默认 0.<日期>.<时分>，例如 0.20261107.0930
# 产物：work/apt/
#   fuxi-device_<版本>_arm64.deb      packaging/fuxi-device（引导工具、服务、udev、UCM…）
#   fuxi-modem_<版本>_arm64.deb       packaging/fuxi-modem + 固定源码构建的 work/modem-bin
#   fuxi-desktop_<版本>_all.deb       packaging/fuxi-desktop（Denial/Phosh/Plasma 会话单元、fuxi-session、portal 配置）
#   fuxi-firmware_<版本>_all.deb       work/firmware-pkg（scripts/fuxi-firmware-pull.sh 拉取；专有，不进 git）
#   linux-image-*-fuxi-*.deb          仅 packaging/apt/kernels.list 中已验收的内核包（硬链接）
#   Packages Packages.gz Release SHA256SUMS fuxi.sources fuxi-pin（公开源必须签名；本地源须显式启用）
# 手机上：rsync work/apt/ → /srv/fuxi-apt/，fuxi.sources → /etc/apt/sources.list.d/，fuxi-pin → /etc/apt/preferences.d/fuxi
set -euo pipefail
B="$(cd "$(dirname "$0")/.." && pwd)"
V="${1:-0.$(date +%Y%m%d.%H%M)}"
DEST="${FUXI_APT_OUT:-$B/work/apt}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
OUT="$TMP/packages"
mkdir -p "$OUT"
[ -n "${FUXI_SIGNING_KEY:-}" ] || [ "${FUXI_APT_LOCAL:-0}" = 1 ] || { echo 'Set FUXI_SIGNING_KEY or explicitly FUXI_APT_LOCAL=1' >&2; exit 1; }
dpkg --validate-version "$V"
python3 "$B/scripts/fuxi-sources.py" check hexagonrpc modemmanager 81voltd
python3 "$B/scripts/fuxi-verify-deps.py"
[ "${FUXI_APT_LOCAL:-0}" = 1 ] || python3 "$B/scripts/fuxi-release-check.py"

# 统一属主 root:root、权限规范化；时间戳固定为提交时间，保证可复现
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(git -C "$B" log -1 --format=%ct)}"
SDE=$SOURCE_DATE_EPOCH
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
mkdir -p "$P/usr/share/doc/fuxi-device/hexagonrpc"
cp "$B/build/sources/hexagonrpc/COPYING" "$P/usr/share/doc/fuxi-device/hexagonrpc/"
cp "$B/work/kbuild/hexagonrpc/source.json" "$P/usr/share/doc/fuxi-device/hexagonrpc/"
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
find "$P" -type d -name __pycache__ -prune -exec rm -rf {} +
chmod 755 "$P"/DEBIAN/{preinst,postinst,postrm} "$P"/usr/sbin/* "$P"/usr/libexec/fuxi/* "$P"/etc/kernel/post*.d/zz-fuxi "$P"/etc/initramfs/post-update.d/zz-fuxi
find "$P" -type d -exec chmod 755 {} +
find "$P" -type f ! -perm -u+x -exec chmod 644 {} +
# 包内不能还有 /usr/local
if grep -rIl "/usr/local/" "$P" --exclude-dir=DEBIAN; then echo "包内仍引用 /usr/local（见上）" >&2; exit 1; fi
( cd "$P" && find . -path ./DEBIAN -prune -o -type f -print | sed 's|^\./||' | LC_ALL=C sort | xargs -d '\n' md5sum ) > "$P/DEBIAN/md5sums"
build "$P" "$OUT/fuxi-device_${V}_arm64.deb"

echo "== fuxi-modem $V"
MB="$B/work/modem-bin"
[ -x "$MB/mm/src/ModemManager" ] && [ -x "$MB/81voltd-sub2" ] || { echo "缺 $MB，先跑 scripts/fuxi-build-modem.sh" >&2; exit 1; }
( cd "$MB" && sha256sum -c --quiet SHA256SUMS ) || { echo "$MB/SHA256SUMS 校验失败" >&2; exit 1; }
P="$TMP/fuxi-modem"
mkdir -p "$P/usr/libexec/fuxi"
cp -a "$B/packaging/fuxi-modem/root/." "$P/"
cp -a "$B/packaging/fuxi-modem/DEBIAN" "$P/DEBIAN"
cp -a "$MB/mm" "$P/usr/libexec/fuxi/mm"
install -m755 "$MB/81voltd-sub2" "$P/usr/libexec/fuxi/81voltd-sub2"
mkdir -p "$P/usr/share/doc/fuxi-modem/"{modemmanager,81voltd}
cp "$B/build/sources/modemmanager/"COPYING* "$P/usr/share/doc/fuxi-modem/modemmanager/"
cp /usr/share/common-licenses/GPL-2 "$P/usr/share/doc/fuxi-modem/81voltd/"
cp "$MB/modemmanager-source.json" "$P/usr/share/doc/fuxi-modem/modemmanager/"
cp "$MB/81voltd-source.json" "$P/usr/share/doc/fuxi-modem/81voltd/"
python3 - "$P/DEBIAN/postinst" "$B/packaging/fuxi-modem/migrate.list" <<'EOF2'
import sys
p, m = sys.argv[1], open(sys.argv[2]).read().rstrip("\n")
s = open(p).read()
assert "@MIGRATE_LIST@" in s
open(p, "w").write(s.replace("@MIGRATE_LIST@", m))
EOF2
sed -i "s/@VERSION@/$V/" "$P/DEBIAN/control"
find "$P" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$P" -type d -exec chmod 755 {} +
find "$P" -type f -exec chmod 644 {} +
chmod 755 "$P"/DEBIAN/{postinst,postrm} "$P"/usr/sbin/* "$P"/usr/bin/* "$P"/usr/libexec/fuxi/81voltd-sub2 \
	"$P"/usr/libexec/fuxi/mm/src/ModemManager "$P"/usr/libexec/fuxi/mm/libmm-glib/libmm-glib.so.0.11.0
if grep -rIl "/usr/local/" "$P" --exclude-dir=DEBIAN; then echo "包内仍引用 /usr/local（见上）" >&2; exit 1; fi
( cd "$P" && find . -path ./DEBIAN -prune -o -type f -print | sed 's|^\./||' | LC_ALL=C sort | xargs -d '\n' md5sum ) > "$P/DEBIAN/md5sums"
build "$P" "$OUT/fuxi-modem_${V}_arm64.deb"

echo "== fuxi-desktop $V"
P="$TMP/fuxi-desktop"
mkdir -p "$P"
cp -a "$B/packaging/fuxi-desktop/root/." "$P/"
cp -a "$B/packaging/fuxi-desktop/DEBIAN" "$P/DEBIAN"
for s in preinst postinst; do
	python3 - "$P/DEBIAN/$s" "$B/packaging/fuxi-desktop/migrate.list" <<'EOF2'
import sys
p, m = sys.argv[1], open(sys.argv[2]).read().rstrip("\n")
s = open(p).read()
assert "@MIGRATE_LIST@" in s
open(p, "w").write(s.replace("@MIGRATE_LIST@", m))
EOF2
done
sed -i "s/@VERSION@/$V/" "$P/DEBIAN/control"
find "$P" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$P" -type d -exec chmod 755 {} +
find "$P" -type f -exec chmod 644 {} +
chmod 755 "$P"/DEBIAN/{preinst,postinst,postrm} "$P"/usr/bin/*
if grep -rIl "/usr/local/" "$P" --exclude-dir=DEBIAN; then echo "包内仍引用 /usr/local（见上）" >&2; exit 1; fi
( cd "$P" && find . -path ./DEBIAN -prune -o -type f -print | sed 's|^\./||' | LC_ALL=C sort | xargs -d '\n' md5sum ) > "$P/DEBIAN/md5sums"
build "$P" "$OUT/fuxi-desktop_${V}_all.deb"

echo "== fuxi-firmware $V"
FW="$B/work/firmware-pkg"
if [ "${FUXI_INCLUDE_FIRMWARE:-0}" = 1 ] && [ -d "$FW/root/usr/lib/firmware" ]; then
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
if [ "${FUXI_APT_SUPPORT_ONLY:-0}" = 1 ]; then
	echo "   仅构建设备支持包；不要求历史内核产物"
else
while read -r name rest; do
	case "$name" in ''|\#*) continue ;; *[!a-z0-9.]*|.*) echo "非法内核构建名: $name" >&2; exit 1 ;; esac
	found=0
	for k in "$B/work/kbuild/$name"/linux-image-*-fuxi-*_arm64.deb; do
		[ -e "$k" ] || continue
		found=1
		ln -f "$k" "$OUT/" 2>/dev/null || cp "$k" "$OUT/"
		echo "   $(basename "$k")"
	done
	[ "$found" = 1 ] || { echo "缺少已验收内核包 $name" >&2; exit 1; }
done < "$B/packaging/apt/kernels.list"
fi
echo "== 发布不可变快照"
cp "$B/packaging/apt/fuxi-pin" "$OUT/"
PUBLISH=()
[ "${FUXI_APT_LOCAL:-0}" != 1 ] || PUBLISH+=(--local)
[ "${FUXI_APT_ADOPT:-0}" != 1 ] || PUBLISH+=(--adopt)
python3 "$B/scripts/fuxi-apt-publish.py" "$OUT" "$DEST" "${PUBLISH[@]}"
echo "   $(grep -c '^Package:' "$DEST/Packages") 个包 → $DEST"
