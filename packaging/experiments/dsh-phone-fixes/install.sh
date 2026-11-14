#!/bin/bash
set -euo pipefail
SOURCE="$(cd -- "$(dirname -- "$0")" && pwd)"
for script in "$SOURCE"/bin/*; do
  [[ -f "$script" ]] || continue
  install -m 755 "$script" "$HOME/.local/bin/${script##*/}"
done
install -m 755 "$SOURCE/bin/dsh-apply-update" "$HOME/.local/bin/dsh-apply-022.sh"
sudo -n install -m 755 "$SOURCE/bin/dsh-web-open" /usr/local/bin/dsh-web-open
sudo -n ln -sfn "$HOME/.local/bin/dsh-app-window-run" /usr/local/bin/dsh-app-window-run
chmod 700 "$HOME/.local/state/dsh-web"
if [[ -f "$HOME/.local/state/dsh-web/url.txt" ]]; then
  chmod 600 "$HOME/.local/state/dsh-web/url.txt"
fi
"$HOME/.local/bin/dsh-post-update-fixes"
