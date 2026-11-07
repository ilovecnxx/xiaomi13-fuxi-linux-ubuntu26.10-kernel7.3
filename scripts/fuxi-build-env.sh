#!/bin/bash
# Build/run a pinned environment. Use sudo only for engines needing it.
set -euo pipefail
B="$(cd "$(dirname "$0")/.." && pwd)"
engine="${FUXI_CONTAINER_ENGINE:-podman}"
arch="${FUXI_BUILD_ARCH:-amd64}"
case "$arch" in amd64|arm64) ;; *) echo 'arch must be amd64 or arm64' >&2; exit 1;; esac
tag="localhost/fuxi-build:20261107-$arch"
case "${1:-}" in
  image)
    "$engine" build --secret=id=host-ca,src=/etc/ssl/certs/ca-certificates.crt \
      --platform="linux/$arch" -t "$tag" -f "$B/packaging/build/Containerfile" "$B/packaging/build"
    ;;
  run)
    shift
    "$engine" run --rm --pull=never --network=none --platform="linux/$arch" \
      --user "$(stat -c %u "$B"):$(stat -c %g "$B")" \
      -v "$B:/src:rw" -w /src "$tag" "$@"
    ;;
  *) echo 'usage: fuxi-build-env.sh image | run <command...>' >&2; exit 1;;
esac
