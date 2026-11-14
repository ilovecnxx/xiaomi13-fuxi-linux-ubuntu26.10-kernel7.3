#!/bin/bash
# No CameraManager or sensor access: test the actual software ISP lifecycle.
set -euo pipefail
[[ $# == 3 ]] || { echo "usage: $0 TEST_EXECUTABLE AUDIT_SO NEW_LOG_DIR" >&2; exit 2; }
client=$(realpath -e -- "$1")
audit=$(realpath -e -- "$2")
mkdir -m 700 -- "$3"
logs=$(realpath -e -- "$3")
unset EGL_CONTEXT_FAIL_CREATE EGL_CONTEXT_FAIL_CURRENT EGL_CONTEXT_FAIL_CONFIG EGL_CONTEXT_FAIL_SHADER EGL_CONTEXT_FAIL_LINK
run() {
    local name=$1 mode=$2 failures=$3
    shift 3
    timeout -k 3s 60s env LD_PRELOAD="$audit" "$@" "$client" "$mode" > "$logs/$name.log" 2>&1
    grep -Fx "EGL_RESTART rounds=100 failures=$failures result=PASS" "$logs/$name.log"
    if [[ $mode == failure ]]; then
        grep -E '^EGL_CONTEXT .* injected=1$' "$logs/$name.log" >/dev/null
    fi
    echo "PASS $name"
}
run normal normal 0
for stage in CREATE CONFIG CURRENT LINK; do
    run "fail-${stage,,}" failure 1 "EGL_CONTEXT_FAIL_$stage=1"
done
run fail-vertex failure 1 EGL_CONTEXT_FAIL_SHADER=1
run fail-fragment failure 1 EGL_CONTEXT_FAIL_SHADER=2
echo "PASS isolated EGL restart matrix; logs=$logs"
