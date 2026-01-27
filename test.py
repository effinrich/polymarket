from py_clob_client.client import ClobClient
import os
from dotenv import load_dotenv

load_dotenv()

host = "https://clob.polymarket.com"
chain_id = 137 # Polygon mainnet
private_key = os.getenv("PRIVATE_KEY")

print("private key: ", private_key)
client = ClobClient(
    host=host,
    chain_id=chain_id,
    key=private_key  # Signer enables L1 methods
)

# Gets API key, or else creates
api_creds = client.create_or_derive_api_creds()

# api_creds = {
#     "apiKey": "550e8400-e29b-41d4-a716-446655440000",
#     "secret": "base64EncodedSecretString",
#     "passphrase": "randomPassphraseString"
# }