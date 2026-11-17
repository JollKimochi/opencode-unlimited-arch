# PLEASE GIVE PROPER CREDITS. JUST DONT REBRAND AND LAUNCH IT AS YOURS.
# NEW VERSION-3.0 (Fixed:503,ECONNRESET, keeper flapping, quota resets, drafts.sqlite unlock, WireGuard enforcement, auto-resume and some other BUGS)
# Opencode-Unlimited-Limit-Bypass (`Remember all burnt IPs reset around midnight of your region`)

[![Version](https://img.shields.io/badge/Version-v3.0-22c55e?style=for-the-badge&logo=semver)](https://github.com/ShadowySphinX/Opencode-Unlimited-Limit-Bypass-)
[![Author](https://img.shields.io/badge/Author-Avatar%20%28ShadowySphinX%29-22d3ee?style=for-the-badge&logo=github)](https://github.com/ShadowySphinX)
[![Repository](https://img.shields.io/badge/GitHub-Opencode--Unlimited-blue?style=for-the-badge&logo=github)](https://github.com/ShadowySphinX/Opencode-Unlimited-Limit-Bypass-)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](https://opensource.org/licenses/MIT)

> **Arch Linux / CachyOS fork.** This fork is based on upstream v3.0 by [Avatar (ShadowySphinX)](https://github.com/ShadowySphinX). It keeps the upstream history and MIT license, and adds a tested Arch/CachyOS setup: MASQUE by default, faster guard rotation, and Arch-specific notes. It is not affiliated with upstream.

Automatic rate-limit rotator, multi-egress proxy router, and autonomous session auto-resume for **OpenCode Desktop** and **OpenCode CLI**.

![OpenCode Unlimited Dashboard](assets/ui-preview.png)

OpenCode's free-tier limits are applied to the inbound public IP address (`/64` prefix on IPv6, `/32` on IPv4). The rotator changes that address and clears the matching client-side lockout.

This tool monitors OpenCode logs for quota and rate-limit errors, cycles the active identity (fresh IPv6 `/64`, Tor circuit, or proxy pool node), and tries to continue the running session without starting a new chat.

---

## Arch Linux / CachyOS (this fork)

Tested on CachyOS with `opencode-beta 2.3.0` and `cloudflare-warp-bin`. This fork is Arch-first; the upstream Windows build is unchanged.

### Quick start

```bash
# 1. Dependencies (yay or paru is required for cloudflare-warp-bin)
yay -S --needed cloudflare-warp-bin opencode-beta

# 2. Clone this fork, not the upstream one-liner
git clone https://github.com/JollKimochi/opencode-unlimited-arch.git
cd opencode-unlimited-arch

# 3. Install the lane, keeper, guard, and shim
bash install.sh
```

If `yay` asks to remove `opencode` because `opencode-beta` conflicts with it, answer **yes**. `opencode-beta` provides `/usr/bin/opencode`; the installer will use it.

### What this fork changes

- **MASQUE by default** in `install.sh` and `scripts/warp-keeper.sh`. WireGuard/UDP is blocked on some networks; MASQUE over 443 is the transport that connects. Override with `OUC_WARP_PROTOCOL=WireGuard` if your ISP throttles MASQUE.
- **Faster guard:** `COOLDOWN=30`, poll `10s`, and the `/64` probe uses Cloudflare `cdn-cgi/trace` through WARP's SOCKS listener, with the old ipify endpoints as fallback.
- **Arch notes and troubleshooting** (below). The upstream Windows-specific sections remain for reference.

### Verify after install

```bash
warp-cli --accept-tos status

curl -x socks5h://127.0.0.1:40000 --max-time 8 \
  https://www.cloudflare.com/cdn-cgi/trace | grep -E '^(warp|ip)='

opencode-unlimited-doctor
```

Expect `Connected`, `warp=on`, and a clean doctor report. Then start OpenCode with the `opencode` shim from `~/.local/bin`.

### Arch / CachyOS troubleshooting

- **Stale server after switching mode or proxy:** close the TUI, then kill the old service before relaunching:
  `pkill -f 'opencode serve --service'`
  A TUI can reconnect to an old server and inherit its previous proxy environment.
- **`opencode` is still the repo package:** `yay -S opencode-beta` replaces it. Check with `pacman -Qo /usr/bin/opencode`; it should say `opencode-beta`.
- **WARP will not connect:** check `warp-cli --accept-tos status`, then restart the keeper:
  `sudo systemctl restart opencode-unlimited-keeper`.
  The default is MASQUE; if your network blocks MASQUE, set `OUC_WARP_PROTOCOL=WireGuard` in the keeper unit and run `warp-cli --accept-tos tunnel protocol set WireGuard`.
- **MCP servers fail only at startup:** wait until `curl` through `127.0.0.1:40000` returns `warp=on` before opening OpenCode. The lane waits for its local proxy, not for WARP's upstream.
- **Guard log:** `tail -f ~/.local/share/opencode-unlimited/guard/guard.log`

---

## Table of Contents
1. [What's New in v3.0](#whats-new-in-v30)
2. [Prerequisites & Downloads](#prerequisites--downloads-what-you-need)
3. [Quick Start (Windows)](#quick-start-windows)
4. [Recommended Settings for Every Method](#recommended-settings-for-every-method)
5. [How Limit Reset Works on Existing Chats](#how-limit-reset-works-on-existing-chats)
6. [Restricted Regions Guide (Russia, Iran, China)](#restricted-regions-guide-russia-iran-china)
7. [Troubleshooting Playbook ("What to do when something happens")](#troubleshooting-playbook-what-to-do-when-something-happens)
8. [Routing Architecture](#routing-architecture)
9. [Linux Setup (Headless & VPS)](#linux-setup-headless--vps)
10. [Building from Source](#building-from-source)

---

## What's New in v3.0

* **Long responses survive flaky edges (Windows):**
  * Fixed a relay bug where the proxy's buffered header reader could swallow the first TLS bytes after `CONNECT`, leaving the tunnel waiting for a response that had already arrived.
  * The relay is now unbuffered, enables `TCP_NODELAY` and TCP keepalive on both sockets, and drains the client briefly when the upstream sends FIN. Windows closes with FIN instead of turning a completed response into an `ECONNRESET`.
  * Mid-stream resets are recorded as `stream_reset` and counted separately from connection failures when the client was still waiting for data. A response that already completed before the server sent RST is not counted. The Proxy tab and health panel now distinguish "the route never came up" from "a live stream was cut".
  * A pinned Cloudflare edge that cuts two live streams within five minutes is retired for new connections. Existing streams are left alone.
  * If WARP reconnects with a new local IPv6 while a request is connecting, the proxy detects the stale bind (`WinError 10049` / `EADDRNOTAVAIL`) and retries once with the fresh address instead of failing every edge.
  * WARP tunnel repair is deferred while another proxy tunnel is active, so a repair cannot kill a response that is still generating. The failed request gets one quick retry first.
* **Connection Recovery & Diagnostics:**
  * The local proxy fails over to another Cloudflare edge instead of stalling on one dead address.
  * A dropped WARP tunnel or proxy route is recorded, repaired, and surfaced in the dashboard instead of returning an empty `503`.
  * **Auto-Repair** is on by default and only performs safe actions: start or restart a stopped proxy, reconnect WARP, repair the tunnel, or re-roll the edge. It never changes the selected mode, clears chats, restarts OpenCode, or deletes the WARP registration, and it respects workers you deliberately disabled.
  * The **Proxy** log tab shows edge failover, `stream_reset`, direct fallback, and tunnel repair events.
  * Standard mode still permits a direct fallback so OpenCode stays usable; the health panel labels it **Using a direct connection** and offers a one-click repair.
* **WARP Keeper Stability:**
  * A connected tunnel is left alone unless the selected WARP scope changed; the keeper repairs disconnected or error states.
  * Routine repairs renew the WireGuard session keys instead of deleting the device registration.
  * In-flight `Connecting` / `Reconnecting` states get a longer grace period before a repair starts.
  * Non-destructive key rotation (`tunnel rotate-keys`) is used for quota rotation.
* **Linux Lane Fixed:**
  * Linux now runs the same hardened Python HTTP CONNECT proxy as Windows, on an ephemeral loopback port. Bun/OpenCode ignores `socks5://` proxy variables, so pointing `HTTPS_PROXY` at WARP's SOCKS listener was never enough.
  * The installer deploys `ouc_common.py`, `zen_proxy.py`, `proxy_chain.py`, and `tor_manager.py` to `$DEST/python`, and `zen-lane.sh` wires the launched OpenCode process to that proxy. There is no dead `41987` fallback on Linux.
  * Isolated proxy mode remains the installer default. `OUC_WARP_MODE=warp` is the explicit opt-in for full TUN.
  * Rotation pauses the keeper with a pause file plus a service stop/start, so a keeper tick cannot reconnect WARP underneath a rotation.
  * `opencode-unlimited-doctor` now checks the Python proxy modules, WARP port `40000`, resolved iptables paths, both services, the shim, and stale pause files.
* **Install/Repair on non-English Windows:**
  * The `.cmd` shims are written as UTF-8 with `chcp 65001` when the Windows profile path contains non-ASCII characters. Previously the app stopped with `UnicodeEncodeError: 'ascii' codec can't encode characters ...` on those systems.
  * ASCII-only paths keep the previous plain shims and do not touch the console code page.
* **Existing Chat Auto-Unlock & History Preservation:**
  * Removes client-side rate limit lockouts (`free-limit`, `go-upsell`) from `%APPDATA%\ai.opencode.desktop\drafts.sqlite` after a successful rotation.
  * Leaves the chat history and session tables untouched.
  * Reloads the Desktop window and sends `continue` when the window can be focused safely; otherwise it falls back to a CLI resume.
* **Detached Desktop Launching:**
  * OpenCode Desktop now launches as an independent detached process (`os.startfile`), so it stays open even if the launcher or terminal is closed.

---

## Prerequisites & Downloads (What You Need)

Install these components before setup:

| Component | Purpose | Requirement | Official Link |
| :--- | :--- | :---: | :--- |
| **OpenCode (Desktop or CLI)** | The AI coding assistant | **Required** | [Download OpenCode](https://opencode.ai/) or [GitHub Releases](https://github.com/opencode-ai/opencode) |
| **Cloudflare WARP** | Egress IPv6 `/64` provider | **Required** (for WARP modes) | [Download Cloudflare WARP (1.1.1.1)](https://one.one.one.one/) |
| **Microsoft WebView2 Runtime** | Powers the modern dashboard UI | **Pre-installed** on Win 10/11 | [WebView2 Evergreen Bootstrapper](https://developer.microsoft.com/en-us/microsoft-edge/webview2/) |
| **Python 3.10+** | Only needed if running from source | Optional (binary is standalone) | [Download Python](https://www.python.org/downloads/) |
| **Tor Expert Bundle** | Decentralized IP circuit rotation | Optional (for Tor mode) | [Tor Project Downloads](https://www.torproject.org/download/tor/) *(or use in-app 1-click download)* |
| **xdotool** | Linux window focus & reload | Optional (Linux GUI users) | `sudo apt install xdotool` / `sudo dnf install xdotool` |

---

## Quick Start (Windows)

> You do **not** need Python installed. Everything is bundled into a single standalone program.

1. **Download & Run:**
   * Download **`OpencodeUnlimited-Multi.exe`** from the repository root, or clone the source and build it.
   * Double-click to launch the dashboard.
2. **Verify Protection Controls (Left Panel):**
   * Ensure these switches are **ON** (they are on by default):
     * **WARP Keeper:** ON
     * **Zen Guard:** ON
     * **Desktop App Proxy:** ON
     * **Auto-Resume on Bypass:** ON (continues the session after a rotation)
3. **Use OpenCode Normally:**
   * Open OpenCode Desktop or run `opencode` in your command prompt/terminal.
   * Work normally. When you hit a free-tier rate limit, the app rotates the egress address and tries to resume the session, which avoids waiting for the quota timer.

---

## Recommended Settings for Every Method

Select the method that best matches your network environment:

### Method 1: Cloudflare WARP (Default)
* **Best for:** General use with IPv6 `/64` rotation.
* **Recommended Settings:**
  * **Scope (Windows):** `Full System TUN` (keeps the WARP adapter active; recommended if App-Only mode shows no adapter).
  * **Scope (Linux / VPS):** `App Only (Isolated)` on port `40000` (leaves the system's normal routing and SSH access in place).
  * **Protocol:** `WireGuard` (MASQUE uses UDP 443, which some ISPs throttle or block).
  * **Switches:** Turn ON **WARP Keeper**, **Zen Guard**, **Auto-Resume**, and **Auto-Repair**.
  * **Strict mode:** Leave it OFF for normal use. It is only for users who prefer a failed request over a direct fallback.

### Method 2: Clean WireGuard Endpoints (Best for ISP Throttling / Restricted Countries)
* **Best for:** Users in Russia, Iran, China, or university/corporate networks where Cloudflare's default Anycast IPs are throttled or blocked (`CF_HAPPY_EYEBALLS_MITM_FAILURE`).
* **Recommended Settings:**
  1. Open the dashboard and click the **Clean Endpoint** tab.
  2. Click **Ping Test** (benchmarks the built-in Anycast endpoint list).
  3. Select the node with the lowest ping (e.g. `162.159.192.1:2408` or port `500` / `4500`).
  4. Click **Apply Endpoint**. WireGuard connects to the selected endpoint.

### Method 3: Tor Network (Best for Strict Censorship & Maximum Privacy)
* **Best for:** Users whose networks completely block UDP / WireGuard traffic, or who require decentralized exit nodes.
* **Recommended Settings:**
  1. Click the **Tor Network** tab on the dashboard.
  2. Click **Start Tor Daemon** (launches an isolated portable Tor daemon on `127.0.0.1:9050` with control port `9051`).
  3. If you already run **Tor Browser**, the app automatically hooks into port `9150`.
  4. On rate limits, a `SIGNAL NEWNYM` command requests a new circuit and waits for a different IPv4 exit address.

### Method 4: Custom Proxy Pool (Clash / V2Ray / Xray / sing-box / Residential)
* **Best for:** Users with established proxy subscriptions or local proxy clients.
* **Recommended Settings:**
  1. Click the **Custom Proxy** tab.
  2. Paste your local proxy address (e.g. `127.0.0.1:7890` or `127.0.0.1:10808`).
  3. Format supported: Single address or multi-line pool (separated by newlines, commas, or semicolons).
  4. Choose protocol: **SOCKS5** or **HTTP**.
  5. On a rate limit, the guard switches to the next pool node and verifies its exit IP before reporting success.

---

## How Limit Reset Works on Existing Chats

A common concern is whether rotating limits requires starting a brand-new chat. It does not. The reset clears the lockout and continues the existing chat.

```
[ User Active Chat / Session ]
           |
           v
+------------------------------------+
| Layer 1: Client SQLite Lock        |  -> %APPDATA%\ai.opencode.desktop\drafts.sqlite
|        ('free-limit' key)          |     disables the input bar and shows the upsell banner
+------------------+-----------------+
                   | purged by Zen Guard / Deep Reset
                   v
+------------------------------------+
| Layer 2: Upstream Cloudflare       |  -> evaluated per egress IP (CF-Connecting-IP)
|        Server Rate Limit           |     applies to the egress IP, not the conversation ID
+------------------------------------+
```

1. **Server-Side Quota (IP-Based):** OpenCode's backend applies free-tier limits to the incoming public IP address. Rotating the IPv6 `/64` presents a different address, which is the behavior the rotator relies on.
2. **Client-Side Lockout Removal:** When OpenCode Desktop hits a rate limit, the local Electron app writes a lockout timestamp into `%APPDATA%\ai.opencode.desktop\drafts.sqlite` (`state` table). Zen Guard deletes this key (`DELETE FROM state WHERE key IN ('free-limit', 'go-upsell')`).
3. **History Preservation:** The reset only touches those lockout keys. Chat history, system prompts, code blocks, and context in the `document` and `session_v2` tables are left unchanged.
4. **Window Reload & Resume:** The app reloads OpenCode Desktop, which refreshes the view and clears the banner, then sends `continue{ENTER}`. If the window cannot be focused safely, it falls back to a CLI resume.

---

## Troubleshooting Playbook ("What to do when something happens")

### 1. What to do when you hit a rate limit wall
* **Automatic Behavior:** With Zen Guard and Auto-Resume on, the guard rotates the egress address, clears the client lockout, and attempts to resume the session. A rotation usually completes within a minute.
* **Manual Override:** To rotate now, click the hop button on the dashboard. Its label follows the active mode: **Rotate /64 Now (Hop)** for WARP, **Rotate Circuit (Hop)** for Tor, and **Rotate Proxy (Hop)** for a custom pool.

### 2. What to do if OpenCode shows "You've reached your free usage limit" banner in an existing chat
* **Cause:** The client-side Electron app is still holding the lockout timestamp in `drafts.sqlite`.
* **Fix:** Click **Deep Reset** on the dashboard, or press `Ctrl+R` in OpenCode Desktop. This usually clears the banner and unlocks the input bar.

### 3. What to do if you get `Connection lost while reading the response: ECONNRESET`
* **What it means:** OpenCode had already received part of a response when the TCP stream ended abruptly. Common causes are a Cloudflare edge recycling a long-lived connection, an ISP or WARP path reset, or the provider closing an idle stream. It is not automatically a quota error, and it does not prove that WARP is down.
* **What v3.0 changed:**
  1. The proxy no longer buffers bytes past the `CONNECT` header. That bug could leave the TLS tunnel half-open until the client timed out or reset.
  2. Both sockets use TCP keepalive and `TCP_NODELAY`, and the proxy drains the client briefly when the upstream sends FIN. This makes Windows close with FIN instead of RST.
  3. A reset after response bytes started, while the client was still waiting for data, is logged as `stream_reset` (not `reset`) and counted in `stream_resets_5m`. A completed response that ends with a server RST after the client closed is not counted.
  4. If the same pinned edge cuts two live streams within five minutes, new connections move to another edge. The current stream is not force-killed.
  5. Tunnel repair waits while another proxy tunnel is active, so a repair cannot tear down a response that is still generating.
* **If it keeps happening:**
  1. Open the **Proxy** tab. `stream_reset` lines on different edges point at the provider or the local network; repeated lines on one edge are what the reroll handles.
  2. Press **Rotate route** (or **Re-roll**) to move to a fresh exit. In Tor mode, restart the circuit instead.
  3. Keep **Auto-Repair** on. It retires a repeatedly failing edge without changing your selected mode.
  4. Do not delete the WARP registration for this error. If WARP itself is disconnected, use **Fix WARP (Reset Keys)**.
  5. If you use Full TUN, make sure no third-party VPN is bound to the same Wintun adapter. If you use App-Only mode, confirm `127.0.0.1:40000` is listening.
* **Honest limit:** a single reset can still come from the provider side. v3.0 makes it visible and recovers the route; it cannot make a remote server keep a stream open indefinitely.

### 3.1 What to do if OpenCode returns `503 Service Unavailable`
* **Cause:** The selected Cloudflare edge or WARP tunnel stopped answering. A stale edge, ISP UDP drop, or a proxy that is not running can all produce a 503.
* **Fix:**
  1. Open the **Proxy** log tab and check the last event.
  2. Press **Fix WARP (Reset Keys)** if the tunnel is down, or **Re-roll** if only the edge is failing.
  3. Leave **Auto-Repair** on. It retries safe fixes with cooldowns instead of looping.
  4. If the health panel says **Using a direct connection**, the app kept OpenCode online through a fallback; press **Repair route** to put it back on your selected egress.

### 4. What to do if WARP keeps reconnecting or looping (`Connecting...`)
* **Cause:** ISP UDP packet drops or corrupt local WireGuard keys.
* **Fix:**
  1. Click **Fix WARP (Reset Keys)** on the dashboard. This rotates cryptographic keys without deleting your device identity.
  2. Switch to the **Clean Endpoint** tab, run a **Ping Test**, and click **Apply Endpoint** to bind to a reliable IP/port (e.g. port `2408` or `500`).

### 5. What to do if WARP shows "Network: unstable"
* **Notice:** Cloudflare WARP can report `Network: unstable` when latency is high or during initial packet bursts. It does not necessarily mean your internet is down.
* In v3.0, the app treats "unstable" as healthy traffic. Do not disconnect or delete your registration.

### 6. What to do if you get `CF_HAPPY_EYEBALLS_MITM_FAILURE`
* **Cause:** MASQUE protocol (UDP 443) blocked by ISP or stale Anycast routing.
* **Fix:** Click **Fix WARP (Reset Keys)** on the dashboard. The app automatically locks the tunnel into WireGuard mode, which bypasses MASQUE MITM checks.

### 7. What to do if port 42914 or 41987 is already in use
* **Cause:** OpenCode's managed service (42914) or this proxy (41987) did not release its socket, usually after a forced shutdown.
* **Fix:** Click **Fix Port Conflict** on the dashboard. The app stops and restarts the OpenCode service through its own CLI, then re-wires and verifies the local proxy.

### 8. Windows SmartScreen or antivirus warning
* **Cause:** PyInstaller one-file executables are occasionally flagged by heuristics or reputation checks. This is more common for unsigned binaries.
* **What the build does:** The build uses `--noupx` and embeds a version resource to reduce avoidable heuristic triggers.
* **Options:** Build from source, verify the downloaded file's SHA-256 hash, or sign the executable with your own code-signing certificate (`OUC_SIGN_PFX` + `OUC_SIGN_PASSWORD`, or `OUC_SIGN_THUMBPRINT`). A trusted signature is the reliable way to clear SmartScreen reputation warnings.

---

## Routing Architecture

```
[ OpenCode Desktop / CLI ]
            |
            v  HTTP / HTTPS via service.json
[ 127.0.0.1:41987  Zen Proxy (TCP keepalive, edge failover) ]
            |
   +--------+------------------------------------------------------+
   | Active egress mode                                            |
   +--------+------------------------------------------------------+
            |
            +-- [1] WARP Full TUN      -> Wintun adapter -> system-wide Cloudflare VPN
            +-- [2] WARP Isolated      -> 127.0.0.1:40000 SOCKS5 -> Cloudflare edge / fresh /64
            +-- [3] Clean Endpoint     -> clean WireGuard IP:2408 -> selected Anycast edge
            +-- [4] Tor Network        -> 127.0.0.1:9050 / 9150 -> 3-hop circuit / fresh IPv4
            +-- [5] Custom Proxy Pool  -> node #1 -> node #2 -> ... -> verified upstream
```

On Linux the lane starts this same proxy on an ephemeral loopback port and exports it only to the launched OpenCode process. Port `41987` is the Windows service proxy.

---

## Linux Setup (Headless & VPS)

Run the automated one-shot installer:
```bash
bash install.sh
```

* **Compatibility:** Debian, Ubuntu, Fedora, Arch Linux, NixOS, and openSUSE.
* **Where things go:** state and workers live in `${XDG_DATA_HOME:-$HOME/.local/share}/opencode-unlimited`; the `opencode` shim goes to `${XDG_BIN_HOME:-$HOME/.local/bin}`. The installer deploys the lane proxy modules to `$DEST/python`.
* **Lane proxy:** `zen-lane.sh` starts `zen_proxy.py` on an ephemeral loopback port and exports `HTTP_PROXY`, `HTTPS_PROXY`, and `ALL_PROXY` to it for the launched OpenCode process. This is required because Bun/OpenCode ignores `socks5://` proxy variables. The lane fails closed if the proxy cannot start; it does not fall back to a dead `41987`.
* **WARP mode:** the installer pins both units to `OUC_WARP_MODE=proxy`, WARP's isolated SOCKS listener on `127.0.0.1:40000`. This keeps SSH and normal host routing intact. Full TUN is an explicit opt-in: set `OUC_WARP_MODE=warp` in the keeper and guard units, then restart them.
* **Systemd Daemons:** Installs `opencode-unlimited-keeper.service` (root) and `opencode-unlimited-guard.service` (user). The installer enables lingering where possible so the user guard survives logout on a headless VPS.
* **Cloud VPS Safe:** Adds firewall rules that allow SSH (port 22 and custom ports) and keeps WARP in isolated proxy mode so the system's normal routing stays in place.
* **Sudoers:** only the exact commands used by the workers are passwordless: `warp-cli`, `systemctl stop/start opencode-unlimited-keeper`, and the resolved `iptables` / `ip6tables` list checks. The guard never falls back to interactive `sudo`, which would hang without a TTY.
* **Rotation:** the guard writes `$DEST/keeper.pause`, stops the keeper, rotates, then removes the pause file and starts the keeper. A keeper tick cannot reconnect WARP in the middle of a rotation.
* **Existing Chat Unlock:** Clears the lockout keys from `~/.config/ai.opencode.desktop/drafts.sqlite` and runs `nohup opencode run --continue "continue" &` after a successful rotation.

### If something fails on Linux
* Run `opencode-unlimited-doctor`. It checks the lane proxy import from `$DEST/python`, WARP port `40000`, the kill-switch (TUN only), both services, the shim, and stale pause files.
* `WARP proxy port 40000 is not listening` → restart the keeper: `sudo systemctl restart opencode-unlimited-keeper`.
* `Linux lane proxy missing` → rerun `bash install.sh`; the Python modules were not deployed.
* A stuck `$DEST/keeper.pause` after a killed rotation → remove it, then restart the keeper. The doctor warns about this file.
* Do not point OpenCode directly at `socks5://127.0.0.1:40000`. Use the `opencode` shim; it starts the HTTP CONNECT proxy that Bun actually honors.

---

## Building from Source

```bash
git clone https://github.com/ShadowySphinX/Opencode-Unlimited-Limit-Bypass-.git
cd Opencode-Unlimited-Limit-Bypass-
pip install -r requirements.txt
```

To compile the standalone Windows executable:
```powershell
powershell -ExecutionPolicy Bypass -File .\build_multi_exe.ps1
```

The build disables UPX, embeds file version metadata, and publishes the result with a retrying file swap so an open executable produces a clear error instead of a Windows `WinError 5`. To sign the result, set `OUC_SIGN_PFX` and `OUC_SIGN_PASSWORD` (or `OUC_SIGN_THUMBPRINT`) before building; `signtool.exe` must be on `PATH`.

---

## Author & Credits

- Built & Maintained by **[Avatar (ShadowySphinX)](https://github.com/ShadowySphinX)**.
- Official Project Repository: [ShadowySphinX/Opencode-Unlimited-Limit-Bypass-](https://github.com/ShadowySphinX/Opencode-Unlimited-Limit-Bypass-)
- Arch/CachyOS fork maintained by **[JollKimochi](https://github.com/JollKimochi)**: [opencode-unlimited-arch](https://github.com/JollKimochi/opencode-unlimited-arch). This fork is not affiliated with the upstream project.

---

## License

MIT License. Copyright (c) 2026 Avatar (ShadowySphinX).
