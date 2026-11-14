#!/bin/bash
set -euo pipefail
B="$(cd "$(dirname "$0")/.." && pwd)"
cd "$B"
case "${1:-host}" in
  host|host-restricted)
    if [[ "${1:-host}" == host-restricted ]]; then
      if [[ -n "${CI:-}" && "${CI}" != false && "${CI}" != 0 ]]; then
        echo 'host-restricted is for local diagnostics only; CI must run host' >&2
        exit 2
      fi
      export FUXI_TEST_ALLOW_NO_SOCKETS=1
      echo 'Restricted host diagnostics: a blocked Unix socket may SKIP the real GPG test; this is not release validation.' >&2
    else
      unset FUXI_TEST_ALLOW_NO_SOCKETS
    fi
    while IFS= read -r -d '' script; do bash -n "$script"; done < <(find scripts -name '*.sh' -print0)
    python3 -m unittest discover -s scripts/tests -v
    python3 apps/superterm/tests/run_tests.py
    ;;
  kernel)
    python3 scripts/fuxi-kernel-source.py check
    O="$B/work/kbuild/ci-out"
    mkdir -p "$O"
    make -C build/linux-standard O="$O" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- fuxi_defconfig
    make -C build/linux-standard O="$O" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- W=1 -j4 \
      arch/arm64/kernel/setup.o drivers/power/supply/qcom_battmgr.o \
      drivers/usb/typec/ucsi/debugfs.o qcom/sm8550-xiaomi-fuxi.dtb
    ;;
  userspace)
    scripts/fuxi-build-hexagonrpc.sh
    scripts/fuxi-build-modem.sh
    scripts/fuxi-build-libssc.sh
    python3 scripts/fuxi-verify-deps.py
    ;;
  bindings)
    O="$B/work/kbuild/ci-out"
    make -C build/linux-standard O="$O" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
      dt_binding_check DT_SCHEMA_FILES='display/panel/xiaomi,fuxi-m3-38-0c-0a.yaml:input/touchscreen/st,ftm5.yaml'
    ;;
  *) echo 'usage: fuxi-ci.sh host|host-restricted|kernel|userspace|bindings' >&2; exit 1;;
esac
