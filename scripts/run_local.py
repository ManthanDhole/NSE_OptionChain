"""Launch the three independent app processes; PostgreSQL runs separately."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    os.chdir(ROOT)
    from dotenv import load_dotenv
    load_dotenv(ROOT/'.env')
    children = []
    def shutdown(*args):
        raise KeyboardInterrupt
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, shutdown)
    try:
        for module in ('services.api', 'services.collector', 'services.ui'):
            children.append(subprocess.Popen([sys.executable, '-m', module]))
        print('UI http://localhost:8765 | API http://localhost:8000 | PostgreSQL 55432', flush=True)
        while True:
            if any(child.poll() is not None for child in children):
                raise RuntimeError('A service exited; check its error output above.')
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=110)
            except subprocess.TimeoutExpired:
                child.kill()


if __name__ == '__main__':
    main()
