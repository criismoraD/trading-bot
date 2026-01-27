
import json
import os
import time
from paper_trading import PaperTradingAccount, Position, OrderSide, PositionSide

print("--- DEBUG SIMULATION ---")

# 1. Config
INITIAL_BALANCE = 1000
GLOBAL_TP = 0.5
TARGET_EQUITY = INITIAL_BALANCE + GLOBAL_TP

# 2. Setup Account
account = PaperTradingAccount(initial_balance=INITIAL_BALANCE, leverage=50)

# 3. Inject a winning position
# Entry: 100, Current: 90 (Short) -> Profit 10 per unit
# Margin: 10
# Qty: (10 * 50) / 100 = 5
# PnL: (100 - 90) * 5 = 50 USD
# Expected Equity: 1000 + 50 = 1050

print("Injecting winning position...")
pos = Position(
    symbol="BTCUSDT",
    side=PositionSide.SHORT,
    entry_price=100.0,
    quantity=5.0,
    margin=10.0,
    leverage=50,
    take_profit=80.0,
    stop_loss=110.0,
    current_price=90.0
)
account.open_positions["debug_id"] = pos

# 4. Create Price Cache
price_cache = {"BTCUSDT": 90.0}

# 5. Calculate Equity Logic (Exact copy from bot.py)
current_equity = account.get_margin_balance(price_cache)
target_equity = account.initial_balance + GLOBAL_TP

print(f"Current Equity: {current_equity}")
print(f"Target Equity: {target_equity}")

check = current_equity >= target_equity
print(f"Condition Met: {check}")

if check:
    print("LOGIC VERIFIED: The code correctly identifies the condition.")
else:
    print("LOGIC FAILED: The code failed to identify the condition.")

# 6. Check Config Read Logic
print("\n--- CHECKING CONFIG READ ---")
try:
    with open('shared_config.json', 'r') as f:
        sh_cfg = json.load(f)
        read_val = sh_cfg.get('trading', {}).get('global_take_profit_usd', 0.0)
        print(f"Read from file: {read_val}")
except Exception as e:
    print(f"Read failed: {e}")
