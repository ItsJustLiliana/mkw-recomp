# Multiplayer prototype

This branch brings PR #345's VPN discovery support onto current upstream. It reuses the game's LAN lobby and
gameplay protocol, with no new synchronization protocol, dedicated server or database. LAN remains off by default.
There is no Custom Online menu, join code, public server list or matchmaking in this milestone. The host runs the game.
Existing LAN menus provide controller navigation and group creation/joining; room lifecycle and race behavior remain
the game's responsibility and need the acceptance test below.

## Windows build and setup

Install uv, CMake and MSYS2 UCRT64 Ninja as described in [tools/build](../tools/build/README.md#on-windows).
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

Direct public-IP port forwarding is not supported by this branch: the socket layer rejects public destinations and
the lobby requires discovery. Forwarding one UDP port does not solve those problems. A discovery server alone would
also not solve NAT traversal or relay gameplay. Tailscale may use its own relay infrastructure when direct NAT
traversal fails; this prototype requires no project-operated permanent central server and adds no UPnP mappings.

## Acceptance test (still required on this branch)

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

## Existing dedicated server path and next milestone

Checked 16 November 2026: [#345](https://github.com/adriadam10/mkw-recomp/pull/345),
[#353](https://github.com/adriadam10/mkw-recomp/pull/353) and
[#354](https://github.com/adriadam10/mkw-recomp/pull/354) are open and unmerged. #345's author reports three completed
1.0.0 races on two Windows PCs over Tailscale at `6b191f2`; that is external evidence, not a test of this branch.

#353 (`ca156790`) implements a separate server process in the game executable:
`mkw.exe --server --host ADDR --port N --data DIR`. It serves TLS/HTTP2/gRPC accounts, friends, rooms, Gamesync,
matchmaking and UDP STUN/NAT checks. This command is **not available on this branch**. Its author reports synthetic
two-user server tests and one real client reaching a room, not a completed two-client race.

#354 (`918bbd65`) adds the client redirection, custom CA, account identity and launcher Online page. It depends on
#343, #344, #345 and #353 and reports no two-PC race test. The current checkout only has LAN SDK sockets and offline
friends/local-wireless/online-service stubs, so importing the server alone would not connect the game to it.

Next: review and port that dependency stack separately, build and run its server protocol tests, then perform the
two-PC race test before adding new UI. Inspect account/token and certificate handling, bounded packets/connections,
disconnect cleanup and shutdown along with the gameplay path. Preserve LAN and offline defaults. Only after that
should Custom Online wrap proven host/join operations; Worldwide and server browsing must remain labelled unavailable
until implemented. Defer the standalone launcher, public matchmaking and automatic updates.
