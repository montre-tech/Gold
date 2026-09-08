#!/usr/bin/env python3
"""
Daily Gold COT + Macro Analysis Report
Uses CFTC Socrata API + FRED API + (optional) QuantGist + Gemini
"""

import requests
import json
import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

# ============================================================
# CONFIGURATION - Read from environment (GitHub Secrets)
# ============================================================

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
QUANTGIST_API_KEY = os.environ.get("QUANTGIST_API_KEY", "")
EMAIL_SENDER = os.environ.get("EMAIL_SENDER", "")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")
EMAIL_RECEIVER = os.environ.get("EMAIL_RECEIVER", "")

# ============================================================
# 1. FETCH COT DATA (CFTC Socrata API - No key required)
# ============================================================

def fetch_cot_gold():
    """
    Fetch the latest Gold COT data from CFTC's official Socrata API.
    No API key required.
    """
    url = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
    params = {
        "$where": "market_and_exchange_names='GOLD - COMMODITY EXCHANGE INC.'",
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": "5"
    }
    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        print(f"  ✓ Retrieved {len(data)} records")
        return data
    except Exception as e:
        print(f"Error fetching COT data: {e}")
        return None

def parse_cot_data(raw_data):
    """Extract key metrics. Handles missing fields gracefully."""
    if not raw_data or len(raw_data) == 0:
        return None
    latest = raw_data[0]
    def safe_float(val):
        try:
            return float(val) if val not in (None, "", ".") else 0.0
        except (ValueError, TypeError):
            return 0.0

    result = {
        "report_date": latest.get("report_date_as_yyyy_mm_dd", "N/A"),
        "open_interest": safe_float(latest.get("open_interest_all")),
        "non_comm_long": safe_float(latest.get("noncomm_positions_long_all")),
        "non_comm_short": safe_float(latest.get("noncomm_positions_short_all")),
        "comm_long": safe_float(latest.get("comm_positions_long_all")),
        "comm_short": safe_float(latest.get("comm_positions_short_all")),
        "m_money_long": safe_float(latest.get("m_money_positions_long_all")),
        "m_money_short": safe_float(latest.get("m_money_positions_short_all")),
    }
    result["non_comm_net"] = result["non_comm_long"] - result["non_comm_short"]
    result["m_money_net"] = result["m_money_long"] - result["m_money_short"]

    if len(raw_data) >= 2:
        prev = raw_data[1]
        prev_net = safe_float(prev.get("noncomm_positions_long_all", 0)) - safe_float(prev.get("noncomm_positions_short_all", 0))
        result["net_change"] = result["non_comm_net"] - prev_net
    else:
        result["net_change"] = 0
    return result

# ============================================================
# 2. FETCH TREASURY YIELDS (FRED API - Requires API key via secret)
# ============================================================

def fetch_treasury_yields():
    """
    Fetch 10-year and 2-year Treasury yields from FRED.
    Uses the FRED_API_KEY from environment (GitHub Secret).
    """
    if not FRED_API_KEY:
        print("  ⚠️ FRED_API_KEY not set. Skipping Treasury yields.")
        return {"dgs10": None, "dgs2": None, "spread": None}

    base = "https://api.stlouisfed.org/fred/series/observations"
    yields = {}

    # Map of series IDs we want
    series_map = {
        "dgs10": "DGS10",
        "dgs2": "DGS2"
    }

    for key, series_id in series_map.items():
        params = {
            "series_id": series_id,
            "api_key": FRED_API_KEY,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 1  # Get the most recent observation
        }

        try:
            r = requests.get(base, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()

            if data.get("observations") and len(data["observations"]) > 0:
                val = data["observations"][0].get("value")
                if val and val != ".":
                    yields[key] = float(val)
                    print(f"  ✓ {series_id}: {yields[key]}%")
                else:
                    print(f"  ⚠️ No valid value for {series_id}")
                    yields[key] = None
            else:
                print(f"  ⚠️ No observations for {series_id}")
                yields[key] = None

        except Exception as e:
            print(f"  ⚠️ Error fetching {series_id}: {e}")
            yields[key] = None

    if yields.get("dgs10") and yields.get("dgs2"):
        yields["spread"] = yields["dgs10"] - yields["dgs2"]

    return yields

# ============================================================
# 3. FETCH ECONOMIC CALENDAR (QuantGist API - Optional)
# ============================================================

def fetch_economic_calendar():
    if not QUANTGIST_API_KEY:
        print("  ⚠️ QuantGist API key not set. Skipping calendar.")
        return None
    url = "https://api.quantgist.com/v1/macro/calendar"
    headers = {"X-API-Key": QUANTGIST_API_KEY}
    params = {"events": "CPI,NFP,FOMC,PPI", "days": 14}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  ⚠️ Error fetching calendar: {e}")
        return None

# ============================================================
# 4. GENERATE ANALYSIS (Gemini API - Optional)
# ============================================================

def generate_analysis(cot_data, yields, calendar):
    """Generate analysis using Gemini, with fallback."""
    if not GEMINI_API_KEY:
        print("  ⚠️ Gemini API key not set. Using fallback analysis.")
        return fallback_analysis(cot_data, yields)

    context = f"""
GOLD COT DATA (as of {cot_data['report_date']}):
- Non-Commercial Net Longs: {cot_data['non_comm_net']:,.0f} contracts
- Change from prior week: {cot_data['net_change']:+,.0f}
- Managed Money Net Longs: {cot_data['m_money_net']:,.0f}
- Open Interest: {cot_data['open_interest']:,.0f}

TREASURY YIELDS:
- 10-Year: {yields.get('dgs10', 'N/A') if yields else 'N/A'}%
- 2-Year: {yields.get('dgs2', 'N/A') if yields else 'N/A'}%
- 10-2 Spread: {yields.get('spread', 'N/A') if yields else 'N/A'}%

UPCOMING EVENTS: {json.dumps(calendar, indent=2) if calendar else 'No calendar data'}

Provide a concise professional analysis:
1. Summary of current gold positioning.
2. Directional bias for the next 1-2 weeks.
3. Key support/resistance levels.
4. How upcoming US data might impact gold.
5. Final verdict (2-3 sentences).
"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{
            "parts": [{
                "text": f"You are a senior macro strategist. Analyze this data and provide a market commentary.\n\n{context}"
            }]
        }]
    }
    try:
        r = requests.post(url, json=payload, timeout=60)
        r.raise_for_status()
        data = r.json()
        if data.get("candidates"):
            return data["candidates"][0]["content"]["parts"][0].get("text", "No analysis.")
    except Exception as e:
        print(f"  ⚠️ Gemini error: {e}")
    return fallback_analysis(cot_data, yields)

def fallback_analysis(cot_data, yields):
    net = cot_data['non_comm_net']
    bias = "Bullish" if net > 150000 else "Neutral" if net > 50000 else "Bearish"
    yield_str = f"{yields.get('dgs10', 'N/A')}%" if yields and yields.get('dgs10') else "N/A"
    return f"""
GOLD POSITIONING (Fallback Analysis)

Net Non-Commercial Longs: {net:,.0f}
Overall Bias: {bias}
10-Year Treasury Yield: {yield_str}

The current positioning suggests {bias.lower()} sentiment.
Monitor upcoming economic data for directional cues.
"""

# ============================================================
# 5. SEND EMAIL
# ============================================================

def send_email(subject, body):
    if not EMAIL_PASSWORD:
        print("Email password not set. Printing report:\n")
        print(body)
        return
    msg = MIMEMultipart()
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"  ✓ Email sent to {EMAIL_RECEIVER}")
    except Exception as e:
        print(f"  ⚠️ Email error: {e}")

# ============================================================
# 6. MAIN
# ============================================================

def main():
    print("="*60)
    print(f"GOLD COT REPORT - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("="*60)

    print("\n[1/4] Fetching COT data...")
    raw = fetch_cot_gold()
    if not raw:
        print("ERROR: No COT data.")
        return
    cot = parse_cot_data(raw)
    if not cot:
        print("ERROR: Parsing failed.")
        return
    print(f"  ✓ Report date: {cot['report_date']}")
    print(f"  ✓ Non-Comm Net: {cot['non_comm_net']:,.0f}")

    print("\n[2/4] Fetching Treasury yields...")
    yields = fetch_treasury_yields()
    if yields:
        print(f"  ✓ 10-Year: {yields.get('dgs10', 'N/A')}%")
        print(f"  ✓ 2-Year: {yields.get('dgs2', 'N/A')}%")

    print("\n[3/4] Fetching economic calendar...")
    calendar = fetch_economic_calendar()
    if calendar:
        print(f"  ✓ Calendar data received")

    print("\n[4/4] Generating analysis...")
    analysis = generate_analysis(cot, yields, calendar)

    report = f"""
GOLD COT + MACRO REPORT - {datetime.now().strftime('%B %d, %Y')}
======================================================================

{analysis}

======================================================================
DATA APPENDIX
Report Date: {cot['report_date']}
Open Interest:   {cot['open_interest']:,.0f}
Non-Comm Long:   {cot['non_comm_long']:,.0f}
Non-Comm Short:  {cot['non_comm_short']:,.0f}
Non-Comm Net:    {cot['non_comm_net']:,.0f}
Net Change:      {cot['net_change']:+,.0f}
Managed Money Net: {cot['m_money_net']:,.0f}

Treasury Yields:
10-Year: {yields.get('dgs10', 'N/A') if yields else 'N/A'}%
2-Year:  {yields.get('dgs2', 'N/A') if yields else 'N/A'}%
10-2 Spread: {yields.get('spread', 'N/A') if yields else 'N/A'}%
======================================================================
"""
    subject = f"Gold COT Report - {datetime.now().strftime('%Y-%m-%d')}"
    send_email(subject, report)
    print("\n✓ Done.")

if __name__ == "__main__":
    main()
