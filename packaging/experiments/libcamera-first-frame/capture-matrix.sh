#!/bin/bash
# Device-only validation of the isolated CPU software ISP candidate.
set -euo pipefail

die() { echo "capture-matrix: $*" >&2; exit 1; }
[[ $# -ge 2 && $# -le 3 ]] || die "usage: $0 BUILD_DIR NEW_LOG_DIR [ROUNDS=6]"
[[ $(id -u) == 0 ]] || die "run as root; captures run as the fuxi user"
rounds=${3:-6}
[[ $rounds =~ ^[1-9]$|^10$ ]] || die "rounds must be 1..10"
build=$(realpath -e -- "$1")
cam=$build/src/apps/cam/cam
[[ -x $cam ]] || die "cam is not built"
[[ $(uname -r) == 7.4.0-rc1-fuxi-cam6hfclk ]] || die "requires cam6hfclk"
[[ $(cat /boot/fuxi/default) == 7.4.0-rc1-fuxi-deb9 ]] || die "preselect deb9 first"
[[ $(cat /boot/fuxi/fallback) == 7.4.0-rc1-fuxi-deb8 ]] || die "retain deb8 fallback"
! pgrep -x cam >/dev/null || die "cam is already running"
! pgrep -x qcam >/dev/null || die "qcam is already running"
mkdir -m 700 -- "$2"
logs=$(realpath -e -- "$2")
export LIBCAMERA_SOFTISP_MODE=cpu
export LIBCAMERA_PIPELINES_MATCH_LIST=simple
unset LD_PRELOAD LD_LIBRARY_PATH LIBCAMERA_IPA_MODULE_PATH LIBCAMERA_IPA_PROXY_PATH LIBCAMERA_IPA_CONFIG_PATH

ldd "$cam" > "$logs/ldd.log"
for library in libcamera.so.0.7 base/libcamera-base.so.0.7; do
    resolved=$(awk -v name="${library##*/}" '$1 == name && $2 == "=>" { print $3 }' "$logs/ldd.log")
    [[ -n $resolved ]] || die "cannot resolve $library"
    [[ $(realpath -e -- "$resolved") == "$(realpath -e -- "$build/src/libcamera/$library")" ]] || die "not using build $library"
done

check_health() {
    dmesg > "$logs/dmesg-latest.log"
    if grep -E 'RPMh batch .*timed out|Error sending AMC|blocked for more than|EXT4-fs error|I/O error' "$logs/dmesg-latest.log"; then
        die "kernel fault; stop and inspect recovery state"
    fi
    [[ $(cat /sys/bus/platform/devices/acb7000.isp/power/runtime_status) == suspended ]] || die "CAMSS did not suspend"
    awk '$1 == "gcc_camera_hf_axi_clk" { found=1; if ($2 != 0) bad=1 }
         END { exit (!found || bad) }' /sys/kernel/debug/clk/clk_summary || die "HF gate still enabled"
}

check_health
runuser -u fuxi -- "$cam" -l > "$logs/cameras.log" 2>&1
grep -F 'ov13b10' "$logs/cameras.log" >/dev/null || die "OV13B10 not found"
printf 'round\tperturbation\tmode\trequested\tobserved\texit\n' > "$logs/results.tsv"
for ((round=1; round<=rounds; round++)); do
    case $(( (round - 1) % 3 )) in
        0) perturbation=191 ;;
        1) perturbation=85 ;;
        2) perturbation=255 ;;
    esac
    for mode in half full; do
        check_health
        if [[ $mode == half ]]; then
            stream=role=viewfinder,width=2104,height=1560
            frames=300
        else
            stream=role=viewfinder,width=4208,height=3120
            frames=120
        fi
        result=0
        runuser -u fuxi -- env MALLOC_PERTURB_="$perturbation" \
            timeout -k 3s 35s "$cam" -c 1 -s "$stream" -C"$frames" \
            > "$logs/round-$round-$mode.log" 2>&1 || result=$?
        observed=$(awk '/^[0-9]+\.[0-9]+ .*cam0-stream0 seq: [0-9]+ bytesused: [1-9][0-9]*$/ { count++ }
                        END { print count+0 }' "$logs/round-$round-$mode.log")
        printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$round" "$perturbation" "$mode" "$frames" "$observed" "$result" >> "$logs/results.tsv"
        check_health
        [[ $result == 0 ]] || die "round $round $mode failed ($result); see $logs"
        [[ $observed == "$frames" ]] || die "round $round $mode returned $observed/$frames frames"
        echo "PASS round=$round mode=$mode frames=$frames perturbation=$perturbation"
    done
    if (( round < rounds )); then
        sleep 10
    fi
done
echo "PASS: $rounds CPU capture rounds; logs=$logs"
