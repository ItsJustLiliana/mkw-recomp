"""Check the production SDK friend-list override against cached server friends, without the game or a network.

Why: an always-empty SDK list hid accepted server friends in the game's Friends menu.
Usage: uv run pytest tests/test_online_friends.py (C compiler required).
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_sdk_friend_list(tmp_path: Path) -> None:
    bundled = ROOT / "build/win/llvm-mingw/bin/x86_64-w64-mingw32-clang.exe"
    cc = shutil.which("clang") or shutil.which("cc") or shutil.which("gcc")
    if not cc and bundled.exists():
        cc = str(bundled)
    if not cc:
        pytest.skip("no C compiler")
    source = (ROOT / "runtime/sdk/ov/ov_net.c").read_text()
    function = source[source.index("static void ov_friend_list(Cpu"):source.index("static void ov_friends_unavailable(")]
    code = r'''
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include "net_online.h"
typedef struct { uint64_t x[32]; } Cpu;
static NetParty cache;
static int enabled = 1;
static int online(void) { return enabled; }
static void net_snapshot(NetParty *p) { *p = cache; }
#define net_party_snapshot net_snapshot
static void *arg_p(Cpu *c, int i) { return (void *)(uintptr_t)c->x[i]; }
static uint64_t arg_x(Cpu *c, int i) { return c->x[i]; }
static void ret_x(Cpu *c, uint64_t v) { c->x[0] = v; }
''' + function + r'''
int main(void) {
    cache.nfriends = 2;
    strcpy(cache.friends[0].code, "111111111111");
    strcpy(cache.friends[0].name, "Friend One");
    strcpy(cache.friends[1].code, "222222222222");
    uint8_t entries[0x400 + 8];
    memset(entries, 0xab, sizeof entries);
    int32_t count = -1;
    Cpu c = {.x = {(uintptr_t)&count, (uintptr_t)entries, 0, 0, 2}};
    ov_friend_list(&c);
    uint64_t id;
    memcpy(&id, entries + 0x10, 8);
    assert(c.x[0] == 0 && count == 2 && id == 111111111111ull);
    assert(!strcmp((char *)entries + 0x18, "Friend One") && entries[0x128] == 1);
    assert(!strcmp((char *)entries + 0x218, "222222222222") && entries[0x328] == 1);
    assert(entries[0x400] == 0xab);
    c.x[0] = (uintptr_t)&count; c.x[3] = 1; c.x[4] = 1;
    ov_friend_list(&c);
    assert(count == 1 && !strcmp((char *)entries + 0x18, "222222222222"));
    enabled = 0; c.x[0] = (uintptr_t)&count;
    ov_friend_list(&c); assert(count == 0);
    enabled = 1; c.x[0] = (uintptr_t)&count; c.x[3] = 100;
    ov_friend_list(&c); assert(count == 0);
    return 0;
}
'''
    src, exe = tmp_path / "friends.c", tmp_path / "friends.exe"
    src.write_text(code)
    subprocess.run([cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-D_CRT_SECURE_NO_WARNINGS", f"-I{ROOT / 'runtime/sdk/ov'}",
                    str(src), "-o", str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
