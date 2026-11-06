#!/bin/bash
# 标准内核 Debian 包（U-Boot + extlinux 路径用）：收敛树 fuxi/consolidate-7.4 + arch/arm64/configs/fuxi_defconfig，
# 树外构建（O=），不内建 initramfs、不内建 cmdline —— initrd 由手机上的 initramfs-tools 生成，
# cmdline 来自 /etc/kernel/cmdline（root=PARTUUID=…），都由 fuxi-kernel-install 写进 extlinux 条目。
# 和 fuxi-build-abl.sh FUXI_DEB=1 的区别：那条路用 build/linux 未跟踪的 .config + 变体叠加 + 内建 initramfs（deb1/deb2）。
# 用法: scripts/fuxi-build-deb.sh <名字>      → work/kbuild/<名字>/linux-image-<版本>-fuxi-<名字>_*.deb
#   FUXI_KTREE=<源码树>（默认 ~/.cache/fuxi-cons）  FUXI_KOUT=<构建目录>（默认 ~/.cache/fuxi-cons-out）
#   FUXI_EXTRA_CFG="-e X -d Y"：在 fuxi_defconfig 上再改（scripts/config 语法）
set -euo pipefail
B="$(cd "$(dirname "$0")/.." && pwd)"
NAME="${1:?用法: fuxi-build-deb.sh <名字>}"
case "$NAME" in *[!a-z0-9.]*) echo "名字只能用小写字母、数字、点"; exit 1 ;; esac
K="${FUXI_KTREE:-$HOME/.cache/fuxi-cons}"
O="${FUXI_KOUT:-$HOME/.cache/fuxi-cons-out}"
L="$B/work/claude-logs/kbuild-deb-$NAME.log"
KO="$B/work/kbuild/$NAME"
[ -f "$K/arch/arm64/configs/fuxi_defconfig" ] || { echo "$K 没有 fuxi_defconfig"; exit 1; }
[ ! -e "$KO" ] || { echo "$KO 已存在，换个名字或先挪走"; exit 1; }
mkdir -p "$O" "$(dirname "$L")"
exec 9>"$O/.fuxi-build.lock"
flock -n 9 || { echo "另一个构建正在用 $O"; exit 1; }
M=(make -C "$K" O="$O" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- -j"$(nproc)" LOCALVERSION="-fuxi-$NAME")
{
  "${M[@]}" fuxi_defconfig
  # 防御：defconfig 以后被谁改回内建 initramfs / ABL 直启也不行
  "$K/scripts/config" --file "$O/.config" --set-str INITRAMFS_SOURCE "" --set-str CMDLINE "" \
    -d FUXI_FORCE_BUILTIN_DTB -e BLK_DEV_INITRD -e RD_ZSTD ${FUXI_EXTRA_CFG:-}
  "${M[@]}" olddefconfig
} > "$L" 2>&1 || { tail -5 "$L"; exit 1; }
for c in 'CONFIG_INITRAMFS_SOURCE=""' 'CONFIG_CMDLINE=""' 'CONFIG_BLK_DEV_INITRD=y' 'CONFIG_RD_ZSTD=y' 'CONFIG_EXT4_FS=y'; do
  grep -qx "$c" "$O/.config" || { echo "[config] 缺 $c"; exit 1; }
done
! grep -q '^CONFIG_FUXI_FORCE_BUILTIN_DTB=y' "$O/.config" || { echo "[config] FUXI_FORCE_BUILTIN_DTB 没关掉"; exit 1; }
KV=$("${M[@]}" -s kernelversion)
KR=$("${M[@]}" -s kernelrelease)
echo "[deb] $KR：Image.gz dtbs + builddeb + dpkg-deb（日志 $L）"
P="$O/debian/linux-image-$KR"
{
  "${M[@]}" Image.gz dtbs
  mkdir -p "$O/debian"; rm -rf "$P"
  # 内核自带的 builddeb 填包内容和维护脚本（run-parts /etc/kernel/post{inst,rm}.d）；主机没有 debhelper，control 自己写
  "${M[@]}" run-command KBUILD_RUN_COMMAND="+\$(srctree)/scripts/package/builddeb linux-image-$KR"
  cat > "$P/DEBIAN/control" <<EOF
Package: linux-image-$KR
Version: $KV-$NAME
Architecture: arm64
Maintainer: fuxi <fuxi@localhost>
Section: kernel
Priority: optional
Installed-Size: $(du -sk --exclude=DEBIAN "$P" | cut -f1)
Description: Linux kernel $KR for Xiaomi 13 (fuxi)
 Image + DTBs (fuxi_defconfig, no built-in initramfs); /etc/kernel/postinst.d/zz-fuxi installs the /boot/fuxi entry.
EOF
  mkdir -p "$KO"
  dpkg-deb --root-owner-group -Zxz -b "$P" "$KO/linux-image-${KR}_$KV-${NAME}_arm64.deb"
} >> "$L" 2>&1 || { grep -m5 -B2 -A3 -E 'error|错误' "$L"; tail -5 "$L"; exit 1; }
[ -f "$P/usr/lib/linux-image-$KR/qcom/sm8550-xiaomi-fuxi.dtb" ] || { echo "[deb] 包里没有 qcom/sm8550-xiaomi-fuxi.dtb"; exit 1; }
rm -rf "$P"
cp "$O/.config" "$KO/config"
echo "$(git -C "$K" rev-parse --short HEAD)$(git -C "$K" diff --quiet HEAD || echo -dirty) $(git -C "$K" rev-parse --abbrev-ref HEAD)" > "$KO/source"
( cd "$KO" && sha256sum ./*.deb config > SHA256SUMS )
ls -la "$KO"
