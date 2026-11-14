#!/bin/bash
# Isolated device validation; no system installation or automatic reboot.
set -euo pipefail
die() { echo "gpu-matrix: $*" >&2; exit 1; }
[[ $# == 3 ]] || die "usage: $0 BUILD_DIR AUDIT_SO NEW_LOG_DIR"
[[ $(id -u) == 0 ]] || die "run as root; captures use fuxi"
recipe=$(dirname -- "$(realpath -e -- "$0")")
build=$(realpath -e -- "$1")
audit=$(realpath -e -- "$2")
cam=$build/src/apps/cam/cam
[[ -x $cam ]] || die "cam is not built"
[[ $(uname -r) == 7.4.0-rc1-fuxi-cam6hfclk ]] || die "requires cam6hfclk"
[[ $(cat /boot/fuxi/default) == 7.4.0-rc1-fuxi-deb9 ]] || die "preselect deb9"
[[ $(cat /boot/fuxi/fallback) == 7.4.0-rc1-fuxi-deb8 ]] || die "retain deb8"
! pgrep -x cam >/dev/null || die "cam already running"
! pgrep -x qcam >/dev/null || die "qcam already running"
mkdir -m 700 -- "$3"
logs=$(realpath -e -- "$3")
unset LD_PRELOAD LD_LIBRARY_PATH LIBCAMERA_IPA_MODULE_PATH LIBCAMERA_IPA_PROXY_PATH LIBCAMERA_IPA_CONFIG_PATH
unset EGL_AUDIT_FAIL_IMPORT EGL_AUDIT_FAIL_FBO EGL_AUDIT_FAIL_DRAW
export LIBCAMERA_PIPELINES_MATCH_LIST=simple LIBCAMERA_SOFTISP_MODE=gpu
ldd "$cam" > "$logs/ldd.log"
for library in libcamera.so.0.7 base/libcamera-base.so.0.7; do
    resolved=$(awk -v name="${library##*/}" '$1 == name && $2 == "=>" { print $3 }' "$logs/ldd.log")
    [[ -n $resolved ]] || die "cannot resolve $library"
    [[ $(realpath -e -- "$resolved") == "$(realpath -e -- "$build/src/libcamera/$library")" ]] || die "wrong library: $library"
done
health() {
    dmesg > "$logs/dmesg-latest.log"
    if grep -E 'RPMh batch .*timed out|Error sending AMC|blocked for more than|EXT4-fs error|I/O error' "$logs/dmesg-latest.log"; then
        die "kernel fault; stop and inspect recovery state"
    fi
    [[ $(cat /sys/bus/platform/devices/acb7000.isp/power/runtime_status) == suspended ]] || die "CAMSS not suspended"
    awk '$1 == "gcc_camera_hf_axi_clk" { found=1; if ($2 != 0) bad=1 }
         END { exit (!found || bad) }' /sys/kernel/debug/clk/clk_summary || die "HF gate enabled"
}
capture() {
    local name=$1 width=$2 height=$3 frames=$4 errors=$5 fault=${6:-} result=0
    local -a inject=()
    [[ -z $fault ]] || inject+=("$fault")
    health
    runuser -u fuxi -- env MALLOC_PERTURB_=191 \
        timeout -s INT -k 3s 180s env LD_PRELOAD="$audit" "${inject[@]}" \
        "$cam" -c 1 -s "role=viewfinder,width=$width,height=$height" -C"$frames" \
        > "$logs/$name.log" 2>&1 || result=$?
    printf '%s\t%s\n' "$name" "$result" >> "$logs/exits.tsv"
    health
    [[ $result == 0 ]] || die "$name exit=$result; inspect $logs"
    python3 "$recipe/check-capture.py" "$logs/$name.log" --frames "$frames" --errors "$errors" \
        > "$logs/$name.json"
    echo "PASS $name: $(<"$logs/$name.json")"
}
health
runuser -u fuxi -- "$cam" -l > "$logs/cameras.log" 2>&1
grep -F ov13b10 "$logs/cameras.log" >/dev/null || die "OV13B10 not found"
for kind in IMPORT FBO DRAW; do
    capture "${kind,,}-once" 2104 1560 60 1 "EGL_AUDIT_FAIL_$kind=5"
    capture "${kind,,}-continuous" 2104 1560 60 60 "EGL_AUDIT_FAIL_$kind=-1"
done
for round in 1 2 3; do
    capture "half-$round" 2104 1560 900 0
    capture "full-$round" 4208 3120 900 0
    sleep 3
done
capture full-long 4208 3120 3600 0
echo "PASS GPU capture matrix; logs=$logs"
