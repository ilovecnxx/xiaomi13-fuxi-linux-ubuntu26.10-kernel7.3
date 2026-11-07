#!/bin/bash
# ABL → 主线 U-Boot（链式加载）镜像，放 recovery_b 试验；回滚：fuxi-test-recovery.sh work/kbuild/abl-fuxi-shim7.img
#   panic 不停住（关 PANIC_HANG）：panic → 复位 → 回 Android，和"一直卡 logo"区分开
#   打包同 fuxi-build-abl.sh：boot.img v1，kernel 字段 = gzip(u-boot-nodtb.bin + fuxi 主线 DTB) + 厂商 fuxi DTB 诱饵
#   U-Boot 优先用自带 DTB（arch/arm/mach-snapdragon/board.c），ABL 传入的诱饵 DTB 不用
#   配置：qcom_defconfig + qcom-phone.config（主机工具不用 libcrypto：本机 OpenSSL 没有 ENGINE），再关掉所有会写盘的功能：
#     EFI capsule（ABL 引导时会写当前槽 boot 分区 = Android）、UMS（把 LUN0 读写暴露给 PC）、
#     fastboot flash、EFI 变量文件存储（往它认为的 ESP 写 ubootefi.var）；环境换成 scripts/fuxi-uboot/fuxi.env
#   唯一的写盘：env 里受保护的启动计数块（/boot/fuxi/bootcount 的 4 KiB 数据块，见 packaging/fuxi-device/root/usr/sbin/fuxi-bootcount）
# 用法: scripts/fuxi-build-uboot.sh [名字，默认 uboot0]  （uboot2 起 booti/extlinux 实测进桌面，11-07）   源码：FUXI_UBOOT=~/.cache/u-boot
#       FUXI_UBOOT_DTB=<主线 dtb 路径>（默认标准内核构建的板级 DTB）
set -euo pipefail
B="$(cd "$(dirname "$0")/.." && pwd)"
NAME="${1:-uboot0}"
U="${FUXI_UBOOT:-$HOME/.cache/u-boot}"
DTB="${FUXI_UBOOT_DTB:-$B/work/kbuild/standard-out/arch/arm64/boot/dts/qcom/sm8550-xiaomi-fuxi.dtb}"
BAIT_DTB="$B/build/edk2-msm/Platform/Xiaomi/sm8550/FdtBlob_compat/fuxi.dtb"
O="$B/work/kbuild/uboot-out"
L="$B/work/claude-logs/uboot-build.log"
for f in "$U/Makefile" "$DTB" "$BAIT_DTB" "$B/scripts/fuxi-uboot/fuxi.env"; do [ -f "$f" ] || { echo "缺少 $f"; exit 1; }; done
M=(make -C "$U" O="$O" CROSS_COMPILE=aarch64-linux-gnu- -j"$(nproc)")
mkdir -p "$O"
cp "$B/scripts/fuxi-uboot/fuxi.env" "$O/fuxi.env"
{
  "${M[@]}" qcom_defconfig qcom-phone.config
  "$U/scripts/config" --file "$O/.config" \
    --set-str ENV_DEFAULT_ENV_TEXT_FILE "$O/fuxi.env" \
    -d EFI_CAPSULE_ON_DISK -d EFI_CAPSULE_ON_DISK_EARLY -d EFI_RUNTIME_UPDATE_CAPSULE -d EFI_CAPSULE_FIRMWARE_RAW \
    -d EFI_CAPSULE_FIRMWARE_FIT -d EFI_CAPSULE_FIRMWARE_MANAGEMENT -d EFI_HAVE_CAPSULE_SUPPORT \
    -d CMD_USB_MASS_STORAGE -d USB_FUNCTION_MASS_STORAGE -d FASTBOOT_FLASH -d FASTBOOT_OEM_RUN \
    -d EFI_VARIABLE_FILE_STORE -e EFI_VARIABLE_NO_STORE \
    -d CMD_SAVEENV -d CMD_GPT -d CMD_MBR -d CMD_ERASEENV \
    -d TOOLS_MKEFICAPSULE -d TOOLS_KWBIMAGE -d TOOLS_LIBCRYPTO \
    -d PANIC_HANG -e CMD_SYSBOOT
  "${M[@]}" olddefconfig
  "${M[@]}" u-boot-nodtb.bin
} > "$L" 2>&1 || { grep -m8 -iE 'error|错误' "$L"; tail -5 "$L"; exit 1; }
# 写盘相关的选项必须真的关掉
bad=$(grep -E '^CONFIG_(EFI_CAPSULE_ON_DISK|EFI_RUNTIME_UPDATE_CAPSULE|EFI_HAVE_CAPSULE_SUPPORT|CMD_USB_MASS_STORAGE|FASTBOOT_FLASH|EFI_VARIABLE_FILE_STORE|CMD_SAVEENV|ENV_IS_IN_[A-Z_]+)=y' "$O/.config" || true)
[ -z "$bad" ] || { echo "[uboot] 这些写盘功能仍然打开：$bad"; exit 1; }
grep -q '^CONFIG_EFI_VARIABLE_NO_STORE=y' "$O/.config" || { echo "[uboot] EFI_VARIABLE_NO_STORE 没生效"; exit 1; }
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
# 日常 DTB 的 simple-framebuffer 是 disabled（Linux 交给 DRM）；U-Boot 用的副本打开它
cp "$DTB" "$W/fuxi.dtb"
fdtput -t s "$W/fuxi.dtb" /chosen/framebuffer@b8000000 status okay
# sm8550.dtsi 的 memory@a0000000 是大小 0 的占位节点（等 bootloader 填）。U-Boot 只读第一个 /memory 节点
# （mach-snapdragon/dram.c），读到它会退到 SMEM 解析 → uboot0 卡 MI logo（11-07）。真实内存表在 memory@80000000
fdtput -r "$W/fuxi.dtb" /memory@a0000000
cat "$O/u-boot-nodtb.bin" "$W/fuxi.dtb" | gzip -9n > "$W/u-boot.gz"
cat "$W/u-boot.gz" "$BAIT_DTB" > "$W/kernel-dtb"
: > "$W/ramdisk"
OUT="$B/work/kbuild/abl-fuxi-$NAME.img"
python3 "$B/build/edk2-msm/tools/mkbootimg.py" \
  --header_version 1 --os_version 13.0.0 --os_patch_level 2022-11 \
  --kernel "$W/kernel-dtb" --ramdisk "$W/ramdisk" \
  --base 0x0 --kernel_offset 0x00000000 --ramdisk_offset 0x01000000 --tags_offset 0x00000100 \
  --pagesize 4096 --cmdline "" -o "$OUT"
cp "$O/.config" "${OUT%.img}.config"; cp "$W/fuxi.dtb" "${OUT%.img}.dtb"
echo "[ok] $OUT $(stat -c %s "$OUT") bytes; U-Boot $(git -C "$U" log -1 --format=%h) $(sha256sum "$OUT" | cut -c1-16)"
