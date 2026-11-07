#!/usr/bin/env bash
# 从手机只读拉取 fuxi 正在用的固件，作为 fuxi-firmware 包的内容（专有固件，不进 git：work/firmware-pkg/ 已忽略）。
#   用法：scripts/fuxi-firmware-pull.sh            → work/firmware-pkg/{root/usr/lib/firmware/…, PROVENANCE.txt}
# 选取规则（只取内核/服务实际按名加载的文件；*.android / *.xiaomi / *.upstream 等对照备份不要）：
#   qcom/sm8550/xiaomi/fuxi/**（modem、modem_pr、ipa_fws、adsp、a740_zap…）
#   qcom/a740_sqe.fw qcom/gmu_gen70200.bin qcom/sm8550/Xiaomi-13-tplg.bin
#   ath12k/WCN7850/hw2.0/{amss,board-2,m3}.bin   qca/hmt*（不含 .android）
# PROVENANCE.txt：每个文件的 sha256 与来源。来源判定：只读挂载 Android 分区 modem_a/modem_b/dsp_a/bluetooth_a
#   （vfat ro / ext4 ro,noload，用完卸载），哈希相同记 android:<分区>:<路径>；再比原厂包 NON-HLOS.bin，相同记
#   stock309:NON-HLOS:<路径>；都不同记 local（本地改过或来自 /odm、上游）。
set -euo pipefail
B="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$B/work/firmware-pkg"
SSH="$B/scripts/fuxi-ssh.sh"
rm -rf "$OUT.new"
mkdir -p "$OUT.new/root/usr/lib/firmware"

LIST='cd /lib/firmware && { find qcom/sm8550/xiaomi/fuxi -type f;
  ls qcom/a740_sqe.fw qcom/gmu_gen70200.bin qcom/sm8550/Xiaomi-13-tplg.bin;
  ls ath12k/WCN7850/hw2.0/amss.bin ath12k/WCN7850/hw2.0/board-2.bin ath12k/WCN7850/hw2.0/m3.bin;
  find qca -maxdepth 1 -type f -name "hmt*" ! -name "*.android"; } | LC_ALL=C sort'

echo "== 拉取固件（tar over ssh）"
"$SSH" "set -e; $LIST > /tmp/fuxi-fw.list; cd /lib/firmware && tar -cf - -T /tmp/fuxi-fw.list" \
	| tar -C "$OUT.new/root/usr/lib/firmware" -xf - --no-same-owner
n=$(find "$OUT.new/root/usr/lib/firmware" -type f | wc -l)
echo "   $n 个文件，$(du -sh "$OUT.new/root/usr/lib/firmware" | cut -f1)"

echo "== 来源判定（只读挂载 Android 分区）"
"$SSH" 'set -e; LANG=C; R=/run/fuxi-fwsrc; P="modem_a modem_b dsp_a bluetooth_a"
	# 安全：不 cd 进挂载点（否则卸载时 busy）；清理只用 umount + rmdir，绝不 rm -r（挂载点下是 Android 分区）
	cleanup() { for p in $P; do if mountpoint -q $R/$p; then umount $R/$p || { echo "umount $R/$p 失败，保持挂载（只读）" >&2; return 1; }; fi; rmdir $R/$p 2>/dev/null || true; done; rmdir $R 2>/dev/null || true; }
	trap cleanup EXIT
	for p in $P; do m=$R/$p; mkdir -p $m; o=ro; [ "$(blkid -s TYPE -o value /dev/disk/by-partlabel/$p)" = ext4 ] && o=ro,noload; mount -o $o /dev/disk/by-partlabel/$p $m; done
	for p in $P; do find $R/$p -type f -exec sha256sum {} + | sed "s|  $R/$p/| $p:|"; done' > "$OUT.new/partitions.sha256"
# 本地有原厂包（OS3.0.309，见 work/stock/）就再比一遍：手机 Android 分区是第三方 307，和它不同的文件可能来自 309。
# 用 mtools 读 NON-HLOS.bin（vfat），不需要 root、不挂载；哈希相同记 stock309:NON-HLOS:<路径>
STOCK="${FUXI_STOCK_DIR:-$B/work/stock/fuxi_images_OS3.0.309.0.WMCCNXM_16.0/images}"
: > "$OUT.new/stock.sha256"
if [ -f "$STOCK/NON-HLOS.bin" ] && command -v mcopy >/dev/null; then
	echo "== 来源判定（原厂包 $STOCK/NON-HLOS.bin）"
	T=$(mktemp -d)
	mcopy -s -n -i "$STOCK/NON-HLOS.bin" ::/ "$T/"
	( cd "$T" && find . -type f -print0 | xargs -0 sha256sum ) | sed 's|  \./| stock309:NON-HLOS:|' > "$OUT.new/stock.sha256"
	rm -rf "$T"
fi
( cd "$OUT.new/root/usr/lib/firmware" && find . -type f | sed 's|^\./||' | LC_ALL=C sort | xargs -d '\n' sha256sum ) > "$OUT.new/files.sha256"
# 优先级：手机 Android 分区 > 原厂包 > local
awk 'FILENAME == ARGV[1] { if (!($1 in src)) src[$1] = "android:" $2; next }
     FILENAME == ARGV[2] { if (!($1 in src)) src[$1] = $2; next }
     { s = ($1 in src) ? src[$1] : "local"; print $1, $2, s }' \
	"$OUT.new/partitions.sha256" "$OUT.new/stock.sha256" "$OUT.new/files.sha256" > "$OUT.new/PROVENANCE.txt"
rm -f "$OUT.new/partitions.sha256" "$OUT.new/stock.sha256" "$OUT.new/files.sha256"
awk '{ split($3, a, ":"); c[a[1] ":" a[2]]++ } END { for (k in c) printf "   %5d  %s\n", c[k], k }' "$OUT.new/PROVENANCE.txt" | sort -k2

rm -rf "$OUT"
mv "$OUT.new" "$OUT"
echo "== 完成：$OUT（PROVENANCE.txt 列出每个文件的 sha256 与来源）"
