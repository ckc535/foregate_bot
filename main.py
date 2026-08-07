import os
import time
import subprocess
from datetime import datetime

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, Grid
from textual.widgets import Header, Footer, Static, Button, DataTable
from textual.worker import Worker, get_current_worker
from textual import work, on
from textual.message import Message
from rich.text import Text
import logging

logging.basicConfig(level=logging.WARNING, handlers=[logging.NullHandler()])

from foregate_client import ForeGateClient
from get_markets import fetch_and_filter_markets
from check_balance import fetch_balance
from scan_all_orderbooks import scan_markets_data



# Tự động tải .env
def load_env_file(filepath=".env"):
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() not in os.environ:
                        os.environ[k.strip()] = v.strip().strip('"').strip("'")

load_env_file(".env")

APP_KEY = os.environ.get("FOREGATE_APP_KEY", "")
APP_SECRET = os.environ.get("FOREGATE_APP_SECRET", "")
API_KEY = os.environ.get("FOREGATE_API_KEY", "")
MIN_VOLUME = float(os.environ.get("MIN_MARKET_VOLUME", "10000"))
MAX_ENDTIME_DAYS = float(os.environ.get("MAX_ENDTIME_DAYS", "0.5"))
TARGET_MAX_GAP_PCT = float(os.environ.get("TARGET_MAX_GAP_PCT") or os.environ.get("MM_TARGET_MAX_GAP_PCT", "5.0"))
SESSION_COOKIE = os.environ.get("FOREGATE_SESSION_COOKIE")


class ScanResultsReady(Message):
    def __init__(self, matched_results, total_scanned):
        self.matched_results = matched_results
        self.total_scanned = total_scanned
        super().__init__()

class PositionsFetchReady(Message):
    def __init__(self, mock_positions):
        self.mock_positions = mock_positions
        super().__init__()

class BalanceFetchReady(Message):
    def __init__(self, balance):
        self.balance = balance
        super().__init__()

class DataFetchError(Message):
    def __init__(self, error_msg, source=""):
        self.error_msg = error_msg
        self.source = source
        super().__init__()

class ForegateDashboard(App):
    """A Textual app for Foregate Bot Dashboard."""

    CSS_PATH = "styles.tcss"
    BINDINGS = [
        ("r", "refresh_data", "Refresh"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client = ForeGateClient(
            app_key=APP_KEY,
            app_secret=APP_SECRET,
            api_key=API_KEY,
        )
        self.fetch_timer = None
        self.cached_markets = []
        self.last_markets_fetch_time = 0

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Header(show_clock=True)

        with Horizontal(id="top_bar"):
            yield Static("Fetching account balance...", id="balance_info")
            yield Button("🔄", id="refresh_balance_btn", classes="refresh_btn", tooltip="Refresh Balance")

        with Grid(id="main_content"):
            with Vertical(classes="table_container"):
                with Horizontal(classes="table_header"):
                    yield Static("🔍 Scan Results", classes="table_title")
                    yield Button("🔄", id="refresh_markets_btn", classes="refresh_btn", tooltip="Scan Orderbooks")
                yield DataTable(id="markets_table")

            with Vertical(classes="table_container"):
                with Horizontal(classes="table_header"):
                    yield Static("💼 Positions", classes="table_title")
                    yield Button("🔄", id="refresh_positions_btn", classes="refresh_btn", tooltip="Refresh Positions")
                yield DataTable(id="positions_table")

        yield Footer()

    def on_mount(self) -> None:
        """Setup tables and start data fetching."""
        # Setup Scan Results Table
        markets_table = self.query_one("#markets_table", DataTable)
        markets_table.cursor_type = "row"
        markets_table.zebra_stripes = True
        markets_table.add_columns(
            "#",
            Text("Market", style="bold white"),
            Text("Outcome", style="bold cyan"),
            Text("Shares", style="bold yellow"),
            Text("Price A", style="bold magenta"),
            Text("Price B", style="bold magenta"),
            Text("Vốn A+B", style="bold green"),
            Text("Gap %", style="bold red"),
        )

        # Setup Positions Table
        positions_table = self.query_one("#positions_table", DataTable)
        positions_table.cursor_type = "row"
        positions_table.zebra_stripes = True
        positions_table.add_columns(
            Text("Market", style="bold cyan"),
            Text("Outcome", style="bold green"),
            Text("Option", style="bold yellow"),
            "Shares",
            "Init Value",
            Text("P&L", style="bold")
        )

        self.fetch_timer = self.set_interval(10, self.action_refresh_data)
        self.action_refresh_data()

    def action_refresh_data(self) -> None:
        """Trigger a background data fetch for all."""
        if self.fetch_timer:
            self.fetch_timer.reset()
        self.action_refresh_balance()
        self.action_refresh_markets()
        self.action_refresh_positions()

    def action_refresh_balance(self) -> None:
        btn = self.query_one("#refresh_balance_btn", Button)
        btn.disabled = True
        btn.label = "⏳"
        self.fetch_balance_worker()

    def action_refresh_markets(self) -> None:
        btn = self.query_one("#refresh_markets_btn", Button)
        btn.disabled = True
        btn.label = "⏳"
        self.fetch_markets_worker()

    def action_refresh_positions(self) -> None:
        btn = self.query_one("#refresh_positions_btn", Button)
        btn.disabled = True
        btn.label = "⏳"
        self.fetch_positions_worker()

    # --- FIX: mỗi worker có group riêng, tránh exclusive=True hủy chéo lẫn nhau ---
    @work(exclusive=True, thread=True, group="balance_fetch")
    def fetch_balance_worker(self) -> None:
        worker = get_current_worker()
        logging.debug("Starting fetch_balance_worker")
        try:
            balance = fetch_balance(self.client)
            logging.debug("fetch_balance_worker success")
            if not worker.is_cancelled:
                self.post_message(BalanceFetchReady(balance))
        except Exception as e:
            logging.error(f"fetch_balance_worker error: {e}")
            if not worker.is_cancelled:
                self.post_message(DataFetchError(str(e), "balance"))

    @work(exclusive=True, thread=True, group="markets_fetch")
    def fetch_markets_worker(self) -> None:
        worker = get_current_worker()
        logging.debug("Starting fetch_markets_worker (fetch + scan)")
        try:
            # Bước 1: Cache danh sách markets (chỉ fetch lại API sau 30 giây để giảm latency)
            now = time.time()
            if not self.cached_markets or (now - self.last_markets_fetch_time > 30):
                markets = fetch_and_filter_markets(self.client, MIN_VOLUME, MAX_ENDTIME_DAYS)
                self.cached_markets = markets
                self.last_markets_fetch_time = now
            else:
                markets = self.cached_markets

            logging.debug(f"Scanning {len(markets)} markets orderbooks...")
            if worker.is_cancelled:
                return

            # Bước 2: Scan orderbooks đa luồng dùng chung connection pool
            matched_results, _ = scan_markets_data(
                markets,
                APP_KEY, APP_SECRET, API_KEY,
                session_cookie=self.client.session_cookie or SESSION_COOKIE,
                target_max_gap_pct=TARGET_MAX_GAP_PCT,
                max_workers=25,
                client=self.client
            )
            logging.debug(f"Scan complete: {len(matched_results)} matched out of {len(markets)} markets")
            if not worker.is_cancelled:
                self.post_message(ScanResultsReady(matched_results, len(markets)))
        except Exception as e:
            logging.error(f"fetch_markets_worker error: {e}")
            if not worker.is_cancelled:
                self.post_message(DataFetchError(str(e), "markets"))

    @work(exclusive=True, thread=True, group="positions_fetch")
    def fetch_positions_worker(self) -> None:
        worker = get_current_worker()
        logging.debug("Starting fetch_positions_worker")
        try:
            # Mock positions for now
            mock_positions = [
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
                                {"optionTitle": "Yes", "shares": 10, "initialValue": 100, "currentValue": 98.5},
                                {"optionTitle": "No", "shares": 10, "initialValue": 100, "currentValue": 102.0},
                            ]
                        }
                    ]
                },
                {
                    "marketId": "440644633370828523",
                    "marketTitle": "LoL: Hanwha Life vs Kiwoon",
                    "outcomes": [
                        {
                            "outcomeName": "Match Winner",
                            "options": [
                                {"optionTitle": "Hanwha Life", "shares": 30, "initialValue": 300, "currentValue": 450},
                                {"optionTitle": "Kiwoon", "shares": 10, "initialValue": 100, "currentValue": 50},
                            ]
                        }
                    ]
                }
            ]

            logging.debug("fetch_positions_worker success")
            if not worker.is_cancelled:
                self.post_message(PositionsFetchReady(mock_positions))
        except Exception as e:
            logging.error(f"fetch_positions_worker error: {e}")
            if not worker.is_cancelled:
                self.post_message(DataFetchError(str(e), "positions"))

    @on(BalanceFetchReady)
    def _on_balance_fetch_ready(self, message: BalanceFetchReady) -> None:
        btn = self.query_one("#refresh_balance_btn", Button)
        btn.disabled = False
        btn.label = "🔄"
        self._update_balance_ui(message.balance)
        self.notify("Balance refreshed!", title="Success", timeout=1)

    @on(ScanResultsReady)
    def _on_scan_results_ready(self, message: ScanResultsReady) -> None:
        btn = self.query_one("#refresh_markets_btn", Button)
        btn.disabled = False
        btn.label = "🔄"
        self._update_markets_table(message.matched_results)
        count = len(message.matched_results)
        total = message.total_scanned
        self.notify(f"✅ Scan hoàn tất: {count} outcomes khớp / {total} markets", title="🔍 Scan Results", timeout=2)
        # Tự động scan lại liên tục ngay lập tức (0.05s delay)
        self.set_timer(0.05, self.action_refresh_markets)

    @on(PositionsFetchReady)
    def _on_positions_fetch_ready(self, message: PositionsFetchReady) -> None:
        btn = self.query_one("#refresh_positions_btn", Button)
        btn.disabled = False
        btn.label = "🔄"
        self._update_positions_table(message.mock_positions)
        self.notify("Positions refreshed!", title="Success", timeout=1)

    @on(DataFetchError)
    def _on_data_fetch_error(self, message: DataFetchError) -> None:
        """Handle UI update on error."""
        if message.source == "balance":
            btn = self.query_one("#refresh_balance_btn", Button)
        elif message.source == "markets":
            btn = self.query_one("#refresh_markets_btn", Button)
        elif message.source == "positions":
            btn = self.query_one("#refresh_positions_btn", Button)
        else:
            return

        btn.disabled = False
        btn.label = "🔄"
        self.notify(f"Error fetching {message.source}: {message.error_msg}", title="Error", severity="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button clicks."""
        if event.button.id == "refresh_balance_btn":
            self.action_refresh_balance()
        elif event.button.id == "refresh_markets_btn":
            self.action_refresh_markets()
        elif event.button.id == "refresh_positions_btn":
            self.action_refresh_positions()

    def _update_balance_ui(self, balance):
        balance_info = self.query_one("#balance_info", Static)
        if not balance:
            balance_info.update("❌ Failed to load balance.")
            return

        logging.debug(f"Balance raw response: {balance}")

        assets = balance.get('data', [])
        if isinstance(assets, list) and assets:
            parts = []
            for asset in assets:
                symbol = asset.get('asset', asset.get('currency', 'UNKNOWN'))
                total = asset.get('totalAmount', asset.get('total', '0'))
                avail = asset.get('availableAmount', asset.get('available', '0'))
                frozen = asset.get('frozenAmount', asset.get('frozen', '0'))
                parts.append(f"💰 {symbol}: {avail} (Total: {total} | Frozen: {frozen})")
            balance_info.update("   ".join(parts))
        elif isinstance(assets, dict) and assets:
            parts = []
            for k, v in assets.items():
                if isinstance(v, dict):
                     parts.append(f"💰 {k}: {v.get('available', 0)} (Total: {v.get('total', 0)})")
            balance_info.update("   ".join(parts) if parts else str(assets))
        else:
            # Hiển thị raw data để debug
            balance_info.update(f"⚠️ Balance data: {str(balance)[:200]}")

    def _update_markets_table(self, scan_results):
        table = self.query_one("#markets_table", DataTable)
        table.clear()

        if not scan_results:
            table.add_row("-", "No scan results", "-", "-", "-", "-", "-", "-")
            return

        for i, item in enumerate(scan_results):
            market_title = item.get('market_title', 'N/A')
            outcome_title = item.get('outcome_title', '')
            shares = item.get('buyable_shares_payout', 0)
            total_cost = item.get('total_capital_spent', 0)
            gap = item.get('percent_gap', 0)

            # Cắt ngắn nếu tên quá dài để giữ bảng gọn gàng
            market_disp = market_title[:37] + "..." if len(market_title) > 40 else market_title
            outcome_disp = outcome_title[:27] + "..." if len(outcome_title) > 30 else outcome_title

            opt1 = item.get('option_1', {})
            opt2 = item.get('option_2', {})
            opt1_name = opt1.get('name', 'Opt A')
            opt1_name_disp = opt1_name[:12] + "..." if len(opt1_name) > 15 else opt1_name
            opt1_vwap = opt1.get('vwap', 0)

            opt2_name = opt2.get('name', 'Opt B')
            opt2_name_disp = opt2_name[:12] + "..." if len(opt2_name) > 15 else opt2_name
            opt2_vwap = opt2.get('vwap', 0)

            price_a_str = f"{opt1_vwap:.3f} ({opt1_name_disp})"
            price_b_str = f"{opt2_vwap:.3f} ({opt2_name_disp})"

            # Màu sắc cho Gap% (Dương = Lãi, Âm = Lỗ)
            if gap > 0:
                gap_style = "bold #00e676"  # Xanh lá - Lãi / Arbitrage!
            elif gap >= -2:
                gap_style = "bold #a6e3a1"  # Xanh nhạt - Lỗ ít
            elif gap >= -5:
                gap_style = "bold #f9e2af"  # Vàng - Lỗ vừa
            else:
                gap_style = "bold #ff1744"  # Đỏ - Lỗ nhiều

            table.add_row(
                str(i + 1),
                Text(market_disp, style="bold white"),
                Text(outcome_disp, style="cyan"),
                Text(f"{shares:.0f}", style="yellow"),
                Text(price_a_str, style="bold magenta"),
                Text(price_b_str, style="bold magenta"),
                Text(f"${total_cost:.2f}", style="green"),
                Text(f"{gap:+.2f}%", style=gap_style),
            )

    def _update_positions_table(self, positions_data):
        table = self.query_one("#positions_table", DataTable)
        table.clear()

        if not positions_data:
            table.add_row("-", "No positions found", "-", "-", "-", "-")
            return

        for market in positions_data:
            market_title = market.get('marketTitle', 'Unknown Market')

            outcomes = market.get("outcomes", [])
            for outcome in outcomes:
                outcome_name = outcome.get("outcomeName", "Unknown Outcome")
                options = outcome.get("options", [])

                for i, option in enumerate(options):
                    opt_title = option.get("optionTitle", "N/A")
                    shares = str(option.get("shares", "0"))
                    init_val = float(option.get("initialValue", 0))
                    curr_val = float(option.get("currentValue", 0))

                    diff = curr_val - init_val
                    percent_diff = (diff / init_val * 100) if init_val > 0 else 0

                    if diff > 0:
                        sign = "+"
                        pnl_style = "bold #00e676" # Bright Green
                    elif diff < 0:
                        sign = ""
                        pnl_style = "bold #ff1744" # Bright Red
                    else:
                        sign = ""
                        pnl_style = "bold white"

                    pnl_str = Text(f"{curr_val:.2f} ({sign}{percent_diff:.2f}%)", style=pnl_style)

                    disp_market = market_title if i == 0 and outcome == outcomes[0] else ""
                    disp_outcome = outcome_name if i == 0 else ""

                    table.add_row(
                        Text(disp_market, style="bold cyan") if disp_market else "",
                        Text(disp_outcome, style="bold green") if disp_outcome else "",
                        Text(opt_title, style="yellow"),
                        shares,
                        f"{init_val:.2f}",
                        pnl_str
                    )

if __name__ == "__main__":
    app = ForegateDashboard()
    app.run()