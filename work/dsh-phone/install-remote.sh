#!/bin/bash
# 手机端安装与配置（由 fuxi-dsh-deploy.sh 通过 ssh bash -s 执行）。
# 配置通过环境变量传入：BIND PORT DSH_VERSION API_KEY NATIVE NPM_REG IDLE_MIN
# 权限：RUN_AS=fuxi（默认，普通用户）或 RUN_AS=root。
#   - 装 Node/npm 始终用 root（apt/全局 npm）。
#   - 日常 DSH 服务与桌面 App 以 RUN_AS 用户的“用户级 systemd”运行，按需启停；
#   - 服务不设开机自启；点桌面图标才 systemctl --user start；
#   - 闲置 IDLE_MIN 分钟后由该用户的用户级 timer 自动停服，闲置时不占内存/电。
set -euo pipefail

: "${BIND:=127.0.0.1}"
: "${PORT:=3080}"
: "${NATIVE:=0}"
: "${NPM_REG:=https://registry.npmmirror.com}"
: "${IDLE_MIN:=10}"
API_KEY="${DSH_API_KEY_IN:-}"   # 由部署端经 stdin 传入，不落命令行
RUN_AS="${RUN_AS:-${DSH_PHONE_USER:-fuxi}}"
USER_NAME="$RUN_AS"
USER_UID="$(id -u "$USER_NAME")"
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
XDG_RUNTIME="/run/user/${USER_UID}"

# 以 RUN_AS 身份执行命令（root 直接执行；普通用户经 sudo -u 并带用户总线环境）
run_user() {
  if [ "$USER_UID" = "0" ]; then
    env XDG_RUNTIME_DIR="$XDG_RUNTIME" HOME="$HOME_DIR" "$@"
  else
    sudo -u "$USER_NAME" env XDG_RUNTIME_DIR="$XDG_RUNTIME" HOME="$HOME_DIR" "$@"
  fi
}
# 确保该用户有持久用户总线（linger），否则用户级服务随会话退出即停
if [ "$USER_UID" = "0" ]; then
  loginctl enable-linger root 2>/dev/null || true
else
  loginctl enable-linger "$USER_NAME" 2>/dev/null || true
fi

echo "-- 架构检查"
ARCH="$(dpkg --print-architecture)"
[ "$ARCH" = "arm64" ] || { echo "非 arm64 架构: $ARCH，中止" >&2; exit 1; }

echo "-- Node.js / npm"
if ! command -v node >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y nodejs npm
fi
node --version
npm --version

echo "-- 安装 @deepseek-ai/dsh@${DSH_VERSION}（固定版本，幂等重装；优先 CN 镜像）"
if [ "${SKIP_INSTALL:-0}" = "1" ]; then
  echo "  跳过 npm 安装（SKIP_INSTALL=1），沿用已安装版本"
else
  npm install -g "@deepseek-ai/dsh@${DSH_VERSION}" --omit=dev --registry "$NPM_REG" \
    || npm install -g "@deepseek-ai/dsh@${DSH_VERSION}" --omit=dev --registry https://registry.npmjs.org
fi
DSH_BIN="$(command -v dsh || echo /usr/local/bin/dsh)"
[ -x "$DSH_BIN" ] || { echo "找不到 dsh 可执行文件" >&2; exit 1; }
echo "dsh: $DSH_BIN ($("$DSH_BIN" --version 2>/dev/null || echo v?))"

echo "-- UI 方案：原版 DeepSeek Harness UI + 前台 Chromium --app（不注入皮肤，不影响 DSH 更新）"
set +e
# 如果之前注入过安卓皮肤，主动撤销，恢复原版 index.html，并移除皮肤文件（幂等）
DIST="$("$DSH_BIN" --profile web --print-frontend-dist 2>/dev/null || true)"
[ -n "$DIST" ] && [ -d "$DIST" ] || DIST="$(find / -path '*dsh-web-frontend/dist' -type d 2>/dev/null | head -1)"
if [ -n "$DIST" ] && [ -f "$DIST/index.html" ]; then
  # 恢复最近一次注入前备份
  for f in $(ls -1t "$DIST"/index.html.bak.* 2>/dev/null); do
    if grep -q 'dsh-android-theme\|deepseek-android' "$DIST/index.html" 2>/dev/null; then
      cp -a "$f" "$DIST/index.html" && echo "  已恢复原版 index.html <- $f"
    fi
    break
  done
  # 若仍残留注入标记则剥离
  if grep -q 'dsh-android-theme\|deepseek-android' "$DIST/index.html" 2>/dev/null; then
    sed -i '/dsh-android-theme/d; /deepseek-android\.\(css\|js\)/d' "$DIST/index.html" && echo "  已剥离残留皮肤引用"
  fi
  rm -f "$DIST/deepseek-android.css" "$DIST/deepseek-android.js" 2>/dev/null && echo "  已移除皮肤文件"
fi

echo "-- 探测 Chromium（前台浏览器）"
# Chromium 私有库路径（/opt/chromium 部署常见）
CHROMIUM_BIN=""
for c in /opt/chromium/root/usr/lib/chromium/chromium /usr/bin/chromium /usr/bin/chromium-browser /usr/bin/google-chrome; do
  [ -x "$c" ] && { CHROMIUM_BIN="$c"; break; }
done
CHROMIUM_LIBS=""
if [ -n "$CHROMIUM_BIN" ] && [ -d /opt/chromium/privatelib ]; then
  CHROMIUM_LIBS="/opt/chromium/privatelib:/opt/chromium/root/usr/lib/chromium"
fi
echo "Chromium: ${CHROMIUM_BIN:-未找到（将退回系统浏览器）}"
set -e

echo "-- 安装桌面启动器"
install -m 0755 /root/dsh-phone/dsh-web-window.py /usr/local/bin/dsh-web-window.legacy 2>/dev/null || true
cat > /usr/local/bin/dsh-web-open <<'OPEN'
#!/bin/bash
# 桌面 App 入口：按需拉起 RUN_AS 用户的 DSH 用户级服务并打开原生窗口。
set -o pipefail
RUN_AS="@RUN_AS@"; RUN_UID="@RUN_UID@"; RUN_HOME="@RUN_HOME@"; PORT="@PORT@"; BIND="@BIND@"
STATE="$RUN_HOME/.local/state/dsh-web"
mkdir -p "$STATE" 2>/dev/null || true
date +%s > "$STATE/last-activity" 2>/dev/null || true
PROBE_HOST="127.0.0.1"; case "$BIND" in 0.0.0.0|"") PROBE_HOST="127.0.0.1";; *) PROBE_HOST="$BIND";; esac
port_up() { python3 -c "import socket,sys; s=socket.socket(); s.settimeout(1)
try: s.connect(('$PROBE_HOST',$PORT)); sys.exit(0)
except Exception: sys.exit(1)" 2>/dev/null; }
user_ctl() {
  if [ "$(id -un)" = "$RUN_AS" ]; then
    XDG_RUNTIME_DIR="/run/user/$RUN_UID" HOME="$RUN_HOME" systemctl --user "$@"
  else
    # 普通用户 → 其他用户（含 root）：用 sudo/runuser 切到目标用户并带其用户总线环境
    if command -v sudo >/dev/null 2>&1 && sudo -n -u "$RUN_AS" true 2>/dev/null; then
      sudo -n -u "$RUN_AS" env XDG_RUNTIME_DIR="/run/user/$RUN_UID" HOME="$RUN_HOME" systemctl --user "$@"
    elif command -v runuser >/dev/null 2>&1 && [ "$(id -u)" = "0" ]; then
      runuser -u "$RUN_AS" -- env XDG_RUNTIME_DIR="/run/user/$RUN_UID" HOME="$RUN_HOME" systemctl --user "$@"
    else
      return 1
    fi
  fi
}
if ! port_up; then
  user_ctl start dsh-web.service 2>/dev/null || true
  for i in $(seq 1 50); do port_up && break; sleep 0.4; done
fi
URLHOST="127.0.0.1"; case "$BIND" in 0.0.0.0|"") URLHOST="127.0.0.1";; *) URLHOST="$BIND";; esac
URL="http://$URLHOST:$PORT/"
# 等待 url.txt 被 launcher 写入最新 token（服务刚起时该文件可能短暂为空）
for i in $(seq 1 25); do
  if [ -s "$STATE/url.txt" ] && grep -q "token=" "$STATE/url.txt" 2>/dev/null; then
    URL="$(cat "$STATE/url.txt")"; break
  fi
  sleep 0.3
done
export DSH_WEB_PORT="$PORT" DSH_WEB_STATE="$STATE" DSH_WEB_BIND="$BIND"

# 原版 UI：裸 Chromium --app 独立应用窗口（无地址栏/无底部栏，不碰浏览器壳）
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$RUN_UID}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/$RUN_UID/bus}"
# 通过用户级 service 启动/重启应用窗口（脱离本会话常驻，重启存活）
user_ctl restart dsh-app-window.service 2>/dev/null || {
  # 回退：直接起（当前用户无 systemd 时）
  exec /usr/local/bin/dsh-app-window-run
}
exit 0
OPEN
chmod 755 /usr/local/bin/dsh-web-open

# 裸 Chromium --app 应用窗口运行器（独立 profile，无地址栏/无底部栏，不碰浏览器壳）
cat > /usr/local/bin/dsh-app-window-run <<'APPWIN'
#!/bin/bash
set -o pipefail
STATE="@RUN_HOME@/.local/state/dsh-web"
PORT="@PORT@"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/@RUN_UID@}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/@RUN_UID@/bus}"
export LD_LIBRARY_PATH="/opt/chromium/privatelib:/opt/chromium/root/usr/lib/chromium${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
# 取最新 token URL
URL="http://127.0.0.1:$PORT/"
for i in $(seq 1 25); do
  if [ -s "$STATE/url.txt" ] && grep -q "token=" "$STATE/url.txt" 2>/dev/null; then URL="$(cat "$STATE/url.txt")"; break; fi
  sleep 0.3
done
exec /opt/chromium/root/usr/lib/chromium/chromium --app="$URL" \
  --user-data-dir="$HOME/.config/dsh-app-chromium" \
  --no-first-run --no-default-browser-check --no-sandbox --disable-dev-shm-usage \
  --ozone-platform=wayland --enable-features=UseOzonePlatform \
  --use-gl=angle --use-angle=gl --enable-gpu-rasterization --ignore-gpu-blocklist \
  --window-size=412,900 \
  --remote-debugging-port=9333
APPWIN
chmod 755 /usr/local/bin/dsh-app-window-run
sed -i "s|@RUN_AS@|${USER_NAME}|g; s|@RUN_UID@|${USER_UID}|g; s|@RUN_HOME@|${HOME_DIR}|g; s|@PORT@|${PORT}|g; s|@BIND@|${BIND}|g; s|@CHROMIUM_BIN@|${CHROMIUM_BIN}|g; s|@CHROMIUM_LIBS@|${CHROMIUM_LIBS}|g" /usr/local/bin/dsh-web-open

# ---- 用户级 systemd 单元（以 fuxi 身份，按需启停，无需 root/polkit） ----
mkdir -p "$HOME_DIR/.config/systemd/user" "$HOME_DIR/.local/bin" "$HOME_DIR/.local/state/dsh-web"
cat > "$HOME_DIR/.local/bin/dsh-web-launcher" <<LAUNCH
#!/bin/bash
set -o pipefail
STATE="\$HOME/.local/state/dsh-web"
mkdir -p "\$STATE"
date +%s > "\$STATE/last-activity"
: > "\$STATE/url.txt"
# 记录 token URL 到文件；对日志中的 token 做脱敏后再输出，避免 token 进入 journald/部署输出。
"${DSH_BIN}" --profile web --no-open --host "${BIND}" --port "${PORT}" 2>&1 | \\
while IFS= read -r line; do
  case "\$line" in
    *"dsh web:"*)
      printf '%s\n' "\$line" | grep -oE 'http[^ ]+' > "\$STATE/url.txt" 2>/dev/null || true
      # 脱敏后打印（token=****）
      printf '%s\n' "\$line" | sed -E 's/(token=)[^ &]+/\1****/g'
      ;;
    *)
      printf '%s\n' "\$line"
      ;;
  esac
done
LAUNCH
chmod 755 "$HOME_DIR/.local/bin/dsh-web-launcher"

cat > "$HOME_DIR/.local/bin/dsh-web-idle-check" <<IDLE
#!/bin/bash
STATE="\$HOME/.local/state/dsh-web"
STAMP="\$STATE/last-activity"
[ -f "\$STAMP" ] || exit 0
now=\$(date +%s); last=\$(cat "\$STAMP" 2>/dev/null || echo "\$now")
idle=\$(( (now - last) / 60 ))
if [ "\$idle" -ge ${IDLE_MIN} ]; then
  systemctl --user stop dsh-web.service 2>/dev/null || true
fi
IDLE
chmod 755 "$HOME_DIR/.local/bin/dsh-web-idle-check"

cat > "$HOME_DIR/.config/systemd/user/dsh-web.service" <<UNIT
[Unit]
Description=DeepSeek Harness Web (on-demand, DeepSeek-Android skin)

[Service]
Type=simple
Environment=DSH_HOME=${HOME_DIR}/.dsh
EnvironmentFile=-${HOME_DIR}/.config/dsh-web.env
ExecStart=%h/.local/bin/dsh-web-launcher
Restart=on-failure
RestartSec=3
UNIT

cat > "$HOME_DIR/.config/systemd/user/dsh-web-idle.service" <<IDSVC
[Unit]
Description=Stop DSH web when idle (power saving)

[Service]
Type=oneshot
ExecStart=%h/.local/bin/dsh-web-idle-check
IDSVC

cat > "$HOME_DIR/.config/systemd/user/dsh-web-idle.timer" <<IDTMR
[Unit]
Description=Periodic DSH web idle check

[Timer]
OnBootSec=3min
OnUnitActiveSec=2min

[Install]
WantedBy=timers.target
IDTMR

# 裸 Chromium --app 应用窗口（用户级 service，脱离会话常驻，重启存活）
cat > "$HOME_DIR/.config/systemd/user/dsh-app-window.service" <<'APPUNIT'
[Unit]
Description=DSH app window (bare Chromium --app)
After=graphical-session.target

[Service]
Type=simple
Environment=XDG_RUNTIME_DIR=/run/user/%U
Environment=WAYLAND_DISPLAY=wayland-0
Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/%U/bus
ExecStart=%h/.local/bin/dsh-app-window-run
Restart=on-failure
RestartSec=2

[Install]
WantedBy=graphical-session.target
APPUNIT
# 用 -m 修正 %U（systemd %U 在当前用户 service 中可用；为兼容再显式替换）
sed -i "s|/run/user/%U|/run/user/${USER_UID}|g" "$HOME_DIR/.config/systemd/user/dsh-app-window.service"
# 把运行器也装到用户目录（service 用 %h/.local/bin）
install -m 0755 /usr/local/bin/dsh-app-window-run "$HOME_DIR/.local/bin/dsh-app-window-run" 2>/dev/null || true
sed -i "s|@RUN_UID@|${USER_UID}|g; s|@RUN_HOME@|${HOME_DIR}|g; s|@PORT@|${PORT}|g" "$HOME_DIR/.local/bin/dsh-app-window-run" 2>/dev/null || true
# 同时修正 /usr/local 版本占位
sed -i "s|@RUN_UID@|${USER_UID}|g; s|@RUN_HOME@|${HOME_DIR}|g; s|@PORT@|${PORT}|g" /usr/local/bin/dsh-app-window-run 2>/dev/null || true

# API key（用户级，fuxi 可读）
if [ -n "${API_KEY:-}" ]; then
  ( umask 077; printf 'DEEPSEEK_API_KEY=%s\n' "$API_KEY" > "$HOME_DIR/.config/dsh-web.env.tmp" )
else
  ( umask 077; printf '# 可选：配置 DeepSeek API key，然后 systemctl --user restart dsh-web\n# DEEPSEEK_API_KEY=sk-xxx\n' > "$HOME_DIR/.config/dsh-web.env.tmp" )
fi
install -m 600 "$HOME_DIR/.config/dsh-web.env.tmp" "$HOME_DIR/.config/dsh-web.env"
rm -f "$HOME_DIR/.config/dsh-web.env.tmp"

if ! chown -R "$USER_NAME":"$USER_NAME" "$HOME_DIR/.config/systemd/user" "$HOME_DIR/.local" "$HOME_DIR/.config/dsh-web.env"; then
  echo "安装失败：无法把配置属主设为 $USER_NAME" >&2; exit 5
fi

# 清理旧的系统级服务（如果之前残留在 root）
systemctl disable --now dsh-web.service 2>/dev/null || true
systemctl disable --now dsh-web-idle.timer 2>/dev/null || true
rm -f /etc/systemd/system/dsh-web.service /etc/systemd/system/dsh-web-idle.timer /etc/systemd/system/dsh-web-idle.service /usr/local/bin/dsh-web-idle-check 2>/dev/null || true
systemctl daemon-reload 2>/dev/null || true

# ---- 桌面图标（App 入口） ----
mkdir -p /usr/share/icons/hicolor/scalable/apps
cp -a /root/dsh-phone/dsh-web.svg /usr/share/icons/hicolor/scalable/apps/dsh-web.svg
cat > /usr/share/applications/dsh-web.desktop <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=DeepSeek
Name[zh_CN]=DeepSeek
Comment=DeepSeek Harness on phone
Comment[zh_CN]=DeepSeek Harness 手机端（安卓风界面）
Exec=/usr/local/bin/dsh-web-open
Icon=dsh-web
Terminal=false
Categories=Utility;Network;
StartupNotify=true
DESKTOP
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -f /usr/share/icons/hicolor >/dev/null 2>&1 || true

echo "-- 启用用户级闲置定时器 + 自检（以 $USER_NAME 身份）"
run_user systemctl --user daemon-reload
run_user systemctl --user enable --now dsh-web-idle.timer
# 自检：临时启动一次确认可监听，随后停掉（按需启动模式，不长期常驻）
run_user systemctl --user start dsh-web.service
sleep 5
run_user systemctl --user --no-pager --lines=0 status dsh-web.service 2>/dev/null | head -6 || true

echo "-- 端口检查（等待服务就绪，最多 ~20s）"
python3 - "$PORT" <<'PY'
import socket, sys, time
p = int(sys.argv[1])
for i in range(50):
    s = socket.socket(); s.settimeout(1)
    try:
        s.connect(("127.0.0.1", p)); print("端口 %d 已监听" % p); s.close(); sys.exit(0)
    except Exception:
        s.close(); time.sleep(0.4)
print("未监听 %d（服务可能未就绪）" % p, file=sys.stderr); sys.exit(1)
PY

echo "-- 登录 URL（token 脱敏显示，完整值仅存手机 $HOME_DIR/.local/state/dsh-web/url.txt）"
sleep 1
if [ -s "$HOME_DIR/.local/state/dsh-web/url.txt" ]; then
  MASKED="$(sed -E 's/(token=).*/\1****/' "$HOME_DIR/.local/state/dsh-web/url.txt")"
  echo "DSH_URL_MASKED=$MASKED"
  echo "DSH_URL_FILE=$HOME_DIR/.local/state/dsh-web/url.txt"
else
  echo "DSH_URL=http://127.0.0.1:${PORT}/"
fi
# 自检完毕：停服回到“按需启动”状态，省电
run_user systemctl --user stop dsh-web.service 2>/dev/null || true
echo "REMOTE_DONE"
