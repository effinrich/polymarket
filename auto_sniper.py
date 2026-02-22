"""
Automated Polymarket market sniper.
Supports both 15-minute and daily crypto markets.
Sends alerts when 15-minute markets become available.
"""

import asyncio
import json
import os
import re
import subprocess
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

# RUN MODE - Set to True to snipe one market then exit, False to run continuously
RUN_ONCE = False

# MARKET TYPES - Which markets to snipe
SNIPE_15MIN_MARKETS = True   # 15-minute "up or down" markets
SNIPE_DAILY_MARKETS = True   # Daily crypto price markets

# Trading parameters
BUY_PRICE = 1.00       # Maximum price to pay (buy winning side at or below this)
BUY_AMOUNT = 10.0      # USDC amount to spend per trade
TRIGGER_SECONDS = 4    # Execute when <= this many seconds remain (more time to fill)
MIN_WIN_PROBABILITY = 0.80  # Only buy if price > this (80% = very confident winner)

# How early to start monitoring a market (minutes before end)
MONITOR_WINDOW_15MIN = 5      # For 15-minute markets
MONITOR_WINDOW_DAILY = 30     # For daily markets (start 30 min before)

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

def send_notification(title, message):
    """Send a macOS notification."""
    try:
        subprocess.run([
            "osascript", "-e",
            f'display notification "{message}" with title "{title}" sound name "Glass"'
        ], check=False)
    except Exception:
        pass


def parse_market_end_time_from_question(market, now_utc):
    """Parse end time from market question text (for 15-min markets)."""
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


def parse_end_date_from_api(market):
    """Parse end time from API endDate field."""
    end_str = market.get("endDate", "")
    if end_str:
        try:
            return datetime.fromisoformat(end_str.replace('Z', '+00:00'))
        except (ValueError, TypeError):
            pass
    return None


def extract_token_ids(market):
    """Extract YES/NO token IDs from market data."""
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
        pass
    
    return yes_token, no_token


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


async def get_active_markets(client):
    """Get active markets from Gamma API."""
    url = f"{GAMMA_API_URL}/markets"
    response = await client.get(url, params={"closed": "false", "limit": 200})
    
    if response.status_code != 200:
        return []
    
    return response.json()


async def find_next_market():
    """Find the next upcoming crypto market (15-min or daily)."""
    async with httpx.AsyncClient(
        headers=DEFAULT_HEADERS,
        timeout=httpx.Timeout(30.0),
        http2=True,
    ) as client:
        now_utc = datetime.now(ZoneInfo("UTC"))
        candidates = []
        found_15min = False
        
        # =================================================================
        # Search for 15-minute "up or down" markets
        # =================================================================
        if SNIPE_15MIN_MARKETS:
            # Get today and tomorrow's date for search queries
            et_tz = ZoneInfo("America/New_York")
            now_et = now_utc.astimezone(et_tz)
            today_str = now_et.strftime("%B %-d")  # e.g., "January 30"
            tomorrow = now_et + timedelta(days=1)
            tomorrow_str = tomorrow.strftime("%B %-d")  # e.g., "January 31"
            
            # Search with multiple query variations to find all markets
            search_terms = [
                "Bitcoin up or down",
                "Ethereum up or down", 
                "Solana up or down",
                "SPX up or down",
                "S&P 500 up or down",
                f"Bitcoin up or down {today_str}",
                f"Ethereum up or down {today_str}",
                f"Solana up or down {today_str}",
                f"Bitcoin up or down {tomorrow_str}",
                f"Ethereum up or down {tomorrow_str}",
                f"Solana up or down {tomorrow_str}",
                "up or down 12AM",  # Catches midnight markets
            ]
            all_markets = []
            
            for term in search_terms:
                try:
                    markets = await search_markets(client, term)
                    all_markets.extend(markets)
                except Exception:
                    pass
            
            # Remove duplicates
            seen = set()
            for m in all_markets:
                cid = m.get("conditionId")
                if cid and cid not in seen:
                    seen.add(cid)
                    question = m.get("question", "").lower()
                    
                    # Accept any up/down market (crypto, SPX, etc.)
                    if "up or down" not in question:
                        continue
                    
                    # Skip already closed markets
                    if m.get("closed"):
                        continue
                    
                    # Use API endDate (more reliable than parsing question)
                    end_time = parse_end_date_from_api(m)
                    if not end_time:
                        # Fallback to parsing from question
                        end_time = parse_market_end_time_from_question(m, now_utc)
                    if not end_time:
                        continue
                    
                    time_remaining = (end_time - now_utc).total_seconds() / 60
                    
                    # Consider markets ending within the next 5 hours (300 minutes)
                    if 0 < time_remaining <= 300:
                        yes_token, no_token = extract_token_ids(m)
                        if yes_token and no_token:
                            found_15min = True
                            candidates.append({
                                "question": m.get("question"),
                                "condition_id": m.get("conditionId"),
                                "yes_token_id": yes_token,
                                "no_token_id": no_token,
                                "end_time": end_time,
                                "minutes_remaining": time_remaining,
                                "market_type": "15min",
                                "monitor_window": MONITOR_WINDOW_15MIN,
                            })
        
        # Alert if 15-min markets found
        if found_15min:
            send_notification(
                "🚀 15-Min Markets Active!",
                f"Found {sum(1 for c in candidates if c['market_type'] == '15min')} active 15-minute markets"
            )
            print("\n🔔 ALERT: 15-minute markets are now active!")
        
        # =================================================================
        # Search for daily crypto price markets
        # =================================================================
        if SNIPE_DAILY_MARKETS:
            try:
                active_markets = await get_active_markets(client)
                seen = set(c["condition_id"] for c in candidates)
                
                for m in active_markets:
                    cid = m.get("conditionId")
                    if cid and cid not in seen:
                        question = m.get("question", "").lower()
                        
                        # Look for daily crypto price markets
                        is_crypto = any(c in question for c in ["bitcoin", "btc", "ethereum", "eth", "solana", "sol"])
                        is_price = any(p in question for p in ["above", "below", "price", "hit", "reach"])
                        
                        if not (is_crypto and is_price):
                            continue
                        
                        end_time = parse_end_date_from_api(m)
                        if not end_time:
                            continue
                        
                        time_remaining = (end_time - now_utc).total_seconds() / 60
                        
                        # Only consider markets ending within 24 hours
                        if 0 < time_remaining <= 1440:
                            yes_token, no_token = extract_token_ids(m)
                            if yes_token and no_token:
                                seen.add(cid)
                                candidates.append({
                                    "question": m.get("question"),
                                    "condition_id": cid,
                                    "yes_token_id": yes_token,
                                    "no_token_id": no_token,
                                    "end_time": end_time,
                                    "minutes_remaining": time_remaining,
                                    "market_type": "daily",
                                    "monitor_window": MONITOR_WINDOW_DAILY,
                                })
            except Exception:
                pass
        
        # Return the market ending soonest
        if candidates:
            candidates.sort(key=lambda x: x["minutes_remaining"])
            return candidates[0]
        
        return None


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
        
        # Track both asks and bids for each side
        self.best_yes_ask = None
        self.best_yes_bid = None
        self.best_no_ask = None
        self.best_no_bid = None
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
        """Determine which side is winning based on price.
        
        Key insight: In these binary markets, prices sum to ~$1.00.
        If one side has no asks (nobody selling), it's likely winning.
        We can infer its price from the other side's asks.
        """
        yes_ask = self.best_yes_ask  # Price to BUY yes
        no_ask = self.best_no_ask    # Price to BUY no
        
        # Calculate implied prices when one side has no asks
        # If NO has asks at $0.01, YES is worth ~$0.99 (implied)
        yes_implied = None
        no_implied = None
        
        if no_ask is not None and no_ask > 0:
            yes_implied = 1.0 - no_ask  # If NO is $0.01, YES implied ~$0.99
        if yes_ask is not None and yes_ask > 0:
            no_implied = 1.0 - yes_ask  # If YES is $0.99, NO implied ~$0.01
        
        # Determine effective prices (ask if available, else implied)
        yes_price = yes_ask if yes_ask else yes_implied
        no_price = no_ask if no_ask else no_implied
        
        print(f"  [DEBUG] YES: ask={yes_ask}, implied={yes_implied:.3f if yes_implied else 'N/A'} | NO: ask={no_ask}, implied={no_implied:.3f if no_implied else 'N/A'}")
        
        # Strategy: Buy the side that's winning (higher implied value)
        # If YES has no asks but NO is at $0.01 → YES is winning at ~$0.99
        # We need to buy via the OTHER side's ask (since winning side has no sellers)
        
        # Check if YES is winning (no asks = nobody selling = winning)
        if yes_ask is None and no_ask is not None and no_ask < 0.10:
            # YES is winning! Buy NO at low price and it'll resolve to 0
            # Actually wait - we want to buy the WINNING side
            # If no sellers for YES, we can't buy YES directly
            # We could: place a bid, or buy via market maker
            # For now, skip if we can't get the winning side
            print(f"  ⚠️ YES winning (no sellers) but can't buy - no ask available")
            return None, None, None
        
        # Check if NO is winning
        if no_ask is None and yes_ask is not None and yes_ask < 0.10:
            print(f"  ⚠️ NO winning (no sellers) but can't buy - no ask available")
            return None, None, None
        
        # Normal case: both sides have asks, pick the higher priced one
        if yes_price is None:
            yes_price = 0
        if no_price is None:
            no_price = 0
            
        # Buy the side with higher probability (closer to 1.0)
        # Must be > MIN_WIN_PROBABILITY and <= BUY_PRICE
        if yes_ask and yes_ask > MIN_WIN_PROBABILITY and yes_ask <= BUY_PRICE:
            return "YES", self.yes_token_id, yes_ask
        elif no_ask and no_ask > MIN_WIN_PROBABILITY and no_ask <= BUY_PRICE:
            return "NO", self.no_token_id, no_ask
        
        # If neither has a qualifying ask, log why
        if yes_price >= no_price:
            if yes_ask is None:
                print(f"  ⚠️ YES has no asks (winners hold, not sell)")
            elif yes_ask > BUY_PRICE:
                print(f"  ⚠️ YES ask ${yes_ask:.2f} > max ${BUY_PRICE} - too expensive")
            elif yes_ask <= MIN_WIN_PROBABILITY:
                print(f"  ⚠️ YES ask ${yes_ask:.2f} <= {MIN_WIN_PROBABILITY} - not confident winner")
        else:
            if no_ask is None:
                print(f"  ⚠️ NO has no asks (winners hold, not sell)")
            elif no_ask > BUY_PRICE:
                print(f"  ⚠️ NO ask ${no_ask:.2f} > max ${BUY_PRICE} - too expensive")
            elif no_ask <= MIN_WIN_PROBABILITY:
                print(f"  ⚠️ NO ask ${no_ask:.2f} <= {MIN_WIN_PROBABILITY} - not confident winner")
        
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
                bids = data.get("bids", [])
                # Set to None if no asks/bids (important for logic)
                self.best_yes_ask = float(asks[0].get("price", 0)) if asks else None
                self.best_yes_bid = float(bids[0].get("price", 0)) if bids else None
            elif asset_id == self.no_token_id:
                asks = data.get("asks", [])
                bids = data.get("bids", [])
                self.best_no_ask = float(asks[0].get("price", 0)) if asks else None
                self.best_no_bid = float(bids[0].get("price", 0)) if bids else None
        except Exception:
            pass
    
    async def fetch_initial_prices(self):
        """Fetch initial prices via REST API."""
        try:
            async with httpx.AsyncClient(headers=DEFAULT_HEADERS, timeout=10.0) as client:
                # Fetch orderbook for YES token
                url = f"{CLOB_API_URL}/book"
                resp = await client.get(url, params={"token_id": self.yes_token_id})
                if resp.status_code == 200:
                    data = resp.json()
                    asks = data.get("asks", [])
                    bids = data.get("bids", [])
                    self.best_yes_ask = float(asks[0].get("price", 0)) if asks else None
                    self.best_yes_bid = float(bids[0].get("price", 0)) if bids else None
                
                # Fetch orderbook for NO token
                resp = await client.get(url, params={"token_id": self.no_token_id})
                if resp.status_code == 200:
                    data = resp.json()
                    asks = data.get("asks", [])
                    bids = data.get("bids", [])
                    self.best_no_ask = float(asks[0].get("price", 0)) if asks else None
                    self.best_no_bid = float(bids[0].get("price", 0)) if bids else None
                
                # Display with "N/A" for missing asks
                yes_str = f"${self.best_yes_ask:.2f}" if self.best_yes_ask else "no asks"
                no_str = f"${self.best_no_ask:.2f}" if self.best_no_ask else "no asks"
                print(f"  Initial prices - YES: {yes_str} | NO: {no_str}")
        except Exception as e:
            print(f"  Failed to fetch initial prices: {e}")
    
    async def monitor_and_snipe(self):
        """Main monitoring loop."""
        if not self.init_client():
            return False
        
        print(f"\nMonitoring: {self.config['question']}")
        print(f"End time: {self.end_time.astimezone(ZoneInfo('America/New_York')).strftime('%I:%M %p ET')}")
        
        # Fetch initial prices before WebSocket
        await self.fetch_initial_prices()
        
        subscribe_msg = {
            "type": "subscribe",
            "channel": "market",
            "assets_ids": [self.yes_token_id, self.no_token_id],
        }
        
        try:
            async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=10) as ws:
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
                        print(f"  {seconds_remaining:.0f}s remaining | YES: {yes_str} | NO: {no_str}")
                    
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
    run_mode = "SINGLE RUN" if RUN_ONCE else "CONTINUOUS"
    market_types = []
    if SNIPE_15MIN_MARKETS:
        market_types.append("15-min")
    if SNIPE_DAILY_MARKETS:
        market_types.append("daily")
    types_str = " + ".join(market_types) if market_types else "NONE"
    
    print(f"""
+---------------------------------------------------------------+
|         AUTOMATED POLYMARKET MARKET SNIPER                    |
+---------------------------------------------------------------+
|  Mode: {mode_str:40}
|  Run:  {run_mode:40}
|  Markets: {types_str:37}
|  Buy Price: ${BUY_PRICE:.3f}  |  Amount: ${BUY_AMOUNT:.2f}
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
            market_type = market.get("market_type", "unknown")
            monitor_window = market.get("monitor_window", MONITOR_WINDOW_15MIN)
            
            print(f"\nFound [{market_type.upper()}]: {market['question']}")
            print(f"  Ends in: {minutes_remaining:.1f} minutes")
            print(f"  Condition ID: {market['condition_id']}")
            
            # If market is within monitoring window, start sniping
            if minutes_remaining <= monitor_window:
                print(f"\nStarting sniper (within {monitor_window} min window)...")
                sniper = AutoSniper(market)
                await sniper.monitor_and_snipe()
                
                if RUN_ONCE:
                    print("\nSingle run complete. Exiting.")
                    break
                
                print("\nSnipe complete. Looking for next market...")
                await asyncio.sleep(5)
            else:
                # Wait until we're within the monitoring window
                wait_seconds = (minutes_remaining - monitor_window) * 60
                wait_until = now_et + timedelta(seconds=wait_seconds)
                print(f"\nWaiting until {wait_until.strftime('%I:%M:%S %p ET')} to start monitoring...")
                print(f"  ({wait_seconds/60:.1f} minutes)")
                
                # If close to monitoring window (<2 min), wait the full time
                # Otherwise check every 60 seconds for new markets
                if wait_seconds <= 120:
                    print(f"  (sleeping {wait_seconds:.0f} seconds until monitoring starts)")
                    await asyncio.sleep(wait_seconds)
                else:
                    await asyncio.sleep(60)
                
        except KeyboardInterrupt:
            print("\n\nStopped by user.")
            break
        except Exception as e:
            print(f"Error: {e}")
            await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())