"""
Candle Service - SQLite-based candle data storage and API
Provides fast local access to historical 1-minute candles for the trading analyzer.

Usage:
    python candle_service.py

Endpoints:
    GET  /api/candles/<symbol>    - Get all candles for a symbol
    GET  /api/candles/<symbol>?from=<ts>&to=<ts> - Get candles in range
    POST /api/sync                - Sync all symbols from trades.json
    POST /api/sync/<symbol>       - Sync a specific symbol
    GET  /api/status              - Get database status
"""

import sqlite3
import json
import time
import os
from datetime import datetime, timedelta
from flask import Flask, jsonify, request
from flask_cors import CORS
import requests

app = Flask(__name__)
CORS(app)  # Allow cross-origin requests from the HTML analyzer

# Configuration
DB_PATH = "candles.db"
# List of trade files to scan for symbols
TRADES_FILES = ["trades_real.json", "trades_V2_2h.json", "trades_2h.json", "trades_4h.json"]
BYBIT_API_BASE = "https://api.bybit.com/v5/market/kline"
DEFAULT_SYNC_DAYS = 5  # Sync 5 days of data by default (can be changed via API)
CANDLES_PER_REQUEST = 1000  # Bybit API limit
PARALLEL_CONNECTIONS = 10  # Number of parallel API requests (safe for Bybit)

# ========== DATABASE FUNCTIONS ==========

def get_db():
    """Get database connection with row factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialize the database schema."""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS candles (
            symbol TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            PRIMARY KEY (symbol, timestamp)
        )
    """)
    
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_candles_symbol ON candles(symbol)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_candles_time ON candles(timestamp)")
    
    # Metadata table to track sync status
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sync_status (
            symbol TEXT PRIMARY KEY,
            last_timestamp INTEGER,
            last_sync TEXT,
            candle_count INTEGER
        )
    """)
    
    conn.commit()
    conn.close()
    print("✅ Database initialized")

def get_latest_timestamp(symbol: str) -> int:
    """Get the latest candle timestamp for a symbol."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(timestamp) FROM candles WHERE symbol = ?", (symbol,))
    result = cursor.fetchone()[0]
    conn.close()
    return result or 0

def get_oldest_timestamp(symbol: str) -> int:
    """Get the oldest candle timestamp for a symbol."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT MIN(timestamp) FROM candles WHERE symbol = ?", (symbol,))
    result = cursor.fetchone()[0]
    conn.close()
    return result or 0

def insert_candles(symbol: str, candles: list):
    """Insert or replace candles into the database."""
    if not candles:
        return 0
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.executemany("""
        INSERT OR REPLACE INTO candles (symbol, timestamp, open, high, low, close)
        VALUES (?, ?, ?, ?, ?, ?)
    """, [(symbol, c['time'], c['open'], c['high'], c['low'], c['close']) for c in candles])
    
    # Update sync status
    cursor.execute("""
        INSERT OR REPLACE INTO sync_status (symbol, last_timestamp, last_sync, candle_count)
        VALUES (?, ?, ?, (SELECT COUNT(*) FROM candles WHERE symbol = ?))
    """, (symbol, candles[-1]['time'], datetime.now().isoformat(), symbol))
    
    conn.commit()
    inserted = cursor.rowcount
    conn.close()
    return len(candles)

# ========== BYBIT API FUNCTIONS ==========

def fetch_candles_from_bybit(symbol: str, start_time: int = None, end_time: int = None, limit: int = 1000) -> list:
    """Fetch 1-minute candles from Bybit API."""
    params = {
        "category": "linear",
        "symbol": symbol,
        "interval": "1",  # 1 minute
        "limit": min(limit, CANDLES_PER_REQUEST)
    }
    
    if start_time:
        params["start"] = start_time * 1000  # Bybit uses milliseconds
    if end_time:
        params["end"] = end_time * 1000
    
    try:
        response = requests.get(BYBIT_API_BASE, params=params, timeout=10)
        data = response.json()
        
        if data.get("retCode") == 0 and data.get("result", {}).get("list"):
            # Bybit returns newest first, we reverse to get oldest first
            raw_candles = data["result"]["list"]
            candles = [{
                "time": int(c[0]) // 1000,  # Convert to seconds
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4])
            } for c in reversed(raw_candles)]
            return candles
        else:
            print(f"⚠️ Bybit API error for {symbol}: {data.get('retMsg', 'Unknown error')}")
            return []
    except Exception as e:
        print(f"❌ Error fetching {symbol}: {e}")
        return []

def sync_symbol(symbol: str, days: int = DEFAULT_SYNC_DAYS) -> dict:
    """
    Sync candles for a symbol.
    - If no data exists: fetch `days` worth of historical data
    - If data exists: fetch only new candles since last timestamp
    """
    result = {"symbol": symbol, "new_candles": 0, "status": "ok"}
    
    latest_ts = get_latest_timestamp(symbol)
    now = int(time.time())
    
    if latest_ts == 0:
        # No data - fetch historical
        start_time = now - (days * 24 * 60 * 60)
        print(f"📥 {symbol}: Fetching {days} days of historical data...")
        
        # Need to paginate since we might need more than 1000 candles
        # 7 days = 10080 minutes = ~11 requests
        total_candles = []
        current_end = now
        
        while current_end > start_time:
            candles = fetch_candles_from_bybit(symbol, end_time=current_end)
            if not candles:
                break
            
            total_candles = candles + total_candles
            current_end = candles[0]["time"] - 1
            
            # Rate limiting
            time.sleep(0.1)
        
        # Filter to only include candles within our desired range
        total_candles = [c for c in total_candles if c["time"] >= start_time]
        
        if total_candles:
            inserted = insert_candles(symbol, total_candles)
            result["new_candles"] = inserted
            print(f"✅ {symbol}: Inserted {inserted} candles")
    else:
        # Data exists - fetch new candles AND re-download last 5 to fix incomplete candles
        # Subtract 5 minutes (5 * 60 seconds) from latest timestamp to ensure we update
        # any incomplete candles that may have been saved mid-formation
        overlap_buffer = 5 * 60  # 5 minutes = 5 candles
        fetch_from = latest_ts - overlap_buffer
        
        print(f"🔄 {symbol}: Updating from {datetime.fromtimestamp(fetch_from).strftime('%Y-%m-%d %H:%M')} (with 5-candle overlap)...")
        
        candles = fetch_candles_from_bybit(symbol, start_time=fetch_from)
        
        if candles:
            inserted = insert_candles(symbol, candles)
            result["new_candles"] = inserted
            result["note"] = "Includes 5-candle overlap for incomplete candle correction"
            print(f"✅ {symbol}: Updated {inserted} candles (including overlap)")
        else:
            print(f"ℹ️ {symbol}: Already up to date")
    
    return result

def get_symbols_from_trades() -> set:
    """Extract unique symbols from all trades JSON files."""
    symbols = set()
    
    for file_path in TRADES_FILES:
        if not os.path.exists(file_path):
            print(f"⚠️ {file_path} not found")
            continue
        
        try:
            print(f"📄 Scanning {file_path}...")
            with open(file_path, 'r') as f:
                data = json.load(f)
            
            if "history" in data:
                for trade in data["history"]:
                    if "symbol" in trade:
                        symbols.add(trade["symbol"])
            
            if "open_positions" in data:
                for trade in data["open_positions"].values():
                    if "symbol" in trade:
                        symbols.add(trade["symbol"])
            
            if "pending_orders" in data:
                for order in data["pending_orders"].values():
                    if "symbol" in order:
                        symbols.add(order["symbol"])
                        
        except Exception as e:
            print(f"❌ Error reading {file_path}: {e}")
    
    return symbols

def export_to_json():
    """Export all candles from SQLite to static JSON files for http.server compatibility."""
    CANDLES_DIR = "candles"
    
    # Create directory if not exists
    if not os.path.exists(CANDLES_DIR):
        os.makedirs(CANDLES_DIR)
        print(f"📁 Created {CANDLES_DIR}/ directory")
    
    conn = get_db()
    cursor = conn.cursor()
    
    # Get all unique symbols
    cursor.execute("SELECT DISTINCT symbol FROM candles")
    symbols = [row[0] for row in cursor.fetchall()]
    
    print(f"📤 Exporting {len(symbols)} symbols to JSON files...")
    
    for symbol in symbols:
        cursor.execute("""
            SELECT timestamp, open, high, low, close
            FROM candles
            WHERE symbol = ?
            ORDER BY timestamp ASC
        """, (symbol,))
        
        rows = cursor.fetchall()
        candles = [{
            "time": row[0],
            "open": row[1],
            "high": row[2],
            "low": row[3],
            "close": row[4]
        } for row in rows]
        
        output = {
            "symbol": symbol,
            "count": len(candles),
            "candles": candles
        }
        
        filepath = os.path.join(CANDLES_DIR, f"{symbol}.json")
        with open(filepath, 'w') as f:
            json.dump(output, f)
        
        print(f"  ✅ {symbol}: {len(candles)} candles → {filepath}")
    
    conn.close()
    print(f"✅ Export complete! Files saved to {CANDLES_DIR}/")

# ========== API ENDPOINTS ==========

@app.route("/api/candles/<symbol>")
def get_candles(symbol: str):
    """Get candles for a symbol, optionally filtered by time range."""
    from_ts = request.args.get("from", type=int, default=0)
    to_ts = request.args.get("to", type=int, default=int(time.time()))
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT timestamp, open, high, low, close
        FROM candles
        WHERE symbol = ? AND timestamp >= ? AND timestamp <= ?
        ORDER BY timestamp ASC
    """, (symbol, from_ts, to_ts))
    
    rows = cursor.fetchall()
    conn.close()
    
    candles = [{
        "time": row["timestamp"],
        "open": row["open"],
        "high": row["high"],
        "low": row["low"],
        "close": row["close"]
    } for row in rows]
    
    return jsonify({
        "symbol": symbol,
        "count": len(candles),
        "candles": candles
    })

@app.route("/api/candles/bulk", methods=["POST"])
def get_bulk_candles():
    """Get candles for multiple symbols in one request."""
    data = request.get_json()
    if not data or "symbols" not in data:
        return jsonify({"error": "Missing 'symbols' list in body"}), 400
    
    symbols = data["symbols"]
    if not symbols:
        return jsonify({})
        
    conn = get_db()
    cursor = conn.cursor()
    
    # Use IN clause for efficient bulk fetch
    placeholders = ",".join(["?"] * len(symbols))
    query = f"""
        SELECT symbol, timestamp, open, high, low, close
        FROM candles
        WHERE symbol IN ({placeholders})
        ORDER BY symbol, timestamp ASC
    """
    
    cursor.execute(query, symbols)
    rows = cursor.fetchall()
    conn.close()
    
    # Group by symbol
    result = {}
    for row in rows:
        sym = row["symbol"]
        if sym not in result:
            result[sym] = []
            
        result[sym].append({
            "time": row["timestamp"],
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"]
        })
        
    return jsonify(result)

@app.route("/api/sync", methods=["POST"])
def sync_all():
    """Sync all symbols from trades.json."""
    days = request.args.get("days", type=int, default=DEFAULT_SYNC_DAYS)
    
    symbols = get_symbols_from_trades()
    if not symbols:
        return jsonify({"error": "No symbols found in trade files"}), 400
    
    results = []
    for symbol in sorted(symbols):
        result = sync_symbol(symbol, days)
        results.append(result)
        time.sleep(0.2)  # Rate limiting between symbols
    
    return jsonify({
        "synced": len(results),
        "results": results
    })

@app.route("/api/sync/<symbol>", methods=["POST"])
def sync_one(symbol: str):
    """Sync a specific symbol."""
    days = request.args.get("days", type=int, default=DEFAULT_SYNC_DAYS)
    result = sync_symbol(symbol.upper(), days)
    return jsonify(result)

@app.route("/api/status")
def get_status():
    """Get database status."""
    conn = get_db()
    cursor = conn.cursor()
    
    # Get overall stats
    cursor.execute("SELECT COUNT(DISTINCT symbol) as symbols, COUNT(*) as total_candles FROM candles")
    stats = cursor.fetchone()
    
    # Get per-symbol stats
    cursor.execute("""
        SELECT symbol, candle_count, last_sync, last_timestamp
        FROM sync_status
        ORDER BY symbol
    """)
    symbol_stats = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    
    # Calculate DB file size
    db_size = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
    
    return jsonify({
        "database": DB_PATH,
        "size_mb": round(db_size / (1024 * 1024), 2),
        "total_symbols": stats["symbols"],
        "total_candles": stats["total_candles"],
        "symbols": symbol_stats
    })

@app.route("/")
def index():
    """Health check endpoint."""
    return jsonify({
        "service": "Candle Service",
        "status": "running",
        "database": DB_PATH,
        "endpoints": [
            "GET  /api/candles/<symbol>",
            "POST /api/sync",
            "POST /api/sync/<symbol>",
            "GET  /api/status"
        ]
    })

# ========== MAIN ==========

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Candle Service - SQLite candle data manager")
    parser.add_argument("--sync", action="store_true", help="Sync candles from Bybit and exit (no server)")
    parser.add_argument("--export", action="store_true", help="Export SQLite to JSON files and exit (no server)")
    parser.add_argument("--serve", action="store_true", help="Start the API server (default if no flags)")
    parser.add_argument("--days", type=int, default=DEFAULT_SYNC_DAYS, help=f"Days of history to sync (default: {DEFAULT_SYNC_DAYS})")
    
    args = parser.parse_args()
    
    print("🕯️ Candle Service")
    init_db()
    
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    def sync_with_delay(sym):
        result = sync_symbol(sym, args.days)
        time.sleep(0.1)
        return result
    
    def run_sync():
        """Run sync for all symbols with parallel connections."""
        symbols = get_symbols_from_trades()
        if not symbols:
            print("⚠️ No symbols found in trade files")
            return
        
        print(f"🔄 Syncing {len(symbols)} symbols ({args.days} days, {PARALLEL_CONNECTIONS} parallel)...")
        
        with ThreadPoolExecutor(max_workers=PARALLEL_CONNECTIONS) as executor:
            futures = {executor.submit(sync_with_delay, sym): sym for sym in sorted(symbols)}
            for future in as_completed(futures):
                pass
        
        print("✅ Sync complete!")
    
    # Mode: Sync only (no server)
    if args.sync:
        run_sync()
        if args.export:
            export_to_json()
        exit(0)
    
    # Mode: Export only (no server, no sync)
    if args.export:
        export_to_json()
        exit(0)
    
    # Mode: Serve only (no sync, just API)
    if args.serve:
        print("🚀 Starting API server on http://localhost:5001 (no sync)")
        app.run(host="0.0.0.0", port=5001, debug=False)
        exit(0)
    
    # Default mode: Sync then serve
    # Check if DB needs initial sync
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM candles")
    count = cursor.fetchone()[0]
    conn.close()
    
    if count == 0:
        print("📊 Database is empty, running initial sync...")
        run_sync()
    else:
        print("🔄 Updating existing symbols...")
        run_sync()
    
    # JSON export only if --export flag was passed (already handled above)
    # Flask API reads directly from SQLite - no JSON needed
    
    print("🚀 Starting API server on http://localhost:5001")
    app.run(host="0.0.0.0", port=5001, debug=False)

