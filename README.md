# NSE Option Chain

A PostgreSQL-backed NIFTY dashboard with separate UI, API, collector, and database
processes. Green calls and red puts flank a centered spot price. The table shows
strikes within ±600 points of spot, RSI, volume/OI ranking highlights, percentages,
and the configurable strong/weak banners.

## Architecture

```text
NSE + TradingView → Collector → PostgreSQL ← API ← UI web server ← Browser
                      ↓                        ↑
                 retry spool          refresh-request queue in SQL
```

- **Collector** (`services/collector.py`): fetches data, enriches RSI, and writes SQL.
  It has no UI or HTTP server. A database lock allows only one collector per database.
- **Database** (`database/schema.sql`): retains every collected snapshot and its raw
  NSE response, all returned strikes for the selected expiry, typed call/put quotes,
  RSI metadata, collection attempts, and calendar/status information.
- **API** (`services/api.py`): reads committed PostgreSQL data. It never calls NSE or
  TradingView. It can queue a refresh request but cannot modify stored market data.
- **UI** (`services/ui.py`, `ui/index.html`): serves the interface and proxies API
  requests. It has no database credentials, provider libraries, or collection thread.

The legacy `nifty_option_chain.py` remains available as a standalone CSV tool.
It is **not** used by the scheduled database collector. Its three-CSV rotation
has no effect on PostgreSQL history.

## Collection schedule

- **NSE trading days only**, Monday–Friday excluding NSE F&O trading holidays.
- **09:15 inclusive to 15:30 exclusive, Asia/Kolkata (IST)**. No new market-data
  request starts at or after 15:30. An in-flight request may finish and be saved
  after the close, so fetched data is not discarded.
- Requests start every **5 seconds** when possible. Slow requests never overlap;
  the next starts when the previous request finishes.
- Holidays come from NSE's `holiday-master?type=trading` **FO** calendar, cached in
  PostgreSQL and refreshed daily. Missing/stale calendars or a new year without
  a verified calendar pause collection. Retries occur every 10 minutes.
- Special weekend/Muhurat sessions outside this regular schedule are not collected.
- Manual refresh cannot bypass the session or holiday rules. Outside the session,
  the UI remains available and displays the last stored snapshot.
- A running computer and services are required. Missed snapshots while the app is
  stopped cannot be reconstructed from a current option-chain endpoint.

All returned strikes for the **nearest available expiry** are stored, not just
those displayed in the UI. RSI enrichment is requested for displayed strikes;
other strikes retain the raw NSE data. This does not collect all available expiries.
PostgreSQL history is never automatically pruned. Plan disk capacity and backups
as collection history grows. A local durable spool retries completed fetches when
SQL writes fail; replay is idempotent.

## Clone

```sh
git clone --branch microservices-architecture-app git@github.com:ManthanDhole/NSE_OptionChain.git
cd NSE_OptionChain
```

Already cloned the repository? Select this architecture branch first:

```sh
git fetch origin
git switch microservices-architecture-app
```

## Run locally without Docker

Requires Python 3.10+ and PostgreSQL 15+ command-line tools (`initdb`, `pg_ctl`) on
PATH. The local setup was verified with PostgreSQL 18 on macOS. Install PostgreSQL
using its official distribution or your package manager if it is not installed.

### First-time startup

Run these commands from the repository directory:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/setup_local.py
python scripts/run_local.py
```

Open [http://localhost:8765](http://localhost:8765).

`setup_local.py` creates an **isolated** PostgreSQL cluster in `.local/pgdata`,
listening only on `127.0.0.1:55432`, and database `nifty`. It generates random
credentials in `.env` (mode 600, excluded from Git), applies the schema, and creates
separate collector and API roles. It does not modify an existing system cluster.
Do not delete `.local/pgdata` or regenerate `.env` against an existing cluster.

### Start again after stopping the application or restarting your computer

From the repository directory, reuse your existing environment and database:

```sh
source .venv/bin/activate
python scripts/setup_local.py
python scripts/run_local.py
```

`setup_local.py` is safe to rerun: it reuses `.env` and the existing database,
starts PostgreSQL if stopped, and reapplies the idempotent schema/role setup.
It does not erase collected history. Do not rerun `initdb` manually or remove
`.env` / `.local/pgdata` to restart the application.

Open [http://localhost:8765](http://localhost:8765). Keep the launcher terminal
running. Outside market hours, a **Session paused** indicator is expected; saved
data remains available and collection resumes at the next eligible session.
Your computer must remain awake and the collector running to record that session.
The native processes are not installed as automatic operating-system startup services.

### Verify startup

In a second terminal:

```sh
pg_isready -h 127.0.0.1 -p 55432
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8765/api/state
```

`/health` should return `{"status": "ok"}`. `/api/state` reports the collector's
schedule reason, loading/error state, and latest database snapshot. An empty
snapshot is normal before the first successful collection. Database activity can
also be inspected with the SQL queries below.

### Shut down safely

Press **Ctrl+C** in the launcher terminal. This stops the UI, API, and collector,
allowing an in-flight capture to finish. PostgreSQL remains running until stopped:

```sh
pg_ctl -D .local/pgdata stop -m fast
```

This closes the database safely and preserves all stored data. Use the restart
commands above next time. If running services separately, stop the collector
first, then the API/UI, and stop PostgreSQL last.

### Run each service independently

`run_local.py` launches three independent processes. To run them in separate
terminals instead, first start PostgreSQL with `python scripts/setup_local.py`,
then activate `.venv` in **each terminal** and run one command per terminal:

```sh
python -m services.api       # 127.0.0.1:8000
python -m services.collector # scheduled data capture
python -m services.ui        # 127.0.0.1:8765
```

`python dashboard.py` now starts **only the UI**, for compatibility. The API and
collector must be running separately. Set `API_URL` when the API runs elsewhere.
For instance: `API_URL=http://127.0.0.1:8001 python -m services.ui`.

### Manage PostgreSQL independently

Check status, stop, or restart the isolated PostgreSQL server:

```sh
pg_ctl -D .local/pgdata status
pg_ctl -D .local/pgdata stop -m fast
pg_ctl -D .local/pgdata -l .local/postgres.log -o '-h 127.0.0.1 -p 55432' start
```

To use an existing PostgreSQL server instead, provision an application database,
set `ADMIN_DATABASE_URL`, `DATABASE_URL`, `API_DATABASE_URL`, `COLLECTOR_PASSWORD`,
and `API_PASSWORD` in `.env`, then run `python -m services.migrate`. The admin
connection is used only for schema/role setup, not by the running services.
`DATABASE_URL` must use role `nifty_collector`, and `API_DATABASE_URL` role `nifty_api`.

## Run with Docker Compose

The app now has multiple independently runnable services; Compose connects them
without coupling them into a single container. Docker Desktop/Engine and Compose
are required. Generate `.env` once (or keep the existing local setup's credentials):

```sh
python3 scripts/setup_local.py --env-only
docker compose up --build -d
```

- UI: [http://localhost:8765](http://localhost:8765)
- API: [http://localhost:8000/health](http://localhost:8000/health)
- Docker PostgreSQL: `127.0.0.1:55433` (native local setup uses **55432**)
- `postgres-data`: durable database volume; `collector-spool`: pending fetched data.

The migration service waits for PostgreSQL, then exits after schema/role setup.
The collector and API start afterward. The UI image contains only the UI server
and HTML, and does not install provider or PostgreSQL packages.

Stop local API/UI/collector processes before starting Compose on the same ports.
Do not run both collectors against the same database; the advisory lock rejects
that configuration. Native and Compose databases are separate stores.

```sh
# Inspect status and recent messages:
docker compose ps
docker compose logs --tail=50 api collector ui
# Follow collection messages (Ctrl+C stops log following, not the services):
docker compose logs -f collector
# Stop the entire stack, preserving database and retry-spool volumes:
docker compose stop
# Start again, or rebuild after code changes:
docker compose up --build -d
```

Use `docker compose down` to remove containers while keeping volumes.
**Do not use `down -v` unless you intend to delete collected database history.**
The previous single-container `docker run nifty-dashboard` workflow has been
replaced by Compose; the UI-only Docker target needs the API and database services.

For company networks, mount an approved CA bundle into the **collector** service
and set `REQUESTS_CA_BUNDLE` there. Linux containers do not inherit the Mac Keychain.
A certificate with invalid constraints needs correction by IT. Certificate
verification remains enabled. The native macOS collector uses `truststore`.

## Startup troubleshooting

- **Port already in use:** stop the previous local launcher or Compose stack before
  starting another. Local UI/API use 8765/8000, native PostgreSQL uses 55432, and
  Compose PostgreSQL uses 55433. `pg_isready` checks database reachability.
- **Database unavailable:** run `python scripts/setup_local.py` for the native
  setup. For Compose, inspect `docker compose ps` and `docker compose logs postgres migrate`.
  Never delete the database directory or volume to resolve a connection error.
- **Collector offline:** start `python scripts/run_local.py`, or run
  `python -m services.collector` if managing processes independently.
- **Collection paused:** check IST time, weekend/holiday status, and the schedule
  reason in `/api/state`. A missing/stale NSE calendar deliberately pauses capture;
  keep the collector running so it can retry.
- **Another collector is already running:** stop the extra process; only one
  collector may write to a given application database.
- **NSE/TradingView request failure:** inspect collector logs and the UI's data
  availability details. The UI continues showing the last committed snapshot.
  Use an approved company CA bundle when HTTPS inspection is in place.
- **Missing `initdb` / `pg_ctl`:** install the PostgreSQL command-line tools and add
  their executable directory to PATH, or use the Compose setup.

## SQL storage and queries

| Table | Contents |
| --- | --- |
| `snapshots` | Collection time, NSE timestamp, expiry, spot, index RSI, raw NSE JSONB, warnings |
| `snapshot_rows` | Full flattened strike rows, including enriched RSI metadata |
| `option_quotes` | Per-snapshot, strike, expiry, CE/PE; numeric price, OI, volume, IV, RSI; remaining fields in JSONB |
| `collection_attempts` | Attempt time, completion, saved snapshot ID, error |
| `trading_calendars` | Verified yearly NSE F&O holidays and source |
| `collector_state` | Durable status and collector heartbeat |
| `refresh_requests` | UI/API refresh queue handled by the collector |

Connect using a SQL client at host `127.0.0.1`, port `55432`, database `nifty`,
user `nifty_api` and `API_PASSWORD` from your private `.env`. The API role can read
history and queue refreshes; it cannot change or delete snapshots. The collector
can insert/update but cannot delete stored history.

Example queries:

```sql
-- Latest snapshots, with capture times shown in IST.
SELECT id, collected_at AT TIME ZONE 'Asia/Kolkata' AS captured_ist,
       market_timestamp, expiry, spot, index_rsi
FROM snapshots ORDER BY collected_at DESC LIMIT 20;

-- Every quote captured today, including strikes outside the UI range.
SELECT s.collected_at AT TIME ZONE 'Asia/Kolkata' AS captured_ist,
       q.expiry, q.strike, q.side, q.last_price, q.open_interest, q.volume, q.rsi_15m
FROM option_quotes q JOIN snapshots s ON s.id = q.snapshot_id
WHERE (s.collected_at AT TIME ZONE 'Asia/Kolkata')::date =
      (now() AT TIME ZONE 'Asia/Kolkata')::date
ORDER BY s.collected_at, q.strike, q.side;

-- Snapshot count per session.
SELECT (collected_at AT TIME ZONE 'Asia/Kolkata')::date AS trading_day, count(*)
FROM snapshots GROUP BY trading_day ORDER BY trading_day DESC;
```

Back up the native database without exposing credentials in command history:

```sh
pg_dump -h 127.0.0.1 -p 55432 -U nifty_admin -d nifty -W -Fc -f nifty.backup
```

Enter `POSTGRES_PASSWORD` from `.env` at the prompt. Keep `.env` and database
backups private and outside source control.

## API

- `GET /health`: API + database connectivity.
- `GET /api/state`: latest stored snapshot (±600 UI range) and collector status.
- `GET /api/snapshots?date=YYYY-MM-DD&limit=100`: history metadata for an IST date.
  Optional `before=<ISO timestamp>` paginates older snapshots; maximum limit 500.
- `GET /api/snapshots/<uuid>`: all stored strike rows for one snapshot.
- `GET /api/snapshots/<uuid>/raw`: the complete original NSE JSON response.
- `GET /api/download?id=<uuid>`: CSV exported from SQL, with all stored strike rows.
- `POST /api/refresh`: queue a fetch only while the collector/session is available.

The UI polls the API once per second for status. This polling reads SQL; it does
not issue external provider requests. API/DB failures keep the last rendered
snapshot visible. An offline collector is detected through its heartbeat.

## Checks

```sh
python -m unittest discover -s tests -v
python scripts/test_database.py
```

The second command creates a **disposable test database**, verifies real SQL
round trips, full-strike retention, atomic rollback, idempotent retries, spool
recovery, service permissions, API reads/exports, and closed-session rejection,
then removes only that test database. Live collection history is untouched.

TradingView may not return RSI for individual NSE option contracts. These values
stay N/A; index RSI is never substituted. Custom strength/weakness banners follow
the configured volume comparisons and are computed from displayed strikes.
