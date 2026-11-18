## Launcher-based race disconnect diagnostics

With a distribution Windows build, open the **Online** tab on the start screen and expand **Race connection diagnostics**. Check **Log race connection issues**, then click **Play**. The launcher persists `MKW_NET_TRACE=1` and `MKW_SEQ_WATCH=1` in the selected installation's `mkw.ini`; Play starts the game in a fresh process so both are active from boot. **No manual edits or code injection required.** Repeat on both PCs.

After a failed race, collect the normal `logs/mkw.log` from each installation. Useful lines are `net-udp:` (socket packet/byte counters), `net-socket:` (nontransient guest socket errors and caller return address), and `seq:` (game phases, including P2PSync and AppErrorViewer). The game may still display a generic disconnect; these are diagnostic signals, not a promise to decode the game's private error result.

Switch **Log race connection issues** off after capturing the logs to avoid diagnostic overhead. The Arch lobby/relay services do **not** need updating for this Windows-client change.

# Multiplayer prototype

This branch integrates the private-server and client work from PRs #353 and #354, retains PR #345's VPN LAN support,
and adds an **Online play** tab to the F1 menu. Online remains off by default. This is experimental: automated server
protocol tests pass, but a two-PC game race and the complete in-game Online Play flow remain unverified.

## Host and join from F1

1. Both players use this modified build and the same game version. Start without mods.
2. For internet play, use the shared Linux server below or connect both PCs to the same trusted Tailscale network.
   On a home LAN, no VPN is needed.
3. For a locally hosted server, open **F1 > Online play**, select Tailscale (or Home network), then **Start**. Wait for
   **Local server running**. For the shared Linux server, do not start a second host here.
4. Copy the **server invite code** and give it to your friend. It includes the private IPv4 endpoint and a typing-error
   checksum. It is not a password or room reservation; keep VPN membership and firewall access restricted to friends.
5. Friend: open the same tab, paste the complete `MKW1-...` invite, then **Connect and save server**. The client probes
   the endpoint, obtains its CA on first use, and saves the server choice. A failed connection preserves the old choice.
6. Restart the game after selecting a server or enabling online. Host mode starts the server again with the launcher.
   The launcher also has an Online play page for server configuration and friend requests.
7. In the game, choose **Online Play > Friends**. The host creates a group and shares the game's room code; the friend
   joins using that room code. The server implements six-digit room aliases. This final game flow still needs manual
   validation on this branch. The F1 server invite selects the server, not a room or race.
8. **Stop** ends the local server, including one started by the launcher, and disconnects its players. Normal Stop
   signals a Windows event so the server closes connections and flushes pending account data; an owned process that
   fails to exit in five seconds is terminated. **Turn custom online off** also restores offline network behavior.

Network/probe/process/file operations from F1 run on a worker, not the present or replay thread. Buttons support the
existing ImGui controller navigation. Entering or pasting an invite currently needs a keyboard; controller text entry
is not implemented. Q/E tab shortcuts are suppressed while typing. Worldwide matchmaking is not a verified feature.

The game's socket-backed Online flow also calls `nn::ldn::Initialize`; the runtime treats that as an idle service
initialization when `MKW_ONLINE=1`, while local-wireless station/access-point operations remain unavailable. This
avoids turning that setup call into error `2203-0022`; it does not implement wireless emulation or prove a race works.


### Windows UDP trace during a race

To distinguish an application-level 120-second timeout from packet loss, start **both** Windows clients from CMD with the optional host-side trace enabled:

```cmd
set MKW_NET_TRACE=1
mkw.exe
```

Run this from the directory containing the Windows game executable (or replace `mkw.exe` with its full path). This environment variable is **not** an `mkw.ini` key. Each client's normal `logs/mkw.log` will include `net-udp: opened`, `bind`, `tx packets`, `rx packets` and nontransient socket errors, with no packet payloads logged. Packet counters are sampled on the first packet and each 256 packets. Compare whether reception continues on **both** machines immediately before `StartTimeoutCounter` and `AppErrorViewer`.

A successful HTTP/2 Gamesync stream does not confirm that UDP/Pia gameplay traffic works. If one PC transmits but never receives, check direct P2P reachability and TURN allocation/port forwarding (UDP 3479 and 49152–49407) before changing the game's timeout. An isolated `EAGAIN` on a nonblocking socket is normal and intentionally not logged.

### Diagnosing a simultaneous mid-race disconnect

On the Arch Linux host, record the session logs with timestamps immediately after a two-PC test:

```sh
journalctl --user -u mkw-lobby.service -u mkw-relay.service --since '20 minutes ago' --no-pager > mkw-server-test.log
```

The lobby now logs Gamesync listener opens/closes (session, member, target count and remaining streams) and the HTTP/2 closure details (`h2_error`, `client_half_closed`, `server_done`). Compare these with the first in-game error and race state transition. A clean `KeepUserSession -> 0` **after** the error is not proof the server timed out the game.

For the public server, re-run `tools/run/host_server.py` with its existing `--root`, `--lan-ip`, `--public-ip` and `--start` values after updating the executable; it now restarts already-active services to apply newly written relay configuration. **This interrupts active games**. Existing `turn_secret` is retained. Check `systemctl --user status mkw-lobby mkw-relay` after restarting. The old coturn `no-dtls` and `no-cli` flags were removed because the current coturn build warns about them. Do not publish the TURN secret from `server/settings.txt` or `turnserver.conf`.

## Shared public server on Linux

The standalone server and coturn relay are separate processes. Build the lobby with
`uv run tools/build/server.py`, install coturn at `deps/coturn/bin/turnserver`, then configure and start both with:

```sh
python tools/run/host_server.py --root /projects/mkw-recomp-server \
  --lan-ip SERVER_LAN_IP --public-ip ROUTER_PUBLIC_IP --start
```

This configures the lobby on TCP 18443, its friends API on TCP 18444, and coturn on UDP 3479 with relay ports
49152–49407. The lobby also needs UDP 3478 (STUN) and 10025, 10125, 33334 (NAT checks). Keep the generated
`server/settings.txt`, certificates, account data and relay secret private; do not send them to friends.

Forward these ports through every NAT layer, keeping the same external and internal port and protocol:

| Protocol | Ports | Forward to |
|---|---|---|
| TCP | 18443, 18444 | the Arch server's LAN address |
| UDP | 3478, 3479, 10025, 10125, 33334 | the Arch server's LAN address |
| UDP | 49152–49407 | the Arch server's LAN address |

Reserve the Arch server's LAN address and the downstream router's WAN address in DHCP. If the Arch server is behind
an AX53 that is itself behind an ISP router, forward the ports on the ISP router to the AX53's WAN address, then
forward them again on the AX53 to the Arch server. Also permit the same ports in the Arch host firewall. A changing
public IPv4 must be updated in the server configuration when it changes.

On each game PC, enter the public IPv4 or hostname plus `:18443` in **F1 > Online play**, select **Connect and save**,
then restart the game before signing in. The embedded host uses TCP 8443/8444 and private-network invites; those are
not the ports for this standalone Linux service. The matcher supports multiple simultaneous sessions, with a
per-process cap of 256 sessions; this is not a load test or multi-process clustering.

The game's public TCP lobby and friends API, successful sign-in, and a TURN allocation do not prove a playable race.
The two-PC race still needs manual validation. If a countdown disconnects, retain both clients' `logs/mkw.log`, the
server log and the on-screen error code before restarting; the host's Gamesync stream ending alone does not identify
whether the cause was peer traffic or session synchronization.
For Tailscale, the online path reports the route address with the overlay's
`255.192.0.0` mask, so both clients are seen within `100.64.0.0/10`. The firewall button asks Windows to permit this
executable from local subnets and Tailscale addresses only; it does not configure your router. First-use CA retrieval
trusts the selected VPN endpoint; server invites carry no cryptographic identity proof. Existing saved CAs are retained
by the F1 connection flow.

## Windows build and setup

Install uv, CMake and MSYS2 UCRT64 Ninja as described in [tools/build](../../tools/build/README.md#on-windows).
From the repository root in PowerShell:

```powershell
uv run tools/build/windeps.py
uv run tools/build/build.py --target windows --dist --runtime-only
uv run tools/build/dist.py --target windows
uv run pytest tests/test_lan_peers.py
uv run tools/git/cfg_rows.py
uv run tools/docs/check.py
```

The `dist.py` step assembles the portable setup tools and dependency DLLs; a runtime-only build by itself is not a
complete player installation. Run `build/dist/windows/mkw.exe` and use Game files to prepare your own legitimate dump. Both PCs must use this
client build and the same game version and compatible mods. Start with 1.0.0 and no mods; 1.8.0 needs separate testing.

For the first private internet test, put both PCs on the same Tailscale network and verify they can reach one another
with `tailscale ping`. Permit only trusted friends through the VPN's access controls. On each PC, set its own VPN IPv4
address and the other PC's VPN IPv4 address before launching. Replace these examples with real addresses:

```powershell
# PC A (host)
$env:MKW_LAN = '1'
$env:MKW_LAN_IP = '100.64.0.1'
$env:MKW_LAN_PEERS = '100.64.0.2'
& ./build/dist/windows/mkw.exe
```

On PC B swap the two addresses. Settings apply to the launched process. Close the game before changing them.
In the game's mode menu, use its LAN mode (the console shortcut is holding L and R and pressing the left stick),
then create a group on A and find/join it on B. Confirm this shortcut and the actual menu labels in the game build
under test. Do not select Nintendo's internet Online mode: its services remain unavailable.

Allow this specific executable through Windows Firewall for the trusted VPN peers. Discovery uses UDP, including
the game's port 49152; allowing only that port has not been established to cover all gameplay traffic. Prefer an
application rule restricted to the two VPN addresses. Do not disable the firewall. The log should report each peer
and the selected VPN address with a 255.192.0.0 mask for Tailscale.

Tailscale does not carry LAN broadcasts. This client copies lobby broadcasts to up to 16 configured peers; ordinary
game packets retain their existing destinations. Tailscale's /32 interface mask is widened only in the address
reported to the game so Pia accepts the network. This does not change Windows routing. Use numeric IPv4 peers to
avoid DNS delays. ZeroTier is another candidate, but its address range, subnet and broadcast behavior need testing.

The LAN discovery path rejects public destinations and requires local discovery. For internet play, use the shared
Linux server above; its F1 server-address field accepts a public IPv4 or hostname and port. Tailscale remains an
alternative for private groups and does not require router port forwarding.

## Acceptance test (still required on this branch)

The server now defaults a private room's missing integer `rule` attribute to 3. Previously the host knew it had
created a private room, but a joiner reading the missing attribute classified it as a public race (rule 0).
Those different start paths match the reported host waiting 25 seconds in SyncState while the other player
continues after the host leaves. The regression reproduces the missing attribute on the previous executable,
then checks creation tracking, room lookup and join on the fixed server. This corrects the protocol defect;
the reported countdown disconnect still needs a real two-PC retest. Restart the hosting server and create a
new room: rooms already in memory do not acquire the attribute from a new executable on disk.

Before a game run, record the hypothesis: both clients discover the group and exchange the game's existing race
traffic over the VPN. Discovery failure points to address selection, peer configuration or firewall policy; joining
without a playable race points to a gameplay/session problem that lobby success alone cannot establish.

1. Record the branch commit, Windows and game versions, GPU, VPN addresses and mods on both PCs.
2. A creates a group; B discovers and joins. Verify both player identities on both screens.
3. Start the same race and confirm each sees the other's movement and item interactions.
4. Finish one entire race; compare results on both PCs and return to the group. Start a second race.
5. Repeat with B hosting. Test guest leave/rejoin, guest network loss, host leave and host process termination.
   Record whether the group closes or migrates; host migration is not promised by this implementation.
6. Restart without `MKW_LAN_PEERS` and check normal LAN discovery; restart with `MKW_LAN=0` and check single-player.
7. Summarize each log with `uv run tools/run/summary.py PATH` before searching it. Keep logs and game images out of git.

Passing the peer-helper test proves configuration and address selection only. It does not prove packet delivery,
race initialization, synchronized inputs/state, results, disconnect recovery or two-PC compatibility.

Local validation on Windows: the runtime-only build links successfully; both changed network files pass Clang
`-Wall -Wextra -Wshadow` syntax checks; the peer-helper and settings tests pass (2 tests); Ruff, settings-row and
documentation checks pass. Portable package assembly and game runs were not performed. `--selftest` with dependency
DLLs on PATH stops at the missing game setup (exit 70), so runtime self-tests are not counted as passed.

## Source and verification status

The integration uses Gael50's MIT commits `ca1567904cfabca413fa0b73ffe030ba69d109b7` (server, #353),
`918bbd6523729cd18636c051a1e6e62101fd155f` (client, #354), and #343/#344's kernel and localization prerequisites.
These PRs were open and unmerged when inspected. Their source work and third-party dependencies are credited in
[research.md](research.md#sources). The server protocol is implemented at the SDK/service API level, with no CPU or
GPU emulation and no copied Nintendo code or assets.

Verification for the F1 integration: a Windows runtime/server executable links in an isolated test folder; the
real server's ten tests pass, including TLS, UDP, malformed messages, memory limits, synthetic two-member room join,
Gamesync, results/referee and matchmaking. Windows fixture teardown signals the actual shutdown event and verifies
normal exit, final log and PID-file removal. Invite, VPN peer and address-change tests pass. Localization and settings
checks pass. The shared Online settings tab was rendered and visually inspected in the launcher, without game images.
These checks are not proof of two clients completing a race. Use the acceptance checklist above before calling it
working multiplayer. The user's existing executable must be closed before a new build can replace it.

F1 now lists authenticated runtime clients separately from game-room members. A dedicated background worker renews
the client lease every 5 seconds even while F1 is closed. Clients that disappear expire after 20 seconds; selecting
a server alone does not create a room. Each row distinguishes server connectivity from game sign-in. The room list
comes from the server's actual session for that player and is not the whole server's client list. Both PCs and the
host need the updated build. Old servers report that the client-status API is unavailable.

The reported 1.8.0 Online Play crash was traced to SDK libc++ `steady_clock::now()` calling `clock_gettime(1)` through
an unavailable console time hook. `ov_time2.c` now supplies CLOCK_MONOTONIC from `plat_now_ns()` as well as realtime
from the wall clock. A standalone regression covers both clocks and wall-clock adjustments. A real 1.8.0 run with
a build-local profile copy reached the Online Play mode page and signed in successfully after this fix. A two-PC
race and private group creation in the real game remain unverified.

Next: test actual Online Play sign-in and a private room with two game clients, then complete a race and test guest
and host disconnects. Keep the existing LAN fallback available throughout. Defer the standalone launcher and public
server directory until this path is validated.
