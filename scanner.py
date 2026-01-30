"""
Query Polymarket Gamma API to find active 15-minute Bitcoin/Ethereum markets.
"""


import asyncio
import json
import re
from datetime import datetime, timedelta
import os
from zoneinfo import ZoneInfo
import httpx
from dotenv import load_dotenv

load_dotenv()

GAMMA_API_URL = "https://gamma-api.polymarket.com"
# Alternative: CLOB API for market data
CLOB_API_URL = "https://clob.polymarket.com"

# Headers to mimic a browser request (more complete)
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "cross-site",
}


def get_current_15min_window_et():
    """Get the current 15-minute window boundaries in ET."""
    timezone_str = os.getenv("TIME_ZONE", "America/New_York")
    et = ZoneInfo(timezone_str)
    now = datetime.now(et)
    
    # Calculate start of current 15-minute window
    minute_slot = (now.minute // 15) * 15
    window_start = now.replace(minute=minute_slot, second=0, microsecond=0)
    window_end = window_start + timedelta(minutes=15)
    
    return now, window_start, window_end


def format_time_window(window_start, window_end):
    """Format the time window for display and search."""
    start_str = window_start.strftime("%I:%M %p").lstrip("0")
    end_str = window_end.strftime("%I:%M %p").lstrip("0")
    return f"{start_str} - {end_str}"


def parse_market_end_time(market, now_utc):
    """
    Parse the end time from a market. 
    For 15-minute markets, the time is often in the question text like:
    "Bitcoin up or down - January 27, 8am ET"
    """
    question = market.get("question", "")
    end_date_str = market.get("endDateIso") or market.get("endDate")
    
    # Try to extract time from question for 15-min markets
    # Pattern: "January 27, 8am ET" or "January 27, 10:15am ET"
    time_pattern = r'(\w+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*ET'
    match = re.search(time_pattern, question, re.IGNORECASE)
    
    if match:
        month_name, day, hour, minute, ampm = match.groups()
        minute = int(minute) if minute else 0
        hour = int(hour)
        day = int(day)
        
        # Convert 12-hour to 24-hour
        if ampm.lower() == 'pm' and hour != 12:
            hour += 12
        elif ampm.lower() == 'am' and hour == 12:
            hour = 0
        
        # Get month number
        months = {
            'january': 1, 'february': 2, 'march': 3, 'april': 4,
            'may': 5, 'june': 6, 'july': 7, 'august': 8,
            'september': 9, 'october': 10, 'november': 11, 'december': 12
        }
        month = months.get(month_name.lower(), 1)
        
        # Assume current year or next year if month has passed
        year = now_utc.year
        et = ZoneInfo("America/New_York")
        
        try:
            end_time_et = datetime(year, month, day, hour, minute, tzinfo=et)
            end_time_utc = end_time_et.astimezone(ZoneInfo("UTC"))
            return end_time_utc
        except ValueError:
            pass
    
    # Fallback: try to parse endDateIso/endDate
    if end_date_str:
        try:
            end_date_str = end_date_str.replace("Z", "+00:00")
            end_time = datetime.fromisoformat(end_date_str)
            if end_time.tzinfo is None:
                end_time = end_time.replace(tzinfo=ZoneInfo("UTC"))
            return end_time
        except ValueError:
            pass
    
    return None


async def search_markets_gamma(client, query):
    """Search for markets using the Gamma API public-search endpoint."""
    url = f"{GAMMA_API_URL}/public-search"
    params = {
        "q": query,  # API requires 'q' parameter, not 'query'
    }
    
    response = await client.get(url, params=params)
    if response.status_code != 200:
        raise Exception(f"Gamma API error {response.status_code}: {response.text}")
    data = response.json()
    
    # public-search returns { events: [...], tags: [...], profiles: [...] }
    # Each event contains a 'markets' array
    all_markets = []
    events = data.get("events", [])
    for event in events:
        markets = event.get("markets", [])
        all_markets.extend(markets)
    
    return all_markets


async def search_markets_clob(client, query_terms):
    """
    Fallback: Search for markets using the CLOB API.
    The CLOB API has a /markets endpoint that doesn't require auth for reading.
    """
    url = f"{CLOB_API_URL}/markets"
    
    response = await client.get(url)
    if response.status_code != 200:
        raise Exception(f"CLOB API error {response.status_code}: {response.text}")
    data = response.json()
    
    # Handle different response formats
    if isinstance(data, dict):
        # Could be { "data": [...] } or { "markets": [...] } or direct list
        markets_list = data.get("data") or data.get("markets") or []
    elif isinstance(data, list):
        markets_list = data
    else:
        markets_list = []
    
    # Filter markets based on query terms
    matching = []
    for market in markets_list:
        question = (market.get("question") or "").lower()
        description = (market.get("description") or "").lower()
        combined = question + " " + description
        if any(term.lower() in combined for term in query_terms):
            # Normalize field names to match Gamma API format
            normalized = {
                "question": market.get("question"),
                "conditionId": market.get("condition_id") or market.get("conditionId"),
                "endDateIso": market.get("end_date_iso") or market.get("endDateIso") or market.get("end_date"),
                "endDate": market.get("end_date") or market.get("endDate"),
                "clobTokenIds": market.get("clob_token_ids") or market.get("clobTokenIds") or market.get("tokens"),
                "outcomes": market.get("outcomes"),
                "description": market.get("description"),
            }
            matching.append(normalized)
    
    return matching


async def search_markets(client, query):
    """Try Gamma API first, fall back to CLOB API if connection fails."""
    try:
        return await search_markets_gamma(client, query)
    except httpx.ConnectError as e:
        print(f"Gamma API connection failed: {e}")
        print("Trying CLOB API as fallback...")
        # Extract search terms from query
        terms = query.replace(" or ", " ").split()
        return await search_markets_clob(client, terms)


async def find_active_15min_market():
    """Find active crypto markets ending soon."""
    print("\n=== Finding Active Crypto Markets ===")
    now_et, window_start, window_end = get_current_15min_window_et()
    
    # Get actual ET time for display
    actual_et = datetime.now(ZoneInfo("America/New_York"))
    print(f"Current time (ET): {actual_et.strftime('%Y-%m-%d %I:%M:%S %p')}")
    print("Searching for crypto markets ending within 20 minutes...")
    print("-" * 60)
    
    # Use httpx with HTTP/2 support and browser-like settings
    async with httpx.AsyncClient(
        headers=DEFAULT_HEADERS,
        timeout=httpx.Timeout(30.0, connect=10.0),
        follow_redirects=True,
        http2=True,  # Enable HTTP/2 like browsers
    ) as client:
        # Search for 15-minute up/down markets
        search_terms = [
            "Bitcoin up or down",
            "Ethereum up or down",
            "Solana up or down",
        ]
        all_markets = []
        
        for term in search_terms:
            print(f"Searching for: '{term}'...", flush=True)
            try:
                markets = await search_markets(client, term)
                print(f"  -> Found {len(markets) if markets else 0} markets", flush=True)
                if isinstance(markets, list):
                    all_markets.extend(markets)
            except httpx.ConnectError as e:
                print(f"  -> Connection error: {e}", flush=True)
            except Exception as e:
                print(f"  -> Error: {e}", flush=True)
        
        print(f"Total markets from search: {len(all_markets)}")
        
        # Remove duplicates by conditionId
        seen_conditions = set()
        unique_markets = []
        for market in all_markets:
            cond_id = market.get("conditionId")
            if cond_id and cond_id not in seen_conditions:
                seen_conditions.add(cond_id)
                unique_markets.append(market)
        
        # Filter for markets ending in next 20 minutes
        now_utc = datetime.now(ZoneInfo("UTC"))
        max_end_time = now_utc + timedelta(minutes=20)
        
        matching_markets = []
        crypto_markets_found = 0
        
        # Debug: show first few market questions
        print(f"\nUnique markets to filter: {len(unique_markets)}")
        if unique_markets:
            print("Sample questions from search results:")
            for m in unique_markets[:5]:
                q = m.get("question", "N/A")[:80]
                end = m.get("endDateIso") or m.get("endDate") or "no end date"
                print(f"  - {q}... (ends: {end})")
        
        for market in unique_markets:
            question = market.get("question", "").lower()
            description = market.get("description", "").lower()
            
            # Check if it's a crypto-related market (use word boundaries to avoid false matches)
            # Using \b word boundaries would be better but for simple matching we check for common patterns
            crypto_terms = ["bitcoin", "ethereum", "btc", "eth", "solana", 
                           "dogecoin", "doge", "xrp", "ripple", "cardano", 
                           "polygon", "matic", "avalanche", "avax", "chainlink"]
            
            # Check for crypto terms (more strict - must have crypto-specific content)
            is_crypto_market = any(
                f" {term}" in f" {question}" or 
                f" {term}" in f" {description}" or
                question.startswith(term) or
                description.startswith(term)
                for term in crypto_terms
            )
            
            # Also check for price-related crypto patterns
            is_crypto_price = any(
                pattern in question 
                for pattern in ["price of bitcoin", "price of ethereum", "price of solana",
                               "btc hit", "eth hit", "bitcoin hit", "ethereum hit",
                               "bitcoin above", "ethereum above", "solana above",
                               "bitcoin up or down", "ethereum up or down", "solana up or down"]
            )
            
            is_crypto_market = is_crypto_market or is_crypto_price
            
            if is_crypto_market:
                crypto_markets_found += 1
            
            # Accept only crypto markets
            if not is_crypto_market:
                continue
            
            # Parse end time - check for time in question first (for 15-min markets)
            end_time = parse_market_end_time(market, now_utc)
            if not end_time:
                continue
            
            # Check if market ends within 2 hours
            time_diff_minutes = (end_time - now_utc).total_seconds() / 60
            
            # Show crypto markets ending in next 20 minutes
            if 0 < time_diff_minutes <= 20:
                print(f"  Crypto market: '{question[:60]}...' ends in {time_diff_minutes:.1f} min")
            
            if now_utc < end_time <= max_end_time:
                # Extract token IDs from clobTokenIds (JSON string like "[\"tokenId1\",\"tokenId2\"]")
                # or outcomes field for parsing
                clob_token_ids = market.get("clobTokenIds", "") or ""
                outcomes_str = market.get("outcomes", "") or ""
                
                # Handle cases where clobTokenIds might be a list
                if isinstance(clob_token_ids, list):
                    clob_token_ids = json.dumps(clob_token_ids)
                if isinstance(outcomes_str, list):
                    outcomes_str = json.dumps(outcomes_str)
                
                
                yes_token = None
                no_token = None
                
                # Parse token IDs - clobTokenIds contains comma-separated or JSON array of token IDs
                # First token is typically YES, second is NO based on outcomes order
                try:
                    if clob_token_ids:
                        # Try parsing as JSON array first
                        if clob_token_ids.startswith("["):
                            token_ids = json.loads(clob_token_ids)
                        else:
                            token_ids = [t.strip() for t in clob_token_ids.split(",")]
                        
                        # Parse outcomes to match token IDs
                        if outcomes_str:
                            if outcomes_str.startswith("["):
                                outcomes = json.loads(outcomes_str)
                            else:
                                outcomes = [o.strip() for o in outcomes_str.split(",")]
                            
                            for i, outcome in enumerate(outcomes):
                                if i < len(token_ids):
                                    outcome_upper = outcome.upper()
                                    # Handle both YES/NO and Up/Down markets
                                    if outcome_upper in ["YES", "UP"]:
                                        yes_token = token_ids[i]
                                    elif outcome_upper in ["NO", "DOWN"]:
                                        no_token = token_ids[i]
                        else:
                            # Default: first is YES/Up, second is NO/Down
                            if len(token_ids) >= 2:
                                yes_token = token_ids[0]
                                no_token = token_ids[1]
                except (json.JSONDecodeError, IndexError) as e:
                    print(f"Could not parse token IDs: {e}")
                
                minutes_remaining = (end_time - now_utc).total_seconds() / 60
                
                matching_markets.append({
                    "question": market.get("question"),
                    "condition_id": market.get("conditionId"),
                    "yes_token_id": yes_token,
                    "no_token_id": no_token,
                    "end_time": end_time.isoformat(),
                    "end_time_et": end_time.astimezone(ZoneInfo("America/New_York")).strftime("%I:%M %p"),
                    "minutes_remaining": round(minutes_remaining, 1),
                })
        
        print(f"\nFilter summary: {crypto_markets_found} crypto markets, {len(matching_markets)} ending within 20 minutes")
        return matching_markets


async def test_connectivity():
    """Test basic connectivity to Polymarket APIs."""
    print("Testing API connectivity...", end=" ")
    
    async with httpx.AsyncClient(
        headers=DEFAULT_HEADERS,
        timeout=httpx.Timeout(10.0),
        follow_redirects=True,
        http2=True,
    ) as client:
        gamma_ok = False
        clob_ok = False
        
        try:
            response = await client.get(f"{GAMMA_API_URL}/public-search", params={"q": "Bitcoin"})
            gamma_ok = response.status_code == 200
        except Exception:
            pass
        
        try:
            response = await client.get(f"{CLOB_API_URL}/markets")
            clob_ok = response.status_code == 200
        except Exception:
            pass
        
        if gamma_ok:
            print("OK")
        elif clob_ok:
            print("Gamma API down, using CLOB fallback")
        else:
            print("FAILED - check your internet connection")
    
    print()


async def main():
    try:
        # Test connectivity first
        await test_connectivity()
        
        print("Starting market search...")
        markets = await find_active_15min_market()
        print("Market search complete.")
        
        if not markets:
            print("No active crypto markets found ending in the next 20 minutes.")
            print("\nTip: Run again closer to a 15-minute window boundary, or check Polymarket directly.")
            return
        
        print(f"Found {len(markets)} matching market(s):\n")
        
        for i, market in enumerate(markets, 1):
            question = market['question']
            # Determine label based on market type
            is_up_down = "up or down" in question.lower()
            yes_label = "UP Token ID:" if is_up_down else "YES Token ID:"
            no_label = "DOWN Token ID:" if is_up_down else "NO Token ID:"
            
            print(f"Market {i}:")
            print(f"  Question:     {question}")
            print(f"  Condition ID: {market['condition_id']}")
            print(f"  {yes_label:14} {market['yes_token_id']}")
            print(f"  {no_label:14} {market['no_token_id']}")
            print(f"  End Time:     {market['end_time_et']} ET ({market['end_time']})")
            print(f"  Time Left:    {market['minutes_remaining']} minutes")
            print()
            
            # Print copy-paste ready config for sniper.py
            print("  +-- Copy to sniper.py ------------------------------------+")
            print(f'  | CONDITION_ID = "{market["condition_id"]}"')
            print(f'  | YES_TOKEN_ID = "{market["yes_token_id"]}"')
            print(f'  | NO_TOKEN_ID = "{market["no_token_id"]}"')
            print(f'  | END_TIME_ISO = "{market["end_time"]}"')
            print("  +----------------------------------------------------------+")
            print()
            
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
