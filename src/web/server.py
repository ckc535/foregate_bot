import logging
import os
import sys
import threading
import time

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from flask import Flask, jsonify, render_template, request
from src.api.account import fetch_balance
from src.api.client import ForeGateClient
from src.api.markets import fetch_and_filter_markets
from src.config import (
    API_KEY,
    APP_KEY,
    APP_SECRET,
    MAX_ENDTIME_DAYS,
    MIN_VOLUME,
    SESSION_COOKIE,
    TARGET_MAX_GAP_PCT,
)
from src.core.scanner import scan_markets_data

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ForegateWebServer")

app = Flask(
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
    static_folder=os.path.join(os.path.dirname(__file__), "static")
)

client = ForeGateClient(
    app_key=APP_KEY,
    app_secret=APP_SECRET,
    api_key=API_KEY,
)

# Shared in-memory state
bot_state = {
    "balance": {"available": 0.0, "frozen": 0.0, "total": 0.0, "symbol": "USDT"},
    "positions": [
        {
            "marketId": "440702501944039371",
            "marketTitle": "LoL: Nongshim vs KT Rolster",
            "outcomes": [
                {
                    "outcomeName": "Game 1 Winner",
                    "options": [
                        {"optionTitle": "Yes", "shares": 50, "initialValue": 500, "currentValue": 550},
                        {"optionTitle": "No", "shares": 20, "initialValue": 200, "currentValue": 180},
                    ]
                },
                {
                    "outcomeName": "Game 2 Winner",
                    "options": [
                        {"optionTitle": "Yes", "shares": 100, "initialValue": 1000, "currentValue": 1050},
                        {"optionTitle": "No", "shares": 40, "initialValue": 400, "currentValue": 380},
                    ]
                }
            ]
        }
    ],
    "scan_results": [],
    "total_markets_scanned": 0,
    "last_scan_time": None,
    "is_scanning": False,
    "status": "Online",
    "progress": {
        "done": 0,
        "total": 0,
        "pct": 0,
        "matched": 0,
        "running": False
    }
}


def _safe_float(val, default=0.0):
    try:
        if val is None:
            return default
        f = float(val)
        return 0.0 if abs(f) < 1e-10 else f
    except (ValueError, TypeError):
        return default


def update_balance_data():
    try:
        raw_bal = fetch_balance(client)
        if not raw_bal:
            return

        data = raw_bal.get('data') if isinstance(raw_bal, dict) else raw_bal
        if isinstance(data, dict):
            avail = _safe_float(data.get('availableAmount') or data.get('available', 0))
            frozen = _safe_float(data.get('frozenAmount') or data.get('frozen', 0))
            total = _safe_float(data.get('totalAmount') or data.get('total', avail + frozen))
            bot_state["balance"] = {
                "available": avail,
                "frozen": frozen,
                "total": total,
                "symbol": "USDT"
            }
        elif isinstance(data, list) and data:
            item = data[0]
            avail = _safe_float(item.get('availableAmount') or item.get('available', 0))
            frozen = _safe_float(item.get('frozenAmount') or item.get('frozen', 0))
            symbol = item.get('asset', 'USDT')
            bot_state["balance"] = {
                "available": avail,
                "frozen": frozen,
                "total": avail + frozen,
                "symbol": symbol
            }
    except Exception as e:
        logger.error(f"Error fetching balance: {e}")


# Market list in-memory cache (30s TTL)
cached_markets = []
last_markets_fetch_time = 0


def get_or_fetch_markets(force=False):
    global cached_markets, last_markets_fetch_time
    now = time.time()
    if force or not cached_markets or (now - last_markets_fetch_time > 30):
        try:
            logger.info("Fetching fresh market list from API (30s TTL)...")
            cached_markets = fetch_and_filter_markets(
                client=client,
                min_volume=MIN_VOLUME,
                max_endtime_days=MAX_ENDTIME_DAYS
            )
            last_markets_fetch_time = now
        except Exception as e:
            logger.error(f"Error fetching market list: {e}")
            if not cached_markets:
                cached_markets = []
    return cached_markets


def run_scanner_cycle():
    if bot_state["is_scanning"]:
        return

    try:
        bot_state["is_scanning"] = True
        bot_state["progress"]["running"] = True

        markets = get_or_fetch_markets()
        if not markets:
            logger.warning("No markets available to scan.")
            return

        def on_progress(done, total, matched_so_far):
            pct = round((done / total * 100), 1) if total > 0 else 0
            matched_cnt = len(matched_so_far) if isinstance(matched_so_far, list) else matched_so_far
            bot_state["progress"] = {
                "done": done,
                "total": total,
                "pct": pct,
                "matched": matched_cnt,
                "running": True
            }

        matched_results, invalid_results = scan_markets_data(
            markets=markets,
            app_key=APP_KEY,
            app_secret=APP_SECRET,
            api_key=API_KEY,
            session_cookie=SESSION_COOKIE,
            target_max_gap_pct=TARGET_MAX_GAP_PCT,
            max_workers=60,
            client=client,
            on_progress=on_progress
        )

        bot_state["scan_results"] = matched_results if isinstance(matched_results, list) else []
        bot_state["total_markets_scanned"] = len(markets)
        bot_state["last_scan_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
    except Exception as e:
        logger.error(f"Error during scan cycle: {e}")
    finally:
        bot_state["is_scanning"] = False
        bot_state["progress"]["running"] = False


def background_worker():
    # Fetch initial balance once at startup
    update_balance_data()
    
    # Continuous live orderbook scanning loop (markets list cached 30s)
    while True:
        try:
            run_scanner_cycle()
        except Exception as e:
            logger.error(f"Background worker error: {e}")
        time.sleep(1)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/balance/refresh", methods=["POST", "GET"])
def refresh_balance_endpoint():
    update_balance_data()
    return jsonify({"status": "ok", "balance": bot_state["balance"]})


@app.route("/api/live")
def get_live_data():
    results = bot_state.get("scan_results", [])
    if not isinstance(results, list):
        results = []
    opps_count = len(results)
    best_gap = max([x.get("percent_gap", 0) for x in results if isinstance(x, dict)], default=0.0)

    total_init = 0.0
    total_curr = 0.0
    for m in bot_state["positions"]:
        for o in m.get("outcomes", []):
            for opt in o.get("options", []):
                total_init += float(opt.get("initialValue", 0))
                total_curr += float(opt.get("currentValue", 0))

    pnl_diff = total_curr - total_init
    pnl_pct = (pnl_diff / total_init * 100) if total_init > 0 else 0.0

    return jsonify({
        "status": bot_state["status"],
        "is_scanning": bot_state["is_scanning"],
        "progress": bot_state["progress"],
        "last_scan_time": bot_state["last_scan_time"],
        "balance": bot_state["balance"],
        "portfolio": {
            "total_value": total_curr,
            "initial_value": total_init,
            "pnl_usd": pnl_diff,
            "pnl_pct": pnl_pct,
        },
        "arbitrage": {
            "opportunities_count": opps_count,
            "best_gap_pct": best_gap,
            "markets_scanned": bot_state["total_markets_scanned"],
        },
        "scan_results": bot_state["scan_results"],
        "positions": bot_state["positions"]
    })


@app.route("/api/summary")
def get_summary():
    return get_live_data()


@app.route("/api/markets")
def get_markets():
    return jsonify({
        "scan_results": bot_state["scan_results"],
        "total_scanned": bot_state["total_markets_scanned"],
        "last_scan_time": bot_state["last_scan_time"]
    })


@app.route("/api/positions")
def get_positions():
    return jsonify({
        "positions": bot_state["positions"]
    })


@app.route("/api/scan", methods=["POST"])
def trigger_scan():
    threading.Thread(target=run_scanner_cycle, daemon=True).start()
    return jsonify({"status": "Scan triggered"})


if __name__ == "__main__":
    t = threading.Thread(target=background_worker, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=5000, debug=False)
