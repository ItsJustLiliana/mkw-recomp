"""Compile the actual F1 server invite codec and check routing, typos and malformed input without a game.

Usage: uv run pytest tests/test_net_invite.py (C compiler required; the bundled Windows compiler is supported).
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_invite_codec(tmp_path: Path) -> None:
    cc = shutil.which("clang") or shutil.which("cc") or shutil.which("gcc")
    bundled = ROOT / "build/win/llvm-mingw/bin/x86_64-w64-mingw32-clang.exe"
    if not cc and bundled.exists():
        cc = str(bundled)
    if not cc:
        pytest.skip("no C compiler")
    source = tmp_path / "invite.c"
    source.write_text(r'''
#include <assert.h>
#include "net_invite.h"
int main(void) {
    char code[32], address[32];
    char endpoint[160];
    assert(net_join_endpoint("203.0.113.5:18443", endpoint));
    assert(!strcmp(endpoint, "203.0.113.5:18443"));
    assert(net_join_endpoint("kart.example.com:18443", endpoint));
    assert(net_join_endpoint("kart.example.com", endpoint));
    assert(!net_join_endpoint("https://kart.example.com", endpoint));
    assert(!net_join_endpoint("kart.example.com:65534", endpoint));
    assert(!net_join_endpoint("kart.example.com:18443junk", endpoint));
    assert(!net_join_endpoint("kart.example.com:", endpoint));
    assert(!net_join_endpoint("kart.example.com:0", endpoint));
    assert(!net_join_endpoint("MKW1-broken", endpoint));
    assert(net_invite_make(0x64400001, 8443, code));
    assert(net_invite_parse(code, address));
    assert(!strcmp(address, "100.64.0.1:8443"));
    for (int i = 0; i < 23; i++) {
        char bad[32];
        strcpy(bad, code);
        bad[i] = '!';
        assert(!net_invite_parse(bad, address) && !address[0]);
    }
    char extra[64];
    snprintf(extra, sizeof extra, "%s-extra", code);
    assert(!net_invite_parse(extra, address));
    assert(!net_invite_parse("", address));
    assert(!net_invite_parse(NULL, address));
    assert(!net_invite_make(0x08080808, 8443, code));
    assert(!net_invite_make(0xffffffff, 8443, code));
    assert(!net_invite_make(0, 8443, code));
    assert(!net_invite_make(0x0a000001, 0, code));
    assert(!net_invite_make(0x0a000001, 65534, code));
    for (unsigned i = 1; i < 1000; i++) {
        unsigned ip = 0x0a000000 + i, port = i + 1024;
        assert(net_invite_make(ip, port, code));
        assert(net_invite_parse(code, address));
        char want[32];
        snprintf(want, sizeof want, "10.0.%u.%u:%u", i >> 8, i & 255, port);
        assert(!strcmp(address, want));
    }
    return 0;
}
''')
    exe = tmp_path / "invite.exe"
    subprocess.run([cc, "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-D_CRT_SECURE_NO_WARNINGS", f"-I{ROOT / 'runtime/sdk/ov'}",
                    str(source), "-o", str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
