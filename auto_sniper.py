"""
Automated Polymarket 15-minute market sniper.
Combines scanner and sniper into one continuous loop.
"""

import asyncio
import json
import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import websockets
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs

load_dotenv()

# =============================================================================
# CONFIGURATION
# =============================================================================

# DRY RUN MODE - Set to False to execute real trades
DRY_RUN = False

# Trading parameters
# Trading parameters
BUY_PRICE = 0.99       # Maximum price to pay (buy winning side below this)
BUY_AMOUNT = 10.0      # USDC amount to spend per trade
TRIGGER_SECONDS = 1    # Execute when <= this many seconds remain
MIN_WIN_PROBABILITY = 0.50  # Only buy if price > this (indicates likely winner)

# How early to start monitoring a market (minutes before end)
MONITOR_WINDOW_MINUTES = 5

# =============================================================================
# API Configuration
# =============================================================================

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
CLOB_API_KEY = os.getenv("CLOB_API_KEY")
CLOB_SECRET = os.getenv("CLOB_SECRET")
CLOB_PASSPHRASE = os.getenv("CLOB_PASSPHRASE")

GAMMA_API_URL = "https://gamma-api.polymarket.com"
CLOB_API_URL = "https://clob.polymarket.com"
CLOB_HOST = "https://clob.polymarket.com"
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
CHAIN_ID = 137

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}


# =============================================================================
# Scanner Functions
# =============================================================================

def parse_market_end_time(market, now_utc):
    """Parse end time from market question text."""
    question = market.get("question", "")
    
    # Pattern: "January 27, 8am ET" or "January 27, 10:15am ET"
    time_pattern = r'(\w+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*ET'
    match = re.search(time_pattern, question, re.IGNORECASE)
    
    if match:
        month_name, day, hour, minute, ampm = match.groups()
        minute = int(minute) if minute else 0
        hour = int(hour)
        day = int(day)
        
        if ampm.lower() == 'pm' and hour != 12:
            hour += 12
        elif ampm.lower() == 'am' and hour == 12:
            hour = 0
        
        months = {
            'january': 1, 'february': 2, 'march': 3, 'april': 4,
            'may': 5, 'june': 6, 'july': 7, 'august': 8,
            'september': 9, 'october': 10, 'november': 11, 'december': 12
        }
        month = months.get(month_name.lower(), 1)
        year = now_utc.year
        et = ZoneInfo("America/New_York")
        
        try:
            end_time_et = datetime(year, month, day, hour, minute, tzinfo=et)
            return end_time_et.astimezone(ZoneInfo("UTC"))
        except ValueError:
            pass
    
    return None


async def search_markets(client, query):
    """Search for markets using Gamma API."""
    url = f"{GAMMA_API_URL}/public-search"
    response = await client.get(url, params={"q": query})
    
    if response.status_code != 200:
        return []
    
    data = response.json()
    all_markets = []
    for event in data.get("events", []):
        all_markets.extend(event.get("markets", []))
    
    return all_markets


async def find_next_market():
    """Find the next upcoming 15-minute crypto market."""
    async with httpx.AsyncClient(
        headers=DEFAULT_HEADERS,
        timeout=httpx.Timeout(30.0),
        http2=True,
    ) as client:
        search_terms = ["Bitcoin up or down", "Ethereum up or down", "Solana up or down"]
        all_markets = []
        
        for term in search_terms:
            try:
                markets = await search_markets(client, term)
                all_markets.extend(markets)
            except Exception:
                pass
        
        # Remove duplicates
        seen = set()
        unique_markets = []
        for m in all_markets:
            cid = m.get("conditionId")
            if cid and cid not in seen:
                seen.add(cid)
                unique_markets.append(m)
        
        now_utc = datetime.now(ZoneInfo("UTC"))
        best_market = None
        best_time_remaining = float('inf')
        
        for market in unique_markets:
            question = market.get("question", "").lower()
            
            # Only crypto up/down markets
            if not any(c in question for c in ["bitcoin", "ethereum", "solana"]):
                continue
            if "up or down" not in question:
                continue
            
            end_time = parse_market_end_time(market, now_utc)
            if not end_time:
                continue
            
            time_remaining = (end_time - now_utc).total_seconds() / 60
            
            # Find market ending soonest but still in the future
            if 0 < time_remaining < best_time_remaining:
                # Parse token IDs
                clob_token_ids = market.get("clobTokenIds", "") or ""
                outcomes_str = market.get("outcomes", "") or ""
                
                if isinstance(clob_token_ids, list):
                    clob_token_ids = json.dumps(clob_token_ids)
                if isinstance(outcomes_str, list):
                    outcomes_str = json.dumps(outcomes_str)
                
                yes_token, no_token = None, None
                
                try:
                    if clob_token_ids and clob_token_ids.startswith("["):
                        token_ids = json.loads(clob_token_ids)
                        if outcomes_str and outcomes_str.startswith("["):
                            outcomes = json.loads(outcomes_str)
                            for i, outcome in enumerate(outcomes):
                                if i < len(token_ids):
                                    if outcome.upper() in ["YES", "UP"]:
                                        yes_token = token_ids[i]
                                    elif outcome.upper() in ["NO", "DOWN"]:
                                        no_token = token_ids[i]
                except (json.JSONDecodeError, IndexError):
                    continue
                
                if yes_token and no_token:
                    best_market = {
                        "question": market.get("question"),
                        "condition_id": market.get("conditionId"),
                        "yes_token_id": yes_token,
                        "no_token_id": no_token,
                        "end_time": end_time,
                        "minutes_remaining": time_remaining,
                    }
                    best_time_remaining = time_remaining
        
        return best_market


# =============================================================================
# Sniper Functions
# =============================================================================

class AutoSniper:
    def __init__(self, market_config):
        self.config = market_config
        self.condition_id = market_config["condition_id"]
        self.yes_token_id = market_config["yes_token_id"]
        self.no_token_id = market_config["no_token_id"]
        self.end_time = market_config["end_time"]
        
        self.best_yes_ask = None
        self.best_no_ask = None
        self.order_executed = False
        self.client = None
    
    def init_client(self):
        """Initialize the CLOB client."""
        if not PRIVATE_KEY:
            print("ERROR: No PRIVATE_KEY in .env")
            return False
        
        try:
            if CLOB_API_KEY and CLOB_SECRET and CLOB_PASSPHRASE:
                creds = ApiCreds(
                    api_key=CLOB_API_KEY,
                    api_secret=CLOB_SECRET,
                    api_passphrase=CLOB_PASSPHRASE
                )
                self.client = ClobClient(CLOB_HOST, key=PRIVATE_KEY, chain_id=CHAIN_ID, creds=creds)
            else:
                self.client = ClobClient(CLOB_HOST, key=PRIVATE_KEY, chain_id=CHAIN_ID)
            return True
        except Exception as e:
            print(f"Client init error: {e}")
            return False
    
    def get_seconds_remaining(self):
        """Calculate seconds until market ends."""
        now = datetime.now(ZoneInfo("UTC"))
        return (self.end_time - now).total_seconds()
    
    def determine_winning_side(self):
        """Determine which side is winning based on price."""
        yes_price = self.best_yes_ask or 0
        no_price = self.best_no_ask or 0
        
        if yes_price > MIN_WIN_PROBABILITY and yes_price < BUY_PRICE:
            return "YES", self.yes_token_id, yes_price
        elif no_price > MIN_WIN_PROBABILITY and no_price < BUY_PRICE:
            return "NO", self.no_token_id, no_price
        return None, None, None
    
    async def execute_order(self, side, token_id, price):
        """Execute a FOK order."""
        if self.order_executed:
            return
        
        self.order_executed = True
        
        if DRY_RUN:
            print(f"\n{'='*60}")
            print(f"DRY RUN - WOULD BUY {side} @ ${price:.4f}")
            print(f"Token: {token_id}")
            print(f"Amount: ${BUY_AMOUNT}")
            print(f"{'='*60}\n")
            return
        
        try:
            order_args = OrderArgs(
                price=price,
                size=BUY_AMOUNT / price,
                side="BUY",
                token_id=token_id,
            )
            
            result = self.client.create_and_post_order(order_args)
            print(f"\n{'='*60}")
            print(f"ORDER EXECUTED: {side} @ ${price:.4f}")
            print(f"Result: {result}")
            print(f"{'='*60}\n")
        except Exception as e:
            print(f"Order execution error: {e}")
    
    def process_orderbook(self, data):
        """Process orderbook update from WebSocket."""
        try:
            asset_id = data.get("asset_id")
            
            if asset_id == self.yes_token_id:
                asks = data.get("asks", [])
                if asks:
                    self.best_yes_ask = float(asks[0].get("price", 0))
            elif asset_id == self.no_token_id:
                asks = data.get("asks", [])
                if asks:
                    self.best_no_ask = float(asks[0].get("price", 0))
        except Exception:
            pass
    
    async def monitor_and_snipe(self):
        """Main monitoring loop."""
        if not self.init_client():
            return False
        
        print(f"\nMonitoring: {self.config['question']}")
        print(f"End time: {self.end_time.astimezone(ZoneInfo('America/New_York')).strftime('%I:%M %p ET')}")
        
        subscribe_msg = {
            "type": "subscribe",
            "channel": "market",
            "assets_ids": [self.yes_token_id, self.no_token_id],
        }
        
        try:
            async with websockets.connect(WS_URL) as ws:
                await ws.send(json.dumps(subscribe_msg))
                print("WebSocket connected, monitoring prices...")
                
                while not self.order_executed:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        data = json.loads(msg)
                        
                        if isinstance(data, list):
                            for item in data:
                                self.process_orderbook(item)
                        else:
                            self.process_orderbook(data)
                    except asyncio.TimeoutError:
                        pass
                    
                    seconds_remaining = self.get_seconds_remaining()
                    
                    if seconds_remaining <= 0:
                        print("Market ended!")
                        break
                    
                    # Status update every 10 seconds
                    if int(seconds_remaining) % 10 == 0:
                        yes_str = f"${self.best_yes_ask:.2f}" if self.best_yes_ask else "N/A"
                        no_str = f"${self.best_no_ask:.2f}" if self.best_no_ask else "N/A"
                        print(f"  {seconds_remaining:.0f}s remaining | UP: {yes_str} | DOWN: {no_str}")
                    
                    # Trigger zone
                    if 0 < seconds_remaining <= TRIGGER_SECONDS:
                        side, token_id, price = self.determine_winning_side()
                        if side:
                            await self.execute_order(side, token_id, price)
                            break
                    
                    await asyncio.sleep(0.1)
                
                return True
        except Exception as e:
            print(f"WebSocket error: {e}")
            return False


# =============================================================================
# Main Loop
# =============================================================================

async def main():
    mode_str = "DRY RUN (no real trades)" if DRY_RUN else "LIVE TRADING"
    print(f"""
+---------------------------------------------------------------+
|         AUTOMATED POLYMARKET 15-MINUTE SNIPER                 |
+---------------------------------------------------------------+
|  Mode: {mode_str:40}
|  Buy Price: ${BUY_PRICE:.2f}  |  Amount: ${BUY_AMOUNT:.2f}
|  Trigger: {TRIGGER_SECONDS} second(s) before end
+---------------------------------------------------------------+
""")
    
    if not DRY_RUN:
        print("WARNING: LIVE TRADING MODE - Real money at risk!")
        print("Press Ctrl+C within 5 seconds to cancel...")
        await asyncio.sleep(5)
    
    while True:
        try:
            now_et = datetime.now(ZoneInfo("America/New_York"))
            print(f"\n[{now_et.strftime('%I:%M:%S %p ET')}] Scanning for markets...")
            
            market = await find_next_market()
            
            if not market:
                print("No upcoming markets found. Waiting 60 seconds...")
                await asyncio.sleep(60)
                continue
            
            minutes_remaining = market["minutes_remaining"]
            print(f"\nFound: {market['question']}")
            print(f"  Ends in: {minutes_remaining:.1f} minutes")
            print(f"  Condition ID: {market['condition_id']}")
            
            # If market is within monitoring window, start sniping
            if minutes_remaining <= MONITOR_WINDOW_MINUTES:
                print(f"\nStarting sniper (within {MONITOR_WINDOW_MINUTES} min window)...")
                sniper = AutoSniper(market)
                await sniper.monitor_and_snipe()
                print("\nSnipe complete. Looking for next market...")
                await asyncio.sleep(5)
            else:
                # Wait until we're within the monitoring window
                wait_seconds = (minutes_remaining - MONITOR_WINDOW_MINUTES) * 60
                wait_until = now_et + timedelta(seconds=wait_seconds)
                print(f"\nWaiting until {wait_until.strftime('%I:%M:%S %p ET')} to start monitoring...")
                print(f"  ({wait_seconds/60:.1f} minutes)")
                await asyncio.sleep(min(wait_seconds, 60))  # Check every minute in case of new markets
                
        except KeyboardInterrupt:
            print("\n\nStopped by user.")
            break
        except Exception as e:
            print(f"Error: {e}")
            await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())