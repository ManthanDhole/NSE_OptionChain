import os
from pathlib import Path
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')
IST = ZoneInfo('Asia/Kolkata')
INTERVAL = max(1, int(os.getenv('REFRESH_SECONDS', '5')))


def database_url(reader=False):
    name = 'API_DATABASE_URL' if reader else 'DATABASE_URL'
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f'{name} is not configured; run scripts/setup_local.py first.')
    return value
