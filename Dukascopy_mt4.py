#!/usr/bin/env python3
"""
Convert dukascopy-node CSV output (timestamp in ms UTC, open, high, low, close, volume)
into MT4-ready files:
  <SYMBOL>_M<tf>.csv  -> MT4 History Center import (YYYY.MM.DD,HH:MM,O,H,L,C,V)
  <SYMBOL><tf>.hst    -> optional MT4 history file (v401)

No third-party packages needed (standard library only).
"""
import argparse
import csv
import glob
import struct
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def load(files, dst_tz):
    dst = ZoneInfo(dst_tz)
    rows = {}
    for path in files:
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                ts = int(float(r["timestamp"])) // 1000
                t = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(dst).replace(tzinfo=None)
                rows[t] = (float(r["open"]), float(r["high"]), float(r["low"]),
                           float(r["close"]), float(r.get("volume") or 0))
    bars = [(t, *v) for t, v in sorted(rows.items())]
    clean = [b for b in bars if b[2] >= max(b[1], b[4]) and b[3] <= min(b[1], b[4])]
    if len(clean) != len(bars):
        print(f"Dropped {len(bars) - len(clean)} bars with inconsistent OHLC")
    return clean


def vol(v, scale):
    return max(1, int(round(v * scale)))


def write_csv(bars, path, digits, scale):
    fmt = f"{{:.{digits}f}}"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        for t, o, h, l, c, v in bars:
            w.writerow([t.strftime("%Y.%m.%d"), t.strftime("%H:%M"),
                        fmt.format(o), fmt.format(h), fmt.format(l), fmt.format(c),
                        vol(v, scale)])
    print(f"Wrote {path}")


def write_hst(bars, path, symbol, period, digits, spread, scale):
    with open(path, "wb") as f:
        f.write(struct.pack("<i64s12siiii13i", 401, b"(C)opyright 2026, Dukascopy converter",
                            symbol.encode("ascii"), period, digits, 0, 0, *([0] * 13)))
        for t, o, h, l, c, v in bars:
            ts = int((t - datetime(1970, 1, 1)).total_seconds())  # broker time as epoch
            f.write(struct.pack("<qddddqiq", ts, o, h, l, c, vol(v, scale), spread, 0))
    print(f"Wrote {path}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input-glob", default="download/*.csv")
    p.add_argument("--symbol", default="XAUUSD")
    p.add_argument("--period", type=int, default=5, help="minutes (1, 5, 15, 30, 60...)")
    p.add_argument("--dst-tz", default="Europe/Athens", help="broker server timezone")
    p.add_argument("--digits", type=int, default=2, help="price digits (check your broker's XAUUSD)")
    p.add_argument("--spread", type=int, default=25, help="fixed spread in points for .hst")
    p.add_argument("--volume-scale", type=float, default=1000,
                   help="Dukascopy volume is fractional; multiplied then rounded to an integer")
    p.add_argument("--hst", action="store_true")
    a = p.parse_args()

    files = sorted(glob.glob(a.input_glob))
    if not files:
        sys.exit(f"No input files match {a.input_glob}")
    bars = load(files, a.dst_tz)
    if not bars:
        sys.exit("No bars found in input")
    write_csv(bars, f"{a.symbol}_M{a.period}.csv", a.digits, a.volume_scale)
    if a.hst:
        write_hst(bars, f"{a.symbol}{a.period}.hst", a.symbol, a.period, a.digits,
                  a.spread, a.volume_scale)
    print(f"Done: {len(bars)} bars, {bars[0][0]} -> {bars[-1][0]} (broker time)")


if __name__ == "__main__":
    main()
