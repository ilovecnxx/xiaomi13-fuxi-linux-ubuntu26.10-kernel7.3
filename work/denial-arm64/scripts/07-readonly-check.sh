#!/bin/bash
# Phase 5: read-only runtime prerequisite checks for the staged ARM64 install tree.
# Does NOT start Denial, does NOT touch Phosh, does NOT change tty/session.
source /home/fuxi/denial-logs/env.sh
set -u
ROOT=/home/fuxi/denial-out/work/install-root
LOG=/home/fuxi/denial-logs/phase5-check.log
exec > >(tee "$LOG") 2>&1
echo "=== denial ARM64 read-only check $(date -Is) ==="
echo "install-root: $ROOT"
echo

section() { echo; echo "### $*"; }

section "1. staged ELF architecture + dynamic library resolution"
for f in "$ROOT/usr/bin/deniald" "$ROOT/usr/bin/denialctl" "$ROOT/usr/bin/denial-portal" \
         "$ROOT/usr/lib/denial/flutter/lib/libflutter_engine.so" \
         "$ROOT/usr/lib/denial/flutter/lib/libapp.so" \
         "$ROOT/usr/lib/denial/settings/denial-settings" \
         "$ROOT/usr/lib/denial/settings/lib/libflutter_engine.so" \
         "$ROOT/usr/lib/denial/settings/lib/libapp.so"; do
  echo "--- $f"
  file -b "$f"
done
echo
echo "--- ldd deniald (missing libs listed as 'not found') ---"
LD_LIBRARY_PATH="$ROOT/usr/lib/denial/flutter/lib" ldd "$ROOT/usr/bin/deniald" 2>&1 | sed -n '1,60p'
echo
echo "--- ldd denialctl ---"
ldd "$ROOT/usr/bin/denialctl" 2>&1 | grep -E "not found|libc.so|libgcc" || true
echo
echo "--- ldd denial-portal ---"
ldd "$ROOT/usr/bin/denial-portal" 2>&1 | grep -E "not found|libc.so|libgcc" || true
echo
echo "--- ldd denial-settings ---"
LD_LIBRARY_PATH="$ROOT/usr/lib/denial/settings/lib" ldd "$ROOT/usr/lib/denial/settings/denial-settings" 2>&1 | grep -E "not found|libc.so|libgcc|libwayland|libEGL|libGLES" || true

section "2. deniald / denialctl help (no compositor start)"
"$ROOT/usr/bin/deniald" --help 2>&1 | head -40
echo "---"
"$ROOT/usr/bin/denialctl" --help 2>&1 | head -25

section "3. DRM / GBM / output topology (read-only)"
echo "--- /dev/dri ---"; ls -l /dev/dri/
echo "--- kernel driver ---"; readlink -f /sys/class/drm/card0/device/driver
echo "--- connectors ---"
for s in /sys/class/drm/card*-*/status; do printf '%-40s %s\n' "$s" "$(cat "$s")"; done
echo "--- modes ---"
for m in /sys/class/drm/card*-*/modes; do echo "$m:"; head -3 "$m"; done
echo "--- boot_vga ---"; cat /sys/class/drm/card0/device/boot_vga 2>/dev/null
echo "--- render node ---"; ls -l /dev/dri/renderD128
echo "--- seat ---"; loginctl show-seat seat0 -p Id -p ActiveSession 2>/dev/null

section "4. EGL/GBM probe on render node (offscreen only, does not touch KMS/Phosh)"
cat > /tmp/egl_gbm_probe.c <<'CEOF'
#include <stdio.h>
#include <fcntl.h>
#include <unistd.h>
#include <gbm.h>
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
int main(void) {
    int fd = open("/dev/dri/renderD128", O_RDWR | O_CLOEXEC);
    if (fd < 0) { perror("open renderD128"); return 2; }
    struct gbm_device *gbm = gbm_create_device(fd);
    if (!gbm) { fprintf(stderr, "gbm_create_device failed\n"); return 3; }
    printf("gbm backend: %s\n", gbm_device_get_backend_name(gbm));
    PFNEGLGETPLATFORMDISPLAYEXTPROC get_display =
        (PFNEGLGETPLATFORMDISPLAYEXTPROC)eglGetProcAddress("eglGetPlatformDisplayEXT");
    if (!get_display) { fprintf(stderr, "no eglGetPlatformDisplayEXT\n"); return 4; }
    EGLDisplay dpy = get_display(EGL_PLATFORM_GBM_KHR, gbm, NULL);
    if (dpy == EGL_NO_DISPLAY) { fprintf(stderr, "eglGetPlatformDisplay failed 0x%x\n", eglGetError()); return 5; }
    EGLint major, minor;
    if (!eglInitialize(dpy, &major, &minor)) { fprintf(stderr, "eglInitialize failed 0x%x\n", eglGetError()); return 6; }
    printf("EGL %d.%d vendor=%s version=%s\n", major, minor,
           eglQueryString(dpy, EGL_VENDOR), eglQueryString(dpy, EGL_VERSION));
    printf("EGL client APIs: %s\n", eglQueryString(dpy, EGL_CLIENT_APIS));
    eglBindAPI(EGL_OPENGL_ES_API);
    EGLint cfg_attr[] = { EGL_SURFACE_TYPE, EGL_PBUFFER_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT,
                          EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8, EGL_NONE };
    EGLConfig cfg; EGLint n = 0;
    if (!eglChooseConfig(dpy, cfg_attr, &cfg, 1, &n) || n < 1) { fprintf(stderr, "eglChooseConfig failed 0x%x\n", eglGetError()); return 7; }
    EGLint ctx_attr[] = { EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE };
    EGLContext ctx = eglCreateContext(dpy, cfg, EGL_NO_CONTEXT, ctx_attr);
    if (ctx == EGL_NO_CONTEXT) { fprintf(stderr, "eglCreateContext failed 0x%x\n", eglGetError()); return 8; }
    EGLint pb_attr[] = { EGL_WIDTH, 64, EGL_HEIGHT, 64, EGL_NONE };
    EGLSurface surf = eglCreatePbufferSurface(dpy, cfg, pb_attr);
    if (surf == EGL_NO_SURFACE) { fprintf(stderr, "eglCreatePbufferSurface failed 0x%x\n", eglGetError()); return 9; }
    if (!eglMakeCurrent(dpy, surf, surf, ctx)) { fprintf(stderr, "eglMakeCurrent failed 0x%x\n", eglGetError()); return 10; }
    printf("GL_RENDERER: %s\n", glGetString(GL_RENDERER));
    printf("GL_VERSION:  %s\n", glGetString(GL_VERSION));
    printf("GL_VENDOR:   %s\n", glGetString(GL_VENDOR));
    glClearColor(0.1f, 0.2f, 0.3f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
    EGLint glerr = glGetError();
    printf("glClear error: 0x%x\n", glerr);
    eglTerminate(dpy);
    gbm_device_destroy(gbm);
    close(fd);
    return glerr == 0 ? 0 : 11;
}
CEOF
gcc -O2 -o /tmp/egl_gbm_probe /tmp/egl_gbm_probe.c -lgbm -lEGL -lGLESv2 2>&1 || echo "gcc probe build FAILED"
if [[ -x /tmp/egl_gbm_probe ]]; then /tmp/egl_gbm_probe; echo "egl_gbm_probe_rc=$?"; fi

section "5. denial-session --check"
# install the session target into fuxi's user unit directory so the check can
# resolve it through the systemd user manager (no unit is enabled or started)
SESS_TARGET_DIR=/home/fuxi/.config/systemd/user
install -d -o fuxi -g fuxi "$SESS_TARGET_DIR"
install -m644 -o fuxi -g fuxi "$ROOT/usr/lib/systemd/user/denial-session.target" "$SESS_TARGET_DIR/denial-session.target"
runuser -u fuxi -- env HOME=/home/fuxi XDG_RUNTIME_DIR=/run/user/1000 \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
  systemctl --user daemon-reload
echo "--- systemctl --user cat denial-session.target ---"
runuser -u fuxi -- env HOME=/home/fuxi XDG_RUNTIME_DIR=/run/user/1000 \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
  systemctl --user cat denial-session.target 2>&1 | head -20
echo
echo "--- denial-session --print-output-config (as fuxi) ---"
runuser -u fuxi -- env HOME=/home/fuxi XDG_RUNTIME_DIR=/run/user/1000 \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
  "$ROOT/usr/bin/denial-session" --print-output-config 2>&1
echo
echo "--- denial-session --check (as fuxi) ---"
runuser -u fuxi -- env HOME=/home/fuxi XDG_RUNTIME_DIR=/run/user/1000 \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
  "$ROOT/usr/bin/denial-session" --check 2>&1
echo "denial_session_check_rc=$?"
echo
echo "--- denial-session --check (as root, systemd-user unavailable path) ---"
"$ROOT/usr/bin/denial-session" --check 2>&1
echo "denial_session_check_root_rc=$?"

section "6. portal prerequisites"
echo "--- dbus service file ---"; sed -n 1,6p "$ROOT/usr/share/dbus-1/services/org.freedesktop.impl.portal.desktop.denial.service"
echo "--- portal descriptor ---"; cat "$ROOT/usr/share/xdg-desktop-portal/portals/denial.portal"
echo "--- routing conf ---"; cat "$ROOT/usr/share/xdg-desktop-portal/denial-portals.conf"
echo "--- installed portal frontends ---"; dpkg-query -W -f='${Status} ${Package}\n' 'xdg-desktop-portal*' 2>/dev/null | grep -v "not-installed" | sort
echo "--- xdg-desktop-portal units active ---"; systemctl --no-pager --state=running list-units 'xdg-desktop-portal*' 2>/dev/null | head -8

section "7. session/unit files in staged tree"
find "$ROOT/usr/lib/systemd/user" "$ROOT/usr/share/wayland-sessions" "$ROOT/usr/share/applications" "$ROOT/etc/denial" -type f | sort

echo
echo "=== check complete $(date -Is) ==="
