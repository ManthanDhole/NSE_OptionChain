"""Option-chain extension for marketcalls/openchart's NSEData client.

Reference: https://github.com/marketcalls/openchart/blob/master/openchart/core.py
OpenChart provides search/historical methods, but no option-chain method.
This extension uses its session with NSE's separate website option-chain API.
Call truststore.inject_into_ssl() before importing this module in your script.
"""

from datetime import datetime

from openchart import NSEData


class NSEOptionChain(NSEData):
    """Extend OpenChart with expiry discovery and full option-chain snapshots."""

    def connect(self):
        # Keep OpenChart's session and browser headers, adapted for the website API.
        self.session.headers.update({
            "Origin": "https://www.nseindia.com",
            "Referer": "https://www.nseindia.com/option-chain",
            "Accept-Encoding": "gzip, deflate",
        })
        response = self.session.get("https://www.nseindia.com/option-chain", timeout=20)
        response.raise_for_status()

    def _get_option_json(self, endpoint, params):
        response = self.session.get(
            f"https://www.nseindia.com/api/{endpoint}", params=params, timeout=20
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise ValueError("NSE returned a non-JSON response; access may be blocked.") from exc
        if not isinstance(payload, dict):
            raise ValueError("NSE returned an unexpected response format.")
        return payload

    def available_expiries(self, symbol="NIFTY"):
        """Return available expiries, nearest first, as DD-Mon-YYYY strings."""
        info = self._get_option_json("option-chain-contract-info", {"symbol": symbol})
        expiries = info.get("expiryDates")
        if not isinstance(expiries, list) or not expiries:
            raise ValueError("NSE returned no available expiry dates.")
        return sorted(expiries, key=lambda value: datetime.strptime(value, "%d-%b-%Y"))

    def option_chain(self, expiry, symbol="NIFTY"):
        """Fetch one index expiry's raw snapshot, including CE/PE, OI and IV."""
        return self._get_option_json("option-chain-v3", {
            "type": "Indices", "symbol": symbol, "expiry": expiry,
        })
