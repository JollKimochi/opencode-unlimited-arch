# Opencode Unlimited - Arch Linux / CachyOS fork

Arch-only fork of [Opencode Unlimited v3](https://github.com/ShadowySphinX/Opencode-Unlimited-Limit-Bypass-) by [Avatar (ShadowySphinX)](https://github.com/ShadowySphinX). MIT licensed, upstream history preserved, not affiliated with upstream.

This is the setup I use on CachyOS: OpenCode CLI + Cloudflare WARP, MASQUE by default, faster guard rotation, and Arch-specific notes. The Windows GUI, EXE, and Windows build scripts are removed from the current tree. Windows users should use the upstream repository.

Tested on CachyOS with `opencode-beta 2.3.0` and `cloudflare-warp-bin`.

## Requirements

- Arch Linux or CachyOS with systemd
- `yay` or `paru` for AUR packages
- `cloudflare-warp-bin`
- `opencode-beta`
- `python3`, `curl`, `ripgrep` (the installer checks these)
- `git` to clone

## Quick start

```bash
# 1. Base dependencies
sudo pacman -S --needed git curl python ripgrep

# 2. AUR packages
yay -S --needed cloudflare-warp-bin opencode-beta
# If yay asks to remove "opencode" because opencode-beta conflicts, answer yes.

# 3. Clone this fork, not the upstream one-liner
git clone --depth 1 https://github.com/JollKimochi/opencode-unlimited-arch.git
cd opencode-unlimited-arch

# 4. Install the lane, keeper, guard, and shim
bash install.sh
```

Open a new terminal after the installer finishes, then run:

```bash
opencode
```

`opencode-beta` provides `/usr/bin/opencode`. The installer adds a shim at `~/.local/bin/opencode`; the shim starts the lane proxy and then the real CLI.

## What this fork changes

- **MASQUE by default** in `install.sh` and `scripts/warp-keeper.sh`. WireGuard/UDP is blocked on some networks; MASQUE over 443 is the transport that connects. Override with `OUC_WARP_PROTOCOL=WireGuard` if your ISP throttles MASQUE.
- **Protocol enforcement:** `zen-guard.sh` and `warp-keeper.sh` re-assert `OUC_WARP_PROTOCOL` (MASQUE by default) before touching the tunnel. Proxy mode only accepts MASQUE; WireGuard there fails with `InvalidKey("Proxy mode only supports MASQUE")`, leaving port 40000 down and every rotation stuck at `no fresh /64`. The guard also holds the wall instead of burning retries when WARP has no IPv6.
- **Session-targeted auto-resume:** `zen-guard.sh` reads the `sessionID` from the quota error and resumes that exact session with `opencode run --session <id> "continue"`. The old `--continue` form is scoped to the guard's working directory (`$HOME`), so the message could land in a different project or fail silently. Resume output is appended to `guard/auto-resume.log`.
- **Faster guard:** `COOLDOWN=30`, poll `10s`, and the `/64` probe uses Cloudflare `cdn-cgi/trace` through WARP's SOCKS listener, with the old ipify endpoints as fallback.
- **Fail-closed lane:** `zen-lane.sh` sets `OUC_STRICT_PROXY=1` by default. If WARP is down, the request fails instead of silently leaving from the home IP and burning the same free-tier quota the lane is trying to rotate. Set `OUC_STRICT_PROXY=0` to restore the old direct fallback.
- **Lane-bound shared service:** the launcher detects OpenCode's shared `serve --service` process and restarts it when it was started outside the lane. Without that, the service keeps sending from the home IP and `/64` rotation can never clear the wall. `opencode-unlimited-doctor` reports whether the service is lane-bound.
- **Protocol-aware repair:** the lane proxy's reconnect path uses `OUC_WARP_PROTOCOL` (MASQUE by default) instead of hard-coding WireGuard. Proxy mode cannot connect with WireGuard, so the old hard-coded value could leave port 40000 dead during a repair.
- **Arch-only tree:** the Windows GUI, EXE, build scripts, and GUI-only Python dependencies are removed from `main`. The upstream history is preserved.

## How it works

OpenCode's free-tier limits are applied to the inbound public IP address (`/64` on IPv6). The guard watches the OpenCode logs for the free-usage-limit message, reads the current WARP IPv6, and rotates WARP when the wall appears. Burned `/64` prefixes are blacklisted until they reset.

On Linux, `zen-lane.sh` starts an ephemeral HTTP CONNECT proxy, exports it to the OpenCode process, and points the upstream at WARP's SOCKS listener on `127.0.0.1:40000`. The CLI never uses the host's direct route. The upstream auto-resume path is kept and uses the same lane.

## Verify after install

```bash
warp-cli --accept-tos status
warp-cli --accept-tos settings | grep -i 'tunnel protocol'
curl -s -x socks5h://127.0.0.1:40000 --max-time 8 \
  https://www.cloudflare.com/cdn-cgi/trace | grep -E '^(warp|ip)='
opencode-unlimited-doctor
pacman -Qo /usr/bin/opencode
opencode --version
type -a opencode
```

Expected:

- `Status update: Connected`
- `WARP tunnel protocol: MASQUE`
- `warp=on`
- doctor reports no failures
- `/usr/bin/opencode` is owned by `opencode-beta`
- `type -a opencode` shows `~/.local/bin/opencode` first

## Troubleshooting

### `opencode` is still the repo package

```bash
pacman -Qo /usr/bin/opencode
```

If it says `owned by opencode`, replace it:

```bash
yay -S opencode-beta
```

Answer **yes** when yay offers to remove `opencode`. After that, `/usr/bin/opencode` should be owned by `opencode-beta`.

### `ECONNREFUSED` or MCP failures at startup

The lane proxy may be up before WARP is ready. Wait for WARP and test the upstream:

```bash
warp-cli --accept-tos status
curl -s -x socks5h://127.0.0.1:40000 --max-time 8 \
  https://www.cloudflare.com/cdn-cgi/trace | grep '^warp=on'
```

Then close OpenCode and open it again. If you changed the proxy or transport mode and a server from the old setup is still alive, kill it before reopening:

```bash
pkill -f 'opencode serve --service'
```

This closes all OpenCode sessions. The old server keeps the old proxy environment, which is why a new TUI can still fail until the server is restarted.

### WARP keeps reconnecting

```bash
warp-cli --accept-tos status
sudo systemctl restart opencode-unlimited-keeper
systemctl --user restart opencode-unlimited-guard
opencode-unlimited-doctor
```

### Rotations fail with `no fresh /64`

Check the protocol before anything else:

```bash
warp-cli --accept-tos settings | grep -i 'tunnel protocol'
```

Proxy mode only supports **MASQUE**. If it says `WireGuard`, every connect fails with `InvalidKey("Proxy mode only supports MASQUE")`, port 40000 stays closed, and the guard can never obtain a new `/64`. Set it back:

```bash
warp-cli --accept-tos tunnel protocol set MASQUE
```

The guard and keeper now re-assert the protocol before every rotation and reconnect, and `opencode-unlimited-doctor` flags the mismatch. If it flips again on its own, find what is writing the setting: an external tool or a manual `warp-cli tunnel protocol set WireGuard` is the only way it changes.

### The guard rotates, but the wall comes back immediately

The free-tier limit is tied to the IP that OpenCode actually uses. If OpenCode's shared background service was started outside the lane, rotating WARP changes nothing: every request still leaves from your home IP.

OpenCode 2.3 keeps one `opencode serve --service` process and the TUI attaches to it. That process inherits its environment when it starts, and it outlives the TUI. Start it in normal mode and it keeps running with no proxy, even after you launch the unlimited shim.

Check what the registered service uses:

```bash
opencode-unlimited-doctor
```

A lane-bound service reports:

```
✓ OpenCode service 12345 is lane-bound (http://127.0.0.1:41234)
```

A direct service reports:

```
! OpenCode service 12345 runs direct — relaunch through the shim
```

`zen-lane.sh` now checks this itself. When the registered service is not bound to a live lane proxy, it stops that service before starting the lane, so the next `opencode` spawns a replacement inside the proxy environment. The lane also runs fail-closed by default (`OUC_STRICT_PROXY=1`): if WARP is unreachable, requests fail instead of quietly falling back to the direct route. Set `OUC_STRICT_PROXY=0` for the old fallback behavior.

Manual fix if you do not want to relaunch:

```bash
pkill -f '[o]pencode serve --service'
opencode
```

Close the normal-mode TUI first. The service is shared, so it cannot be lane-bound and direct at the same time.

### Auto-resume lands in the wrong session

`zen-guard.sh` runs from `$HOME`. The old `opencode run --continue "continue"` form continues the last session of the guard's current project, not necessarily the session that hit the wall. It can land in another project or exit without sending anything, while the guard still logs `[auto-resume] sent 'continue'`.

The guard now extracts `sessionID` from the quota line and resumes exactly that session:

```bash
opencode run --session <session-id> "continue"
```

That form works from any directory. Check `~/.local/share/opencode-unlimited/guard/auto-resume.log` if a resume does not appear in the session.

Rotate as usual when the wall comes back. A fresh `/64` can have quota again; if a rotation produces a block that is already burned, the guard retries and blacklists it. Only if every fresh block walls immediately should you suspect the WARP pool itself.

### `ERR_PROXY_TUNNEL` 503 or `ECONNRESET`

The lane is fail-closed by default (`OUC_STRICT_PROXY=1`). When WARP's local SOCKS listener drops while WARP reconnects, the proxy returns **503** instead of sending the request from your home IP. A mid-stream `ECONNRESET` is the same event: WARP tore down the tunnel that was carrying the response.

This is not the free-tier wall. Check the tunnel and retry:

```bash
warp-cli --accept-tos status
ss -ltn | grep ':40000'
curl -s -x socks5h://127.0.0.1:40000 -m 10 \
  https://www.cloudflare.com/cdn-cgi/trace | grep -E '^(ip|warp)='
```

If the status is `Connected` and the probe returns `warp=on`, retry the request in OpenCode. The lane proxy's repair path now preserves `OUC_WARP_PROTOCOL` (MASQUE by default); it no longer switches the tunnel to WireGuard, which proxy mode cannot use.

If you prefer availability over strictness, launch with `OUC_STRICT_PROXY=0`. Be aware that requests then fall back to the direct route when WARP is down, which burns the same home-IP quota the guard is trying to rotate.

### WireGuard works better than MASQUE

Set the protocol for the installer and for the keeper service:

```bash
OUC_WARP_PROTOCOL=WireGuard bash install.sh
sudo systemctl edit opencode-unlimited-keeper
```

In the editor, add:

```ini
[Service]
Environment=OUC_WARP_PROTOCOL=WireGuard
```

Then:

```bash
sudo systemctl restart opencode-unlimited-keeper
```

The guard service does not set the transport; the keeper does.

### Logs

```bash
tail -50 ~/.local/share/opencode-unlimited/guard/guard.log
sudo journalctl -u opencode-unlimited-keeper -n 80 --no-pager
opencode-unlimited-doctor
```

### Uninstall

From the repository:

```bash
bash uninstall.sh
```

This removes the shim, units, and deployed scripts. The `opencode-beta` and `cloudflare-warp-bin` packages remain installed.

## Update

```bash
cd opencode-unlimited-arch
git pull
bash install.sh
```

The clone uses `--depth 1` so it does not download the old Windows history. If you want the full upstream history:

```bash
git fetch --unshallow
```

## Credits and license

- Upstream: [Avatar (ShadowySphinX)](https://github.com/ShadowySphinX) - [Opencode-Unlimited-Limit-Bypass-](https://github.com/ShadowySphinX/Opencode-Unlimited-Limit-Bypass-)
- Arch fork: [JollKimochi](https://github.com/JollKimochi)
- License: [MIT](LICENSE)

This fork is not affiliated with the upstream author. Keep the upstream credit if you redistribute it.
