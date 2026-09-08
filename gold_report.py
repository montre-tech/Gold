#!/usr/bin/env python3
"""
Daily Gold COT + Macro Analysis Report
Runs automatically via GitHub Actions
"""

import requests
import json
import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from typing import Dict, List, Optional

# ============================================================
# CONFIGURATION - Set these via GitHub Secrets or hardcode
# ============================================================

FRED_API_KEY = os.environ.get("FRED_API_KEY", "YOUR_FRED_KEY_HERE")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "YOUR_GEMINI_KEY_HERE")
QUANTGIST_API_KEY = os.environ.get("QUANTGIST_API_KEY", "YOUR_QUANTGIST_KEY_HERE")

EMAIL_SENDER = os.environ.get("EMAIL_SENDER", "your_email@gmail.com")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "your_app_password")
EMAIL_RECEIVER = os.environ.get("EMAIL_RECEIVER", "recipient@email.com")

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
    """Extract key metrics from raw COT data."""
    if not raw_data or len(raw_data) == 0:
        return None
    
    latest = raw_data[0]
    
    # Helper to safely convert string to float
    def to_float(val):
        try:
            return float(val) if val else 0.0
        except (ValueError, TypeError):
            return 0.0
    
    result = {
        "report_date": latest.get("report_date_as_yyyy_mm_dd", "N/A"),
        "open_interest": to_float(latest.get("open_interest_all")),
        "non_comm_long": to_float(latest.get("noncomm_positions_long_all")),
        "non_comm_short": to_float(latest.get("noncomm_positions_short_all")),
        "comm_long": to_float(latest.get("comm_positions_long_all")),
        "comm_short": to_float(latest.get("comm_positions_short_all")),
        "m_money_long": to_float(latest.get("m_money_positions_long_all")),
        "m_money_short": to_float(latest.get("m_money_positions_short_all")),
    }
    
    result["non_comm_net"] = result["non_comm_long"] - result["non_comm_short"]
    result["m_money_net"] = result["m_money_long"] - result["m_money_short"]
    
    # Calculate change from previous week if available
    if len(raw_data) >= 2:
        prev = raw_data[1]
        prev_net = to_float(prev.get("noncomm_positions_long_all", 0)) - to_float(prev.get("noncomm_positions_short_all", 0))
        result["net_change"] = result["non_comm_net"] - prev_net
    else:
        result["net_change"] = 0
    
    return result

# ============================================================
# 2. FETCH TREASURY YIELDS (FRED API - Free key required)
# ============================================================

def fetch_treasury_yields():
    """
    Fetch 10-year and 2-year Treasury yields from FRED.
    Series IDs: DGS10 (10-year), DGS2 (2-year)
    """
    if not FRED_API_KEY or FRED_API_KEY == "YOUR_FRED_KEY_HERE":
        print("FRED API key not set. Skipping yield data.")
        return None
    
    base_url = "https://api.stlouisfed.org/fred/series/observations"
    
    yields = {}
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
            "limit": 1
        }
        try:
            response = requests.get(base_url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            if data.get("observations") and len(data["observations"]) > 0:
                val = data["observations"][0].get("value")
                yields[key] = float(val) if val and val != "." else None
        except Exception as e:
            print(f"Error fetching {series_id}: {e}")
            yields[key] = None
    
    if yields.get("dgs10") and yields.get("dgs2"):
        yields["spread"] = yields["dgs10"] - yields["dgs2"]
    else:
        yields["spread"] = None
    
    return yields

# ============================================================
# 3. FETCH ECONOMIC CALENDAR (QuantGist API - Free key)
# ============================================================

def fetch_economic_calendar():
    """
    Fetch upcoming high-impact USD economic events.
    Uses QuantGist free API.
    """
    if not QUANTGIST_API_KEY or QUANTGIST_API_KEY == "YOUR_QUANTGIST_KEY_HERE":
        print("QuantGist API key not set. Skipping calendar.")
        return None
    
    url = "https://api.quantgist.com/v1/macro/calendar"
    headers = {"X-API-Key": QUANTGIST_API_KEY}
    params = {
        "events": "CPI,NFP,FOMC,PPI",
        "days": 14
    }
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching economic calendar: {e}")
        return None

# ============================================================
# 4. GENERATE AI ANALYSIS (Gemini API - Free tier)
# ============================================================

def generate_ai_analysis(cot_data, yields, calendar):
    """
    Send all data to Gemini API for analysis.
    """
    if not GEMINI_API_KEY or GEMINI_API_KEY == "YOUR_GEMINI_KEY_HERE":
        print("Gemini API key not set. Using fallback analysis.")
        return generate_fallback_analysis(cot_data, yields)
    
    # Build context for the AI
    context = f"""
GOLD COT DATA (as of {cot_data.get('report_date', 'N/A')}):
- Non-Commercial Net Longs: {cot_data.get('non_comm_net', 0):,.0f} contracts
- Change from prior week: {cot_data.get('net_change', 0):+,.0f} contracts
- Managed Money Net Longs: {cot_data.get('m_money_net', 0):,.0f}
- Open Interest: {cot_data.get('open_interest', 0):,.0f}
- Non-Commercial Longs: {cot_data.get('non_comm_long', 0):,.0f}
- Non-Commercial Shorts: {cot_data.get('non_comm_short', 0):,.0f}

TREASURY YIELDS:
- 10-Year Treasury: {yields.get('dgs10', 'N/A')}%
- 2-Year Treasury: {yields.get('dgs2', 'N/A')}%
- 10-2 Spread: {yields.get('spread', 'N/A')}%

UPCOMING ECONOMIC EVENTS:
{json.dumps(calendar, indent=2) if calendar else 'No calendar data available'}

Based on this data, provide:
1. A brief summary of current gold positioning
2. Directional bias for gold in the next 1-2 weeks
3. Key support and resistance levels
4. How upcoming economic data (CPI, FOMC) might impact gold
5. A final verdict (2-3 sentences)

Keep it concise and actionable. Use a professional tone.
"""
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    
    payload = {
        "contents": [{
            "parts": [{
                "text": f"You are a senior macro strategist specializing in gold and commodity markets. Analyze the following data and provide a professional market commentary.\n\n{context}"
            }]
        }]
    }
    
    try:
        response = requests.post(url, json=payload, timeout=60)
        response.raise_for_status()
        data = response.json()
        
        # Extract the text from Gemini's response
        if data.get("candidates") and len(data["candidates"]) > 0:
            candidate = data["candidates"][0]
            if candidate.get("content") and candidate["content"].get("parts"):
                return candidate["content"]["parts"][0].get("text", "Analysis not available.")
        
        return "Error: Could not parse Gemini response."
    
    except Exception as e:
        print(f"Error calling Gemini API: {e}")
        return generate_fallback_analysis(cot_data, yields)

def generate_fallback_analysis(cot_data, yields):
    """Fallback analysis if AI API fails."""
    net = cot_data.get('non_comm_net', 0)
    change = cot_data.get('net_change', 0)
    
    bias = "Bullish" if net > 150000 else "Neutral" if net > 50000 else "Bearish"
    direction = "increasing" if change > 0 else "decreasing"
    
    return f"""
GOLD POSITIONING SUMMARY (Fallback Analysis)

Net Non-Commercial Longs: {net:,.0f} contracts ({direction} by {abs(change):,.0f} from prior week)
Overall Bias: {bias}

The current positioning suggests {bias.lower()} sentiment in the gold market.
10-Year Treasury Yield: {yields.get('dgs10', 'N/A')}%

Recommendation: Monitor upcoming economic data for directional cues.
"""

# ============================================================
# 5. SEND EMAIL REPORT
# ============================================================

def send_email(subject: str, body: str):
    """Send the report via Gmail SMTP."""
    if not EMAIL_PASSWORD or EMAIL_PASSWORD == "your_app_password":
        print("Email password not set. Printing report instead.")
        print("\n" + "="*60)
        print(subject)
        print("="*60)
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
        print(f"Email sent to {EMAIL_RECEIVER}")
    except Exception as e:
        print(f"Error sending email: {e}")

# ============================================================
# 6. MAIN FUNCTION
# ============================================================

def main():
    print("="*60)
    print("GOLD COT DAILY REPORT - " + datetime.now().strftime("%Y-%m-%d %H:%M"))
    print("="*60)
    
    # 1. Fetch COT data
    print("\n[1/4] Fetching COT data...")
    raw_cot = fetch_cot_gold()
    if not raw_cot:
        print("ERROR: Failed to fetch COT data.")
        return
    cot_data = parse_cot_data(raw_cot)
    if not cot_data:
        print("ERROR: Failed to parse COT data.")
        return
    print(f"  ✓ Report date: {cot_data['report_date']}")
    print(f"  ✓ Non-Comm Net: {cot_data['non_comm_net']:,.0f}")
    
    # 2. Fetch Treasury yields
    print("\n[2/4] Fetching Treasury yields...")
    yields = fetch_treasury_yields()
    if yields:
        print(f"  ✓ 10-Year: {yields.get('dgs10', 'N/A')}%")
        print(f"  ✓ 2-Year: {yields.get('dgs2', 'N/A')}%")
    
    # 3. Fetch economic calendar
    print("\n[3/4] Fetching economic calendar...")
    calendar = fetch_economic_calendar()
    if calendar:
        print(f"  ✓ Calendar data received")
    
    # 4. Generate analysis
    print("\n[4/4] Generating AI analysis...")
    analysis = generate_ai_analysis(cot_data, yields, calendar)
    
    # Build the full report
    report_date = datetime.now().strftime("%B %d, %Y")
    subject = f"Gold COT Report - {report_date}"
    
    full_report = f"""
GOLD COT + MACRO DAILY REPORT
Generated: {report_date}
================================================================================

{analysis}

================================================================================
DATA APPENDIX

COT Data (as of {cot_data['report_date']}):
  Open Interest:           {cot_data['open_interest']:,.0f}
  Non-Commercial Longs:    {cot_data['non_comm_long']:,.0f}
  Non-Commercial Shorts:   {cot_data['non_comm_short']:,.0f}
  Non-Commercial Net:      {cot_data['non_comm_net']:,.0f}
  Change from prior week:  {cot_data['net_change']:+,.0f}
  Managed Money Net:       {cot_data['m_money_net']:,.0f}

Treasury Yields:
  10-Year:                 {yields.get('dgs10', 'N/A')}%
  2-Year:                  {yields.get('dgs2', 'N/A')}%
  10-2 Spread:             {yields.get('spread', 'N/A')}%

================================================================================
"""
    
    # Send the email
    send_email(subject, full_report)
    print("\n✓ Report complete!")

if __name__ == "__main__":
    main()
