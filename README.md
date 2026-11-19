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
  RSI metadata, collection attempts, and collector status information.
- **API** (`services/api.py`): reads committed PostgreSQL data. It never calls NSE or
  TradingView. It can queue a refresh request but cannot modify stored market data.
- **UI** (`services/ui.py`, `ui/index.html`): serves the interface and proxies API
  requests. It has no database credentials, provider libraries, or collection thread.

The legacy `nifty_option_chain.py` remains available as a standalone CSV tool.
It is **not** used by the scheduled database collector. Its three-CSV rotation
has no effect on PostgreSQL history.

## Continuous collection

- Collection runs whenever the collector is up: **all hours, every day**, including
  weekends and holidays. No 09:15–15:30 time gate or NSE holiday-calendar lookup is used.
- Requests start every **5 seconds** when possible. Slow requests never overlap;
  the next starts when the previous request finishes.
- Manual refresh is available whenever the collector is online, regardless of time
  or day. It queues work through SQL instead of calling a provider from the UI/API.
- NSE may return the last available snapshot outside market hours. Each capture
  retains its collection timestamp separately from NSE's own market timestamp.
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

### Start and stop with one script

After installing the prerequisites and `.venv` dependencies below, run from the
repository directory (no environment activation required):

```sh
python3 nifty-options.py start
python3 nifty-options.py status
python3 nifty-options.py stop
python3 nifty-options.py restart
```

`start` prepares/starts PostgreSQL and runs the UI, API, and collector in the
background. Open http://localhost:8765. You may close the terminal; the computer
must remain awake. `stop` waits for collection to finish before stopping the local
database and preserves saved data. Repeated starts/stops are safe. Logs append to
`.local/app.log`; PostgreSQL logs are in `.local/postgres.log`. `status` exits
with code 1 when the managed app is stopped.

Use this script consistently for background processes. It refuses to stop
unrelated processes using app ports. Stop an older foreground launcher with
Ctrl+C before using `nifty-options.py start`.

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
running. Collection starts immediately and continues at any time of day.
Your computer must remain awake and the collector running to capture snapshots.
The native processes are not installed as automatic operating-system startup services.

### Verify startup

In a second terminal:

```sh
pg_isready -h 127.0.0.1 -p 55432
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8765/api/state
```

`/health` should return `{"status": "ok"}`. `/api/state` reports the collector's
collector availability, loading/error state, and latest database snapshot. An empty
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
python -m services.collector # continuous data capture
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

## Run in Kubernetes with Helm

The Helm chart deploys the UI, API, collector, migration Job and persistent
PostgreSQL independently. The UI is exposed by a **ClusterIP Service** and reached
with `kubectl port-forward`; API/database traffic stays inside the cluster.

Build the images and make them available to the target cluster (load them into
kind/minikube or push to a registry):

```sh
docker build --target backend -t nifty-backend:local .
docker build --target ui -t nifty-ui:local .
# For kind, replace nifty with your cluster name:
kind load docker-image nifty-backend:local nifty-ui:local --name nifty
```

Install and open the UI:

```sh
helm upgrade --install nifty ./helm/nse-option-chain \
  -n nifty --create-namespace --wait --wait-for-jobs --timeout 15m
kubectl -n nifty port-forward service/nifty-nse-option-chain-ui 8765:80
```

Visit [http://localhost:8765](http://localhost:8765) and leave port-forward running.
No separate API port-forward is needed: the UI proxies requests through the
internal API Service. Stop any local app already using port 8765 first.

```sh
helm test nifty -n nifty --logs
```

See [the Helm setup guide](helm/nse-option-chain/README.md) for cluster prerequisites,
registry images, configurable values, persistent storage, Secret management,
external PostgreSQL, company CA bundles, upgrades and troubleshooting. Kubernetes
1.28+ and a default/configured StorageClass are required. This installation uses
its own database; it does not automatically import native/Compose history.

## Startup troubleshooting

- **Port already in use:** stop the previous local launcher or Compose stack before
  starting another. Local UI/API use 8765/8000, native PostgreSQL uses 55432, and
  Compose PostgreSQL uses 55433. `pg_isready` checks database reachability.
- **Database unavailable:** run `python scripts/setup_local.py` for the native
  setup. For Compose, inspect `docker compose ps` and `docker compose logs postgres migrate`.
  Never delete the database directory or volume to resolve a connection error.
- **Collector offline:** start `python scripts/run_local.py`, or run
  `python -m services.collector` if managing processes independently.
- **Collector stopped/unavailable:** start the collector and check its logs and
  database connectivity. There are no time-of-day or holiday restrictions.
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
| `trading_calendars` | Legacy holiday records retained for compatibility; no longer used |
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
- `POST /api/refresh`: queue a fetch whenever the collector is online.

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
recovery, service permissions, API reads/exports, and offline-collector rejection and continuous capture,
then removes only that test database. Live collection history is untouched.

TradingView may not return RSI for individual NSE option contracts. These values
stay N/A; index RSI is never substituted. Custom strength/weakness banners follow
the configured volume comparisons and are computed from displayed strikes.

### Daily 9:20 AM reference

The collector already persists every successful option-chain fetch and all strikes
in PostgreSQL. `/api/baseline` selects today's earliest saved snapshot captured
between 09:20:00 and 09:20:59 IST, requiring the NSE timestamp to also be in that
same minute/date. Later captures and stale NSE responses are never substituted.
The stored history survives restarts; no CSV or extra collection process is needed.

Turn **9:20 AM on** to show reference call/put prices, OI, and volume in yellow
under the current values, matched by expiry and strike. Missing contracts show a
dash. The reference toggle is off by default and independent of volume/OI ranking
highlights. The UI checks for the reference every 15 seconds while enabled. If the
app was not collecting successfully during that minute, it reports unavailable.
Continuous collection and the five-second refresh remain unchanged. Your computer
and collector must be running at 9:20 AM IST to capture the reference.

### Previous session closing summary

The bottom of the dashboard shows the latest available prior session's closing
snapshot, dated by the NSE timestamp rather than the collection date. This handles
repeated weekend/off-hours quotes. Only stored NSE quotes from 15:30–15:59 IST
qualify; if none exist, the panel reports unavailable instead of using intraday
data. The exact available session date, timestamp, and expiry are displayed.

`GET /api/previous-close` returns the two highest distinct positive volume and OI
levels for calls and puts, grouping tied strikes. It also returns side-specific
and combined totals across every stored strike for that snapshot's expiry,
including strikes outside the live table's ±600 range. These are snapshot totals,
not sums across time or across all expiries. Missing/negative values are excluded
and coverage counts are shown. The panel refreshes once a minute. Only previously
captured data can be shown; this does not backfill historical NSE data.

### Highest-volume strike movement

CALLS and PUTS signal cells also show the latest change of the highest-volume
strike(s), with the old/new strike and capture time in IST. Existing STRONG/WTT/WTB
signals remain visible. For movement labels, top means a higher strike and bottom
means a lower strike, independently of the unchanged table order/alignment. A move
to a higher strike means **SHIFTED FROM BOTTOM TO TOP**; a move to a lower strike
means **SHIFTED FROM TOP TO BOTTOM**. Overlapping tied leader sets are labelled **VOLUME LEADER TIE CHANGED**.

History is reconstructed from persisted snapshots for the displayed snapshot's
IST capture day and expiry, using the same current ±600 strike window throughout
the comparison. Calls and puts are tracked independently. The latest shift remains
visible until another leader change, survives page reloads/app restarts, and resets
for a new capture day/expiry. No movement is shown without two different observed
leader sets. This tracks which strike has the maximum volume (100% in the existing
ranking), not a change in the second-highest-volume ratio.

When the second-highest distinct volume falls strictly below 75% of the maximum,
the side is STRONG. If its preceding weak state was WTT or WTB, the movement line
shows **WTT → STRONG** or **WTB → STRONG**, the old/new leader strikes and first
observed change time in IST. This replaces the direction-only label while the
strong episode continues. The recorded time does not change with each refresh;
history is reconstructed from SQL and survives reloads. Exactly 75% stays neutral;
a WTT/WTB state can cross this boundary before becoming STRONG. Missing/mixed data
breaks the weak-to-strong comparison. Call/put transitions are independent and
reset with the capture day or expiry. Times reflect successful captures, not an
exact tick-by-tick exchange event time.

Reverse changes are also recorded: **STRONG → WTT** or **STRONG → WTB** when the
second-highest volume rises above 75% and its strike direction establishes WTT or
WTB. Both directions show the first observed change time and old/new leader strikes.
The event stays visible while that status continues; exactly 75% remains neutral.

The live option-chain table ends with a **TOTAL** row summing call/put volume and
OI across **all collected strikes for the snapshot’s expiry**, including strikes
outside the displayed ±600 range. Totals refresh with each snapshot; unavailable
or negative values are excluded, and entirely unavailable totals show a dash.
Price and RSI columns remain blank (dash). The TOTAL label shows the full collected
strike count; totals are computed in PostgreSQL independently of the UI range.

Second-highest volume and OI cells use a very faint green/red tint when their value
is strictly below 75% of that side's maximum for the same metric. At or above 75%,
the existing second-highest shade remains. Percentage labels stay visible; the
Highlights toggle still disables all ranking colours and percentages.

The Nifty current-price row shows a status and second-highest/max percentage under
each of the call/put Volume and OI columns. Metrics are ranked independently among
the displayed strikes. WTT means the second-highest strike is above the highest;
WTB means below. Below 75% is STRONG; exactly 75% is NEUTRAL. Overlapping tied
strikes show MIXED; insufficient levels show N/A. The Highlights switch hides
these percentages along with the other ranking percentages. Table order and
alignment remain unchanged, with the current price centered in the strike column.

The top signal movement line displays the most recent strike-shift or matching
status-transition event. If a WTT↔WTB flip and a volume-leader move happen in the
same capture, the strike movement direction takes priority. Simultaneous changes
to/from STRONG retain their status-transition label. Older status events cannot
hide newer strike movements; displayed strikes and times always belong to the
selected event. Higher-to-lower means top-to-bottom, independent of table order.

### Overall market sentiment

The purple center cell above STRIKE updates from the same current CALL/PUT volume
statuses as the top-row banners (displayed strike range). Rules are symmetric:

| CALL | PUT | Sentiment |
| --- | --- | --- |
| WTT | WTT | Nifty is in Bull Run |
| WTB | WTB | Nifty is in Blood Bath |
| WTT | STRONG (or reversed) | Nifty is in Slightly Bullish |
| STRONG | WTB (or reversed) | Nifty is in Slightly Bearish |
| STRONG | STRONG | Nifty is Neutral |
| WTT | WTB (or reversed) | Nifty is Both Side Risky |

Exactly-75% neutral statuses and mixed directions show
NO MATCHING RULE. Insufficient data shows WAITING FOR DATA. This is the configured
rule result, using volume rather than OI or the historical transition label.

### Near-spot price highlights

On Thursdays (IST), prices **greater than 93 and at most 105** receive a cyan
highlight; on Tuesdays (IST), the range is **greater than 23 and at most 34**.
This applies independently to CALL and PUT price cells at the nearest collected
strike ending in 50 at/below spot and the nearest at/above spot (for example,
25,450 and 25,550 around 25,510). At an exact eligible spot strike, the same strike
qualifies once. Other strikes and weekdays are unaffected. The Highlights toggle
also disables these price highlights. Hover over a matching price for its rule.
