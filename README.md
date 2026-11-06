# NSE Option Chain

A NIFTY 50 option-chain dashboard that fetches a new snapshot every **5 seconds**.

- Green call data on the left and red put data on the right.
- Strike prices centered, with the NIFTY spot price between lower and higher strikes.
- All available strikes within **600 points above or below spot** for the selected expiry.
- Volume nearest the strike, followed by open interest and price; RSI at the outer edges.
- Highest call and put volume cards, with the corresponding strike prices.
- NIFTY index RSI and individual call/put RSI on **15-minute candles**, when available.
- Loading indicators, manual refresh, CSV download, and retention of the latest **three** snapshots.

The application requests data from NSE and RSI from TradingView. These services
may return delayed, unavailable, or unchanged data, especially outside market hours.

## Get the code

```sh
git clone git@github.com:ManthanDhole/NSE_OptionChain.git
cd NSE_OptionChain
```

The SSH clone URL requires a GitHub SSH key. Alternatively:

```sh
git clone https://github.com/ManthanDhole/NSE_OptionChain.git
cd NSE_OptionChain
```

## Run without Docker

Requires **Python 3.10 or newer** and an internet connection. Python 3.13 is used
by the Docker image.

### macOS / Linux

Create an environment, install dependencies, and start the dashboard:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python dashboard.py
```

### Windows (PowerShell)

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python dashboard.py
```

Open [http://localhost:8765](http://localhost:8765). Keep the terminal running.
Press **Ctrl+C** to stop the server and automatic refreshes.

For later launches, activate the same environment and run `python dashboard.py`.

### Local options

Use another port:

```sh
python dashboard.py --port 8766
```

List currently available expiries, then supply one in `DD-Mon-YYYY` format:

```sh
python nifty_option_chain.py --list-expiries
python dashboard.py --expiry DD-Mon-YYYY
```

Replace `DD-Mon-YYYY` with an actual expiry returned by the first command.
Without `--expiry`, the application selects the nearest available expiry on each fetch.

CSV snapshots are stored beside the Python scripts by default. To use a separate
folder on macOS / Linux:

```sh
NIFTY_DATA_DIR=./data python dashboard.py
```

On Windows PowerShell:

```powershell
$env:NIFTY_DATA_DIR = ".\data"
python dashboard.py
```

## Run with Docker

Requires **Docker Engine or Docker Desktop**, running on your machine.
Run the following commands from the repository directory.

### Build the image

```sh
docker build -t nifty-dashboard .
```

### Start the container

```sh
docker run -d --init --name nifty-dashboard -p 127.0.0.1:8765:8765 -v nifty-data:/data nifty-dashboard
```

Open [http://localhost:8765](http://localhost:8765).

- Port `8765` is published only on the local computer.
- The application runs as a non-root user inside the container.
- CSV snapshots are saved in the `nifty-data` Docker volume, preserving the latest
  three snapshots across container removal and recreation.
- The first fetch starts immediately; subsequent fetches run every 5 seconds.
- If a fetch exceeds 5 seconds, the next one starts after it finishes. Requests
  never overlap, and previous data remains visible while loading or after errors.

### Logs, stop, and restart

```sh
docker logs -f nifty-dashboard
docker stop nifty-dashboard
docker start nifty-dashboard
```

To recreate the container after a code change:

```sh
docker stop nifty-dashboard
docker rm nifty-dashboard
docker build -t nifty-dashboard .
docker run -d --init --name nifty-dashboard -p 127.0.0.1:8765:8765 -v nifty-data:/data nifty-dashboard
```

Removing the container with `docker rm` leaves the named data volume intact.

### Custom expiry or host port

Use an available expiry from the script's `--list-expiries` output. Keep
`--host 0.0.0.0` when overriding the image's default application arguments:

```sh
docker run --rm --init -p 127.0.0.1:8766:8765 -v nifty-data:/data nifty-dashboard --host 0.0.0.0 --expiry DD-Mon-YYYY
```

Open [http://localhost:8766](http://localhost:8766) for this example. Stop any
existing dashboard container before starting another one against the same data volume.

The container health check confirms that the web server responds. It does not
confirm that external NSE or TradingView requests are succeeding.

## Certificates and data availability

### Company proxy / Zscaler

On macOS, the script uses the system certificate store through `truststore`.
Linux containers do not inherit the Mac's trusted certificates. On networks that
inspect HTTPS, request an approved CA bundle from your IT team, then mount it
read-only and configure Requests to use it:

```sh
docker run --rm --init -p 127.0.0.1:8765:8765 -v nifty-data:/data -v /absolute/path/company-ca-bundle.pem:/certs/company-ca-bundle.pem:ro -e REQUESTS_CA_BUNDLE=/certs/company-ca-bundle.pem nifty-dashboard
```

Replace the host certificate path with your actual PEM bundle. For a local
macOS / Linux launch, the equivalent configuration is:

```sh
REQUESTS_CA_BUNDLE=/absolute/path/company-ca-bundle.pem python dashboard.py
```

If the error includes `Basic Constraints of CA cert not marked critical`, the
company certificate does not meet the container's TLS validation requirements.
Ask IT for a corrected certificate/bundle. Re-exporting the same certificate
will not fix its constraints. Certificate verification stays enabled.

### Missing RSI / failed updates

TradingView may not expose individual option contracts through its scanner.
Their RSI values remain **N/A**; NIFTY index RSI is never substituted for option
RSI. TradingView's latest RSI may include the currently forming 15-minute candle.
The CSV records RSI provider details and retrieval times where available.

The dashboard shows NSE's market timestamp separately from the last successful
fetch time. During an outage it retains the most recent snapshot and shows an
error. Open **Data availability details** to inspect provider messages.

## Run the script without the UI

With the local Python environment activated:

```sh
python nifty_option_chain.py
python nifty_option_chain.py --list-expiries
python nifty_option_chain.py --rsi-source openchart
python nifty_option_chain.py --rsi-source yfinance
python nifty_option_chain.py --rsi-source auto
```

The default provider is TradingView. `auto` tries Yahoo first and falls back to
OpenChart for missing per-option RSI values. Each successful script run saves a
CSV and removes older generated snapshots beyond the newest three. Unrelated
CSV files and custom-named outputs are left untouched.

## Run checks

```sh
python -m unittest discover -s tests -v
```

These checks cover refresh scheduling, loading states, failure recovery,
configured snapshot storage, CSV download, and HTTP routes. Provider requests
are simulated; passing checks does not establish live market-data availability.
