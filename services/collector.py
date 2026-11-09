"""Independent continuous collector: provider -> durable spool -> PostgreSQL."""
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import signal
import threading
import time
import uuid
from .config import INTERVAL, ROOT
from .store import connect, save_snapshot, state_update

log = logging.getLogger('collector')
STOP = threading.Event()


def queue_snapshot(snapshot, attempt_id):
    folder = Path(os.getenv('SPOOL_DIR', ROOT / 'data' / 'spool'))
    folder.mkdir(parents=True, exist_ok=True)
    snapshot['id'] = str(uuid.uuid4())
    path = folder / (snapshot['id']+'.json')
    temporary = path.with_suffix('.tmp')
    payload = {'snapshot': snapshot, 'attempt_id': attempt_id}
    with temporary.open('w') as handle:
        json.dump(payload, handle, default=lambda value: value.isoformat())
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    return path


def drain_spool():
    folder = Path(os.getenv('SPOOL_DIR', ROOT / 'data' / 'spool'))
    for path in sorted(folder.glob('*.json')):
        item = json.loads(path.read_text())
        snapshot = item['snapshot']
        snapshot['collected_at'] = datetime.fromisoformat(snapshot['collected_at'])
        snapshot_id = save_snapshot(snapshot)
        with connect() as conn:
            conn.execute('UPDATE collection_attempts SET snapshot_id=%s,finished_at=now(),error=NULL WHERE id=%s', (snapshot_id, item['attempt_id']))
        path.unlink()
        state_update(last_success=snapshot['collected_at'].isoformat(), error=None)


def run():
    # Session lock prevents a second collector from doubling collection frequency.
    with connect(autocommit=True) as lock:
        if not lock.execute('SELECT pg_try_advisory_lock(8765001) AS acquired').fetchone()['acquired']:
            raise RuntimeError('Another collector is already running')
        next_fetch = 0
        while not STOP.is_set():
            lock.execute('SELECT 1')  # Exit if our session lock was lost during a DB restart.
            attempt_id = None
            try:
                drain_spool()
                with connect() as conn:
                    pending = conn.execute('UPDATE refresh_requests SET handled_at=now() WHERE handled_at IS NULL RETURNING id').fetchall()
                state_update(loading=False, collector_available=True,
                             collection_status='Continuous collection — no time restrictions',
                             refresh_seconds=INTERVAL,
                             next_refresh=time.time()+max(0, next_fetch-time.monotonic()))
                if time.monotonic() < next_fetch and not pending:
                    STOP.wait(min(1, next_fetch-time.monotonic()))
                    continue
                started = time.monotonic()
                with connect() as conn:
                    attempt_id = conn.execute('INSERT INTO collection_attempts(started_at) VALUES(now()) RETURNING id').fetchone()['id']
                state_update(loading=True, error=None, next_refresh=None)
                from .provider import fetch_snapshot
                snapshot = fetch_snapshot()
                queue_snapshot(snapshot, attempt_id)
                drain_spool()
                next_fetch = max(started+INTERVAL, time.monotonic())
                state_update(loading=False, error=None)
                log.info('Stored %s strikes at %s', len(snapshot['rows']), snapshot['collected_at'])
            except Exception as exc:
                log.error('Collection failed: %s', exc)
                try:
                    if attempt_id:
                        with connect() as conn:
                            conn.execute('UPDATE collection_attempts SET finished_at=now(),error=%s WHERE id=%s', (str(exc), attempt_id))
                    state_update(loading=False, error=str(exc), next_refresh=time.time()+INTERVAL)
                except Exception:
                    log.error('Database unavailable; queued snapshots will be retried')
                STOP.wait(INTERVAL)
        state_update(loading=False, collector_available=False, collection_status='Collector stopped', next_refresh=None)


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *args: STOP.set())
    run()


if __name__ == '__main__':
    main()
