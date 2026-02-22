#!/usr/bin/env python3
"""Check all open up/down markets."""

import httpx
from datetime import datetime
from zoneinfo import ZoneInfo

GAMMA_API_URL = "https://gamma-api.polymarket.com"
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}

def main():
    now_utc = datetime.now(ZoneInfo("UTC"))
    now_et = now_utc.astimezone(ZoneInfo("America/New_York"))
    print(f"Current time: {now_et.strftime('%I:%M %p ET')}")
    print()
    
    # Search for up/down markets
    search_terms = [
        "Bitcoin up or down",
        "Ethereum up or down",
        "Solana up or down",
        "up or down February",
    ]
    
    seen = set()
    open_markets = []
    
    with httpx.Client(headers=DEFAULT_HEADERS, timeout=30) as client:
        for term in search_terms:
            resp = client.get(f"{GAMMA_API_URL}/public-search", params={"q": term})
            if resp.status_code != 200:
                continue
            
            data = resp.json()
            for event in data.get("events", []):
                for m in event.get("markets", []):
                    cid = m.get("conditionId")
                    if cid in seen:
                        continue
                    seen.add(cid)
                    
                    q = m.get("question", "").lower()
                    if "up or down" not in q:
                        continue
                    
                    closed = m.get("closed", False)
                    end_str = m.get("endDate", "")
                    
                    if end_str:
                        end = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
                        end_et = end.astimezone(ZoneInfo("America/New_York"))
                        mins = (end - now_utc).total_seconds() / 60
                        
                        open_markets.append({
                            "question": m.get("question"),
                            "closed": closed,
                            "end_time": end_et,
                            "minutes": mins,
                        })
    
    # Sort by end time
    open_markets.sort(key=lambda x: x["minutes"])
    
    print("=" * 70)
    print("ALL UP/DOWN MARKETS (sorted by end time)")
    print("=" * 70)
    
    for m in open_markets:
        status = "CLOSED" if m["closed"] else "OPEN"
        end_str = m["end_time"].strftime("%I:%M %p ET %b %d")
        mins = m["minutes"]
        
        if mins > 0:
            time_str = f"{mins:7.1f} min"
        else:
            time_str = f"{mins:7.1f} min (PAST)"
        
        print(f"[{status:6}] {end_str} | {time_str} | {m['question'][:50]}")
    
    print()
    print("=" * 70)
    print("OPEN MARKETS ONLY")
    print("=" * 70)
    open_only = [m for m in open_markets if not m["closed"] and m["minutes"] > 0]
    for m in open_only:
        end_str = m["end_time"].strftime("%I:%M %p ET %b %d")
        print(f"  {end_str} | {m['minutes']:7.1f} min | {m['question']}")
    
    if not open_only:
        print("  NO OPEN MARKETS FOUND!")

if __name__ == "__main__":
    main()
