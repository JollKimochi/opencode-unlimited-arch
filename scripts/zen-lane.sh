#!/bin/bash
# zen lane — launches OpenCode through the local egress proxy.
#
# OpenCode's free-tier limit is applied to the public IP address the server
# sees. On Linux, WARP runs in isolated proxy mode and listens on
# 127.0.0.1:40000. OpenCode's Bun runtime does not understand socks5://
# proxy environment values, so this launcher starts the stdlib HTTP CONNECT
# proxy (zen_proxy.py) on a private localhost port and points the normal
# HTTP(S)_PROXY variables at it.
#
# The proxy handles WARP, Tor, and custom-proxy modes, edge failover, keepalive,
# and fallback reporting. A temporary machine-id is mounted inside a private
# namespace when the kernel allows it; the namespace disappears on exit.
set -u

BRAND="opencode-unlimited"
DEST="${OUC_DEST:-${XDG_DATA_HOME:-$HOME/.local/share}/$BRAND}"
PYPROXY="$DEST/python/zen_proxy.py"

SERVICE_STATE="${XDG_STATE_HOME:-$HOME/.local/state}/opencode/service.json"

# The desktop app ships its own opencode-cli and registers it as the shared
# background service. A version-mismatched TUI replaces that service on
# launch, which restarts the desktop's connection. Prefer the desktop CLI so
# both sides always run the same version; OUC_DESKTOP_CLI=0 opts out.
desktop_cli(){
  local base="$HOME/.config/ai.opencode.desktop/cli" ver dir
  [ -d "$base" ] || return 1
  ver=$(python3 -c '
import json, sys
try: d = json.load(open(sys.argv[1]))
except Exception: raise SystemExit(1)
v = d.get("version")
print(v if isinstance(v, str) else "")' "$SERVICE_STATE" 2>/dev/null)
  if [ -n "$ver" ] && [ -x "$base/$ver-x64/opencode-cli" ]; then
    printf '%s\n' "$base/$ver-x64/opencode-cli"
    return 0
  fi
  dir=$(ls -d "$base"/*/ 2>/dev/null | sort -V | tail -1)
  [ -n "$dir" ] && [ -x "${dir}opencode-cli" ] || return 1
  printf '%s\n' "${dir}opencode-cli"
}

REAL="${OUC_REAL_BIN:-$HOME/.opencode/bin/opencode}"
if [ "${OUC_DESKTOP_CLI:-1}" != "0" ]; then
  DCLI=$(desktop_cli) && REAL="$DCLI"
fi
if [ ! -x "$REAL" ]; then
  for c in "$HOME/.local/bin/opencode" /usr/local/bin/opencode /usr/bin/opencode; do
    [ -x "$c" ] || continue
    # Never treat one of our own shims as the real binary.
    grep -qs "zen-lane" "$c" && continue
    REAL="$c"
    break
  done
fi
[ -x "$REAL" ] || { echo "opencode binary not found — set OUC_REAL_BIN or reinstall" >&2; exit 1; }
[ -f "$PYPROXY" ] || {
  echo "zen-lane: local proxy missing at $PYPROXY — rerun install.sh" >&2
  exit 1
}

TD=$(mktemp -d /tmp/ouc-lane.XXXXXX 2>/dev/null || {
  mkdir -p /tmp/opencode-unlimited
  mktemp -d /tmp/opencode-unlimited/lane.XXXXXX
}) || { echo "zen-lane: could not create a temporary directory" >&2; exit 1; }

PROXY_PID=""
cleanup(){
  if [ -n "$PROXY_PID" ]; then
    kill "$PROXY_PID" 2>/dev/null || true
    wait "$PROXY_PID" 2>/dev/null || true
  fi
  rm -rf "$TD"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# A silent direct fallback burns the home IP and keeps the free-tier wall
# alive no matter how many /64 rotations happen. Fail closed by default;
# set OUC_STRICT_PROXY=0 to restore the old direct fallback.
export OUC_STRICT_PROXY="${OUC_STRICT_PROXY:-1}"

# ---- start the local HTTP CONNECT proxy on an ephemeral port
PORT_FILE="$TD/proxy.port"
PROXY_LOG="$TD/proxy.log"
PYTHONPATH="$DEST/python${PYTHONPATH:+:$PYTHONPATH}" \
  python3 "$PYPROXY" 0 --port-file "$PORT_FILE" >"$PROXY_LOG" 2>&1 &
PROXY_PID=$!

PROXY_PORT=""
for _ in $(seq 1 60); do
  if [ -s "$PORT_FILE" ]; then
    PROXY_PORT=$(cat "$PORT_FILE" 2>/dev/null)
    break
  fi
  kill -0 "$PROXY_PID" 2>/dev/null || break
  sleep 0.2
done

if [ -z "$PROXY_PORT" ]; then
  echo "zen-lane: local egress proxy did not start — refusing to leak the home IP." >&2
  [ -s "$PROXY_LOG" ] && tail -n 6 "$PROXY_LOG" >&2
  echo "Run opencode-unlimited-doctor, then retry." >&2
  exit 1
fi

PROXY_URL="http://127.0.0.1:$PROXY_PORT"
export HTTP_PROXY="$PROXY_URL"
export HTTPS_PROXY="$PROXY_URL"
export ALL_PROXY="$PROXY_URL"
export http_proxy="$PROXY_URL"
export https_proxy="$PROXY_URL"
export all_proxy="$PROXY_URL"
export NO_PROXY="127.0.0.1,localhost,::1"
export no_proxy="$NO_PROXY"

# ---- keep the shared background service on this lane
# OpenCode 2.3 keeps a `serve --service` process that outlives the TUI and is
# reused by later launches. A service started in normal mode has no proxy env:
# every request leaves from the home IP, so WARP /64 rotations change nothing
# and the free-tier wall never clears. If the registered service is not bound
# to a live lane proxy, stop it so this launch spawns a fresh one with our env.

service_lane_port(){
  local pid="$1" env_url port
  kill -0 "$pid" 2>/dev/null || return 1
  # The desktop bundles its CLI as `opencode-cli serve --service`; the system
  # package uses `opencode serve --service`. Match both, or a running desktop
  # service looks unbound and gets killed on every terminal launch.
  tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null \
    | grep -qE '(^|/)opencode(-cli)? serve --service( |$)' || return 1
  env_url=$(tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null \
    | sed -n -e 's/^HTTPS_PROXY=//p' -e 's/^https_proxy=//p' \
             -e 's/^ALL_PROXY=//p' -e 's/^all_proxy=//p' | head -1)
  case "$env_url" in
    http://127.0.0.1:*|https://127.0.0.1:*) ;;
    *) return 1 ;;
  esac
  port="${env_url##*:}"
  port="${port%%/*}"
  case "$port" in ''|*[!0-9]*) return 1 ;; esac
  (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null || return 1
  echo "$port"
}

reap_shared_service(){
  local pid
  [ -f "$SERVICE_STATE" ] || return 0
  pid=$(python3 -c '
import json, sys
try: d = json.load(open(sys.argv[1]))
except Exception: raise SystemExit(1)
p = d.get("pid")
print(p if isinstance(p, int) and p > 0 else "")' "$SERVICE_STATE" 2>/dev/null)
  [ -n "$pid" ] || return 0
  [ -n "$(service_lane_port "$pid")" ] && return 0
  echo "zen-lane: shared OpenCode service $pid is not on the WARP lane — restarting it" >&2
  kill "$pid" 2>/dev/null || true
  for _ in $(seq 1 40); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.1
  done
  kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
  rm -f "$SERVICE_STATE" "$SERVICE_STATE.pty-handoff" 2>/dev/null || true
}

case "${1:-}" in
  ""|run|serve) reap_shared_service ;;
esac

# ---- private machine-id (some endpoints sniff it; fresh per launch costs nothing)
if [ -e /etc/machine-id ]; then
  tr -dc 'a-f0-9' < /dev/urandom | head -c 32 > "$TD/machine-id"
fi

CAN_UNSHARE=0
unshare -rm true 2>/dev/null && CAN_UNSHARE=1

run_opencode(){
  if [ "$CAN_UNSHARE" = "1" ]; then
    unshare -rm bash -c '
      if [ -s "$1/machine-id" ] && [ -e /etc/machine-id ]; then
        mount --bind "$1/machine-id" /etc/machine-id 2>/dev/null || true
      fi
      shift
      exec "$@"
    ' _ "$TD" "$REAL" "$@"
  else
    # Container / VPS kernels can block user namespaces. The proxy environment
    # still carries every HTTP(S) request, so isolated egress remains intact.
    "$REAL" "$@"
  fi
}

run_opencode "$@"
RC=$?
exit "$RC"
