"""Run tests on a disposable, loopback-only PostgreSQL cluster (Linux/WSL).

Requires installed PostgreSQL binaries and openssl. Never uses DATABASE_URL.
The cluster is stopped in finally; its temporary directory is then removed.
"""
import argparse
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
from urllib.parse import urlencode


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bin", default="/usr/lib/postgresql/18/bin")
    args, pytest_args = parser.parse_known_args()
    binary = Path(args.bin)
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="studymore-pg-") as folder:
        work = Path(folder)
        data = work / "data"
        local_env = {name: value for name, value in os.environ.items() if not name.startswith("PG")}
        def run(*cmd):
            return subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=local_env)
        run(str(binary / "initdb"), "-D", str(data), "-U", "studymore_test_owner", "-A", "trust", "--no-locale", "--encoding=UTF8")
        run("openssl", "req", "-new", "-x509", "-nodes", "-days", "1", "-subj", "/CN=localhost",
            "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
            "-keyout", str(data / "server.key"), "-out", str(data / "server.crt"))
        (data / "server.key").chmod(0o600)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        with (data / "postgresql.conf").open("a") as config:
            config.write(f"\nlisten_addresses='127.0.0.1'\nport={port}\nssl=on\nunix_socket_directories='{work}'\n")
        started = False
        try:
            run(str(binary / "pg_ctl"), "-D", str(data), "-l", str(work / "postgres.log"), "-w", "start")
            started = True
            run(str(binary / "createdb"), "-h", "127.0.0.1", "-p", str(port), "-U", "studymore_test_owner", "studymore_test_suite")
            env = local_env.copy()
            for key in ("DATABASE_URL", "DATABASE_URL_DIRECT", "VERCEL", "VERCEL_ENV", "STUDYMORE_ENV"):
                env.pop(key, None)
            tls = urlencode({"sslmode": "verify-full", "sslrootcert": data / "server.crt"})
            env["STUDYMORE_TEST_DATABASE_URL"] = f"postgresql://studymore_test_owner@127.0.0.1:{port}/studymore_test_suite?{tls}"
            # Copy source only: tests cannot read or mutate user SQLite databases.
            checkout = work / "checkout"
            checkout.mkdir()
            for name in ("src", "migrations", "scripts"):
                shutil.copytree(root / name, checkout / name, ignore=shutil.ignore_patterns("*.db", "*.db-*", "*.sqlite*", "uploads", "__pycache__", ".env", "app copy.py"))
            shutil.copy2(root / "index.py", checkout / "index.py")
            result = subprocess.run([sys.executable, "-m", "pytest", "-q", *(pytest_args or ["src"])], cwd=checkout, env=env)
            return result.returncode
        finally:
            if started:
                run(str(binary / "pg_ctl"), "-D", str(data), "-m", "fast", "-w", "stop")


if __name__ == "__main__":
    sys.exit(main())
