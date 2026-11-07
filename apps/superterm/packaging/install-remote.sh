#!/bin/bash
# SuperTerm installer — runs over SSH on the phone (Ubuntu 26.10 arm64, Phosh).
# Installs the app into ~/.local/share/superterm (versioned), wires a desktop entry,
# launcher, and icon for the target user. Idempotent; supports --rollback.
#
# Usage (invoked by scripts/superterm-deploy.sh, or directly as root):
#   install-remote.sh [--user fuxi] [--rollback] [--no-deps]
set -euo pipefail

TARGET_USER="fuxi"
ROLLBACK=0
INSTALL_DEPS=1
SRC_DIR="/root/superterm-pkg"   # staged by deploy script
while [ $# -gt 0 ]; do
  case "$1" in
    --user) TARGET_USER="$2"; shift 2;;
    --rollback) ROLLBACK=1; shift;;
    --no-deps) INSTALL_DEPS=0; shift;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

UNAME="$(id -un)"
if [ "$UNAME" != "root" ]; then echo "must run as root" >&2; exit 1; fi
UID_T="$(id -u "$TARGET_USER")"
HOME_T="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
APP_ROOT="$HOME_T/.local/share/superterm"
DESKTOP_DIR="$HOME_T/.local/share/applications"
ICON_DIR="$HOME_T/.local/share/icons/hicolor/scalable/apps"

log(){ echo "[superterm] $*"; }

if [ "$ROLLBACK" = 1 ]; then
  log "rollback: restore most recent version"
  PREV="$(ls -1d "$APP_ROOT"/versions/* 2>/dev/null | sort | tail -2 | head -1 || true)"
  if [ -n "$PREV" ] && [ -d "$PREV" ]; then
    ln -sfn "$PREV" "$APP_ROOT/current.new" && mv -Tf "$APP_ROOT/current.new" "$APP_ROOT/current"
    log "current -> $PREV"
  else
    log "no previous version to roll back to"
  fi
  exit 0
fi

log "arch/version checks"
[ "$(dpkg --print-architecture)" = arm64 ] || { echo "requires arm64" >&2; exit 1; }
command -v node >/dev/null || { echo "node missing" >&2; exit 1; }
python3 -c 'import gi; gi.require_version("Gtk","4.0"); gi.require_version("Adw","1")' || {
  echo "GTK4/libadwaita python bindings missing" >&2; exit 1; }
python3 -c 'import gi; gi.require_version("WebKit","6.0")' 2>/dev/null || {
  log "installing gir1.2-webkit-6.0 (GTK4 WebKit bindings)"
  [ "$INSTALL_DEPS" = 1 ] && DEBIAN_FRONTEND=noninteractive apt-get install -y gir1.2-webkit-6.0 || true; }

log "install agent CLIs (codex, claude) if missing"
if [ "$INSTALL_DEPS" = 1 ]; then
  command -v codex >/dev/null || npm install -g @openai/codex >/dev/null 2>&1 || log "codex install failed (will retry later)"
  command -v claude >/dev/null || npm install -g @anthropic-ai/claude-code >/dev/null 2>&1 || log "claude install failed (will retry later)"
fi

log "stage versioned app"
mkdir -p "$APP_ROOT/versions"
VERSION="$(date +%Y%m%d-%H%M%S)"
DEST="$APP_ROOT/versions/$VERSION"
mkdir -p "$DEST"
cp -a "$SRC_DIR"/. "$DEST"/
chown -R "$TARGET_USER:$TARGET_USER" "$APP_ROOT"

log "switch current -> $VERSION"
ln -sfn "$DEST" "$APP_ROOT/current.new" && mv -Tf "$APP_ROOT/current.new" "$APP_ROOT/current"
# launcher points at current
mkdir -p "$HOME_T/.local/bin"
cat > "$HOME_T/.local/bin/superterm-launch" <<LAUNCH
#!/bin/bash
export PYTHONPATH="$APP_ROOT/current/src\${PYTHONPATH:+:\$PYTHONPATH}"
export GDK_BACKEND="\${GDK_BACKEND:-wayland,x11}"
exec python3 -m superterm.main "\$@"
LAUNCH
chmod 0755 "$HOME_T/.local/bin/superterm-launch"
chown "$TARGET_USER:$TARGET_USER" "$HOME_T/.local/bin/superterm-launch"

log "install desktop entry + icon"
mkdir -p "$DESKTOP_DIR" "$ICON_DIR"
install -m 0644 "$SRC_DIR/packaging/superterm.desktop" "$DESKTOP_DIR/superterm.desktop"
install -m 0644 "$SRC_DIR/packaging/superterm.svg" "$ICON_DIR/superterm.svg"
# ensure PATH includes ~/.local/bin for the Exec lookup; use absolute fallback
sed -i "s|^Exec=.*|Exec=$HOME_T/.local/bin/superterm-launch|" "$DESKTOP_DIR/superterm.desktop"
chown -R "$TARGET_USER:$TARGET_USER" "$HOME_T/.local/share"
command -v update-desktop-database >/dev/null && sudo -u "$TARGET_USER" update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -f -t "$HOME_T/.local/share/icons/hicolor" 2>/dev/null || true

log "doctor check"
sudo -u "$TARGET_USER" env HOME="$HOME_T" PYTHONPATH="$APP_ROOT/current/src" python3 -m superterm.headless doctor 2>&1 | tail -12 || true

log "done. version=$VERSION current=$APP_ROOT/current"
echo "SUPERTERM_INSTALL_DONE version=$VERSION"
