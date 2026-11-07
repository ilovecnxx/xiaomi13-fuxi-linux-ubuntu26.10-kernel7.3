#!/usr/bin/env bash
# 在小米 13（fuxi，Ubuntu 26.10 arm64）上安装 Denial 桌面：用上游已签名的预编译 arm64 deb。
#
#   scripts 里原来的 01..07 是「在手机上从源码编 Flutter engine + Rust」的方案，
#   2026-10-31 因 OOM 在 ninja 648/4525 被内核杀掉（见 phase2-engine.log），已放弃：
#   Denial v0.6.2 起上游直接发布 arm64 deb（Pacman/APT/DNF 三套仓库 + 直接下载）。
#
# 主机侧运行。做的事：
#   1. 下载 denial / denial-flutter-engine 的 arm64 deb 和 SHA256SUMS 到 artifacts/；
#   2. 用 SHA256SUMS 校验；
#   3. scp 到手机并用 apt 安装（缺的 xserver-xorg-core 由 apt 补）；
#   4. 把 /etc/denial/session.conf 设为手机形态 DENIAL_SHELL_PROFILE=mobile；
#   5. 部署 denial.service（tty7 会话，与 phosh.service / plasmamobile.service 互斥）
#      和 fuxi-session（新增 denial 切换项）；
#   6. 跑 denial-session --check（只读，不启动会话）。
# 不会启动 Denial、不会停 Phosh、不碰内核/引导。
#
# 用法：
#   work/denial-arm64/install-prebuilt.sh
#   FUXI_HOST=<无线 IP> work/denial-arm64/install-prebuilt.sh
#
# 装完切换会话（会断开当前图形会话，约 90 s，phoc 对 SIGTERM 不敏感）：
#   scripts/fuxi-ssh.sh 'fuxi-session denial'    # Phosh → Denial
#   scripts/fuxi-ssh.sh 'fuxi-session phosh'     # Denial → Phosh
set -euo pipefail

B="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
VER=0.6.2
REL=1
ART="$B/work/denial-arm64/artifacts"
HOST="${FUXI_HOST:-10.42.0.2}"
KEY="$B/work/ssh/fuxi_root"
KNOWN="$B/work/ssh/known_hosts"
URL="https://github.com/denialwm/denial/releases/download/v${VER}"

DEBS=(
  "denial-flutter-engine_${VER}-${REL}_arm64.deb"
  "denial_${VER}-${REL}_arm64.deb"
)

ssh_phone() {
  ssh -i "$KEY" -o UserKnownHostsFile="$KNOWN" -o StrictHostKeyChecking=accept-new \
    -o ConnectTimeout=10 "root@$HOST" "$@"
}
scp_phone() {
  scp -i "$KEY" -o UserKnownHostsFile="$KNOWN" -o StrictHostKeyChecking=accept-new \
    -o ConnectTimeout=10 "$@"
}

mkdir -p "$ART"
cd "$ART"

echo "== 下载 =="
[[ -f SHA256SUMS ]] || curl -fsSL -o SHA256SUMS "$URL/SHA256SUMS"
for f in "${DEBS[@]}"; do
  [[ -f "$f" ]] || curl -fsSL -o "$f" "$URL/$f"
  echo "   $f  $(stat -c%s "$f") bytes"
done

echo "== 校验（SHA256SUMS）=="
for f in "${DEBS[@]}"; do
  want="$(awk -v p="./$f" '$2 == p { print $1 }' SHA256SUMS)"
  [[ -n "$want" ]] || { echo "SHA256SUMS 里找不到 $f" >&2; exit 1; }
  got="$(sha256sum "$f" | cut -d' ' -f1)"
  [[ "$want" == "$got" ]] || { echo "校验失败: $f" >&2; exit 1; }
  echo "   ok  $f"
done

echo "== 传到手机并安装 =="
scp_phone "${DEBS[@]}" "root@$HOST:/root/"
ssh_phone 'cd /root && DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ./denial-flutter-engine_'"$VER-$REL"'_arm64.deb ./denial_'"$VER-$REL"'_arm64.deb'

echo "== 手机形态（mobile shell）=="
ssh_phone 'if grep -q "^DENIAL_SHELL_PROFILE=mobile" /etc/denial/session.conf 2>/dev/null; then
    echo "   已经是 mobile";
  else
    cp -a /etc/denial/session.conf /etc/denial/session.conf.orig-$(date +%Y%m%d) 2>/dev/null || true
    printf "\n# Xiaomi 13 fuxi: phone shell (mobile) default\nDENIAL_SHELL_PROFILE=mobile\n" >> /etc/denial/session.conf
    echo "   已写入 DENIAL_SHELL_PROFILE=mobile";
  fi'

echo "== 会话 unit + 切换脚本 =="
# 由 fuxi-desktop 包提供（/usr/lib/systemd/system/denial.service、/usr/bin/fuxi-session）；见 scripts/fuxi-build-pkgs.sh
ssh_phone 'dpkg -s fuxi-desktop >/dev/null 2>&1 || { echo "   缺 fuxi-desktop：先在手机上 apt install fuxi-desktop（本地源 /srv/fuxi-apt）" >&2; exit 1; }; systemctl daemon-reload; systemctl reset-failed denial.service phosh.service 2>/dev/null || true'

echo "== 只读检查（不启动会话）=="
ssh_phone 'dpkg-query -W -f="    ${Package} ${Version} (${Architecture})\n" denial denial-flutter-engine; echo; /usr/bin/denial-session --check'

cat <<'EOF'

OK。Denial 已安装，但当前会话没有改动（如果之前是 Phosh，仍然是 Phosh）。
切换 / 回退（会显示在手机屏幕上）：
  scripts/fuxi-ssh.sh 'fuxi-session denial'   # 进 Denial（停 Phosh）
  scripts/fuxi-ssh.sh 'fuxi-session phosh'    # 回 Phosh（停 Denial）
EOF
