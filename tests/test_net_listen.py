"""When this PC's server listens again (runtime/sdk/ov/net_listen.h): tests/c/t_net_listen.c, built with the C compiler
at hand, replays the address changes of 14/11/2026 (Tailscale's address gone for 4 s, each change restarting the server
and ending every player's session) and checks the server listens again only on changes that last.

Usage: uv run pytest tests
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_net_listen(tmp_path: Path) -> None:
    cc = shutil.which("clang") or shutil.which("cc") or shutil.which("gcc")
    bundled = ROOT / "build/win/llvm-mingw/bin/x86_64-w64-mingw32-clang.exe"
    if not cc and bundled.exists():
        cc = str(bundled)
    if not cc:
        pytest.skip("no C compiler")
    exe = tmp_path / "t_net_listen.exe"
    subprocess.run([cc, "-std=gnu11", "-O1", "-Wall", "-Wextra", "-Werror", f"-I{ROOT / 'runtime/sdk/ov'}",
                    str(ROOT / "tests/c/t_net_listen.c"), "-o", str(exe)], check=True)  # fmt: skip
    out = subprocess.run([str(exe)], capture_output=True, text=True, check=False)
    assert out.returncode == 0 and " 0 failed" in out.stdout, out.stdout
