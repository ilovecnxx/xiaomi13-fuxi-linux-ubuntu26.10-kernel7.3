#!/usr/bin/env bash
# 抓一张手机屏幕 → 裁成真实分辨率 → 存回电脑。
# 用法: scripts/fuxi-shot.sh [文件名前缀]
#   OUTDIR 可覆盖保存目录（默认 XDG 图片/手机截图）
# 依赖: 手机可 SSH（scripts/fuxi-ssh.sh）、手机内有 grim、主机有 ffmpeg。
set -euo pipefail
B="$(cd "$(dirname "$0")/.." && pwd)"
PICTURES="$(xdg-user-dir PICTURES 2>/dev/null || echo "$HOME/Pictures")"
BASE="${OUTDIR:-$PICTURES/手机截图}"
NAME="${1:-shot-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$BASE"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

REMOTE="/tmp/fuxi-shot-$$.png"
# 手机端：以 fuxi 用户进入当前图形会话截图。
# socket 名不固定：Phosh 用 wayland-0，Denial 用 wayland-1（还有 phoc 被 SIGKILL 后的死 socket），
# 所以按“真的有进程在监听”来挑（2026-11-07）。
WD="$("$B/scripts/fuxi-ssh.sh" 'for s in /run/user/1000/wayland-*; do
    case "$s" in *.lock) continue ;; esac
    [ -S "$s" ] || continue
    if command -v ss >/dev/null 2>&1; then
      ss -xl 2>/dev/null | grep -qF "$s" || continue
    fi
    basename "$s"; break
  done')"
WD="${WD:-wayland-0}"
if ! "$B/scripts/fuxi-ssh.sh" "sudo -u fuxi XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY='$WD' grim '$REMOTE'"; then
  echo "ERROR: 手机端 grim 截图失败（手机是否在线 / 图形会话是否在跑？socket=$WD）" >&2
  exit 1
fi
"$B/scripts/fuxi-ssh.sh" "cat '$REMOTE'; rm -f '$REMOTE'" > "$TMP/raw.png"

# 截图尺寸不应超过面板真实分辨率：Phosh 的合成缓冲可能是 2700x6000（面板 2.5 倍），
# 真实画面在左上角；Denial 直接给 1080x2400 左右，不需要裁（2026-11-07）。
MODE=$("$B/scripts/fuxi-ssh.sh" "cat /sys/class/drm/card*-*/modes 2>/dev/null | grep -m1 -E '^[0-9]+x[0-9]+$'" | tr -d '[:space:]')
MODE="${MODE:-1080x2400}"
MODEW="${MODE%x*}"; MODEH="${MODE#*x}"
read -r SHOTW SHOTH < <(ffprobe -v error -select_streams v:0 \
  -show_entries stream=width,height -of csv=p=0 "$TMP/raw.png" | tr ',' ' ')
CROPW=$(( SHOTW < MODEW ? SHOTW : MODEW ))
CROPH=$(( SHOTH < MODEH ? SHOTH : MODEH ))
if [ "$CROPW" -eq "$SHOTW" ] && [ "$CROPH" -eq "$SHOTH" ]; then
  cp "$TMP/raw.png" "$BASE/$NAME.png"
else
  ffmpeg -loglevel error -y -i "$TMP/raw.png" -vf "crop=$CROPW:$CROPH:0:0" "$BASE/$NAME.png"
fi
echo "$BASE/$NAME.png"
