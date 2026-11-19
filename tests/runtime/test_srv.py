"""The online server (runtime/srv/). Without the network: the protobuf reader and writer against the encoding guide's
examples and malformed input, the STUN reply against RFC 5769's sample addresses, the NAT check, the value tree, Gamesync
writes and the race referee (tests/runtime/t_srv.c), built with the C compiler at hand, warnings as errors. With MKW_SRV_EXE
set to a built mkw executable (beside its libraries): the real `--server` on 127.0.0.1, asked over UDP (STUN, NAT check)
and over TLS + HTTP/2 the way the game asks: the certificate chains to the CA the server made and names the game's
hosts, ALPN is h2, a unary call gets one empty message and grpc-status 0, a server stream one empty message and stays
open; then a room made, found by its ID and joined, Gamesync between its two players (participants held until both are
in, no echo of unchanged writes, station ids, a race refereed and closed with ratings, a player leaving), matchmaking
backfill and multiple independent lobbies.

Usage: uv run pytest tests            (MKW_SRV_EXE=build/dist/windows/mkw.exe for the server tests)
"""

import os
import shutil
import socket
import ssl
import struct
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRV = ROOT / "runtime/srv"
PORT = 18443  # not 8443: a game's own server may be running here


@pytest.fixture(scope="module")
def t_srv(tmp_path_factory: pytest.TempPathFactory) -> Path:
    cc = shutil.which("clang") or shutil.which("cc") or shutil.which("gcc")
    bundled = ROOT / "build/win/llvm-mingw/bin/x86_64-w64-mingw32-clang.exe"
    if not cc and bundled.exists():
        cc = str(bundled)
    if not cc:
        pytest.skip("no C compiler")
    exe = tmp_path_factory.mktemp("srv") / "t_srv.exe"
    srcs = [ROOT / "tests/runtime/t_srv.c", *(SRV / f"srv_{n}.c" for n in ("pb", "udp", "value", "docs", "referee"))]
    libm = [] if os.name == "nt" else ["-lm"]  # the referee's Elo
    subprocess.run([cc, "-std=gnu11", "-O1", "-Wall", "-Wextra", "-Werror", f"-I{SRV}", *map(str, srcs), "-o", str(exe),
                    *libm], check=True)  # fmt: skip
    return exe


def test_srv_blocks(t_srv: Path) -> None:
    out = subprocess.run([str(t_srv)], capture_output=True, text=True, check=False)
    assert out.returncode == 0 and " 0 failed" in out.stdout, out.stdout


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """The server's data folder while it runs."""
    exe = os.environ.get("MKW_SRV_EXE")
    if not exe:
        pytest.skip("MKW_SRV_EXE not set")
    data = tmp_path_factory.mktemp("srvdata")
    # The three mode flows intentionally perform more than 20 sign-ins from loopback within one minute.
    (data / "settings.txt").write_text("login_limit=64\n")
    args = [exe, "--server", "--host", "127.0.0.1", "--port", str(PORT), "--data", str(data), "--seconds", "180"]
    # Its log lines also go to stderr: into a file, since a pipe nobody reads fills up and blocks the server.
    err = tmp_path_factory.mktemp("srverr") / "stderr.txt"
    with err.open("w") as f:
        srv = subprocess.Popen(args, stderr=f)
    deadline = time.monotonic() + 20  # the first start makes two RSA keys
    while time.monotonic() < deadline:
        if srv.poll() is not None:
            pytest.fail(f"server ended: {srv.returncode} {err.read_text(errors='replace')}")
        try:
            socket.create_connection(("127.0.0.1", PORT), timeout=0.5).close()
            break
        except OSError:
            time.sleep(0.1)
    else:
        srv.kill()
        pytest.fail("server not listening")
    yield data
    if os.name == "nt":
        # F1 uses this event to stop a server owned by the launcher. Check final store flush and cleanup.
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.OpenEventW.restype = wintypes.HANDLE
        kernel.SetEvent.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        event = kernel.OpenEventW(2, False, f"Local\\MKWRecompServerStop-{srv.pid}")
        try:
            assert event and kernel.SetEvent(event), "server stop event missing"
            assert srv.wait(timeout=10) == 0
            assert "stopped" in (data / "logs/server.log").read_text()
            assert not (data / "pid").exists()
        finally:
            if event:
                kernel.CloseHandle(event)
            if srv.poll() is None:
                srv.kill()
                srv.wait()
    else:
        srv.kill()
        srv.wait()


def test_srv_capacity_snapshot(server: Path) -> None:
    """The same process reports its aggregate capacity across independent lobbies."""
    log = server / "logs/server.log"
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if "capacity: sessions=" in log.read_text(errors="replace"):
            break
        time.sleep(0.05)
    else:
        pytest.fail("server did not publish its initial lobby capacity snapshot")
    snapshot = next(line for line in log.read_text().splitlines() if "capacity: sessions=" in line)
    assert "sessions=0/256" in snapshot
    assert "members=0" in snapshot and "listeners=0" in snapshot


def test_srv_stun_over_udp(server: Path) -> None:
    """A STUN binding request to 3478 and 3479 comes back with the address and port the server saw."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        s.settimeout(1)
        txid = os.urandom(12)
        for port in (3478, 3479):
            s.sendto(struct.pack(">HHI", 1, 0, 0x2112A442) + txid, ("127.0.0.1", port))
            reply = s.recv(512)
            kind, length, cookie = struct.unpack(">HHI", reply[:8])
            assert (kind, cookie, reply[8:20]) == (0x0101, 0x2112A442, txid)
            atype, alen, _, family, xport = struct.unpack(">HHBBH", reply[20:28])
            assert (atype, alen, family) == (0x20, 8, 1) and length == len(reply) - 20
            assert xport ^ 0x2112 == s.getsockname()[1]


def test_srv_relay_diagnostic(server: Path) -> None:
    """An unanswered relay allocation is visible once, without logging payloads or repeated retries."""
    message = "TURN Allocate received: relay traffic is not implemented"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        for _ in range(12):
            s.sendto(struct.pack(">HHI", 3, 0, 0x2112A442) + os.urandom(12), ("127.0.0.1", 3479))
    log = server / "logs/server.log"
    deadline = time.monotonic() + 2
    while message not in log.read_text() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert log.read_text().count(message) == 1


def frame(kind: int, flags: int, stream: int, payload: bytes = b"") -> bytes:
    return struct.pack(">I", len(payload))[1:] + bytes([kind, flags]) + struct.pack(">I", stream) + payload


def literal(name: str, value: str) -> bytes:
    """An HPACK header field without indexing, plain strings (RFC 7541 6.2.2), lengths as 7-bit prefix integers
    (5.1: a token is longer than 126 bytes)."""

    def length(n: int) -> bytes:
        if n < 127:
            return bytes([n])
        out, n = b"\x7f", n - 127
        while n >= 128:
            out, n = out + bytes([n & 0x7F | 0x80]), n >> 7
        return out + bytes([n])

    return b"\x00" + length(len(name)) + name.encode() + length(len(value)) + value.encode()


def grpc_request(stream: int, path: str) -> bytes:
    fields = [(":method", "POST"), (":scheme", "https"), (":path", path), (":authority", "gamesync.npln.nintendo.net"),
              ("content-type", "application/grpc"), ("te", "trailers")]  # fmt: skip
    headers = frame(1, 0x4, stream, b"".join(literal(n, v) for n, v in fields))
    return headers + frame(0, 0x1, stream, b"\x00\x00\x00\x00\x00")  # one empty message, END_STREAM


def test_srv_tls_grpc_catch_all(server: Path) -> None:
    ctx = ssl.create_default_context(cadata=ssl.DER_cert_to_PEM_cert((server / "pki/ca.der").read_bytes()))
    ctx.set_alpn_protocols(["h2"])
    unary = "/nn.npln.friends.v1.Friends/ActivateUser"
    held = "/nn.npln.maintenance.v1.MaintenanceScheduleService/SubscribeMaintenanceSchedules"
    data: dict[int, bytes] = {1: b"", 3: b""}
    ended: set[int] = set()
    with socket.create_connection(("127.0.0.1", PORT), timeout=5) as raw:
        with ctx.wrap_socket(raw, server_hostname="gamesync.npln.nintendo.net") as tls:  # chain and name checked
            assert tls.selected_alpn_protocol() == "h2"
            preface = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n" + frame(4, 0, 0)
            tls.sendall(preface + grpc_request(1, unary) + grpc_request(3, held))
            buf, deadline = b"", time.monotonic() + 3
            tls.settimeout(0.3)
            while time.monotonic() < deadline and not (1 in ended and data[3]):
                try:
                    chunk = tls.recv(65536)
                except TimeoutError:
                    continue
                if not chunk:
                    break
                buf += chunk
                while len(buf) >= 9 and len(buf) >= 9 + int.from_bytes(buf[:3], "big"):
                    n = int.from_bytes(buf[:3], "big")
                    kind, flags, sid = buf[3], buf[4], int.from_bytes(buf[5:9], "big") & 0x7FFFFFFF
                    payload, buf = buf[9 : 9 + n], buf[9 + n :]
                    if kind == 4 and not flags & 1:
                        tls.sendall(frame(4, 1, 0))  # SETTINGS ACK
                    assert kind != 3 or sid not in data, "a call was reset"  # RST_STREAM
                    if kind == 0 and sid in data:
                        data[sid] += payload
                    if kind in (0, 1) and flags & 1:
                        ended.add(sid)
    assert data[1] == b"\x00" * 5 and 1 in ended, "the unary call: one empty message, then trailers"
    assert data[3] == b"\x00" * 5 and 3 not in ended, "the server stream: one empty message, then held open"
    log = (server / "logs/server.log").read_text()
    assert f"{unary} -> 0" in log and f"{held} held open" in log


class H2:
    """A bare HTTP/2 client over TLS, enough to push the server: frames in and out, flow control on what it sends, no
    HPACK decoding (what the server answered is read from its log)."""

    def __init__(self, data: Path, port: int = PORT) -> None:
        ctx = ssl.create_default_context(cadata=ssl.DER_cert_to_PEM_cert((data / "pki/ca.der").read_bytes()))
        ctx.set_alpn_protocols(["h2"])
        self.s = ctx.wrap_socket(socket.create_connection(("127.0.0.1", port), timeout=5), server_hostname="localhost")
        self.buf, self.conn_win, self.init_win, self.next_sid = b"", 65535, 65535, 1
        self.win: dict[int, int] = {}
        self.ended: set[int] = set()
        self.data: dict[int, bytes] = {}
        self.closed = False
        self.s.sendall(b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n" + frame(4, 0, 0))

    def pump(self, seconds: float) -> None:
        self.s.settimeout(seconds)
        try:
            chunk = self.s.recv(65536)
        except TimeoutError:
            return
        if not chunk:
            self.closed = True
            return
        self.buf += chunk
        while len(self.buf) >= 9 and len(self.buf) >= 9 + int.from_bytes(self.buf[:3], "big"):
            n = int.from_bytes(self.buf[:3], "big")
            kind, flags, sid = self.buf[3], self.buf[4], int.from_bytes(self.buf[5:9], "big") & 0x7FFFFFFF
            payload, self.buf = self.buf[9 : 9 + n], self.buf[9 + n :]
            if kind == 4 and not flags & 1:  # SETTINGS: the stream window, then ACK
                for i in range(0, len(payload), 6):
                    key, value = struct.unpack(">HI", payload[i : i + 6])
                    if key == 4:
                        self.win = {k: w + value - self.init_win for k, w in self.win.items()}
                        self.init_win = value
                self.s.sendall(frame(4, 1, 0))
            elif kind == 8:  # WINDOW_UPDATE
                inc = int.from_bytes(payload[:4], "big") & 0x7FFFFFFF
                if sid:
                    self.win[sid] = self.win.get(sid, self.init_win) + inc
                else:
                    self.conn_win += inc
            elif kind == 7:  # GOAWAY
                self.closed = True
            if kind == 0:
                self.data[sid] = self.data.get(sid, b"") + payload
                if payload:  # the connection's window back (each stream's own is far from used up here)
                    self.s.sendall(frame(8, 0, 0, len(payload).to_bytes(4, "big")))
            if kind in (0, 1) and flags & 1 or kind == 3:
                self.ended.add(sid)

    def sid(self) -> int:
        """The next stream id (odd, rising), for a connection whose streams are all numbered this way."""
        self.next_sid += 2
        return self.next_sid - 2

    def open(self, sid: int, path: str, extra: tuple[tuple[str, str], ...] = ()) -> None:
        fields = [(":method", "POST"), (":scheme", "https"), (":path", path), (":authority", "localhost"),
                  ("content-type", "application/grpc"), *extra]  # fmt: skip
        self.win[sid] = self.init_win
        self.s.sendall(frame(1, 0x4, sid, b"".join(literal(n, v) for n, v in fields)))

    def send(self, sid: int, data: bytes) -> None:
        """DATA within the windows the server grants; gives up when the stream or the connection ends."""
        while data and sid not in self.ended and not self.closed:
            n = min(16384, len(data), self.conn_win, self.win[sid])
            if n <= 0:
                self.pump(0.2)
                continue
            self.s.sendall(frame(0, 0, sid, data[:n]))
            data, self.conn_win, self.win[sid] = data[n:], self.conn_win - n, self.win[sid] - n


def test_srv_memory_budget(server: Path) -> None:
    """Streams that send most of a 1 MiB message and never finish it hold memory; past the connection's 4 MiB the next
    one is refused with RESOURCE_EXHAUSTED, the connection lives on, and what a reset stream held is given back."""
    h = H2(server)
    held = b"\x00" + (1 << 20).to_bytes(4, "big") + b"\xab" * (900 << 10)
    for i in range(6):
        h.open(2 * i + 1, f"/test.Budget/Hold{i}")
        h.send(2 * i + 1, held)
    h.pump(0.5)
    log = (server / "logs/server.log").read_text()
    busy = [i for i in range(6) if f"/test.Budget/Hold{i} -> 8 server busy" in log]
    assert busy and busy[0] >= 3, f"refused too early or never: {busy}"  # 3 streams of ~1 MiB fit in 4 MiB
    assert not h.closed and 1 not in h.ended, "the connection and the first streams live on"
    for i in range(busy[0]):  # reset what holds memory: it comes back
        h.s.sendall(frame(3, 0, 2 * i + 1, (8).to_bytes(4, "big")))  # RST_STREAM CANCEL
        h.ended.add(2 * i + 1)
    h.open(21, "/test.Budget/Again")
    h.send(21, held)
    h.pump(0.5)
    assert "/test.Budget/Again -> 8" not in (server / "logs/server.log").read_text()
    h.s.close()


def pb(num: int, value: bytes | int | str) -> bytes:
    """One protobuf field: an int as a varint, bytes and str length-delimited."""

    def varint(n: int) -> bytes:
        out = b""
        while True:
            out += bytes([n & 0x7F | (0x80 if n > 0x7F else 0)])
            n >>= 7
            if not n:
                return out

    if isinstance(value, int):
        return varint(num << 3) + varint(value)
    raw = value.encode() if isinstance(value, str) else value
    return varint(num << 3 | 2) + varint(len(raw)) + raw


def fields(msg: bytes) -> dict[int, list[bytes | int]]:
    """A message's fields: varints and fixed values as ints, length-delimited ones as bytes."""
    out: dict[int, list[bytes | int]] = {}
    i = 0

    def varint() -> int:
        nonlocal i
        n = shift = 0
        while True:
            b = msg[i]
            i += 1
            n |= (b & 0x7F) << shift
            shift += 7
            if not b & 0x80:
                return n

    while i < len(msg):
        key = varint()
        if key & 7 == 0:
            out.setdefault(key >> 3, []).append(varint())
        elif key & 7 in (1, 5):  # fixed64, fixed32: their bits as an int
            n = 8 if key & 7 == 1 else 4
            out.setdefault(key >> 3, []).append(int.from_bytes(msg[i : i + n], "little"))
            i += n
        else:
            n = varint()
            out.setdefault(key >> 3, []).append(msg[i : i + n])
            i += n
    return out


def call(h: H2, sid: int, path: str, msg: bytes, extra: tuple[tuple[str, str], ...] = (), end: bool = True) -> None:
    h.open(sid, path, extra)
    h.s.sendall(frame(0, 1 if end else 0, sid, bytes(1) + len(msg).to_bytes(4, "big") + msg))


def wait(h: H2, done, seconds: float = 3) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not done() and not h.closed:
        h.pump(0.2)


TENANT = "0100test0000000"
SIGN_IN = "/nn.npln.auth.v1.Auth/IssuePrearrangedUserToken"
ACK = bytes.fromhex("0a080a02081e1202081e")  # KeepAlive: Ack {30 s, 30 s}


def sign_in(h: H2, sid: int, code: str, secret: str) -> None:
    ident = f"droplet-online-v1;code={code};secret={secret};friends="
    call(h, sid, SIGN_IN, pb(1, f"tenants/{TENANT}") + pb(3, pb(1, ident)))


def test_srv_sign_in_and_presence(server: Path) -> None:
    """A friend code signs in and gets its user name and a token; another secret for the same code is refused; with
    the token, a KeepAlive presence change is acknowledged and the player's name is kept for the friends API."""
    h = H2(server)
    sign_in(h, 1, "123456789012", "00112233445566778899aabbccddeeff")
    wait(h, lambda: 1 in h.ended)
    reply = fields(h.data[1][5:])
    user = fields(reply[1][0])[1][0]
    token = fields(reply[2][0])
    assert user == f"tenants/{TENANT}/users/u-{123456789012:016x}".encode()
    assert token[1][0] == user and token[2][0].count(b".") == 2 and fields(token[4][0])[1][0] == 3600
    bearer = token[2][0].decode()

    sign_in(h, 3, "123456789012", "ffeeddccbbaa99887766554433221100")
    wait(h, lambda: 3 in h.ended)
    assert 3 not in h.data, "a second secret for a code is refused"

    name = pb(1, "PlayerName") + pb(2, pb(7, "Tester"))
    change = pb(1, pb(1, pb(1, f"tenants/{TENANT}/users/current/presence") + pb(3, name)))
    call(h, 5, "/nn.npln.presence.v1.PresenceService/KeepAlive", change, (("authorization", f"Bearer {bearer}"),),
         end=False)  # fmt: skip
    wait(h, lambda: len(h.data.get(5, b"")) >= 15)
    assert h.data[5][:15] == bytes(4) + bytes([10]) + ACK, h.data.get(5)
    wait(h, lambda: False, 1.5)  # the account's name is written within a second
    accounts = (server / "accounts.tsv").read_text(encoding="utf-8")
    assert "123456789012" in accounts and accounts.rstrip().endswith("Tester")
    log = (server / "logs/server.log").read_text()
    assert f"{SIGN_IN} -> 7 friend code belongs to another emulator" in log
    h.s.close()


def api(server: Path, method: str, path: str, code: str = "", secret: str = "", body: str = "") -> tuple[int, dict]:
    """One friends API request (gRPC port + 1) the way the runtime makes it; its status and JSON."""
    import http.client
    import json

    ctx = ssl.create_default_context(cadata=ssl.DER_cert_to_PEM_cert((server / "pki/ca.der").read_bytes()))
    ctx.check_hostname = False  # the runtime pins the CA and checks no name: the certificate names Nintendo hosts
    conn = http.client.HTTPSConnection("127.0.0.1", PORT + 1, context=ctx, timeout=5)
    headers = {"X-Droplet-Code": code, "X-Droplet-Secret": secret, "X-Droplet-Version": "mkw-recomp"}
    if body:
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=body or None, headers=headers)
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r.status, json.loads(data) if r.getheader("Content-Type") == "application/json" else {"raw": data}


def test_srv_client_presence(server: Path) -> None:
    """Runtime clients are real authenticated leases, distinct from game sign-in and room membership."""
    a, b = ("909090900001", "de" * 32), ("909090900002", "ed" * 32)
    assert api(server, "GET", "/v1/clients", *a)[0] == 405
    assert api(server, "POST", "/v1/clients", "not-a-code", a[1])[0] == 403
    assert api(server, "POST", "/v1/clients", "000000000000", a[1])[0] == 403
    status, first = api(server, "POST", "/v1/clients", *a)
    assert status == 200 and first["room"] == "" and first["members"] == []
    assert {x["code"]: x["online"] for x in first["clients"]}[a[0]] is False
    assert api(server, "POST", "/v1/clients", a[0], "00" * 32)[0] == 403
    status, both = api(server, "POST", "/v1/clients", *b)
    assert status == 200 and {a[0], b[0]} <= {x["code"] for x in both["clients"]}
    assert not any("secret" in x or "ip" in x for x in both["clients"])
    h = H2(server)
    sign_in(h, 1, *a)
    wait(h, lambda: 1 in h.ended)
    status, signed_in = api(server, "POST", "/v1/clients", *a)
    assert status == 200 and {x["code"]: x["online"] for x in signed_in["clients"]}[a[0]] is True
    assert signed_in["members"] == [], "signing in alone does not create a party"
    assert api(server, "POST", "/v1/clients/leave", *b)[0] == 200
    _, remaining = api(server, "POST", "/v1/clients", *a)
    assert b[0] not in {x["code"] for x in remaining["clients"]}
    api(server, "POST", "/v1/clients/leave", *a)
    h.s.close()


def test_srv_friends_api(server: Path) -> None:
    """Two players: a request by code (written SW-...), seen as sent and received, accepted; then each one's friend
    list, over the API and over the game's SubscribeFriendUsers stream. Wrong secret, unknown action and one's own code
    are refused."""
    a, b = ("111111111111", "aa" * 16), ("222222222222", "bb" * 16)
    h = H2(server)
    sign_in(h, 1, *a)
    sign_in(h, 3, *b)
    wait(h, lambda: {1, 3} <= h.ended)
    bearer_a = fields(fields(h.data[1][5:])[2][0])[2][0].decode()
    status, ca = api(server, "GET", "/v1/ca.der")
    assert status == 200 and ca["raw"] == (server / "pki/ca.der").read_bytes()
    assert api(server, "GET", "/v1/friends", a[0], "cc" * 16)[0] == 403
    assert api(server, "POST", "/v1/friends/hug", *a, body='{"code":"222222222222"}')[0] == 404
    assert api(server, "POST", "/v1/friends/request", *a, body='{"code":"111111111111"}')[0] == 409
    status, mine = api(server, "POST", "/v1/friends/request", *a, body='{"code":"SW-2222-2222-2222"}')
    assert status == 200 and [e["code"] for e in mine["sent"]] == [b[0]] and not mine["friends"]
    assert api(server, "POST", "/v1/clients", *a)[1]["friends"] == [], "pending requests are not game friends"
    status, theirs = api(server, "GET", "/v1/friends", *b)
    assert status == 200 and [e["code"] for e in theirs["received"]] == [a[0]]
    status, theirs = api(server, "POST", "/v1/friends/accept", *b, body='{"code":"111111111111"}')
    assert status == 200 and [(e["code"], e["online"]) for e in theirs["friends"]] == [(a[0], True)]
    status, cached = api(server, "POST", "/v1/clients", *a)
    assert status == 200 and [e["code"] for e in cached["friends"]] == [b[0]], "SDK snapshot includes accepted friends"
    call(h, 5, "/nn.npln.friends.v1.Friends/SubscribeFriendUsers", b"", (("authorization", f"Bearer {bearer_a}"),))
    wait(h, lambda: len(h.data.get(5, b"")) > 5)
    friend = fields(fields(h.data[5][5:])[1][0])
    assert friend[1][0] == f"{int(b[0]):x}".encode()
    assert fields(friend[2][0])[2][0] == f"tenants/{TENANT}/users/u-{int(b[0]):016x}".encode()
    h.s.close()


def test_srv_bad_arguments() -> None:
    exe = os.environ.get("MKW_SRV_EXE")
    if not exe:
        pytest.skip("MKW_SRV_EXE not set")
    for args in (["--host", "not-an-address"], ["--seconds", "0"], ["--port", "70000"], ["--what"]):
        assert subprocess.run([exe, "--server", *args], capture_output=True, timeout=10).returncode == 2, args


def test_srv_nat_check_over_udp(server: Path) -> None:
    """Pia's NAT check on 10025, 10125 and 33334: the type back with the port and address the server saw (a server on
    127.0.0.1 alone has no other address to report); a datagram of another length gets nothing."""
    log = (server / "logs/server.log").read_text()
    ports = [p for p in (10025, 10125, 33334) if f"UDP 127.0.0.1 port {p} is taken" not in log]
    assert ports, "every NAT check port is taken on this PC"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        s.settimeout(1)
        for port in ports:
            s.sendto(struct.pack(">IIII", 101, 50000, 0xC0000201, 0), ("127.0.0.1", port))  # 192.0.2.1:50000
            assert struct.unpack(">IIII", s.recv(64)) == (101, s.getsockname()[1], 0x7F000001, 0)
        s.sendto(struct.pack(">IIII", 103, 1, 2, 0)[:15], ("127.0.0.1", ports[0]))
        with pytest.raises(TimeoutError):
            s.recv(64)


# ---- rooms, Gamesync, the referee, matchmaking
GSS = "/nn.npln.matchmaking.v1.GameSessionService/"
MM = "/nn.npln.matchmaking.v1.MatchmakingService/"  # the service is not known; the server dispatches on the method
GSYNC = "/nn.npln.gamesync.v1.Gamesync/"


def mv(v: object) -> bytes:
    """A MapValue: int as int64 (3, non-negative here), bool (2), float as a double (5), str (7), list (9), dict (10)."""
    if isinstance(v, bool):
        return pb(2, int(v))
    if isinstance(v, int):
        return pb(3, v)
    if isinstance(v, float):
        return bytes([5 << 3 | 1]) + struct.pack("<d", v)
    if isinstance(v, str):
        return pb(7, v)
    if isinstance(v, list):
        return pb(9, b"".join(pb(1, mv(x)) for x in v))
    assert isinstance(v, dict)
    return pb(10, mapmsg(v))


def mapmsg(d: dict) -> bytes:
    return b"".join(pb(1, pb(1, k) + pb(2, mv(v))) for k, v in d.items())


def value(m: bytes) -> object:
    """A MapValue the server wrote, as Python."""
    f = fields(m)
    if 3 in f:
        n = f[3][-1]
        return n - (1 << 64) if n >= 1 << 63 else n
    if 2 in f:
        return bool(f[2][-1])
    if 5 in f:
        return struct.unpack("<d", f[5][-1].to_bytes(8, "little"))[0]
    if 7 in f:
        return f[7][-1].decode()
    if 9 in f:
        return [value(x) for x in fields(f[9][-1]).get(1, [])]
    if 10 in f:
        return mapof(f[10][-1])
    return None


def mapof(m: bytes) -> dict:
    out = {}
    for e in fields(m).get(1, []):
        ef = fields(e)
        out[ef[1][0].decode()] = value(ef[2][0]) if 2 in ef else None
    return out


def msgs(data: bytes) -> list[bytes]:
    """The complete gRPC messages in a stream's DATA."""
    out = []
    while len(data) >= 5 and len(data) >= 5 + int.from_bytes(data[1:5], "big"):
        n = int.from_bytes(data[1:5], "big")
        out.append(data[5 : 5 + n])
        data = data[5 + n :]
    return out


def pump_all(hs: list[H2], done, seconds: float = 3) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not done():
        for h in hs:
            h.pump(0.05)


def headers(token: str = "", tenant: bool = True) -> tuple[tuple[str, str], ...]:
    out = (("authorization", f"Bearer {token}"),) if token else ()
    return out + ((("npln-tenant-id", TENANT),) if tenant else ())


def unary(h: H2, path: str, msg: bytes, token: str = "", tenant: bool = True) -> dict:
    """One unary call: its reply's fields ({} when it sent none: see the log for its status)."""
    sid = h.sid()
    call(h, sid, path, msg, headers(token, tenant))
    wait(h, lambda: sid in h.ended)
    m = msgs(h.data.get(sid, b""))
    return fields(m[0]) if m else {}


def signed(h: H2, code: str) -> str:
    """Signs a friend code in; its bearer token."""
    sid = h.sid()
    sign_in(h, sid, code, code * 2 + "ab" * 4)
    wait(h, lambda: sid in h.ended)
    return fields(fields(msgs(h.data[sid])[0])[2][0])[2][0].decode()


def test_srv_external_turn_credentials(tmp_path: Path) -> None:
    """Configured coturn receives expiring credentials, checked against an independent HMAC implementation."""
    import base64
    import hashlib
    import hmac

    exe = os.environ.get("MKW_SRV_EXE")
    if not exe:
        pytest.skip("MKW_SRV_EXE not set")
    secret = "test-relay-shared-secret-" + "a" * 32
    (tmp_path / "settings.txt").write_text(f"turn_host=203.0.113.5\nturn_port=3479\nturn_secret={secret}\n")
    with (tmp_path / "stderr.txt").open("w") as log:
        process = subprocess.Popen([exe, "--server", "--host", "127.0.0.1", "--port", "18453",
                                    "--data", str(tmp_path), "--seconds", "30"], stderr=log)
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            assert process.poll() is None, (tmp_path / "stderr.txt").read_text()
            try:
                socket.create_connection(("127.0.0.1", 18453), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.05)
        connection = H2(tmp_path, port=18453)
        token = signed(connection, "818181818181")
        response = unary(connection, GSS + "AllocateIceServerSet", b"", token)
        relay = fields(response[3][0])
        assert relay[1] == [b"203.0.113.5"] and relay[2] == [3479]
        username, password = relay[4][0], relay[5][0]
        expires, user = username.decode().split(":", 1)
        assert int(expires) > time.time() and user == f"u-{818181818181:016x}"
        expected = base64.b64encode(hmac.new(secret.encode(), username, hashlib.sha1).digest())
        assert password == expected and secret.encode() not in password
        connection.s.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


def events(h: H2, sid: int) -> list[tuple]:
    """A listen stream's responses: ("target", id, state) and ("doc", id, kind, path, fields)."""
    out: list[tuple] = []
    for m in msgs(h.data.get(sid, b"")):
        f = fields(m)
        if 3 in f:
            t = fields(f[3][0])
            out.append(("target", t[1][0].decode(), t[2][0]))
        if 2 in f:
            d = fields(f[2][0])
            doc = fields(d[3][0])
            out.append(("doc", d[1][0].decode(), d[2][0], doc[1][0].decode().removeprefix("docs/"),
                        mapof(doc[2][0]) if 2 in doc else {}))  # fmt: skip
    return out


def w_set(path: str, d: dict) -> bytes:
    return pb(2, pb(1, pb(1, "docs/" + path) + pb(2, mapmsg(d))))


def write(h: H2, token: str, *writes: bytes, extra: bytes = b"") -> dict:
    return unary(h, GSYNC + "WriteDocuments", b"".join(pb(1, w) for w in writes) + extra, token, tenant=False)


def station(h: H2, token: str) -> int:
    """A station id allocated as MKW does it: load the counter (its name a bare string), maximum with 20000, add 1,
    store it."""
    k = "`upcsid`"
    ops = [(k, 6, "__upcsidn"), (k, 4, mv(20000)), (k, 3, mv(1)), (k, 7, "__upcsidn")]
    transform = pb(1, "docs/st") + b"".join(pb(2, pb(1, k) + pb(n, op)) for k, n, op in ops)
    results = fields(fields(write(h, token, pb(3, transform))[1][0])[2][0])[1]
    assert len(results) == 1  # one value per distinct field
    return value(results[0])


def test_srv_room_gamesync_referee(server: Path) -> None:
    """A room made by A, found by its room ID and joined by B; both take a Gamesync token with their credential and
    listen. The participants query stays without "current" until both participation documents exist; a write that
    changes nothing reaches nobody; station ids follow allocation order; a race request is answered in the same batch
    (status 0) and a report closes it (status 1, Elo ratings as doubles); B's deferred write runs when B leaves."""
    a, b = H2(server), H2(server)
    ta, tb = signed(a, "333333333333"), signed(b, "444444444444")
    ua, ub = f"{333333333333:016x}", f"{444444444444:016x}"
    attrs = mapmsg({"batching_key": "cv1"})
    host = pb(1, "tenants/current/users/current") + pb(2, attrs)
    room_attrs = {"rom_id": "cv1", "course_id": 3, "user_name": "Host"}
    expected_attrs = {**room_attrs, "rule": 3}
    ticket = pb(2, f"tenants/{TENANT}/matchmakingConfigs/Private24") + pb(3, host) + pb(6, pb(11, mapmsg(room_attrs)))
    made = unary(a, GSS + "CreateGameSessionCreationTicket", pb(1, "tenants/current") + pb(2, ticket), ta)
    ct = made[1][0].decode()
    assert ct.startswith(f"tenants/{TENANT}/gameSessionCreationTickets/") and made[4] == [1], made
    tracked = unary(a, GSS + "TrackGameSessionCreationTicket", pb(1, ct), ta)
    assert tracked[4] == [2] and len(tracked[5]) == 1
    gs = fields(tracked[6][0])
    name = gs[1][0].decode()
    assert gs[8] == [b"gamesync.npln.nintendo.net"] and gs[9] == [443] and 12 not in gs
    assert mapof(gs[11][0]) == expected_attrs, "the joiner must classify this session as a private room (rule 3)"
    entry = fields(tracked[5][0])
    assert entry[2][0].decode() == f"{name}/userSessions/us-{ua}"
    cred_a = entry[3][0]

    made = unary(a, GSS + "CreateGameSessionShortAlias", pb(1, "tenants/current") + pb(2, pb(2, name)), ta)
    alias = made[1][0].decode()
    assert f"tenants/{TENANT}/GameSessionShortAliases/" in alias and made[2] == [name.encode()]
    digits = alias.rsplit("/", 1)[1]
    assert len(digits) == 6 and digits.isdigit()
    typed = f"tenants/current/GameSessionShortAliases/{digits[:3]} {digits[3:]}"  # as the game shows it
    assert unary(b, GSS + "GetGameSessionShortAlias", pb(1, typed), tb)[2] == [name.encode()]
    found = unary(b, GSS + "GetGameSession", pb(1, name), tb)
    assert mapof(found[11][0]) == expected_attrs, "room-code lookup must return the private-room rule"
    batch = unary(b, GSS + "BatchGetGameSessions", pb(1, "tenants/current") + pb(2, name)
                  + pb(2, "tenants/current/gameSessions/missing"), tb)
    assert len(batch[1]) == 1 and mapof(fields(batch[1][0])[11][0]) == expected_attrs
    joined = unary(b, GSS + "JoinGameSession", pb(1, name) + pb(3, pb(2, attrs)), tb)
    assert mapof(fields(joined[2][0])[11][0]) == expected_attrs
    entries = [fields(e) for e in joined[1]]
    assert [e[2][0].decode().rsplit("/", 1)[1] for e in entries] == [f"us-{ua}", f"us-{ub}"]
    cred_b = entries[1][3][0]
    # F1 shows only actual members of the caller's room, not everyone connected to this server.
    _, party = api(server, "POST", "/v1/clients", "333333333333", "333333333333" * 2 + "ab" * 4)
    assert party["room"] == digits
    assert {x["code"] for x in party["members"]} == {"333333333333", "444444444444"}
    _, outsider = api(server, "POST", "/v1/clients", "909090900003", "ce" * 32)
    assert not outsider["room"] and not outsider["members"]
    ice_set = unary(b, GSS + "AllocateIceServerSet", b"", tb)
    ice = fields(ice_set[2][0])
    relay = fields(ice_set[3][0])
    assert ice[1] == [b"stun.npln.nintendo.net"] and ice[2] == [3478]
    assert relay[1] == [b"relay.npln.nintendo.net"] and relay[2] == [3479] and relay[3] == [1]
    assert relay[4] and relay[5]

    def token(h: H2, us: str, cred: bytes) -> str:
        r = unary(h, GSYNC + "IssueToken", pb(1, f"userSessions/us-{us}") + pb(2, cred), tenant=False)
        gst = fields(r[1][0])
        assert gst[1] == [f"userSessions/us-{us}".encode()] and gst[2] == gst[3]
        return gst[2][0].decode()

    ga, gb = token(a, ua, cred_a), token(b, ub, cred_b)
    assert not unary(b, GSYNC + "IssueToken", pb(1, f"userSessions/us-{ua}") + pb(2, cred_b), tenant=False)
    assert f"{GSYNC}IssueToken -> 7" in (server / "logs/server.log").read_text(), "another member's credential"

    def target(n: int, spec: bytes) -> bytes:
        return pb(3, pb(1, pb(1, f"x/targets/{n}") + spec))

    targets = (target(1, pb(3, pb(1, "docs/__pgn/g1/__pus"))) + target(2, pb(2, pb(1, "docs/__gs/f") + pb(1, "docs/no")))
               + target(3, pb(3, pb(1, "docs/@@RefereeResult"))) + target(4, pb(3, pb(1, "docs/__us"))))  # fmt: skip
    la, lb = a.sid(), b.sid()
    for h, sid, tok in ((a, la, ga), (b, lb, gb)):
        call(h, sid, GSYNC + "KeepUserSession", targets, headers(tok, False), end=False)
    pump_all([a, b], lambda: len(events(a, la)) >= 10 and len(events(b, lb)) >= 10)
    ev = events(a, la)
    assert ev[0] == ("target", "1", 1) and ("target", "1", 2) not in ev, "participants held: nobody is in yet"
    assert [(e[2], e[3]) for e in ev if e[0] == "doc" and e[1] == "2"] == [(1, "__gs/f"), (3, "no")]
    assert ("target", "2", 2) in ev and ("target", "3", 2) in ev
    gsf = next(e[4] for e in ev if e[0] == "doc" and e[3] == "__gs/f")
    assert gsf["gsid"] == name.rsplit("/", 1)[1] and gsf["mcn"] == "Private24" and gsf["maxu"] == 24 and gsf["p"] == 0
    us = {e[3]: e[4] for e in ev if e[0] == "doc" and e[1] == "4"}
    assert us[f"__us/us-{ua}"]["ussid"] == 1 and us[f"__us/us-{ub}"]["ussid"] == 2 and us[f"__us/us-{ua}"]["st"] == 2

    def docs_of(h: H2, sid: int, target: str) -> list[tuple]:
        return [e for e in events(h, sid) if e[0] == "doc" and e[1] == target]

    pa, pb_ = f"__pgn/g1/__pus/us-{ua}", f"__pgn/g1/__pus/us-{ub}"
    write(a, ga, w_set(pa, {"in": True}))
    pump_all([a, b], lambda: len(docs_of(a, la, "1")) == 1 and len(docs_of(b, lb, "1")) == 1)
    assert docs_of(a, la, "1")[0][2:4] == (2, pa) and docs_of(b, lb, "1")[0][2:4] == (2, pa), "sent to everyone"
    assert ("target", "1", 2) not in events(a, la), "still held: B is not in"
    seen = (len(events(a, la)), len(events(b, lb)))
    write(a, ga, w_set(pa, {"in": True}))  # the same document again
    pump_all([a, b], lambda: False, 0.6)
    assert (len(events(a, la)), len(events(b, lb))) == seen, "an unchanged document is sent to nobody"
    write(b, gb, w_set(pb_, {"in": True}))
    pump_all([a, b], lambda: ("target", "1", 2) in events(a, la) and ("target", "1", 2) in events(b, lb))
    ev = events(a, la)
    assert ev.index(("target", "1", 2)) > max(i for i, e in enumerate(ev) if e[0] == "doc" and e[3] == pb_)

    assert (station(a, ga), station(b, gb), station(a, ga)) == (20001, 20002, 20001), "a member's id stays"

    req = {"users": [1, 2], "ussid_map": {"0": 1, "1": 2}, "mode": "normal", "course_id": 3, "option": {}}
    write(a, ga, w_set(f"@@RefereeRequest/1/Request/R{ua[-4:]}", req))
    pump_all([a, b], lambda: docs_of(a, la, "3") and docs_of(b, lb, "3"))
    result = docs_of(b, lb, "3")[0]
    assert result[4] == {"request_id": f"R{ua[-4:]}", "status": 0, "result": [], "user_session_count": 2}, result
    race = result[3].rsplit("/", 1)[1]
    assert len(race) == 36 and race.count("-") == 4
    write(a, ga, w_set(f"@@RefereeReport/{race}/User/1", {"result": [0, 1], "result_order": [1, 2]}))
    pump_all([a, b], lambda: any(e[4].get("status") == 1 for e in docs_of(b, lb, "3")))
    final = [e for e in docs_of(b, lb, "3") if e[4].get("status") == 1][-1][4]
    assert final["result"] == [0, 1] and final["user_session_count"] == 2
    assert final["ratings"] == {"1": 1512.0, "2": 1488.0} and final["coefficients"] == {"1": 0.0, "2": 0.0}

    # B registers "delete my participation record" for when its last listen stream ends.
    deferment = pb(1, "userSessions/current/deferments/leave") + pb(2, pb(4, pb(1, "docs/" + pb_)))
    write(b, gb, extra=pb(2, pb(1, pb(1, deferment))))
    # A reconnect can temporarily overlap the old KeepUserSession with a replacement.
    # Closing only the old stream must not run the player's deferred deletion.
    lb2 = b.sid()
    call(b, lb2, GSYNC + "KeepUserSession", targets, headers(gb, False), end=False)
    pump_all([a, b], lambda: len(events(b, lb2)) >= 10)
    b.s.sendall(frame(0, 1, lb))
    pump_all([a, b], lambda: lb in b.ended)
    assert lb in b.ended, "the superseded listen stream ends promptly"
    pump_all([a, b], lambda: False, 0.3)
    assert not any(e[2] == 3 and e[3] in (pb_, f"__us/us-{ub}") for e in docs_of(a, la, "4") + docs_of(a, la, "1"))
    assert station(b, gb) == 20002, "the active replacement stream keeps the player in the room"
    b.s.sendall(frame(0, 1, lb2))
    pump_all([a, b], lambda: lb2 in b.ended and any(e[2] == 3 and e[3] == f"__us/us-{ub}" for e in docs_of(a, la, "4")))
    assert lb2 in b.ended, "the last listen stream ends promptly"
    gone = [(e[1], e[3]) for e in events(a, la) if e[0] == "doc" and e[2] == 3]
    assert ("1", pb_) in gone and ("4", f"__us/us-{ub}") in gone, gone
    log = (server / "logs/server.log").read_text()
    assert f"{GSYNC}KeepUserSession -> 0" in log and "left room" in log
    assert f"{GSYNC}WriteDocuments -> 0" not in log, "writes come several times a second: their success is not logged"
    a.s.close()
    b.s.close()


@pytest.mark.parametrize(("mode", "rule"), [("Normal24", 0), ("Battle24", 5), ("Survival24", 6)])
def test_srv_matchmaking(server: Path, mode: str, rule: int) -> None:
    """Two players' tickets in the same queue are matched into one session: each tracking stream sees SEARCHING, then
    PLACING, then SUCCEEDED with both member entries and the session, and ends; a cancelled ticket ends CANCELLED.
    Then the lobby allows backfill (__gs/m.ebf) with a player really in it: a third player's ticket joins it, and the
    stream listening sees the newcomer's __us document "added"."""
    c, d = H2(server), H2(server)
    tc, td = signed(c, "555555555555"), signed(d, "666666666666")
    config = f"tenants/{TENANT}/matchmakingConfigs/{mode}"

    def ticket(h: H2, tok: str, key: str) -> bytes:
        member = pb(1, "tenants/current/users/current") + pb(2, mapmsg({"batching_key": key, "npln_user_id": "x"}))
        r = unary(
            h, MM + "CreateMatchmakingTicket", pb(1, "tenants/current") + pb(2, pb(2, config) + pb(4, member)), tok
        )
        assert r[5] == [1] and r[2] == [config.encode()] and f"tenants/{TENANT}/matchmakingTickets/" in r[1][0].decode()
        return r[1][0]

    names = [ticket(c, tc, "cv7"), ticket(d, td, "cv7")]
    lone = ticket(c, tc, "cv8")
    streams = [(c, c.sid()), (d, d.sid())]
    for (h, sid), tok, n in zip(streams, (tc, td), names, strict=True):
        call(h, sid, MM + "TrackMatchmakingTicket", pb(1, n), headers(tok))
    cancel = c.sid()
    call(c, cancel, MM + "TrackMatchmakingTicket", pb(1, lone), headers(tc))
    unary(c, MM + "CancelMatchmakingTicket", pb(1, lone), tc)
    pump_all([c, d], lambda: all(sid in h.ended for h, sid in streams), 15)
    for h, sid in streams:
        states = [fields(m)[5][0] for m in msgs(h.data[sid])]
        assert sid in h.ended and states == [1, 2, 3], states
        last = fields(msgs(h.data[sid])[-1])
        users = [fields(fields(e)[1][0])[1][0].decode() for e in last[6]]
        assert users == [
            f"tenants/{TENANT}/users/u-{555555555555:016x}",
            f"tenants/{TENANT}/users/u-{666666666666:016x}",
        ]
        session = fields(last[7][0])
        assert mapof(session[11][0]) == {"batching_key": "cv7", "rule": rule}
        assert session[8] == [b"gamesync.npln.nintendo.net"]
        assert all(fields(e)[3][0] for e in last[6]), "each member's credential"
    assert [fields(m)[5][0] for m in msgs(c.data[cancel])] == [1, 6] and cancel in c.ended

    # Fill the newly formed lobby while its first clients are still loading, before any __pus/ebf write.
    f = H2(server)
    tf = signed(f, "888888888888")
    session_name = fields(fields(msgs(c.data[streams[0][1]])[-1])[7][0])[1][0]
    early = unary(f, GSS + "JoinGameSession", pb(1, session_name), tf)
    assert len(early[1]) == 3, "free seats are available during initial lobby loading"

    uc, ue = f"{555555555555:016x}", f"{777777777777:016x}"
    entry = fields(fields(msgs(c.data[streams[0][1]])[-1])[6][0])
    issued = unary(c, GSYNC + "IssueToken", pb(1, f"userSessions/us-{uc}") + pb(2, entry[3][0]), tenant=False)
    gc = fields(issued[1][0])[2][0].decode()
    lc = c.sid()
    call(c, lc, GSYNC + "KeepUserSession", pb(3, pb(1, pb(1, "t/1") + pb(3, pb(1, "docs/__us")))), headers(gc, False),
         end=False)  # fmt: skip
    pump_all([c], lambda: ("target", "1", 2) in events(c, lc))
    write(c, gc, w_set("__gs/m", {"cp": True, "ip": True, "ebf": True}), w_set(f"__pgn/g1/__pus/us-{uc}", {}))
    # A disconnected player can start a fresh matchmaking ticket while the
    # original session still has a live peer. Reattach to its existing member
    # slot rather than making a second solo lobby 15 seconds later.
    returning = ticket(d, td, "cv7")
    sr = d.sid()
    call(d, sr, MM + "TrackMatchmakingTicket", pb(1, returning), headers(td))
    pump_all([c, d], lambda: sr in d.ended, 6)
    returned = fields(msgs(d.data[sr])[-1])
    assert [fields(m)[5][0] for m in msgs(d.data[sr])] == [1, 2, 3]
    assert fields(returned[7][0])[1][0] == session_name
    assert len(returned[6]) == 3, "returning player reuses member entry; no duplicate racer"
    assert "returning player reattached to " in (server / "logs/server.log").read_text()
    # An already connected racer starting a second ticket is NOT a reconnect:
    # never complete that ticket into the active P2P race in parallel.
    connected_ticket = ticket(c, tc, "cv7")
    sc = c.sid()
    call(c, sc, MM + "TrackMatchmakingTicket", pb(1, connected_ticket), headers(tc))
    pump_all([c, d], lambda: len(msgs(c.data.get(sc, b""))) > 0, 3)
    assert [fields(m)[5][0] for m in msgs(c.data[sc])] == [1], "active peer must not reattach"
    unary(c, MM + "CancelMatchmakingTicket", pb(1, connected_ticket), tc)
    pump_all([c, d], lambda: sc in c.ended, 3)
    assert [fields(m)[5][0] for m in msgs(c.data[sc])] == [1, 6]
    e = H2(server)
    te = signed(e, "777777777777")
    name = ticket(e, te, "cv7")
    se = e.sid()  # after the ticket's own call: stream ids only rise
    call(e, se, MM + "TrackMatchmakingTicket", pb(1, name), headers(te))
    pump_all([c, e], lambda: se in e.ended, 6)
    assert [fields(m)[5][0] for m in msgs(e.data[se])] == [1, 2, 3]
    entries = [fields(x) for x in fields(msgs(e.data[se])[-1])[6]]
    assert [x[2][0].decode().rsplit("/", 1)[1] for x in entries][-1] == f"us-{ue}" and len(entries) == 4
    added = [x for x in events(c, lc) if x[0] == "doc" and x[3] == f"__us/us-{ue}"]
    assert added and added[0][2] == 1 and added[0][4]["ussid"] == 4, added
    # Friend-card lookup and direct join use the same public lobby, with the mode's actual rule.
    batch = unary(f, GSS + "BatchGetGameSessions", pb(1, "tenants/current") + pb(2, session_name), tf)
    assert fields(batch[1][0])[1] == [session_name]
    assert mapof(fields(batch[1][0])[11][0])["rule"] == rule
    joined = unary(f, GSS + "JoinGameSession", pb(1, session_name), tf)
    assert len(joined[1]) == 4, "a friend joins the accepting existing public lobby"
    assert fields(joined[2][0])[1][0] == session_name, "friend join must target the requested lobby"
    # A malformed or vanished friend's lobby must NOT silently select f's
    # most recently joined session. Old code fell back to session_recent().
    missing = unary(f, GSS + "JoinGameSession",
                    pb(1, f"tenants/{TENANT}/gameSessions/gs-nonexistent"), tf)
    assert not missing, "a missing friend lobby must fail, never return stale credentials"
    outsider = H2(server)
    outsider_token = signed(outsider, "999999999999")
    write(c, gc, w_set("__gs/m", {"cp": True, "ip": True, "ebf": True, "bfmax": 4}))
    assert not unary(outsider, GSS + "JoinGameSession", pb(1, session_name), outsider_token), "full lobby refuses join"
    write(c, gc, w_set("__gs/m", {"cp": False, "ip": True, "ebf": True}))
    assert not unary(outsider, GSS + "JoinGameSession", pb(1, session_name), outsider_token), "closed lobby refuses join"
    rejected = unary(d, GSS + "JoinGameSession", pb(1, session_name), td)
    assert rejected, "an existing member may reconnect even after admission closes"
    assert "joined gs-" in (server / "logs/server.log").read_text()

    # Regression for Nov 19's stuck "Waiting for players to join": the
    # client deliberately closes KeepUserSession (the server logged -> 0),
    # then presses Online again. That is a new matchmaking cycle, NOT a
    # transport reset. Returning the stale gs-0001 credentials traps it.
    d_entry = fields(fields(msgs(d.data[streams[1][1]])[-1])[6][1])
    ud = f"{666666666666:016x}"
    issued_d = unary(d, GSYNC + "IssueToken",
                     pb(1, f"userSessions/us-{ud}") + pb(2, d_entry[3][0]), tenant=False)
    gd = fields(issued_d[1][0])[2][0].decode()
    ld = d.sid()
    call(d, ld, GSYNC + "KeepUserSession",
         pb(3, pb(1, pb(1, "test/leave") + pb(3, pb(1, "docs/__us")))),
         headers(gd, False), end=False)
    pump_all([c, d], lambda: "member=u-" + ud + " streams=1" in
             (server / "logs/server.log").read_text(), 3)
    d.s.sendall(frame(0, 1, ld, b""))  # intentional half-close
    pump_all([c, d], lambda: ld in d.ended, 3)
    fresh = ticket(d, td, "cv7")
    sf = d.sid()
    call(d, sf, MM + "TrackMatchmakingTicket", pb(1, fresh), headers(td))
    pump_all([c, d], lambda: bool(msgs(d.data.get(sf, b""))), 3)
    assert [fields(m)[5][0] for m in msgs(d.data[sf])] == [1], (
        "intentional departure must not silently reuse the previously raced session")
    unary(d, MM + "CancelMatchmakingTicket", pb(1, fresh), td)
    pump_all([c, d], lambda: sf in d.ended, 3)
    assert [fields(m)[5][0] for m in msgs(d.data[sf])] == [1, 6]

    for h in (c, d, e, f, outsider):
        h.s.close()


def test_srv_multiple_matchmaking_lobbies(server: Path) -> None:
    """A full queue is split into separate sessions; all four players complete in two simultaneous lobbies."""
    clients = [H2(server) for _ in range(4)]
    try:
        tokens = [signed(h, f"{700000000000 + i:012d}") for i, h in enumerate(clients)]
        config = f"tenants/{TENANT}/matchmakingConfigs/Normal2"
        names = []
        for i, (h, token) in enumerate(zip(clients, tokens, strict=True)):
            member = pb(1, "tenants/current/users/current") + pb(
                2, mapmsg({"batching_key": "multi-lobby", "npln_user_id": str(i)})
            )
            created = unary(
                h, MM + "CreateMatchmakingTicket", pb(1, "tenants/current") + pb(2, pb(2, config) + pb(4, member)),
                token,
            )
            names.append(created[1][0])

        streams = [h.sid() for h in clients]
        for h, token, sid, name in zip(clients, tokens, streams, names, strict=True):
            call(h, sid, MM + "TrackMatchmakingTicket", pb(1, name), headers(token))
        pump_all(clients, lambda: all(sid in h.ended for h, sid in zip(clients, streams, strict=True)), 15)

        sessions = []
        for h, sid in zip(clients, streams, strict=True):
            assert sid in h.ended, "every matchmaking ticket should complete"
            terminal = fields(msgs(h.data[sid])[-1])
            assert terminal[5] == [3]
            assert len(terminal[6]) == 2, "each lobby contains exactly the configured two players"
            sessions.append(fields(terminal[7][0])[1][0])
        assert len(set(sessions)) == 2, "the full queue is served by two independent game sessions"
    finally:
        for h in clients:
            h.s.close()
