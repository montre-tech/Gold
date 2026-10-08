#!/usr/bin/env python3
"""
Download forex intraday candles from Financial Modeling Prep (FMP) and convert
them into files MT4 can use for backtesting.

Outputs
  1. <SYMBOL>_<TF>.csv  -> import via MT4: Tools > History Center (F2) > Import
                           format: YYYY.MM.DD,HH:MM,Open,High,Low,Close,Volume
  2. <SYMBOL><PERIOD>.hst -> optional; copy into <MT4 data folder>\\history\\<server>\\

Usage
  pip install requests
  python fmp_to_mt4.py --apikey YOUR_KEY --symbol EURUSD --from 2025-01-01 --to 2026-10-01
  python fmp_to_mt4.py --apikey YOUR_KEY --symbol EURUSD --from 2025-01-01 --to 2026-10-01 --hst --spread 12

Time zones
  FMP timestamps are (to my knowledge) US/Eastern. MT4 brokers usually use a
  server time like GMT+2 / GMT+3 (DST) which follows New York 5pm close.
  Use --src-tz and --dst-tz to convert. Check one bar against your broker's
  chart to confirm the offset before trusting a long backtest.
"""
import argparse
import csv
import struct
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

BASE = "https://financialmodelingprep.com/stable/historical-chart/{tf}"
PERIOD_MINUTES = {"1min": 1, "5min": 5, "15min": 15, "30min": 30, "1hour": 60, "4hour": 240}


def fetch_range(apikey, symbol, tf, start, end, chunk_days, pause):
    """Fetch in small date windows (API caps rows per request), de-duplicated."""
    rows = {}
    cur = start
    while cur <= end:
        win_end = min(cur + timedelta(days=chunk_days - 1), end)
        params = {
            "symbol": symbol,
            "from": cur.strftime("%Y-%m-%d"),
            "to": win_end.strftime("%Y-%m-%d"),
            "apikey": apikey,
        }
        for attempt in range(4):
            try:
                r = requests.get(BASE.format(tf=tf), params=params, timeout=60)
                if r.status_code == 429:
                    time.sleep(5 * (attempt + 1))
                    continue
                r.raise_for_status()
                data = r.json()
                break
            except requests.RequestException as e:
                print(f"  retry {attempt + 1} ({e})", file=sys.stderr)
                time.sleep(2 * (attempt + 1))
        else:
            sys.exit(f"Failed window {params['from']} -> {params['to']}")

        if isinstance(data, dict):  # error payload
            sys.exit(f"API error: {data}")

        for d in data:
            rows[d["date"]] = d
        print(f"{params['from']} -> {params['to']}: {len(data)} bars (total {len(rows)})")
        cur = win_end + timedelta(days=1)
        time.sleep(pause)
    return rows


def convert(rows, src_tz, dst_tz):
    """Return sorted list of (datetime_in_broker_time, o, h, l, c, v)."""
    src, dst = ZoneInfo(src_tz), ZoneInfo(dst_tz)
    out = []
    for key, d in rows.items():
        t = datetime.strptime(key, "%Y-%m-%d %H:%M:%S").replace(tzinfo=src)
        t = t.astimezone(dst).replace(tzinfo=None)  # naive broker-time
        out.append((t, float(d["open"]), float(d["high"]), float(d["low"]),
                    float(d["close"]), float(d.get("volume") or 0)))
    out.sort(key=lambda x: x[0])
    # Drop bars with broken OHLC
    clean = [b for b in out if b[2] >= max(b[1], b[4]) and b[3] <= min(b[1], b[4])]
    if len(clean) != len(out):
        print(f"Dropped {len(out) - len(clean)} bars with inconsistent OHLC")
    return clean


def write_csv(bars, path, digits):
    fmt = f"{{:.{digits}f}}"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        for t, o, h, l, c, v in bars:
            w.writerow([t.strftime("%Y.%m.%d"), t.strftime("%H:%M"),
                        fmt.format(o), fmt.format(h), fmt.format(l), fmt.format(c),
                        int(v) if v else 1])  # MT4 dislikes 0 volume
    print(f"Wrote {path}")


def write_hst(bars, path, symbol, period_min, digits, spread):
    """MT4 history format v401 (build 600+)."""
    with open(path, "wb") as f:
        header = struct.pack(
            "<i64s12siiii13i",
            401,
            b"(C)opyright 2026, FMP converter",
            symbol.encode("ascii"),
            period_min,
            digits,
            0,   # timesign
            0,   # last sync
            *([0] * 13),
        )
        f.write(header)
        for t, o, h, l, c, v in bars:
            ts = int((t - datetime(1970, 1, 1)).total_seconds())  # broker time as "UTC" epoch
            f.write(struct.pack("<qddddqiq", ts, o, h, l, c, int(v) if v else 1, spread, 0))
    print(f"Wrote {path}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--apikey", required=True)
    p.add_argument("--symbol", default="EURUSD")
    p.add_argument("--interval", default="5min", choices=PERIOD_MINUTES.keys())
    p.add_argument("--from", dest="start", required=True, help="YYYY-MM-DD")
    p.add_argument("--to", dest="end", required=True, help="YYYY-MM-DD")
    p.add_argument("--src-tz", default="America/New_York", help="timezone of FMP timestamps")
    p.add_argument("--dst-tz", default="Europe/Athens",
                   help="broker server tz (Europe/Athens ~ GMT+2/+3 with NY-close alignment)")
    p.add_argument("--digits", type=int, default=None, help="price digits (default 5, or 3 for JPY)")
    p.add_argument("--chunk-days", type=int, default=3)
    p.add_argument("--pause", type=float, default=0.3, help="seconds between requests")
    p.add_argument("--hst", action="store_true", help="also write an MT4 .hst file")
    p.add_argument("--spread", type=int, default=10, help="fixed spread in points for .hst")
    a = p.parse_args()

    digits = a.digits if a.digits is not None else (3 if "JPY" in a.symbol.upper() else 5)
    start = datetime.strptime(a.start, "%Y-%m-%d")
    end = datetime.strptime(a.end, "%Y-%m-%d")

    rows = fetch_range(a.apikey, a.symbol, a.interval, start, end, a.chunk_days, a.pause)
    if not rows:
        sys.exit("No data returned (check symbol, dates and your FMP plan limits).")

    bars = convert(rows, a.src_tz, a.dst_tz)
    pm = PERIOD_MINUTES[a.interval]
    write_csv(bars, f"{a.symbol}_M{pm}.csv", digits)
    if a.hst:
        write_hst(bars, f"{a.symbol}{pm}.hst", a.symbol, pm, digits, a.spread)
    print(f"Done: {len(bars)} bars, {bars[0][0]} -> {bars[-1][0]} (broker time)")


if __name__ == "__main__":
    main()
