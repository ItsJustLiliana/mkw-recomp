"""Build the standalone Linux lobby server without game files, SDL, Vulkan or a graphical session.

Why: a shared server should use only the existing service code and pinned TLS/HTTP2 libraries, not a game runtime.
Usage: uv run tools/build/server.py [--out build/server] [--jobs 1]. GCC/Clang and ar must be installed.
"""
import argparse
import concurrent.futures
import os
import json
import shlex
from pathlib import Path
import shutil
import subprocess
import sys

from srvdeps import ensure

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=ROOT / "build/server")
    ap.add_argument("--jobs", type=int, default=1)
    args = ap.parse_args()
    if not sys.platform.startswith("linux"):
        ap.error("the standalone server is built on Linux")
    cc = shutil.which(os.environ.get("CC", "cc"))
    if not cc:
        ap.error("install a C compiler first")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    deps = ensure(False)
    common = ["-std=gnu11", "-O1", "-D_GNU_SOURCE", "-ffunction-sections", "-fdata-sections"]
    jobs = []
    for group, (sources, flags) in deps.groups.items():
        for i, source in enumerate(sources):
            jobs.append((source, out / f"{group}-{i}.o", flags))
    includes = [f"-I{ROOT / 'runtime'}", f"-I{ROOT / 'runtime/srv'}", *[f"-I{p}" for p in deps.includes]]
    flags = [*includes, *deps.defines, "-DMKW_SERVER_STANDALONE", "-Wall", "-Wextra"]
    sources = [*sorted((ROOT / "runtime/srv").glob("*.c")), ROOT / "runtime/platform/linux_fs.c"]
    for i, source in enumerate(sources):
        jobs.append((source, out / f"server-{i}.o", flags))

    def compile_one(job: tuple) -> Path:
        source, obj, flags = job
        command = [cc, *common, *flags, "-MMD", "-MF", str(obj.with_suffix(".d")), "-c", str(source), "-o", str(obj)]
        stamp = obj.with_suffix(".cmd")
        spec = json.dumps(command)
        depfile = obj.with_suffix(".d")
        if obj.exists() and stamp.exists() and stamp.read_text() == spec and depfile.exists():
            dependencies = shlex.split(depfile.read_text().partition(":")[2].replace("\\\n", " "))
            if all(Path(p).exists() and Path(p).stat().st_mtime_ns <= obj.stat().st_mtime_ns for p in dependencies):
                return obj
        subprocess.run(command, check=True)
        stamp.write_text(spec)
        return obj

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(args.jobs, 4))) as pool:
        objects = list(pool.map(compile_one, jobs))
    exe = out / "mkw-server"
    subprocess.run([cc, "-Wl,--gc-sections", *map(str, objects), "-lm", "-lpthread", "-o", str(exe)], check=True)
    print(exe)


if __name__ == "__main__":
    main()
