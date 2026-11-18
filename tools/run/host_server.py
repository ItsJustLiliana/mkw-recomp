"""Configure this user's standalone lobby and coturn services without changing other applications.

Why: players share one Linux service; game hosts and Tailscale are not the public server's lifecycle.
Usage: python tools/run/host_server.py --root /projects/mkw-recomp-server --lan-ip ADDRESS --public-ip ADDRESS
       Add --start to enable the two isolated systemd user services after the binaries have been built.
"""
import argparse
import ipaddress
from pathlib import Path
import secrets
import subprocess


def write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o600)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--lan-ip", type=ipaddress.IPv4Address, required=True)
    ap.add_argument("--public-ip", type=ipaddress.IPv4Address, required=True)
    ap.add_argument("--port", type=int, default=18443)
    ap.add_argument("--start", action="store_true")
    args = ap.parse_args()
    if not 1024 <= args.port <= 65533:
        ap.error("port must leave space for the friends API at port + 1")
    root = args.root.resolve()
    lobby = root / "build/server/mkw-server"
    relay = root / "deps/coturn/bin/turnserver"
    if not lobby.is_file() or not relay.is_file():
        ap.error("build mkw-server and install coturn under ROOT/deps/coturn first")
    data = root / "server"
    data.mkdir(mode=0o700, parents=True, exist_ok=True)
    data.chmod(0o700)
    settings = data / "settings.txt"
    values = dict(line.split("=", 1) for line in settings.read_text().splitlines() if "=" in line) if settings.exists() else {}
    values.update(turn_host=str(args.public_ip), turn_port="3479")
    values.setdefault("turn_secret", secrets.token_hex(24))
    if len(values["turn_secret"]) < 32:
        ap.error("the existing TURN secret is too short; no service was started")
    write_private(settings, "".join(f"{key}={value}\n" for key, value in values.items()))
    config = root / "turnserver.conf"
    lines = ["# MKW-managed relay", "listening-port=3479", f"listening-ip={args.lan_ip}",
             f"relay-ip={args.lan_ip}", f"external-ip={args.public_ip}/{args.lan_ip}",
             "realm=mkw-recomp", "use-auth-secret", f"static-auth-secret={values['turn_secret']}",
             "min-port=49152", "max-port=49407", "total-quota=128", "user-quota=4", "relay-threads=1",
             "no-tcp", "no-tcp-relay", "no-tls", "no-multicast-peers", "log-file=stdout"]
    # The public relay must not provide UDP access to the server's private LAN or local applications.
    for block in ["0.0.0.0-0.255.255.255", "10.0.0.0-10.255.255.255", "100.64.0.0-100.127.255.255",
                  "127.0.0.0-127.255.255.255", "169.254.0.0-169.254.255.255", "172.16.0.0-172.31.255.255",
                  "192.168.0.0-192.168.255.255", "224.0.0.0-255.255.255.255"]:
        lines.append(f"denied-peer-ip={block}")
    write_private(config, "\n".join(lines) + "\n")
    units = Path.home() / ".config/systemd/user"
    commands = {"mkw-lobby": f"{lobby} --host {args.lan_ip} --port {args.port} --data {data}",
                "mkw-relay": f"{relay} -c {config}"}
    for name, command in commands.items():
        path = units / f"{name}.service"
        if path.exists() and "# MKW-managed" not in path.read_text():
            ap.error(f"refusing to replace an unmanaged unit: {path}")
        write_private(path, f"""# MKW-managed
[Unit]
Description=Mario Kart shared {'lobby' if name == 'mkw-lobby' else 'relay'}
After=network-online.target
[Service]
Type=simple
WorkingDirectory={root}
ExecStart={command}
Restart=on-failure
RestartSec=3
TimeoutStopSec=10
UMask=0077
NoNewPrivileges=yes
MemoryHigh=128M
MemoryMax=256M
CPUQuota=50%
TasksMax=32
[Install]
WantedBy=default.target
""")
    if args.start:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        # enable --now does not reload the config of an already-running service.
        # Restart both so the new relay settings and server binary are actually used.
        subprocess.run(["systemctl", "--user", "enable", "mkw-relay.service", "mkw-lobby.service"], check=True)
        subprocess.run(["systemctl", "--user", "restart", "mkw-relay.service", "mkw-lobby.service"], check=True)
        subprocess.run(["systemctl", "--user", "is-active", "mkw-relay.service", "mkw-lobby.service"], check=True)
    print(f"Configured lobby {args.lan_ip}:{args.port}, friends API {args.port + 1}, relay UDP 3479/49152-49407.")
    print("The shared relay secret remains in owner-only files and was not printed.")


if __name__ == "__main__":
    main()
