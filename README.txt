KITE CONNECT - UNIVERSAL HISTORICAL DATA DOWNLOADER
==================================================

Purpose
-------
Select an exchange, instrument/contract, interval and duration, then download historical candles to CSV.

Supported documented candle intervals:
  minute, 3minute, 5minute, 10minute, 15minute, 30minute, 60minute, day

Supported exchange filters in the instrument master:
  NSE, BSE, NFO, BFO, CDS, MCX

Instrument types:
  ALL, EQ, FUT, CE, PE

Important F&O caveat
--------------------
The instrument selector is populated from the current Kite instrument master. It can select contracts
that appear in that current master. It is NOT an archive of every expired futures/options contract.
To fetch an expired contract later, you need its historical instrument token from a saved instrument
master captured while that contract was listed, and the broker must still serve that contract's history.
This app does not invent or reconstruct expired contract tokens.

Authentication
--------------
1. Install Python 3.10 or newer.
2. Extract this ZIP.
3. Double-click RUN_ME.bat on Windows.
4. Enter API key and API secret.
5. Click Login / Generate access token.
6. Complete Zerodha login and paste the fresh request_token value from the redirect URL.
7. Click Load / Refresh instruments.
8. Choose Exchange and Type, search for the instrument, and select the exact row.
9. Choose interval and duration.
10. Click DOWNLOAD HISTORICAL DATA.

The request token is short-lived and single-use. Paste it promptly. The access token expires according
to Zerodha's session policy; log in again when it expires.

Duration
--------
Presets: 1 month, 3 months, 6 months, 1 year, 2 years, 3 years, 5 years.
Custom dates: enter both dates in YYYY-MM-DD.
Preset periods are calculated automatically using calendar months/years from the computer's local date.
Set the computer timezone correctly for the exchange/data period.

Batching and rate limits
------------------------
The downloader splits long ranges into conservative batches and waits between requests. The batch
windows are operational safety settings based on commonly reported historical API limits, not a
guarantee that every interval/contract/date range will be accepted. Zerodha may change API behaviour.
If the API rejects a batch, the app reports failure rather than silently declaring success.

Data checks
-----------
The app clears instrument selection whenever exchange/type/search filters change, removes duplicate timestamps, checks basic numeric OHLCV consistency, and writes a JSON manifest with instrument identity, request batches, candle counts, and empty-batch warnings. It cannot guarantee
that every theoretically expected candle is present: holidays, no-trade periods, inactive contracts,
special sessions, exchange suspensions and broker data availability can produce gaps.

Output
------
CSV files are saved in ./output. Filename includes exchange, symbol, instrument type, interval and dates.
F&O data includes OI if the API returns it and the checkbox is selected.

Security
--------
No credentials are included in this ZIP. config.json and .kite_session.json are generated locally.
They contain sensitive credentials/tokens. Do not share them or upload them to source control.
Use your own API key and secret. Never paste secrets or tokens into chats.


Version 2 improvements
----------------------
- Stale instrument selection is cleared whenever exchange, instrument type, or search text changes.
- Preset durations use calendar months and calendar years, not rough 30/365-day approximations.
- Download progress indicator added.
- Each CSV now has a companion .manifest.json recording contract metadata, batch ranges, returned candle counts, duplicate count, and validation limitations.
- Empty API responses are explicitly flagged in the final dialog and manifest for review.
