"""Check the real SDK clock observer without the game: Online Play's libc++ steady_clock must not enter an unset
console time hook. Usage: uv run pytest tests/test_online_clock.py (a C compiler is required).
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_online_sdk_clocks(tmp_path: Path) -> None:
    cc = shutil.which("clang") or shutil.which("cc") or shutil.which("gcc")
    bundled = ROOT / "build/win/llvm-mingw/bin/x86_64-w64-mingw32-clang.exe"
    if not cc and bundled.exists():
        cc = str(bundled)
    if not cc:
        pytest.skip("no C compiler")
    source = (ROOT / "runtime/sdk/ov/ov_time2.c").read_text()
    observer = source[source.index("static void obs_clock_gettime("):source.index('HOST_OBSERVER("clock_gettime"')]
    program = r'''
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
typedef struct { uint64_t x[32]; } Cpu;
static uint64_t wall = 1800000000123456789ull, mono = 9876543210ull;
static int originals;
static uint64_t arg_x(Cpu *c, int i) { return c->x[i]; }
static void *arg_p(Cpu *c, int i) { return (void *)(uintptr_t)c->x[i]; }
static void ret_x(Cpu *c, uint64_t v) { c->x[0] = v; }
static uint64_t plat_unix_time_ns(void) { return wall; }
static uint64_t plat_now_ns(void) { return mono; }
static uint64_t rt_original(Cpu *c) { (void)c; originals++; return (uint64_t)-1; }
static const char *cfg_get(const char *s) { (void)s; return NULL; }
#define rt_log(...) ((void)0)
''' + observer + r'''
int main(void) {
    Cpu c = {0};
    int64_t ts[2] = {-1, -1};
    c.x[1] = (uintptr_t)ts;
    c.x[0] = 0;
    obs_clock_gettime(&c);
    assert(c.x[0] == 0 && ts[0] == 1800000000 && ts[1] == 123456789 && !originals);
    c.x[0] = 1;
    obs_clock_gettime(&c);
    assert(c.x[0] == 0 && ts[0] == 9 && ts[1] == 876543210 && !originals);
    // A wall-clock correction must never change the monotonic clock used for online deadlines.
    wall = 1;
    mono += 1000000000;
    c.x[0] = 1;
    obs_clock_gettime(&c);
    assert(c.x[0] == 0 && ts[0] == 10 && ts[1] == 876543210 && !originals);
    c.x[0] = 99;
    obs_clock_gettime(&c);
    assert(c.x[0] == (uint64_t)-1 && originals == 1);
    return 0;
}
'''
    src, exe = tmp_path / "clock.c", tmp_path / "clock.exe"
    src.write_text(program)
    subprocess.run([cc, "-std=gnu11", str(src), "-o", str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
