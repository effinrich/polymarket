# Polymarket Auto Sniper Bot

An automated trading bot for [Polymarket](https://polymarket.com) that scans for upcoming 15-minute BTC/ETH/SOL prediction markets and executes last-second trades using the Polymarket CLOB API.

> ⚠️ **IMPORTANT**: This bot trades real money (USDC on Polygon). Always start with `DRY_RUN = True` (the default) before enabling live trading. You can lose funds. Use at your own risk.

---

## Table of Contents

- [What This Does](#what-this-does)
- [How It Works](#how-it-works)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
  - [For Non-Coders](#for-non-coders-step-by-step)
  - [For Coders](#for-coders-quick-start)
- [Wallet Setup](#wallet-setup)
- [Polymarket API Credentials](#polymarket-api-credentials)
- [Configuration (.env)](#configuration-env)
- [Running the Bot](#running-the-bot)
- [Script Reference](#script-reference)
- [Safety & DRY RUN Mode](#safety--dry-run-mode)
- [Security](#security)
- [Troubleshooting](#troubleshooting)
- [Dependencies](#dependencies)
- [Disclaimer](#disclaimer)

---

## What This Does

This bot watches Polymarket for binary prediction markets that expire in ~15 minutes (e.g., "Will BTC be above $X at 2:15pm?"). In the final seconds before expiration, when the winning side is almost certain, it places a last-second Fill-or-Kill order at a high price (default: $0.99 on a $1 payout) to capture a small but near-certain profit.

**Risk**: The market can flip in the last second. This is a high-frequency, high-risk strategy.

---

## How It Works

```
scanner.py        → Finds active 15-min BTC/ETH/SOL markets on Polymarket
       ↓
sniper.py         → Monitors a specific market via WebSocket, fires at the end
       ↓
auto_sniper.py    → Combines both — scan → snipe → repeat, continuously
approve.py        → One-time setup: approves USDC spending on your wallet
```

---

## Prerequisites

You need the following installed on your computer before starting:

| Requirement | Version | Notes |
|-------------|---------|-------|
| **Python** | 3.10 or higher | The programming language this runs on |
| **pip** | Latest | Comes with Python — used to install packages |
| **A Polygon wallet** | — | MetaMask or any EVM wallet with a private key |
| **USDC on Polygon** | Enough to trade | The collateral token Polymarket uses |
| **A Polymarket account** | — | Must accept ToS at polymarket.com |

---

## Installation

### For Non-Coders (Step-by-Step)

#### Step 1: Install Python

1. Go to [python.org/downloads](https://www.python.org/downloads/)
2. Download the latest **Python 3.12** (or 3.10+) installer for your OS
3. **Windows**: Run the installer and check ✅ **"Add Python to PATH"** before clicking Install
4. **Mac**: Run the `.pkg` installer
5. Verify it worked — open a Terminal (Mac) or Command Prompt (Windows) and type:
   ```
   python --version
   ```
   You should see something like `Python 3.12.x`

#### Step 2: Download This Project

**Option A — Download ZIP** (easiest):
1. Click the green **Code** button on this GitHub page
2. Click **Download ZIP**
3. Unzip the folder somewhere easy to find (like your Desktop)

**Option B — Git clone** (if you have Git):
```bash
git clone https://github.com/effinrich/polymarket.git
cd polymarket
```

#### Step 3: Open a Terminal in the Project Folder

- **Windows**: Open the unzipped folder, click the address bar, type `cmd`, press Enter
- **Mac**: Right-click the folder in Finder → "New Terminal at Folder"

#### Step 4: Install Dependencies

Type this exactly and press Enter:
```bash
pip install -r requirements.txt
```

Wait for it to finish. You'll see packages downloading.

#### Step 5: Set Up Your Credentials

1. Create a file named `.env` in the project folder (see [Configuration](#configuration-env) below)
2. Fill in your wallet private key and Polymarket API credentials

#### Step 6: Approve USDC (One-Time Setup)

```bash
python approve.py
```

This grants the Polymarket contract permission to use your USDC. Run this once.

#### Step 7: Run the Bot

```bash
python auto_sniper.py
```

---

### For Coders (Quick Start)

```bash
git clone https://github.com/effinrich/polymarket.git
cd polymarket

# Create a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate        # Mac/Linux
.venv\Scripts\activate           # Windows

# Install dependencies
pip install -r requirements.txt

# Copy the env template and fill in your credentials
cp .env.example .env
nano .env   # or open in your editor

# One-time USDC approval
python approve.py

# Run the auto bot (DRY_RUN=True by default)
python auto_sniper.py
```

---

## Wallet Setup

You need a **Polygon (MATIC) wallet** with USDC.

1. **Get MetaMask**: [metamask.io](https://metamask.io) — install the browser extension
2. **Add Polygon network** to MetaMask:
   - Network Name: `Polygon Mainnet`
   - RPC URL: `https://polygon-rpc.com`
   - Chain ID: `137`
   - Currency Symbol: `MATIC`
3. **Get USDC on Polygon**: Bridge from Ethereum via [app.uniswap.org](https://app.uniswap.org) or buy directly on an exchange that supports Polygon withdrawals
4. **Export your Private Key** from MetaMask:
   - Account menu → Account Details → Export Private Key
   - ⚠️ Never share this with anyone

---

## Polymarket API Credentials

You need API credentials from Polymarket to place orders.

1. Go to [polymarket.com](https://polymarket.com) and connect your wallet
2. Accept the Terms of Service
3. Run `python approve.py` — it will print your API credentials:
   ```
   API Key: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
   Secret: your-secret-here
   Passphrase: your-passphrase-here
   ```
4. Copy these into your `.env` file

---

## Configuration (.env)

Create a file named `.env` in the project root with the following content:

```env
# Your Polygon wallet private key (from MetaMask → Export Private Key)
# WARNING: Keep this secret. Never commit this file to git.
PRIVATE_KEY=your_private_key_here

# Polymarket CLOB API credentials (generated by running approve.py)
CLOB_API_KEY=your_api_key_here
CLOB_SECRET=your_api_secret_here
CLOB_PASSPHRASE=your_api_passphrase_here

# --- Used automatically by sniper.py (set by auto_sniper.py) ---
# CONDITION_ID=
# YES_TOKEN_ID=
# NO_TOKEN_ID=
# END_TIME_ISO=
```

> ⚠️ **Never commit your `.env` file to GitHub.** Make sure `.env` is listed in your `.gitignore` file.

---

## Running the Bot

### Option 1: Fully Automated (Recommended)

```bash
python auto_sniper.py
```

Scans for markets, waits for the right moment, fires, then scans again. Runs continuously until you stop it with `Ctrl+C`.

### Option 2: Manual — Scan First, Then Snipe

**Step 1** — Find a market:
```bash
python scanner.py
```

This prints out the market details and a config block. Copy the values.

**Step 2** — Set them in your `.env`:
```env
CONDITION_ID=0xabc123...
YES_TOKEN_ID=123456...
NO_TOKEN_ID=789012...
END_TIME_ISO=2026-01-26T14:15:00-05:00
```

**Step 3** — Run the sniper:
```bash
python sniper.py
```

### Option 3: One-Time USDC Approval Only

```bash
python approve.py
```

---

## Script Reference

| Script | Purpose |
|--------|---------|
| `approve.py` | One-time: approves USDC allowance on Polygon. Also prints your API credentials. |
| `scanner.py` | Queries Polymarket for active 15-minute BTC/ETH markets expiring within 20 minutes. |
| `sniper.py` | Monitors a single market via WebSocket; fires a FOK order in the final seconds. |
| `auto_sniper.py` | Orchestrates scanner + sniper in a continuous loop. Main entry point. |

---

## Safety & DRY RUN Mode

**DRY RUN is enabled by default.** The bot will simulate everything without spending real money.

To enable live trading, open `auto_sniper.py` (or `sniper.py`) and change:

```python
DRY_RUN = True   # ← Change to False for live trading
```

**Other configurable settings in `auto_sniper.py`:**

```python
BUY_PRICE = 0.99              # Price to pay per share (max $1 payout)
BUY_AMOUNT = 10               # USDC to spend per trade
TRIGGER_SECONDS = 1           # Seconds before close to fire
MIN_WIN_PROBABILITY = 0.50    # Minimum implied probability to trade
MONITOR_WINDOW_MINUTES = 5    # How early to start monitoring
```

---

## Security

### CRITICAL: Protect Your Private Key

- Your `.env` file contains your **wallet private key** — anyone who has it can drain your wallet
- **Never** commit `.env` to GitHub
- Add `.env` to your `.gitignore` file:
  ```
  .env
  .env.local
  *.env
  ```
- If you have already committed your `.env` to a public repo, **rotate your credentials immediately**:
  1. Transfer any funds out of the exposed wallet to a new wallet
  2. Generate new Polymarket API credentials by running `approve.py` with your new wallet
  3. Remove the `.env` file from git history using [BFG Repo-Cleaner](https://rtyley.github.io/bfg-repo-cleaner/) or:
     ```bash
     git filter-branch --force --index-filter \
       "git rm --cached --ignore-unmatch .env .env.local" \
       --prune-empty --tag-name-filter cat -- --all
     git push origin --force --all
     ```

---

## Troubleshooting

### `pip: command not found`
Try `pip3` instead of `pip`, or:
```bash
python -m pip install -r requirements.txt
```

### `python: command not found`
Try `python3` instead of `python`. On Windows, make sure you checked **"Add to PATH"** during Python installation.

### `ModuleNotFoundError`
You forgot to install dependencies. Run:
```bash
pip install -r requirements.txt
```

### `Error: PRIVATE_KEY not found in .env file`
Make sure your `.env` file exists in the same folder as the scripts and has `PRIVATE_KEY=` filled in with your actual key (no quotes needed).

### `No markets found`
The scanner only finds markets within a 20-minute expiration window. 15-minute markets open at the top and bottom of each hour. Try again closer to the :00 or :30 mark.

### Bot fires but order is rejected
- Make sure you ran `approve.py` first (USDC allowance required)
- Make sure you have enough USDC in your wallet (at least `BUY_AMOUNT` + a small amount of MATIC for gas)
- Confirm you are on Polygon Mainnet (chain ID 137), not Ethereum or a testnet

### Orders fail on live trading after DRY_RUN worked
Make sure `CLOB_API_KEY`, `CLOB_SECRET`, and `CLOB_PASSPHRASE` are all set in `.env`. Run `approve.py` again to regenerate them if needed.

---

## Dependencies

```
py-clob-client>=0.17.0   # Polymarket order execution SDK
python-dotenv>=1.0.0     # Load .env config
aiohttp>=3.9.0           # Async HTTP
websockets>=12.0         # Real-time order book feed
httpx>=0.27.0            # Sync/async HTTP client
tzdata>=2024.1           # Timezone data
```

Install all with:
```bash
pip install -r requirements.txt
```

---

## Disclaimer

This software is provided for educational purposes only. Trading prediction markets involves substantial risk of financial loss. Past performance does not guarantee future results. The authors are not responsible for any financial losses incurred through use of this software. Always test thoroughly in DRY_RUN mode before enabling live trading.
