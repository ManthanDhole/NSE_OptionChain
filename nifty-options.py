#!/usr/bin/env python3
"""Control the local application: python3 nifty-options.py start|stop|status|restart."""
import argparse
import fcntl
from pathlib import Path
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parent
LOCAL = ROOT / '.local'
PIDFILE = LOCAL / 'app.pid'
LOG = LOCAL / 'app.log'
PYTHON = ROOT / '.venv/bin/python'
LAUNCHER = ROOT / 'scripts/run_local.py'
DATA = LOCAL / 'pgdata'


def managed_pid():
    # Confirm identity before signalling a PID that may have been reused.
    try:
        pid = int(PIDFILE.read_text().strip())
        if pid <= 1:
            return None
        result = subprocess.run(['ps', '-p', str(pid), '-o', 'stat=', '-o', 'command='], capture_output=True, text=True)
        state, command = result.stdout.strip().split(maxsplit=1)
        if 'Z' not in state and command.endswith(' -u ' + str(LAUNCHER)):
            return pid
    except (OSError, ValueError):
        pass
    return None


def pg_running():
    return (DATA / 'PG_VERSION').exists() and subprocess.run(['pg_ctl', '-D', str(DATA), 'status'], capture_output=True).returncode == 0


def port_busy(port):
    with socket.socket() as sock:
        sock.settimeout(1)
        return sock.connect_ex(('127.0.0.1', port)) == 0


def ready():
    try:
        opener = build_opener(ProxyHandler({}))
        for path in ('/', '/health'):
            with opener.open('http://127.0.0.1:8765' + path, timeout=2) as r:
                if r.status != 200:
                    return False
        return True
    except OSError:
        return False


def stop_services():
    pid = managed_pid()
    if pid:
        os.kill(pid, signal.SIGINT)
        print('Stopping app services; allowing the current fetch to finish…', flush=True)
        deadline = time.monotonic() + 125
        while managed_pid() == pid:
            if time.monotonic() >= deadline:
                raise RuntimeError('Services have not exited. Database left running; check ' + str(LOG))
            time.sleep(.25)
    elif port_busy(8765) or port_busy(8000):
        raise RuntimeError('An unmanaged service is using an app port. Stop its launcher first; database left running.')
    PIDFILE.unlink(missing_ok=True)


def stop():
    stop_services()
    if pg_running():
        subprocess.run(['pg_ctl', '-D', str(DATA), 'stop', '-m', 'fast'], check=True)
    print('App and local database stopped. Saved data is preserved.')


def start():
    if managed_pid():
        print('App is already running: http://localhost:8765')
        return
    if any(port_busy(port) for port in (8765, 8000)):
        raise RuntimeError('Port 8765 or 8000 is already in use. Stop the existing app or port-forward first.')
    if not PYTHON.exists():
        raise RuntimeError('Create .venv and install requirements.txt first; see README.md.')
    subprocess.run([str(PYTHON), str(ROOT / 'scripts/setup_local.py')], cwd=ROOT, check=True)
    with LOG.open('ab', buffering=0) as log:
        process = subprocess.Popen([str(PYTHON), '-u', str(LAUNCHER)], cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    PIDFILE.write_text(str(process.pid) + '\n')
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            PIDFILE.unlink(missing_ok=True)
            raise RuntimeError('App exited during startup. Check ' + str(LOG))
        if ready():
            print('App started: http://localhost:8765\nLog: ' + str(LOG))
            return
        time.sleep(.5)
    stop_services()
    raise RuntimeError('Startup timed out. App stopped; database left running. Check ' + str(LOG))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['start', 'stop', 'status', 'restart'])
    args = parser.parse_args()
    if not shutil.which('pg_ctl'):
        parser.exit(1, 'pg_ctl is missing. Add PostgreSQL command-line tools to PATH.\n')
    LOCAL.mkdir(exist_ok=True)
    with (LOCAL / 'app-control.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.command == 'status':
            running = bool(managed_pid())
            print('App: ' + ('running at http://localhost:8765' if running else 'stopped (or not managed by this script)'))
            print('Local database: ' + ('running' if pg_running() else 'stopped'))
            return 0 if running else 1
        if args.command in ('stop', 'restart'):
            stop()
        if args.command in ('start', 'restart'):
            start()
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
