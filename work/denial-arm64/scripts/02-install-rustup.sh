#!/bin/bash
set -x
export HOME=/home/fuxi
export CARGO_HOME=/home/fuxi/.cargo
export RUSTUP_HOME=/home/fuxi/.rustup
export PATH="$CARGO_HOME/bin:$PATH"
date -Is
curl --proto '=https' --tlsv1.2 -sSf -m 120 https://sh.rustup.rs -o /tmp/rustup-init.sh || { echo "download rustup-init FAILED"; exit 1; }
sh /tmp/rustup-init.sh -y --profile minimal --default-toolchain none --no-modify-path
rustup toolchain install 1.98.0 --profile minimal -c clippy,rustfmt
rc=$?
rustup toolchain list
rustc --version; cargo --version
echo "rustup_rc=$rc"
date -Is
exit $rc
