#!/bin/bash
# Both clients must be compiled from restart-camera.cpp against their own builds.
set -euo pipefail
die() { echo "restart-matrix: $*" >&2; exit 1; }
[[ $# == 4 ]] || die "usage: $0 BASELINE_CLIENT PATCHED_CLIENT CONTEXT_AUDIT_SO NEW_LOG_DIR"
[[ $(id -u) == 0 ]] || die "run as root; captures use fuxi"
baseline=$(realpath -e -- "$1")
patched=$(realpath -e -- "$2")
audit=$(realpath -e -- "$3")
[[ -x $baseline && -x $patched ]] || die "clients not built"
[[ $(uname -r) == 7.4.0-rc1-fuxi-cam6hfclk ]] || die "requires cam6hfclk"
[[ $(cat /boot/fuxi/default) == 7.4.0-rc1-fuxi-deb9 ]] || die "preselect deb9"
[[ $(cat /boot/fuxi/fallback) == 7.4.0-rc1-fuxi-deb8 ]] || die "retain deb8"
! pgrep -x cam >/dev/null || die "cam already running"
! pgrep -x qcam >/dev/null || die "qcam already running"
mkdir -m 700 -- "$4"
logs=$(realpath -e -- "$4")
unset LD_PRELOAD LD_LIBRARY_PATH LIBCAMERA_IPA_MODULE_PATH LIBCAMERA_IPA_PROXY_PATH LIBCAMERA_IPA_CONFIG_PATH
unset EGL_CONTEXT_FAIL_CREATE EGL_CONTEXT_FAIL_CURRENT EGL_CONTEXT_FAIL_CONFIG EGL_CONTEXT_FAIL_SHADER EGL_CONTEXT_FAIL_LINK
export LIBCAMERA_PIPELINES_MATCH_LIST=simple LIBCAMERA_SOFTISP_MODE=gpu
ldd "$baseline" > "$logs/baseline-ldd.log"
ldd "$patched" > "$logs/patched-ldd.log"
health() {
    dmesg > "$logs/dmesg-latest.log"
    if grep -E 'RPMh batch .*timed out|Error sending AMC|blocked for more than|EXT4-fs error|I/O error' "$logs/dmesg-latest.log"; then
        die "kernel fault; inspect recovery state"
    fi
    [[ $(cat /sys/bus/platform/devices/acb7000.isp/power/runtime_status) == suspended ]] || die "CAMSS not suspended"
    awk '$1 == "gcc_camera_hf_axi_clk" { found=1; if ($2 != 0) bad=1 }
         END { exit (!found || bad) }' /sys/kernel/debug/clk/clk_summary || die "HF gate enabled"
}
capture() {
    local name=$1 client=$2 expected=$3 fault=$4 result=0
    shift 4
    local -a inject=()
    [[ -z $fault ]] || inject+=("$fault")
    health
    runuser -u fuxi -- env MALLOC_PERTURB_=191 \
        timeout -s INT -k 3s 180s env LD_PRELOAD="$audit" "${inject[@]}" "$client" "$@" \
        > "$logs/$name.log" 2>&1 || result=$?
    printf '%s\t%s\n' "$name" "$result" >> "$logs/exits.tsv"
    health
    [[ $result == "$expected" ]] || die "$name exit=$result expected=$expected; inspect $logs"
    if [[ $expected == 0 ]]; then
        grep -E '^RESTART .* result=PASS$' "$logs/$name.log" || die "$name missing final result"
        if [[ -n $fault ]]; then
            grep -E '^EGL_CONTEXT .* injected=1$' "$logs/$name.log" >/dev/null || die "fault was not injected"
        fi
    else
        grep -Fx 'EGL_CONTEXT created=3 destroyed=0 live=3 shaders=0 programs=0 invalid_deletes=0 injected=0' \
            "$logs/$name.log" >/dev/null || die "baseline did not reproduce expected leak"
        grep -Fx 'RESTART rounds=3 start_failures=0 error_frames=0 result=FAIL' \
            "$logs/$name.log" >/dev/null || die "baseline failed for another reason"
    fi
    echo "PASS $name expected_exit=$expected"
}
capture baseline "$baseline" 1 '' 3 30 0 0
capture repeated "$patched" 0 '' 20 60 0 0
capture full "$patched" 0 '' 8 60 0 0 4208 3120
for stage in CREATE CONFIG CURRENT LINK; do
    capture "fail-${stage,,}" "$patched" 0 "EGL_CONTEXT_FAIL_$stage=1" 3 30 1 0
done
capture fail-vertex "$patched" 0 EGL_CONTEXT_FAIL_SHADER=1 3 30 1 0
capture fail-fragment "$patched" 0 EGL_CONTEXT_FAIL_SHADER=2 3 30 1 0
capture fail-frame-current "$patched" 0 EGL_CONTEXT_FAIL_CURRENT=5 3 30 0 1
echo "PASS same-process restart matrix; logs=$logs"
