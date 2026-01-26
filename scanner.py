"""
Query Polymarket Gamma API to find active 15-minute Bitcoin/Ethereum markets.
"""

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import aiohttp


GAMMA_API_URL = "https://gamma-api.polymarket.com"


def get_current_15min_window_et():
    """Get the current 15-minute window boundaries in ET."""
    et = ZoneInfo("America/New_York")
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


async def search_markets(session, query):
    """Search for markets using the Gamma API public-search endpoint."""
    url = f"{GAMMA_API_URL}/public-search"
    params = {
        "query": query,
        "limit": 100,
    }
    
    async with session.get(url, params=params) as response:
        if response.status != 200:
            text = await response.text()
            raise Exception(f"API error {response.status}: {text}")
        data = await response.json()
        # public-search returns results in a nested structure
        return data.get("markets", data) if isinstance(data, dict) else data


async def find_active_15min_market():
    """Find active 15-minute Bitcoin or Ethereum markets ending soon."""
    now_et, window_start, window_end = get_current_15min_window_et()
    time_window_str = format_time_window(window_start, window_end)
    
    print(f"Current time (ET): {now_et.strftime('%Y-%m-%d %I:%M:%S %p')}")
    print(f"Looking for 15-minute window: {time_window_str}")
    print("-" * 60)
    
    async with aiohttp.ClientSession() as session:
        # Search for Bitcoin and Ethereum up/down markets
        search_terms = ["Bitcoin Up or Down", "Ethereum Up or Down"]
        all_markets = []
        
        for term in search_terms:
            try:
                markets = await search_markets(session, term)
                if isinstance(markets, list):
                    all_markets.extend(markets)
            except Exception as e:
                print(f"Error searching for '{term}': {e}")
        
        # Remove duplicates by condition_id
        seen_conditions = set()
        unique_markets = []
        for market in all_markets:
            cond_id = market.get("condition_id")
            if cond_id and cond_id not in seen_conditions:
                seen_conditions.add(cond_id)
                unique_markets.append(market)
        
        # Filter for 15-minute markets ending in less than 20 minutes
        now_utc = datetime.now(ZoneInfo("UTC"))
        max_end_time = now_utc + timedelta(minutes=20)
        
        matching_markets = []
        
        for market in unique_markets:
            question = market.get("question", "").lower()
            description = market.get("description", "").lower()
            
            # Check if it's a Bitcoin or Ethereum up/down market
            is_crypto_market = any(term in question or term in description 
                                   for term in ["bitcoin", "ethereum", "btc", "eth"])
            is_up_down = "up" in question or "down" in question
            
            if not (is_crypto_market and is_up_down):
                continue
            
            # Parse end time
            end_time_str = market.get("end_date_iso") or market.get("endDate")
            if not end_time_str:
                continue
            
            try:
                # Handle various ISO format variations
                end_time_str = end_time_str.replace("Z", "+00:00")
                if "." in end_time_str:
                    end_time = datetime.fromisoformat(end_time_str)
                else:
                    end_time = datetime.fromisoformat(end_time_str)
                
                if end_time.tzinfo is None:
                    end_time = end_time.replace(tzinfo=ZoneInfo("UTC"))
                
            except ValueError as e:
                print(f"Could not parse end time '{end_time_str}': {e}")
                continue
            
            # Check if market ends within 20 minutes
            if now_utc < end_time <= max_end_time:
                # Extract token IDs
                tokens = market.get("tokens", [])
                yes_token = None
                no_token = None
                
                for token in tokens:
                    outcome = token.get("outcome", "").upper()
                    if outcome == "YES":
                        yes_token = token.get("token_id")
                    elif outcome == "NO":
                        no_token = token.get("token_id")
                
                minutes_remaining = (end_time - now_utc).total_seconds() / 60
                
                matching_markets.append({
                    "question": market.get("question"),
                    "condition_id": market.get("condition_id"),
                    "yes_token_id": yes_token,
                    "no_token_id": no_token,
                    "end_time": end_time.isoformat(),
                    "end_time_et": end_time.astimezone(ZoneInfo("America/New_York")).strftime("%I:%M %p"),
                    "minutes_remaining": round(minutes_remaining, 1),
                })
        
        return matching_markets


async def main():
    try:
        markets = await find_active_15min_market()
        
        if not markets:
            print("No active 15-minute Bitcoin/Ethereum markets found ending in the next 20 minutes.")
            print("\nTip: These markets may only be available during certain trading hours.")
            return
        
        print(f"Found {len(markets)} matching market(s):\n")
        
        for i, market in enumerate(markets, 1):
            print(f"Market {i}:")
            print(f"  Question:     {market['question']}")
            print(f"  Condition ID: {market['condition_id']}")
            print(f"  YES Token ID: {market['yes_token_id']}")
            print(f"  NO Token ID:  {market['no_token_id']}")
            print(f"  End Time:     {market['end_time_et']} ET ({market['end_time']})")
            print(f"  Time Left:    {market['minutes_remaining']} minutes")
            print()
            
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
