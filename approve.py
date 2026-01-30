"""
Approve USDC for trading on Polymarket using py-clob-client.
"""

import os
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, AssetType, BalanceAllowanceParams

# Load environment variables from .env file
load_dotenv()


PRIVATE_KEY = os.getenv("PRIVATE_KEY")

# 1. Instantiate the client (create an object)
client = ClobClient(
    host="https://clob.polymarket.com",
    key=PRIVATE_KEY,
    chain_id=137  # e.g., Polygon Mainnet
)


# Get existing API key, or create one if none exists
user_api_creds = client.create_or_derive_api_creds()

# print(user_api_creds)
print("API Key:", user_api_creds.api_key)
print("Secret:", user_api_creds.api_secret)
print("Passphrase:", user_api_creds.api_passphrase)

# Configuration
# PRIVATE_KEY = os.getenv("PRIVATE_KEY")
CLOB_API_KEY = user_api_creds.api_key
CLOB_SECRET = user_api_creds.api_secret
CLOB_PASSPHRASE = user_api_creds.api_passphrase

# Polymarket CLOB API endpoint for Polygon Mainnet
HOST = "https://clob.polymarket.com"
CHAIN_ID = 137  # Polygon Mainnet


def main():
    if not PRIVATE_KEY:
        print("Error: PRIVATE_KEY not found in .env file")
        return

    try:
        # Initialize ClobClient with API credentials if available
        if CLOB_API_KEY and CLOB_SECRET and CLOB_PASSPHRASE:
            creds = ApiCreds(
                api_key=CLOB_API_KEY,
                api_secret=CLOB_SECRET,
                api_passphrase=CLOB_PASSPHRASE,
            )
            client = ClobClient(
                host=HOST,
                key=PRIVATE_KEY,
                chain_id=CHAIN_ID,
                creds=creds,
            )
        else:
            # Initialize without API credentials (for allowance operations)
            client = ClobClient(
                host=HOST,
                key=PRIVATE_KEY,
                chain_id=CHAIN_ID,
            )

        # Approve USDC for the exchange to spend
        print("Setting USDC allowance for Polymarket exchange...")
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        result = client.update_balance_allowance(params)

        print("Success")
        if result:
            print(f"Transaction result: {result}")

    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    main()
