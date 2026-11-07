#!/bin/bash
# 引导层（shim）镜像：放 recovery_b，ABL 引导它，它从根分区 /boot/fuxi/ 选内核并 kexec 过去。
#   DTB：sm8550-xiaomi-fuxi-shim（不起 ADSP/基带/PCIe/蓝牙，irqchip.gicv3_nolpi=1，panic=10）
#   initramfs：scripts/legacy/fuxi-shim/init + busybox + 静态 kexec（kexec-tools 2.0.28）
# 用法: scripts/legacy/fuxi-build-shim.sh [名字，默认 shim1]   产物: work/kbuild/abl-fuxi-<名字>.img
set -euo pipefail
B="$(cd "$(dirname "$0")/../.." && pwd)"
NAME="${1:-shim1}"
BB="$B/build/initramfs/bin/busybox"
KX="$B/work/kbuild/kexec-static"
INIT="$B/scripts/legacy/fuxi-shim/init"
for f in "$BB" "$KX" "$INIT"; do [ -f "$f" ] || { echo "缺少 $f"; exit 1; }; done
case "$B" in *' '*) echo "路径含空格，INITRAMFS_SOURCE 不支持"; exit 1 ;; esac
LIST="$B/work/kbuild/shim-initramfs.list"
cat > "$LIST" <<LIST
# 由 scripts/legacy/fuxi-build-shim.sh 生成
dir /dev 0755 0 0
nod /dev/console 0600 0 0 c 5 1
dir /bin 0755 0 0
dir /proc 0755 0 0
dir /sys 0755 0 0
dir /tmp 0755 0 0
dir /newroot 0755 0 0
file /init $INIT 0755 0 0
file /bin/busybox $BB 0755 0 0
file /bin/kexec $KX 0755 0 0
LIST
FUXI_DTB=sm8550-xiaomi-fuxi-shim FUXI_PM=0 FUXI_EXTRA_CFG="-e KEXEC -e KEXEC_FILE" FUXI_INITRAMFS="$LIST" \
  "$B/scripts/fuxi-build-abl.sh" "$NAME"
