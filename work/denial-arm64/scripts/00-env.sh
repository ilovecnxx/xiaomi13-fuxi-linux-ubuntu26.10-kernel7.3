#!/bin/bash
# Denial ARM64 native build environment on fuxi (source this)
export HOME=/home/fuxi
export XDG_CACHE_HOME=/home/fuxi/.cache
export CARGO_HOME=/home/fuxi/.cargo
export RUSTUP_HOME=/home/fuxi/.rustup
export PATH="$CARGO_HOME/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export http_proxy=http://10.42.0.1:7898
export https_proxy=$http_proxy
export HTTP_PROXY=$http_proxy
export HTTPS_PROXY=$http_proxy
export http_proxy_lower=$http_proxy
export no_proxy=localhost,127.0.0.1,10.42.0.1,10.42.0.2
export NO_PROXY=$no_proxy
export DENIAL_PC_BUILD_ROOT=/home/fuxi/denial-cache/pc-build
export DENIAL_FLUTTER_ENGINE_CACHE_ROOT=/home/fuxi/denial-cache/flutter-engine
export DENIAL_PC_DEPENDENCY_ROOT=/home/fuxi/denial-cache/pc-dependencies
export DENIAL_BUILD_JOBS=6
export DENIAL_FLUTTER_ENGINE_JOBS=6
export CARGO_NET_RETRY=5
export GIT_HTTP_LOW_SPEED_LIMIT=1000
export GIT_HTTP_LOW_SPEED_TIME=120
export SOURCE_DATE_EPOCH=1790899200
