"""Readiness only: a live collector updates its SQL heartbeat."""
from datetime import datetime, timezone
from .store import connect

if __name__ == '__main__':
    with connect() as conn:
        row = conn.execute('SELECT heartbeat FROM collector_state WHERE id=1').fetchone()
    age = (datetime.now(timezone.utc)-row['heartbeat']).total_seconds()
    raise SystemExit(0 if age < 180 else 1)
