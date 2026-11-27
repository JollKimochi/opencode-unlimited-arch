#!/usr/bin/env bash
# Launch OpenCode Desktop through the Opencode Unlimited WARP lane.
#
# The Desktop app runs its own bundled opencode-cli as a background service.
# That service inherits this process environment, so it reaches opencode.ai
# through zen_proxy -> WARP SOCKS (127.0.0.1:40000) instead of the host route.
# The guard keeps watching ~/.local/share/opencode/log/opencode.log and rotates
# the /64 on the free-usage wall, exactly like the CLI lane.
set -euo pipefail

# Match the CLI lane: fail closed when WARP is down instead of silently
# leaving from the home IP.
export OUC_STRICT_PROXY="${OUC_STRICT_PROXY:-1}"

DEST="${OUC_DEST:-${XDG_DATA_HOME:-$HOME/.local/share}/opencode-unlimited}"
PORT="${OUC_DESKTOP_PROXY_PORT:-40100}"
PROXY="http://127.0.0.1:$PORT"

find_app(){
  if [ -n "${OUC_DESKTOP_APP:-}" ]; then printf '%s\n' "$OUC_DESKTOP_APP"; return 0; fi
  local c
  for c in "$(command -v opencode-desktop 2>/dev/null || true)" \
           "$HOME/Downloads/opencode-desktop-linux-x86_64.AppImage" \
           /opt/OpenCode/ai.opencode \
           /opt/OpenCode/OpenCode; do
    if [ -n "$c" ] && [ -x "$c" ]; then printf '%s\n' "$c"; return 0; fi
  done
  return 1
}

port_up(){ timeout 1 bash -c "</dev/tcp/127.0.0.1/$1" 2>/dev/null; }

APP="$(find_app)" || {
  echo "opencode desktop not found. Install opencode-desktop-bin or set OUC_DESKTOP_APP." >&2
  exit 1
}

if ! port_up "$PORT"; then
  nohup python3 "$DEST/python/zen_proxy.py" "$PORT" >/dev/null 2>&1 &
  for _ in $(seq 1 50); do
    port_up "$PORT" && break
    sleep 0.2
  done
fi
if ! port_up "$PORT"; then
  echo "lane proxy failed to start on 127.0.0.1:$PORT" >&2
  exit 1
fi

export HTTP_PROXY="$PROXY" HTTPS_PROXY="$PROXY" ALL_PROXY="$PROXY"
export http_proxy="$PROXY" https_proxy="$PROXY" all_proxy="$PROXY"
export NO_PROXY="127.0.0.1,localhost,::1" no_proxy="127.0.0.1,localhost,::1"

# Electron/Chromium use --proxy-server for their own traffic (login, updates);
# the bundled CLI service uses the environment variables above.
flags=(--proxy-server="$PROXY")
# Root sessions need the Chromium sandbox switch; normal users do not.
[ "$(id -u)" -eq 0 ] && flags+=(--no-sandbox)
exec "$APP" "${flags[@]}" "$@"
