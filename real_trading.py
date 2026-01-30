"""
Real Trading Module for Bybit Futures (Testnet/Mainnet)
Uses pybit library to interact with Bybit V5 API
Mirrors PaperTradingAccount interface for seamless switching
"""
import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum

from pybit.unified_trading import HTTP
from logger import trading_logger as logger, log_trade

# Bybit Fee Rates (same as paper trading for consistency)
MAKER_FEE = 0.0002  # 0.02%
TAKER_FEE = 0.00055 # 0.055%


class OrderType(Enum):
    MARKET = "Market"
    LIMIT = "Limit"


class OrderSide(Enum):
    BUY = "Buy"
    SELL = "Sell"


class OrderStatus(Enum):
    PENDING = "New"
    FILLED = "Filled"
    CANCELLED = "Cancelled"
    PARTIALLY_FILLED = "PartiallyFilled"


class PositionSide(Enum):
    LONG = "Buy"
    SHORT = "Sell"


@dataclass
class RealPosition:
    """Represents an open position on Bybit"""
    symbol: str
    side: PositionSide
    entry_price: float
    quantity: float
    margin: float
    leverage: int
    take_profit: float
    stop_loss: Optional[float] = None
    order_id: str = ""
    unrealized_pnl: float = 0.0
    strategy_case: int = 0
    fib_high: Optional[float] = None
    fib_low: Optional[float] = None
    entry_fib_level: Optional[float] = None
    creation_fib_level: Optional[float] = None
    opened_at: str = ""
    created_at: str = ""
    bybit_order_id: str = ""  # Bybit's order ID
    
    def calculate_pnl(self, current_price: float) -> float:
        """Calculate unrealized PnL"""
        if self.side == PositionSide.SHORT:
            pnl = (self.entry_price - current_price) * self.quantity
        else:
            pnl = (current_price - self.entry_price) * self.quantity
        self.unrealized_pnl = pnl
        return pnl


class RealTradingAccount:
    """Real Trading Account using Bybit API - mirrors PaperTradingAccount interface"""
    
    def __init__(self, api_key: str, api_secret: str, testnet: bool = False,
                 demo: bool = True, leverage: int = 10, trades_file: str = "trades_real.json"):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.demo = demo
        self.leverage = leverage
        self.trades_file = trades_file
        
        # Load initial balance from config
        self.initial_balance = 1000.0
        try:
             with open('shared_config.json', 'r') as f:
                config = json.load(f)
                self.initial_balance = float(config.get('trading', {}).get('initial_balance', 1000.0))
        except Exception as e:
            logger.error(f"Error loading initial_balance: {e}")
        
        # Initialize Bybit client
        # demo=True uses api-demo.bybit.com (Bybit Demo Trading)
        # testnet=True uses api-testnet.bybit.com (old Testnet)
        self.session = HTTP(
            testnet=testnet,
            demo=demo,
            api_key=api_key,
            api_secret=api_secret
        )
        
        # Local tracking (mirrors paper trading)
        self.open_positions: Dict[str, RealPosition] = {}
        self.pending_orders: Dict[str, dict] = {}  # order_id -> order info
        self.trade_history: List[dict] = []
        self.trade_history: List[dict] = []
        self.cancelled_history: List[dict] = []
        self.equity_history: List[dict] = []
        
        # Stats
        self.stats = {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "cancelled_orders": 0
        }
        
        # Balance tracking
        self._initial_balance = None
        self.balance = 0.0
        self.available_margin = 0.0  # Cache for available margin
        self._last_sync = 0  # Timestamp of last sync
        self._sync_interval = 5  # Seconds between syncs
        
        # Price cache
        self.price_cache: Dict[str, float] = {}
        
        # Load existing trades
        self._load_trades()
        
        # Force sync with Bybit on startup (ignore cache)
        self._last_sync = 0
        self._sync_account()
        self._sync_pending_orders()
        
        # Save synced state to JSON
        self._save_trades()
        
        logger.info(f"🔗 Bybit {'Demo' if demo else 'Testnet' if testnet else 'MAINNET'} connection initialized")
        if self.open_positions:
            logger.info(f"📊 Loaded {len(self.open_positions)} existing positions from Bybit")
    
    def _sync_account(self):
        """Sync local state with Bybit account (with caching)"""
        import time
        now = time.time()
        
        # Skip if synced recently (unless forced)
        if now - self._last_sync < self._sync_interval:
            return
        
        try:
            # Get account balance
            balance_info = self.session.get_wallet_balance(accountType="UNIFIED")
            if balance_info.get("retCode") == 0:
                account_data = balance_info.get("result", {}).get("list", [{}])[0]
                # Get total equity and available margin from account level
                self.available_margin = float(account_data.get("totalAvailableBalance", 0))
                
                coins = account_data.get("coin", [])
                for coin in coins:
                    if coin.get("coin") == "USDT":
                        self.balance = float(coin.get("walletBalance", 0))
                        if self._initial_balance is None:
                            self._initial_balance = self.balance
                        break
            
            # Get open positions
            positions = self.session.get_positions(category="linear", settleCoin="USDT")
            if positions.get("retCode") == 0:
                # Sync positions: Merge API data with Local Metadata
                active_symbols_api = set()
                
                for pos in positions.get("result", {}).get("list", []):
                    size = float(pos.get("size", 0))
                    if size > 0:
                        symbol = pos.get("symbol")
                        active_symbols_api.add(symbol)
                        
                        side_str = pos.get("side") # Buy or Sell
                        side = PositionSide.LONG if side_str == "Buy" else PositionSide.SHORT
                        
                        # Preserve metadata from local state if exists
                        existing_pos = self.open_positions.get(symbol)
                        
                        # Fix Case Loss: If not in open_positions, check if it was a pending order
                        if not existing_pos:
                             # Look for a pending order with same symbol
                             for p_ord in list(self.pending_orders.values()):
                                 if p_ord.get("symbol") == symbol:
                                     # Found it! It was likely filled/modified externally
                                     existing_pos = RealPosition(
                                         symbol=symbol,
                                         side=PositionSide.SHORT, # Placeholder, will be overwritten
                                         entry_price=0,
                                         quantity=0,
                                         margin=0,
                                         leverage=0,
                                         take_profit=0,
                                         strategy_case=p_ord.get("strategy_case", 0),
                                         fib_high=p_ord.get("fib_high"),
                                         fib_low=p_ord.get("fib_low"),
                                         entry_fib_level=p_ord.get("entry_fib_level"),
                                         opened_at=datetime.now(timezone.utc).isoformat(),
                                         order_id=f"RECOVERED-{symbol}"
                                     )
                                     break
                        
                        strategy_case = existing_pos.strategy_case if existing_pos else 0
                        fib_high = existing_pos.fib_high if existing_pos else None
                        fib_low = existing_pos.fib_low if existing_pos else None
                        entry_fib = existing_pos.entry_fib_level if existing_pos else None
                        opened_at = existing_pos.opened_at if existing_pos else datetime.now(timezone.utc).isoformat()
                        order_id = existing_pos.order_id if existing_pos else f"BYBIT-{symbol}"
                        
                        # Update/Create position object
                        self.open_positions[symbol] = RealPosition(
                            symbol=symbol,
                            side=side,
                            entry_price=float(pos.get("avgPrice", 0)),
                            quantity=size,
                            margin=float(pos.get("positionIM", 0)),
                            leverage=int(pos.get("leverage", self.leverage)),
                            take_profit=float(pos.get("takeProfit", 0)) or 0.0,
                            stop_loss=float(pos.get("stopLoss", 0)) or None,
                            unrealized_pnl=float(pos.get("unrealisedPnl", 0)),
                            strategy_case=strategy_case,
                            fib_high=fib_high,
                            fib_low=fib_low,
                            entry_fib_level=entry_fib,
                            opened_at=opened_at,
                            # created_at no viene de bybit
                            order_id=order_id
                        )

                # Remove locally closed positions (not in API anymore)
                for symbol in list(self.open_positions.keys()):
                    if symbol not in active_symbols_api:
                        # Position closed externally?
                        logger.info(f"Position {symbol} not found in Bybit, recording as closed")
                        # Try to get close price from cache or use entry price as fallback
                        close_price = self.price_cache.get(symbol, self.open_positions[symbol].entry_price)
                        # We use the order_id key which is stored in the dictionary
                        # Note: open_positions uses symbol as key based on line 221, but let's verify usage in loop
                        # logic at line 241 says: for symbol in list(self.open_positions.keys())
                        # So the key is the symbol.
                        # But wait, _record_closed_position expects 'order_id' as the key if open_positions uses order_id? 
                        # Let's check how open_positions is keyed.
                        # Line 221: self.open_positions[symbol] = ... 
                        # So it is keyed by symbol.
                        # However, _record_closed_position implementation (line 626) takes 'order_id'.
                        # Line 628 checks: if order_id not in self.open_positions: return
                        # So _record_closed_position expects the dictionary key.
                        # Since the dictionary key is 'symbol' (from line 221), we should pass 'symbol' as the first arg.
                        self._record_closed_position(symbol, close_price, "Closed Externally/TP/SL")
            
            logger.info(f"💰 Balance: ${self.balance:.2f} | Available: ${self.available_margin:.2f} | Open: {len(self.open_positions)}")
            self._last_sync = now
            
        except Exception as e:
            logger.error(f"❌ Failed to sync with Bybit: {e}")
    
    def _force_sync(self):
        """Force an immediate sync regardless of cache"""
        self._last_sync = 0
        self._sync_account()
    
    def _load_trades(self):
        """Load trade history and active states from file"""
        try:
            if os.path.exists(self.trades_file):
                with open(self.trades_file, 'r') as f:
                    data = json.load(f)
                    self.trade_history = data.get("trade_history", [])
                    self.cancelled_history = data.get("cancelled_history", [])
                    self.equity_history = data.get("equity_history", [])
                    self.stats = data.get("stats", self.stats)
                    
                    # Load open positions
                    positions_data = data.get("open_positions", {})
                    for symbol, pos_data in positions_data.items():
                        # Reconstruct RealPosition object
                        try:
                            side_str = pos_data.get("side", "Buy")
                            side_enum = PositionSide.LONG if side_str == "Buy" else PositionSide.SHORT
                            
                            self.open_positions[symbol] = RealPosition(
                                symbol=pos_data.get("symbol"),
                                side=side_enum,
                                entry_price=pos_data.get("entry_price"),
                                quantity=pos_data.get("quantity"),
                                margin=pos_data.get("margin"),
                                leverage=pos_data.get("leverage"),
                                take_profit=pos_data.get("take_profit"),
                                stop_loss=pos_data.get("stop_loss"),
                                order_id=pos_data.get("order_id"),
                                unrealized_pnl=pos_data.get("unrealized_pnl", 0),
                                strategy_case=pos_data.get("strategy_case", 0),
                                fib_high=pos_data.get("fib_high"),
                                fib_low=pos_data.get("fib_low"),
                                entry_fib_level=pos_data.get("entry_fib_level"),
                                opened_at=pos_data.get("opened_at"),
                                bybit_order_id=pos_data.get("bybit_order_id", "")
                            )
                        except Exception as e:
                            logger.error(f"Error restoring position {symbol}: {e}")

                    # Load pending orders
                    self.pending_orders = data.get("pending_orders", {})
                    
        except Exception as e:
            logger.warning(f"Could not load trades file: {e}")
    
    def _save_trades(self):
        """Save trade history to file"""
        try:
            data = {
                "trade_history": self.trade_history,
                "cancelled_history": self.cancelled_history,
                "stats": self.stats,
                "balance": self.balance,
                "open_positions": {k: self._serialize_position(v) for k, v in self.open_positions.items()},
                "pending_orders": self.pending_orders
            }
            with open(self.trades_file, 'w') as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Failed to save trades: {e}")
    
    def _serialize_position(self, pos: RealPosition) -> dict:
        """Serialize position to dict"""
        return {
            "symbol": pos.symbol,
            "side": pos.side.value if hasattr(pos.side, 'value') else pos.side,
            "entry_price": pos.entry_price,
            "quantity": pos.quantity,
            "margin": pos.margin,
            "leverage": pos.leverage,
            "take_profit": pos.take_profit,
            "stop_loss": pos.stop_loss,
            "order_id": pos.order_id,
            "unrealized_pnl": pos.unrealized_pnl,
            "strategy_case": pos.strategy_case,
            "fib_high": pos.fib_high,
            "fib_low": pos.fib_low,
            "entry_fib_level": pos.entry_fib_level,
            "opened_at": pos.opened_at,
            "bybit_order_id": pos.bybit_order_id
        }
    
    def _set_leverage(self, symbol: str):
        """Set leverage for a symbol (uses max available if config exceeds limit)"""
        # Cache for max leverage per symbol
        if not hasattr(self, '_leverage_cache'):
            self._leverage_cache = {}
        
        # Get max leverage if not cached
        if symbol not in self._leverage_cache:
            try:
                info = self.session.get_instruments_info(category="linear", symbol=symbol)
                if info.get("retCode") == 0:
                    instruments = info.get("result", {}).get("list", [])
                    if instruments:
                        max_lev = float(instruments[0].get("leverageFilter", {}).get("maxLeverage", self.leverage))
                        self._leverage_cache[symbol] = int(max_lev)
                        logger.debug(f"{symbol} max leverage: {max_lev}x")
            except Exception as e:
                self._leverage_cache[symbol] = self.leverage
        
        # Use minimum of configured and max available
        max_available = self._leverage_cache.get(symbol, self.leverage)
        effective_leverage = min(self.leverage, max_available)
        
        try:
            self.session.set_leverage(
                category="linear",
                symbol=symbol,
                buyLeverage=str(effective_leverage),
                sellLeverage=str(effective_leverage)
            )
            if effective_leverage < self.leverage:
                logger.info(f"⚙️ {symbol}: Using {effective_leverage}x (max available)")
        except Exception as e:
            # Leverage might already be set
            pass
        
        return effective_leverage
    
    def get_available_margin(self) -> float:
        """Get available margin for new trades (uses cached value)"""
        # Sync if needed (respects cache interval)
        self._sync_account()
        return self.available_margin
    
    def get_unrealized_pnl(self, current_prices: Dict[str, float] = None) -> float:
        """Get total unrealized PnL"""
        total_pnl = 0.0
        for pos in self.open_positions.values():
            if current_prices and pos.symbol in current_prices:
                pos.calculate_pnl(current_prices[pos.symbol])
            total_pnl += pos.unrealized_pnl
        return total_pnl
    
    def get_margin_balance(self, current_prices: Dict[str, float] = None) -> float:
        """Get margin balance (balance + unrealized PnL)"""
        return self.balance + self.get_unrealized_pnl(current_prices)
    
    def place_limit_order(self, symbol: str, side: OrderSide, price: float,
                          margin: float, take_profit: float, stop_loss: Optional[float] = None,
                          strategy_case: int = 0,
                          fib_high: Optional[float] = None,
                          fib_low: Optional[float] = None,
                          entry_fib_level: Optional[float] = None,
                          current_price: Optional[float] = None,
                          estimated_commission: float = 0.0) -> Optional[dict]:
        """Place a limit order on Bybit"""
        from config import MIN_AVAILABLE_MARGIN
        
        if self.get_available_margin() < MIN_AVAILABLE_MARGIN:
            print(f"⚠️ Insufficient margin for {symbol}")
            return None
        
        # Set leverage
        self._set_leverage(symbol)
        
        # Calculate quantity
        notional_value = margin * self.leverage
        quantity = notional_value / price
        
        # Round quantity to valid precision
        quantity = self._round_qty(symbol, quantity)
        price = self._round_price(symbol, price)
        take_profit = self._round_price(symbol, take_profit)
        if stop_loss:
            stop_loss = self._round_price(symbol, stop_loss)
        
        try:
            # Place order with TP/SL
            order_params = {
                "category": "linear",
                "symbol": symbol,
                "side": side.value,
                "orderType": "Limit",
                "qty": str(quantity),
                "price": str(price),
                "timeInForce": "GTC",
                "takeProfit": str(take_profit),
                "tpTriggerBy": "LastPrice",
                "tpslMode": "Full",
                "positionIdx": 0  # One-way mode
            }
            
            if stop_loss:
                order_params["stopLoss"] = str(stop_loss)
                order_params["slTriggerBy"] = "LastPrice"
            
            result = self.session.place_order(**order_params)
            
            if result.get("retCode") == 0:
                order_id = result.get("result", {}).get("orderId")
                
                # Track locally
                order_info = {
                    "id": order_id,
                    "symbol": symbol,
                    "side": side.value,
                    "price": price,
                    "quantity": quantity,
                    "margin": margin,
                    "take_profit": take_profit,
                    "stop_loss": stop_loss,
                    "strategy_case": strategy_case,
                    "fib_high": fib_high,
                    "fib_low": fib_low,
                    "entry_fib_level": entry_fib_level,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "status": "PENDING"
                }
                self.pending_orders[order_id] = order_info
                self._save_trades()
                
                sl_text = f" | SL: ${stop_loss:.4f}" if stop_loss else ""
                print(f"📝 REAL Limit Order: {side.value} {symbol} @ ${price:.4f} | TP: ${take_profit:.4f}{sl_text}")
                logger.info(f"Bybit Order placed: {order_id}")
                
                return order_info
            else:
                error_msg = result.get("retMsg", "Unknown error")
                logger.error(f"❌ Order failed: {error_msg}")
                print(f"❌ Order failed: {error_msg}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Order exception: {e}")
            print(f"❌ Order exception: {e}")
            return None
    
    def place_market_order(self, symbol: str, side: OrderSide, current_price: float,
                           margin: float, take_profit: float, stop_loss: Optional[float] = None,
                           strategy_case: int = 0,
                           fib_high: Optional[float] = None,
                           fib_low: Optional[float] = None,
                           entry_fib_level: Optional[float] = None,
                           estimated_commission: float = 0.0) -> Optional[RealPosition]:
        """Place a market order on Bybit"""
        from config import MIN_AVAILABLE_MARGIN
        
        if self.get_available_margin() < MIN_AVAILABLE_MARGIN:
            print(f"⚠️ Insufficient margin for {symbol}")
            return None
        
        # Set leverage
        self._set_leverage(symbol)
        
        # Calculate quantity
        notional_value = margin * self.leverage
        quantity = notional_value / current_price
        
        # Round values
        quantity = self._round_qty(symbol, quantity)
        take_profit = self._round_price(symbol, take_profit)
        if stop_loss:
            stop_loss = self._round_price(symbol, stop_loss)
        
        try:
            order_params = {
                "category": "linear",
                "symbol": symbol,
                "side": side.value,
                "orderType": "Market",
                "qty": str(quantity),
                "takeProfit": str(take_profit),
                "tpTriggerBy": "LastPrice",
                "tpslMode": "Full",
                "positionIdx": 0
            }
            
            if stop_loss:
                order_params["stopLoss"] = str(stop_loss)
                order_params["slTriggerBy"] = "LastPrice"
            
            result = self.session.place_order(**order_params)
            
            if result.get("retCode") == 0:
                order_id = result.get("result", {}).get("orderId")
                
                # Get fill price from order info
                fill_price = current_price  # Use current price as estimate
                
                # Recalculate fib level based on actual fill price
                actual_fib_level = entry_fib_level
                if fib_high and fib_low and (fib_high - fib_low) != 0:
                     actual_fib_level = (fill_price - fib_low) / (fib_high - fib_low)

                position = RealPosition(
                    symbol=symbol,
                    side=PositionSide.SHORT if side == OrderSide.SELL else PositionSide.LONG,
                    entry_price=fill_price,
                    quantity=quantity,
                    margin=margin,
                    leverage=self.leverage,
                    take_profit=take_profit,
                    stop_loss=stop_loss,
                    order_id=order_id,
                    strategy_case=strategy_case,
                    fib_high=fib_high,
                    fib_low=fib_low,
                    entry_fib_level=actual_fib_level,
                    opened_at=datetime.now(timezone.utc).isoformat(),
                    bybit_order_id=order_id
                )
                
                self.open_positions[symbol] = position
                self._save_trades()
                
                log_trade("OPEN", symbol, side.value, fill_price, case=strategy_case)
                print(f"⚡ REAL Market Order: {side.value} {symbol} @ ${fill_price:.4f}")
                
                return position
            else:
                error_msg = result.get("retMsg", "Unknown error")
                logger.error(f"❌ Market order failed: {error_msg}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Market order exception: {e}")
            return None
    
    def check_positions(self, symbol: str, current_price: float):
        """Check positions - Fast local update, no API calls"""
        self.price_cache[symbol] = current_price
        
        # Update local PnL tracking (no API call)
        for order_id, pos in list(self.open_positions.items()):
            if pos.symbol == symbol:
                pos.calculate_pnl(current_price)
        
        # Periodically sync with Bybit (throttled, not on every tick)
        import time
        now = time.time()
        if not hasattr(self, '_last_position_check'):
            self._last_position_check = 0
        
        # Only check closed positions every 10 MINUTES to avoid API spam
        if now - self._last_position_check >= 600:
            self._last_position_check = now
            self._check_closed_positions()
    
    def _check_closed_positions(self):
        """Sync with Bybit to detect positions closed by TP/SL"""
        try:
            positions = self.session.get_positions(category="linear", settleCoin="USDT")
            if positions.get("retCode") != 0:
                return
            
            bybit_positions = {}
            for pos in positions.get("result", {}).get("list", []):
                size = float(pos.get("size", 0))
                if size > 0:
                    bybit_positions[pos.get("symbol")] = pos
            
            # Check if any local positions are no longer on Bybit
            for order_id, local_pos in list(self.open_positions.items()):
                if local_pos.symbol not in bybit_positions:
                    # Position was closed (likely by TP/SL)
                    close_price = self.price_cache.get(local_pos.symbol, local_pos.entry_price)
                    pnl = local_pos.calculate_pnl(close_price)
                    
                    reason = "TP" if pnl > 0 else "SL"
                    self._record_closed_position(order_id, close_price, reason)
                    
        except Exception as e:
            logger.warning(f"Failed to sync positions: {e}")
    
    def _record_closed_position(self, order_id: str, close_price: float, reason: str):
        """Record a closed position to history"""
        if order_id not in self.open_positions:
            return
        
        position = self.open_positions.pop(order_id)
        pnl = position.calculate_pnl(close_price)
        
        # Update stats
        self.stats["total_trades"] += 1
        if pnl > 0:
            self.stats["wins"] += 1
        else:
            self.stats["losses"] += 1
        
        # Add to history
        trade_record = {
            "trade_index": len(self.trade_history),
            "order_id": order_id,
            "symbol": position.symbol,
            "side": position.side.value,
            "entry_price": position.entry_price,
            "close_price": close_price,
            "quantity": position.quantity,
            "margin": position.margin,
            "used_margin": position.margin,
            "pnl": pnl,
            "strategy_case": position.strategy_case,
            "reason": reason,
            "fib_high": position.fib_high,
            "fib_low": position.fib_low,
            "take_profit": position.take_profit,
            "stop_loss": position.stop_loss,
            "opened_at": position.opened_at,
            "entry_fib_level": position.entry_fib_level,
            "opened_at": position.opened_at,
            "closed_at": datetime.now(timezone.utc).isoformat()
        }
        self.trade_history.append(trade_record)
        
        # Update Equity History
        # self.equity_history.append({
        #     "timestamp": int(datetime.now(timezone.utc).timestamp()),
        #     "balance": self.balance,
        #     "equity": self.get_margin_balance(),
        #     "pnl": pnl
        # })
        
        self._save_trades()
        
        emoji = "💰" if pnl > 0 else "📉"
        log_trade("CLOSE", position.symbol, position.side.value, close_price, pnl=pnl, case=position.strategy_case)
        logger.info(f"{emoji} Position closed ({reason}): {position.symbol} | PnL: ${pnl:.4f}")
        print(f"{emoji} REAL Position closed ({reason}): {position.symbol} | PnL: ${pnl:.4f}")
    
    def check_pending_orders(self, symbol: str, current_price: float):
        """Check pending orders - Cancel zone detection via WebSocket"""
        self.price_cache[symbol] = current_price
        
                # Load cancel zone config
        cancel_c1 = 0.2
        cancel_c3 = 0.3
        cancel_c4 = 0.79
        try:
            with open('shared_config.json', 'r') as f:
                cfg = json.load(f)
                trading_cfg = cfg.get('trading', {})
                cancel_c1 = trading_cfg.get('c1_cancel_below', 0.2)
                cancel_c3 = trading_cfg.get('c3_cancel_below', 0.3)
                cancel_c4 = trading_cfg.get('c4_cancel_below', 0.79)
        except:
            pass
        
        # Check each pending order for cancel zone
        orders_to_cancel = []
        for order_id, order in list(self.pending_orders.items()):
            if order.get("symbol") != symbol:
                continue
            
            fib_high = order.get("fib_high")
            fib_low = order.get("fib_low")
            strategy_case = order.get("strategy_case", 0)
            
            if fib_high and fib_low:
                fib_range = fib_high - fib_low
                if fib_range > 0:
                    current_fib = (current_price - fib_low) / fib_range
                    
                    # C1: Cancel if price drops to cancel zone
                    if strategy_case == 1 and current_fib <= cancel_c1:
                        orders_to_cancel.append((order_id, f"Precio tocó {cancel_c1*100}% (C1 anulado)"))
                    
                    # C3: Cancel if price drops to cancel zone  
                    if strategy_case == 3 and current_fib <= cancel_c3:
                        orders_to_cancel.append((order_id, f"Precio tocó {cancel_c3*100}% (C3 anulado)"))

                    # C4: Cancel if price drops to cancel zone (79%)
                    if strategy_case == 4 and current_fib <= cancel_c4:
                        orders_to_cancel.append((order_id, f"Precio tocó {cancel_c4*100}% (C4 anulado)"))
        
        # Cancel orders via API
        for order_id, reason in orders_to_cancel:
            self.cancel_order(order_id, reason)
        
        # Periodic sync with Bybit (every 10 min)
        import time
        now = time.time()
        if not hasattr(self, '_last_order_check'):
            self._last_order_check = 0
        
        if now - self._last_order_check >= 600:
            self._last_order_check = now
            self._sync_pending_orders()
    
    def _sync_pending_orders(self):
        """Sync pending orders with Bybit"""
        try:
            orders = self.session.get_open_orders(category="linear", settleCoin="USDT")
            if orders.get("retCode") != 0:
                return
            
            bybit_orders = {o.get("orderId"): o for o in orders.get("result", {}).get("list", [])}
            
            # 1. Update existing pending orders
            for order_id, local_order in list(self.pending_orders.items()):
                if order_id not in bybit_orders:
                    # Order is no longer pending - likely filled or cancelled
                    try:
                        # Check history to confirm
                        filled_order = self._check_order_status(order_id)
                        if filled_order:
                            status = filled_order.get("orderStatus")
                            if status in ["Filled", "PartiallyFilled"]:
                                self._handle_filled_order(order_id, local_order, filled_order)
                            elif status == "Cancelled":
                                del self.pending_orders[order_id]
                                self._save_trades()
                        else:
                             # Not found? Maybe manual cancel or rejected
                             del self.pending_orders[order_id]
                             self._save_trades()
                    except:
                        del self.pending_orders[order_id]
            
            # 2. Check for "Ghost" orders (TP/SL) that shouldn't be here
            # We rarely want to ADD orders from Bybit to local if we didn't create them, 
            # especially TP/SL orders which can spam the monitor.
            # So we SKIP adding unknown orders to self.pending_orders.
            pass
                        
        except Exception as e:
            logger.warning(f"Failed to sync orders: {e}")
    
    def _check_order_status(self, order_id: str) -> Optional[dict]:
        """Check specific order status"""
        try:
            result = self.session.get_order_history(category="linear", orderId=order_id)
            if result.get("retCode") == 0:
                orders = result.get("result", {}).get("list", [])
                if orders:
                    return orders[0]
        except Exception:
            pass
        return None
    
    def _handle_filled_order(self, order_id: str, local_order: dict, bybit_order: dict):
        """Handle a filled limit order"""
        if order_id in self.pending_orders:
            del self.pending_orders[order_id]
        
        fill_price = float(bybit_order.get("avgPrice", local_order.get("price")))
        
        # Recalculate fib level based on actual fill price
        fib_high = local_order.get("fib_high")
        fib_low = local_order.get("fib_low")
        actual_fib_level = local_order.get("entry_fib_level")
        
        if fib_high and fib_low and (fib_high - fib_low) != 0:
             actual_fib_level = (fill_price - fib_low) / (fib_high - fib_low)

        position = RealPosition(
            symbol=local_order["symbol"],
            side=PositionSide.SHORT if local_order["side"] == "Sell" else PositionSide.LONG,
            entry_price=fill_price,
            quantity=local_order["quantity"],
            margin=local_order["margin"],
            leverage=self.leverage,
            take_profit=local_order["take_profit"],
            stop_loss=local_order.get("stop_loss"),
            order_id=order_id,
            strategy_case=local_order.get("strategy_case", 0),
            fib_high=fib_high,
            fib_low=fib_low,
            entry_fib_level=actual_fib_level,
            opened_at=datetime.now(timezone.utc).isoformat(),
            bybit_order_id=order_id
        )
        
        self.open_positions[local_order["symbol"]] = position
        self._save_trades()
        
        log_trade("OPEN", local_order["symbol"], local_order["side"], fill_price, case=local_order.get("strategy_case", 0))
        print(f"✅ REAL Order filled: {local_order['side']} {local_order['symbol']} @ ${fill_price:.4f}")
    
    def cancel_order(self, order_id: str, reason: str = "Manual Cancel"):
        """Cancel a pending order"""
        if order_id not in self.pending_orders:
            return
        
        try:
            order = self.pending_orders[order_id]
            result = self.session.cancel_order(
                category="linear",
                symbol=order["symbol"],
                orderId=order_id
            )
            
            if result.get("retCode") == 0:
                del self.pending_orders[order_id]
                self.stats["cancelled_orders"] += 1
                self._save_trades()
                print(f"🚫 Order cancelled: {order_id}")
            else:
                logger.warning(f"Failed to cancel order: {result.get('retMsg')}")
                
        except Exception as e:
            logger.error(f"Cancel order exception: {e}")

        # Record in cancelled history
        self.cancelled_history.append({
            "order_id": order_id,
            "symbol": order.get("symbol"),
            "reason": reason,
            "cancelled_at": datetime.now(timezone.utc).isoformat(),
            "strategy_case": order.get("strategy_case", 0),
            "price": order.get("price"),
            "quantity": order.get("quantity")
        })
        self._save_trades()
    
    def close_all_positions(self, price_cache: Dict[str, float], reason: str = "Global Close"):
        """Close all open positions"""
        for order_id, pos in list(self.open_positions.items()):
            try:
                # Close position with market order in opposite direction
                close_side = "Buy" if pos.side == PositionSide.SHORT else "Sell"
                result = self.session.place_order(
                    category="linear",
                    symbol=pos.symbol,
                    side=close_side,
                    orderType="Market",
                    qty=str(pos.quantity),
                    reduceOnly=True,
                    positionIdx=0
                )
                
                if result.get("retCode") == 0:
                    close_price = price_cache.get(pos.symbol, pos.entry_price)
                    self._record_closed_position(order_id, close_price, reason)
                    
            except Exception as e:
                logger.error(f"Failed to close position {pos.symbol}: {e}")
    
    def _round_qty(self, symbol: str, qty: float) -> float:
        """Round quantity to valid precision for symbol"""
        # Cache for qty steps
        if not hasattr(self, '_qty_step_cache'):
            self._qty_step_cache = {}
        
        if symbol not in self._qty_step_cache:
            try:
                info = self.session.get_instruments_info(category="linear", symbol=symbol)
                if info.get("retCode") == 0:
                    instruments = info.get("result", {}).get("list", [])
                    if instruments:
                        qty_step = float(instruments[0].get("lotSizeFilter", {}).get("qtyStep", 1))
                        self._qty_step_cache[symbol] = qty_step
            except:
                self._qty_step_cache[symbol] = 1  # Default to integer
        
        qty_step = self._qty_step_cache.get(symbol, 1)
        
        # Round down to nearest valid step
        import math
        rounded = math.floor(qty / qty_step) * qty_step
        
        # Format to remove trailing zeros
        if qty_step >= 1:
            return int(rounded)
        else:
            decimals = len(str(qty_step).split('.')[-1].rstrip('0'))
            return round(rounded, decimals)
    
    def _round_price(self, symbol: str, price: float) -> float:
        """Round price to valid precision for symbol"""
        # TODO: Get from Bybit API instrument info
        if price > 1000:
            return round(price, 2)
        elif price > 1:
            return round(price, 4)
        else:
            return round(price, 6)
    
    def update_max_simultaneous(self):
        """Track max simultaneous positions (for stats compatibility)"""
        current = len(self.open_positions) + len(self.pending_orders)
        if current > self.stats.get("max_simultaneous", 0):
            self.stats["max_simultaneous"] = current
    
    def record_equity_point(self, current_prices: Dict[str, float] = None):
        """Record equity point for history chart (mirrors PaperTradingAccount)"""
        unrealized_pnl = self.get_unrealized_pnl(current_prices)
        margin_balance = self.balance + unrealized_pnl
        
        active_ops = len(self.open_positions) + len(self.pending_orders)
        
        point = {
            "time": datetime.now(timezone.utc).isoformat(),
            "balance": round(self.balance, 2),
            "unrealized_pnl": round(unrealized_pnl, 4),
            "equity": round(margin_balance, 2),
            "active_operations_count": active_ops
        }
        
        self.equity_history.append(point)
        # Keep limit
        if len(self.equity_history) > 10000:
            self.equity_history.pop(0)
            
        # Force save
        self._save_trades()

    def update_positions_pnl(self, current_prices: Dict[str, float]):
        """Update PnL for all positions"""
        for pos in self.open_positions.values():
            if pos.symbol in current_prices:
                pos.calculate_pnl(current_prices[pos.symbol])
    
    def get_status(self) -> dict:
        """Get account status for web interface"""
        self._sync_account()
        unrealized = self.get_unrealized_pnl()
        available = self.get_available_margin()
        return {
            "balance": round(self.balance, 2),
            "initial_balance": self._initial_balance or self.balance,
            "unrealized_pnl": round(unrealized, 2),
            "total_unrealized_pnl": round(unrealized, 2),  # Alias for compatibility
            "available_margin": round(available, 2),
            "margin_balance": round(self.balance + unrealized, 2),  # Added for compatibility
            "open_positions": len(self.open_positions),
            "pending_orders": len(self.pending_orders),
            "total_trades": self.stats.get("total_trades", 0),
            "wins": self.stats.get("wins", 0),
            "losses": self.stats.get("losses", 0),
            "win_rate": round(self.stats["wins"] / max(1, self.stats["total_trades"]) * 100, 1),
            "mode": "TESTNET" if self.testnet else "MAINNET"
        }
    
    def print_status(self):
        """Print account status"""
        status = self.get_status()
        print(f"\n{'='*50}")
        mode = "DEMO" if self.demo else ("TESTNET" if self.testnet else "MAINNET")
        print(f"🏦 BYBIT {mode} ACCOUNT")
        print(f"{'='*50}")
        print(f"Balance: ${status['balance']:.2f}")
        print(f"Available Margin: ${status['available_margin']:.2f}")
        print(f"Unrealized PnL: ${status['unrealized_pnl']:.2f}")
        print(f"Open Positions: {status['open_positions']}")
        print(f"Pending Orders: {status['pending_orders']}")
        print(f"Win Rate: {status['win_rate']}%")
        print(f"{'='*50}\n")
    
    def get_open_trades_for_web(self) -> dict:
        """Get open trades data for web interface"""
        return {
            "positions": [self._serialize_position(p) for p in self.open_positions.values()],
            "pending": list(self.pending_orders.values()),
            "history": self.trade_history[-50:],  # Last 50 trades
            "cancelled": self.cancelled_history[-20:]
        }
    
        # Keep only last 1000 points
        if len(self.equity_history) > 1000:
            self.equity_history = self.equity_history[-1000:]
    
    def print_open_trades(self):
        """Print open trades (compatibility with PaperTradingAccount)"""
        if not self.open_positions and not self.pending_orders:
            print("📭 No hay operaciones abiertas")
            return
        
        print(f"\n📊 OPERACIONES ABIERTAS ({len(self.open_positions)} posiciones, {len(self.pending_orders)} órdenes)")
        for pos in self.open_positions.values():
            pnl_emoji = "🟢" if pos.unrealized_pnl >= 0 else "🔴"
            print(f"  {pos.symbol} | {pos.side.value} | Entry: ${pos.entry_price:.4f} | {pnl_emoji} PnL: ${pos.unrealized_pnl:.4f}")
