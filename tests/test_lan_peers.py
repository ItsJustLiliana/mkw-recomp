"""Compile the production VPN peer helpers without the game or GPU.

Checks address restrictions, broadcast selection, the peer limit and configuration reloads.
Usage: uv run pytest tests/test_lan_peers.py (needs a C compiler; Windows needs MinGW Clang).
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_peer_selection(tmp_path: Path) -> None:
    cc = shutil.which("clang") or shutil.which("cc") or shutil.which("gcc")
    bundled = ROOT / "build/win/llvm-mingw/bin/x86_64-w64-mingw32-clang.exe"
    if os.name == "nt" and bundled.exists():
        cc = str(bundled)
    if not cc:
        pytest.skip("no C compiler; install tools/build/windeps.py on Windows")
    source = (ROOT / "runtime/sdk/ov/ov_socket.c").read_text()
    policy = source[source.index("int ov_lan_only("):source.index("// 0, or -1 with ENETUNREACH")]
    peers = source[source.index("static int net_startup("):source.index("static int msg_flags(")]
    prelude = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#else
#include <arpa/inet.h>
#include <netdb.h>
#endif
#define OV_LAN_PEERS_MAX 16
typedef int PlatMutex;
#define PLAT_MUTEX_INIT 0
#define plat_mutex_lock(x) ((void)(x))
#define plat_mutex_unlock(x) ((void)(x))
#define rt_log(...) ((void)0)
static const char *setting = "", *local = "";
static const char *cfg_get(const char *key) {
    return !strcmp(key, "MKW_LAN_PEERS") ? setting : local;
}
static uint32_t ip(const char *s) {
    struct in_addr a;
    assert(inet_pton(AF_INET, s, &a) == 1);
    return a.s_addr;
}
'''
    main = r'''
int main(void) {
    uint32_t out[OV_LAN_PEERS_MAX], first;
    assert(net_startup() == 0);
    assert(!ov_lan_peer_first(&first));
    setting = "100.64.0.2,10.0.0.2;192.168.1.2 8.8.8.8";
    assert(ov_lan_peer_first(&first) && first == ip("100.64.0.2"));
    assert(ov_lan_peers(ip("255.255.255.255"), out, 16) == 3);
    assert(out[1] == ip("10.0.0.2") && out[2] == ip("192.168.1.2"));
    assert(!ov_lan_peers(ip("10.0.0.99"), out, 16));
    ov_lan_set_broadcast(ip("100.127.255.255"));
    assert(ov_lan_peers(ip("100.127.255.255"), out, 1) == 1);
    assert(ov_lan_peers(ip("100.127.255.255"), out, 0) == 0);
    setting = "";
    assert(!ov_lan_peer_first(&first));
    setting = "10.0.0.1 10.0.0.2 10.0.0.3 10.0.0.4 10.0.0.5 10.0.0.6 10.0.0.7 10.0.0.8 "
              "10.0.0.9 10.0.0.10 10.0.0.11 10.0.0.12 10.0.0.13 10.0.0.14 10.0.0.15 10.0.0.16 10.0.0.17";
    assert(ov_lan_peers(ip("255.255.255.255"), out, 16) == 16);
    local = "127.0.0.2";
    assert(ov_lan_bind_address() == ip(local));
    local = "100.64.0.1";
    assert(!ov_lan_bind_address());
    local = "invalid";
    assert(!ov_lan_bind_address());
    assert(!ov_lan_only(0x08080808));
    assert(ov_lan_only(0x64400001));
    return 0;
}
'''
    src, exe = tmp_path / "peers.c", tmp_path / "peers.exe"
    src.write_text(prelude + policy + peers + main)
    command = [cc, "-std=gnu11", str(src), "-o", str(exe)]
    if os.name == "nt":
        command += ["-lws2_32"]
    subprocess.run(command, check=True)
    subprocess.run([str(exe)], check=True)
