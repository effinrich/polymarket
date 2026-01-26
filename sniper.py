"""
High-frequency trading sniper for Polymarket 15-minute markets.
Monitors order book via WebSocket and executes FOK orders in final second.
"""

import asyncio
import json
import os
from datetime import datetime, timezone

import websockets
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType

# Load environment variables
load_dotenv()

# =============================================================================
# CONFIGURATION - Set these before running
# =============================================================================

# DRY RUN MODE - Set to False to execute real trades
DRY_RUN = True

# Target market configuration (set these from scanner.py output)
CONDITION_ID = ""  # e.g., "0x..."
YES_TOKEN_ID = ""  # Token ID for YES outcome
NO_TOKEN_ID = ""   # Token ID for NO outcome
END_TIME_ISO = ""  # e.g., "2026-01-26T20:00:00+00:00"

# Trading parameters
BUY_PRICE = 0.99      # Maximum price to pay
BUY_AMOUNT = 10.0     # USDC amount to spend
TRIGGER_SECONDS = 1   # Trigger when <= this many seconds remain

# =============================================================================
# API Configuration
# =============================================================================

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
CLOB_API_KEY = os.getenv("CLOB_API_KEY")
CLOB_SECRET = os.getenv("CLOB_SECRET")
CLOB_PASSPHRASE = os.getenv("CLOB_PASSPHRASE")

CLOB_HOST = "https://clob.polymarket.com"
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
CHAIN_ID = 137  # Polygon Mainnet


class MarketSniper:
    """Monitors order book and snipes winning side in final second."""

    def __init__(self):
        self.yes_best_ask = None
        self.no_best_ask = None
        self.order_executed = False
        self.client = None
        self.end_time = None

    def initialize_client(self):
        """Initialize the CLOB client with credentials."""
        if not PRIVATE_KEY:
            raise ValueError("PRIVATE_KEY not found in .env")

        creds = None
        if CLOB_API_KEY and CLOB_SECRET and CLOB_PASSPHRASE:
            creds = ApiCreds(
                api_key=CLOB_API_KEY,
                api_secret=CLOB_SECRET,
                api_passphrase=CLOB_PASSPHRASE,
            )

        self.client = ClobClient(
            host=CLOB_HOST,
            key=PRIVATE_KEY,
            chain_id=CHAIN_ID,
            creds=creds,
        )
        print("[INIT] CLOB client initialized")

    def parse_end_time(self):
        """Parse the market end time."""
        if not END_TIME_ISO:
            raise ValueError("END_TIME_ISO not configured")
        
        end_str = END_TIME_ISO.replace("Z", "+00:00")
        self.end_time = datetime.fromisoformat(end_str)
        if self.end_time.tzinfo is None:
            self.end_time = self.end_time.replace(tzinfo=timezone.utc)
        
        print(f"[INIT] Market ends at: {self.end_time.isoformat()}")

    def get_seconds_remaining(self):
        """Calculate seconds until market closes."""
        now = datetime.now(timezone.utc)
        delta = (self.end_time - now).total_seconds()
        return delta

    def determine_winning_side(self):
        """
        Determine the winning side based on best ask prices.
        Winning side = the side with implied probability > 50% (price > 0.50).
        Returns: ('YES', token_id, best_ask) or ('NO', token_id, best_ask) or None
        """
        yes_price = self.yes_best_ask
        no_price = self.no_best_ask

        # If YES is winning (price > 0.50 means market thinks YES is likely)
        if yes_price and yes_price > 0.50:
            return ("YES", YES_TOKEN_ID, yes_price)
        
        # If NO is winning
        if no_price and no_price > 0.50:
            return ("NO", NO_TOKEN_ID, no_price)

        return None

    async def execute_order(self, side: str, token_id: str, price: float):
        """Execute a Fill-or-Kill order."""
        if self.order_executed:
            return

        self.order_executed = True
        
        print(f"\n{'='*60}")
        print(f"[TRIGGER] Executing {side} order!")
        print(f"  Token ID: {token_id}")
        print(f"  Price: ${price:.4f}")
        print(f"  Amount: ${BUY_AMOUNT}")
        print("  Order Type: FOK (Fill or Kill)")
        print(f"{'='*60}\n")

        if DRY_RUN:
            print("[DRY RUN] WOULD BUY - No real order placed")
            return

        try:
            # Create FOK order for the winning side
            order_args = OrderArgs(
                token_id=token_id,
                price=BUY_PRICE,
                size=BUY_AMOUNT / BUY_PRICE,  # Convert USDC to shares
                side="BUY",
                order_type=OrderType.FOK,  # Fill or Kill
            )

            result = self.client.create_and_post_order(order_args)
            print(f"[ORDER] Result: {result}")

        except Exception as e:
            print(f"[ERROR] Order failed: {e}")

    def process_orderbook_update(self, data: dict):
        """Process Level 1 order book update from WebSocket."""
        asset_id = data.get("asset_id")
        
        # Extract best ask from the order book
        asks = data.get("asks", [])
        if asks:
            # Asks are sorted, first one is best (lowest)
            best_ask = float(asks[0].get("price", 0))
        else:
            best_ask = None

        # Update the appropriate side
        if asset_id == YES_TOKEN_ID:
            self.yes_best_ask = best_ask
        elif asset_id == NO_TOKEN_ID:
            self.no_best_ask = best_ask

    async def subscribe_to_market(self, ws):
        """Subscribe to order book updates for both tokens."""
        # Subscribe to YES token
        if YES_TOKEN_ID:
            sub_msg = {
                "type": "subscribe",
                "channel": "book",
                "market": YES_TOKEN_ID,
            }
            await ws.send(json.dumps(sub_msg))
            print(f"[WS] Subscribed to YES token: {YES_TOKEN_ID[:20]}...")

        # Subscribe to NO token
        if NO_TOKEN_ID:
            sub_msg = {
                "type": "subscribe",
                "channel": "book",
                "market": NO_TOKEN_ID,
            }
            await ws.send(json.dumps(sub_msg))
            print(f"[WS] Subscribed to NO token: {NO_TOKEN_ID[:20]}...")

    async def monitor_loop(self):
        """Main monitoring loop using WebSocket."""
        print(f"\n[START] Sniper active - DRY_RUN={DRY_RUN}")
        print(f"[CONFIG] Trigger at <= {TRIGGER_SECONDS}s remaining")
        print(f"[CONFIG] Buy price: ${BUY_PRICE}, Amount: ${BUY_AMOUNT}")
        print("-" * 60)

        async with websockets.connect(WS_URL) as ws:
            await self.subscribe_to_market(ws)

            # Create task to receive messages
            async def receive_messages():
                async for message in ws:
                    try:
                        data = json.loads(message)
                        msg_type = data.get("type", "")
                        
                        if msg_type == "book":
                            self.process_orderbook_update(data)
                        elif msg_type == "error":
                            print(f"[WS ERROR] {data}")
                    except json.JSONDecodeError:
                        pass

            # Start receiving messages in background
            receive_task = asyncio.create_task(receive_messages())

            try:
                # Main monitoring loop
                while not self.order_executed:
                    seconds_remaining = self.get_seconds_remaining()

                    # Market has ended
                    if seconds_remaining <= 0:
                        print("[END] Market closed - no order executed")
                        break

                    # Display status periodically
                    if seconds_remaining <= 30:
                        winning = self.determine_winning_side()
                        status = f"YES=${self.yes_best_ask or 0:.3f} NO=${self.no_best_ask or 0:.3f}"
                        winner_str = f"{winning[0]}@${winning[2]:.3f}" if winning else "N/A"
                        print(f"[{seconds_remaining:5.1f}s] {status} | Winning: {winner_str}")

                    # TRIGGER CONDITION: <= 1 second remaining but > 0
                    if 0 < seconds_remaining <= TRIGGER_SECONDS:
                        winning = self.determine_winning_side()
                        
                        if winning:
                            side, token_id, best_ask = winning
                            
                            # Safety check: winning side available below $0.99
                            if best_ask < BUY_PRICE:
                                await self.execute_order(side, token_id, best_ask)
                            else:
                                print(f"[SKIP] {side} price ${best_ask:.3f} >= ${BUY_PRICE}")
                        else:
                            print("[SKIP] No clear winning side (both <= 0.50)")

                    # Adaptive sleep: faster as we approach trigger
                    if seconds_remaining > 10:
                        await asyncio.sleep(1.0)
                    elif seconds_remaining > 2:
                        await asyncio.sleep(0.1)
                    else:
                        await asyncio.sleep(0.01)  # 10ms resolution in final seconds

            finally:
                receive_task.cancel()
                try:
                    await receive_task
                except asyncio.CancelledError:
                    pass

    async def run(self):
        """Main entry point."""
        # Validate configuration
        if not CONDITION_ID or not YES_TOKEN_ID or not NO_TOKEN_ID or not END_TIME_ISO:
            print("[ERROR] Please configure CONDITION_ID, YES_TOKEN_ID, NO_TOKEN_ID, and END_TIME_ISO")
            print("        Run scanner.py first to get these values.")
            return

        self.initialize_client()
        self.parse_end_time()

        seconds_remaining = self.get_seconds_remaining()
        if seconds_remaining <= 0:
            print("[ERROR] Market has already ended")
            return

        print(f"[INFO] {seconds_remaining:.1f} seconds until market closes")
        await self.monitor_loop()


async def main():
    sniper = MarketSniper()
    await sniper.run()


if __name__ == "__main__":
    print("""
╔═══════════════════════════════════════════════════════════════╗
║           POLYMARKET 15-MINUTE MARKET SNIPER                  ║
╠═══════════════════════════════════════════════════════════════╣
║  1. Run scanner.py to find active market                      ║
║  2. Copy CONDITION_ID, YES_TOKEN_ID, NO_TOKEN_ID, END_TIME    ║
║  3. Set DRY_RUN = False when ready for real trades            ║
╚═══════════════════════════════════════════════════════════════╝
""")
    asyncio.run(main())
