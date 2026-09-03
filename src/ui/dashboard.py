import logging
import time
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, Grid
from textual.message import Message
from textual.widgets import Header, Footer, Static, Button, DataTable
from textual.worker import get_current_worker

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

logging.basicConfig(level=logging.WARNING, handlers=[logging.NullHandler()])


class ScanResultsReady(Message):
    def __init__(self, matched_results, total_scanned):
        self.matched_results = matched_results
        self.total_scanned = total_scanned
        super().__init__()


class ScanProgressUpdated(Message):
    def __init__(self, done, total, matched_so_far):
        self.done = done
        self.total = total
        self.matched_so_far = matched_so_far
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
    """A 3D Cyberpunk styled Textual app for Foregate Bot Dashboard."""

    CSS_PATH = "../../styles.tcss"
    BINDINGS = [
        ("r", "refresh_data", "🔄 Refresh All"),
        ("m", "action_refresh_markets", "⚡ Scan Markets"),
        ("q", "quit", "❌ Quit"),
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
        self.last_opps_count = 0
        self.max_gap_found = 0.0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        # 3D KPI Stat Cards Row
        with Grid(id="kpi_container"):
            with Vertical(classes="kpi_card kpi_card_cyan", id="card_balance"):
                yield Static("💳 AVAILABLE BALANCE", classes="kpi_title")
                yield Static("$0.00 USDT", id="kpi_balance_val", classes="kpi_value")
                yield Static("Frozen: $0.00", id="kpi_balance_sub", classes="kpi_sub")

            with Vertical(classes="kpi_card kpi_card_emerald", id="card_positions"):
                yield Static("📈 PORTFOLIO & P&L", classes="kpi_title")
                yield Static("$0.00", id="kpi_position_val", classes="kpi_value")
                yield Static("PnL: +$0.00 (0.00%)", id="kpi_position_sub", classes="kpi_sub")

            with Vertical(classes="kpi_card kpi_card_purple", id="card_targets"):
                yield Static("🎯 ARBITRAGE TARGETS", classes="kpi_title")
                yield Static("0 Opps", id="kpi_targets_val", classes="kpi_value")
                yield Static("Max Gap: 0.00%", id="kpi_targets_sub", classes="kpi_sub")

            with Vertical(classes="kpi_card kpi_card_amber", id="card_engine"):
                yield Static("⚡ SCANNER ENGINE", classes="kpi_title")
                yield Static("ACTIVE", id="kpi_engine_val", classes="kpi_value")
                yield Static("Cycle: Every 10s", id="kpi_engine_sub", classes="kpi_sub")

        # Main Tables Grid
        with Grid(id="main_content"):
            with Vertical(classes="table_container"):
                with Horizontal(classes="table_header"):
                    yield Static("🔍 Live Arbitrage Opportunities", classes="table_title")
                    yield Button("⚡ Scan", id="refresh_markets_btn", classes="refresh_btn", tooltip="Scan Orderbooks")
                yield DataTable(id="markets_table")

            with Vertical(classes="table_container"):
                with Horizontal(classes="table_header"):
                    yield Static("💼 Active Positions & Risk", classes="table_title")
                    yield Button("🔄 Sync", id="refresh_positions_btn", classes="refresh_btn", tooltip="Refresh Positions")
                yield DataTable(id="positions_table")

        yield Footer()

    def on_mount(self) -> None:
        markets_table = self.query_one("#markets_table", DataTable)
        markets_table.cursor_type = "row"
        markets_table.zebra_stripes = True
        markets_table.add_columns(
            Text("#", style="bold cyan"),
            Text("Market Title", style="bold white"),
            Text("Outcome", style="bold #38bdf8"),
            Text("Shares", style="bold #facc15"),
            Text("Price A (VWAP)", style="bold #c084fc"),
            Text("Price B (VWAP)", style="bold #c084fc"),
            Text("Total Cost", style="bold #4ade80"),
            Text("Gap Spread", style="bold #f43f5e"),
        )

        positions_table = self.query_one("#positions_table", DataTable)
        positions_table.cursor_type = "row"
        positions_table.zebra_stripes = True
        positions_table.add_columns(
            Text("Market", style="bold cyan"),
            Text("Outcome", style="bold #38bdf8"),
            Text("Option", style="bold #facc15"),
            Text("Shares", style="bold white"),
            Text("Init Cost", style="bold white"),
            Text("P&L Unrealized", style="bold #4ade80"),
        )

        self.fetch_timer = self.set_interval(10, self.action_refresh_data)
        self.action_refresh_data()

    def action_refresh_data(self) -> None:
        if self.fetch_timer:
            self.fetch_timer.reset()
        self.action_refresh_balance()
        self.action_refresh_markets()
        self.action_refresh_positions()

    def action_refresh_balance(self) -> None:
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

    @work(exclusive=True, thread=True, group="balance_fetch")
    def fetch_balance_worker(self) -> None:
        worker = get_current_worker()
        try:
            balance = fetch_balance(self.client)
            if not worker.is_cancelled:
                self.post_message(BalanceFetchReady(balance))
        except Exception as e:
            if not worker.is_cancelled:
                self.post_message(DataFetchError(str(e), "balance"))

    @work(exclusive=True, thread=True, group="markets_fetch")
    def fetch_markets_worker(self) -> None:
        worker = get_current_worker()
        try:
            now = time.time()
            if not self.cached_markets or (now - self.last_markets_fetch_time > 30):
                markets = fetch_and_filter_markets(self.client, MIN_VOLUME, MAX_ENDTIME_DAYS)
                self.cached_markets = markets
                self.last_markets_fetch_time = now
            else:
                markets = self.cached_markets

            if worker.is_cancelled:
                return

            def on_progress(done, total, matched_so_far):
                if not worker.is_cancelled:
                    self.post_message(ScanProgressUpdated(done, total, matched_so_far))

            matched_results, _ = scan_markets_data(
                markets,
                APP_KEY, APP_SECRET, API_KEY,
                session_cookie=self.client.session_cookie or SESSION_COOKIE,
                target_max_gap_pct=TARGET_MAX_GAP_PCT,
                max_workers=60,
                client=self.client,
                on_progress=on_progress
            )
            if not worker.is_cancelled:
                self.post_message(ScanResultsReady(matched_results, len(markets)))
        except Exception as e:
            if not worker.is_cancelled:
                self.post_message(DataFetchError(str(e), "markets"))

    @work(exclusive=True, thread=True, group="positions_fetch")
    def fetch_positions_worker(self) -> None:
        worker = get_current_worker()
        try:
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

            if not worker.is_cancelled:
                self.post_message(PositionsFetchReady(mock_positions))
        except Exception as e:
            if not worker.is_cancelled:
                self.post_message(DataFetchError(str(e), "positions"))

    @on(BalanceFetchReady)
    def _on_balance_fetch_ready(self, message: BalanceFetchReady) -> None:
        self._update_balance_ui(message.balance)

    @on(ScanProgressUpdated)
    def _on_scan_progress_updated(self, message: ScanProgressUpdated) -> None:
        engine_val = self.query_one("#kpi_engine_val", Static)
        engine_sub = self.query_one("#kpi_engine_sub", Static)
        pct = int((message.done / message.total) * 100) if message.total > 0 else 0
        engine_val.update(f"{message.done}/{message.total} ({pct}%)")
        engine_sub.update("🔴 LIVE SCANNING...")
        # NOTE: Giữ bảng ổn định, không clear bảng trong lúc đang chạy dở các outcome

    @on(ScanResultsReady)
    def _on_scan_results_ready(self, message: ScanResultsReady) -> None:
        btn = self.query_one("#refresh_markets_btn", Button)
        btn.disabled = False
        btn.label = "⚡ Scan"
        self._update_markets_table(message.matched_results)
        count = len(message.matched_results)
        total = message.total_scanned
        self.last_opps_count = count
        engine_val = self.query_one("#kpi_engine_val", Static)
        engine_sub = self.query_one("#kpi_engine_sub", Static)
        engine_val.update("LIVE STREAM")
        engine_sub.update("Loop: 1s")
        # Continuous loop: auto-trigger next scan after 1s
        self.set_timer(1.0, self.action_refresh_markets)

    @on(PositionsFetchReady)
    def _on_positions_fetch_ready(self, message: PositionsFetchReady) -> None:
        btn = self.query_one("#refresh_positions_btn", Button)
        btn.disabled = False
        btn.label = "🔄 Sync"
        self._update_positions_table(message.mock_positions)

    @on(DataFetchError)
    def _on_data_fetch_error(self, message: DataFetchError) -> None:
        if message.source == "markets":
            btn = self.query_one("#refresh_markets_btn", Button)
            btn.disabled = False
            btn.label = "⚡ Scan"
        elif message.source == "positions":
            btn = self.query_one("#refresh_positions_btn", Button)
            btn.disabled = False
            btn.label = "🔄 Sync"

        self.notify(f"⚠️ {message.source.capitalize()} Error: {message.error_msg}", title="Network Warning", severity="warning")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "refresh_markets_btn":
            self.action_refresh_markets()
        elif event.button.id == "refresh_positions_btn":
            self.action_refresh_positions()

    def _safe_float(self, val, default=0.0):
        try:
            if val is None:
                return default
            f = float(val)
            # handle '0e-18' or tiny float
            return 0.0 if abs(f) < 1e-10 else f
        except (ValueError, TypeError):
            return default

    def _update_balance_ui(self, balance):
        bal_val_elem = self.query_one("#kpi_balance_val", Static)
        bal_sub_elem = self.query_one("#kpi_balance_sub", Static)

        if not balance:
            bal_val_elem.update("$0.00 USDT")
            bal_sub_elem.update("⚠️ Balance Offline")
            return

        data = balance.get('data') if isinstance(balance, dict) else balance
        if isinstance(data, dict):
            avail = self._safe_float(data.get('availableAmount') or data.get('available', 0))
            frozen = self._safe_float(data.get('frozenAmount') or data.get('frozen', 0))
            total = self._safe_float(data.get('totalAmount') or data.get('total', avail + frozen))

            bal_val_elem.update(f"${avail:,.2f} USDT")
            bal_sub_elem.update(f"Frozen: ${frozen:,.2f} | Total: ${total:,.2f}")
        elif isinstance(data, list) and data:
            item = data[0]
            avail = self._safe_float(item.get('availableAmount') or item.get('available', 0))
            frozen = self._safe_float(item.get('frozenAmount') or item.get('frozen', 0))
            symbol = item.get('asset', 'USDT')
            bal_val_elem.update(f"${avail:,.2f} {symbol}")
            bal_sub_elem.update(f"Frozen: ${frozen:,.2f}")
        else:
            bal_val_elem.update("$0.00 USDT")
            bal_sub_elem.update("Syncing...")

    def _update_markets_table(self, scan_results):
        targets_val = self.query_one("#kpi_targets_val", Static)
        targets_sub = self.query_one("#kpi_targets_sub", Static)

        if not scan_results:
            targets_val.update("0 Opps")
            targets_sub.update("Max Gap: 0.00%")
            if getattr(self, "_last_rendered_keys", None) != []:
                table = self.query_one("#markets_table", DataTable)
                table.clear()
                table.add_row("-", "No arbitrage opportunities match criteria", "-", "-", "-", "-", "-", "-")
                self._last_rendered_keys = []
            return

        best_gap = max([item.get('percent_gap', 0) for item in scan_results], default=0.0)
        targets_val.update(f"{len(scan_results)} Opps")
        targets_sub.update(f"🔥 Best Gap: {best_gap:+.2f}%")

        curr_keys = [
            f"{x.get('market_id')}_{x.get('outcome_id')}_{x.get('buyable_shares_payout')}_{x.get('percent_gap')}_{x.get('total_capital_spent')}"
            for x in scan_results
        ]
        if getattr(self, "_last_rendered_keys", None) == curr_keys:
            # Dữ liệu giống hệt lần trước -> Không redraw để tránh giật/mất vị trí dòng đang xem
            return
        self._last_rendered_keys = curr_keys

        table = self.query_one("#markets_table", DataTable)
        table.clear()

        for i, item in enumerate(scan_results):
            market_title = item.get('market_title', 'N/A')
            outcome_title = item.get('outcome_title', '')
            shares = item.get('buyable_shares_payout', 0)
            total_cost = item.get('total_capital_spent', 0)
            gap = item.get('percent_gap', 0)

            market_disp = market_title[:32] + "..." if len(market_title) > 35 else market_title
            outcome_disp = outcome_title[:22] + "..." if len(outcome_title) > 25 else outcome_title

            opt1 = item.get('option_1', {})
            opt2 = item.get('option_2', {})
            opt1_name = opt1.get('name', 'A')[:10]
            opt1_limit = opt1.get('limit_price', opt1.get('vwap', 0))
            opt2_name = opt2.get('name', 'B')[:10]
            opt2_limit = opt2.get('limit_price', opt2.get('vwap', 0))

            price_a_str = f"{opt1_limit:.3f} ({opt1_name})"
            price_b_str = f"{opt2_limit:.3f} ({opt2_name})"

            if gap > 0:
                gap_tag = f"▲ +{gap:.2f}%"
                gap_style = "bold #00e676"
            elif gap >= -2:
                gap_tag = f"◈ {gap:.2f}%"
                gap_style = "bold #38bdf8"
            elif gap >= -5:
                gap_tag = f"▼ {gap:.2f}%"
                gap_style = "bold #facc15"
            else:
                gap_tag = f"▼ {gap:.2f}%"
                gap_style = "bold #f43f5e"

            table.add_row(
                Text(str(i + 1), style="bold cyan"),
                Text(market_disp, style="bold white"),
                Text(outcome_disp, style="#38bdf8"),
                Text(f"{shares:,.0f}", style="#facc15"),
                Text(price_a_str, style="#c084fc"),
                Text(price_b_str, style="#c084fc"),
                Text(f"${total_cost:,.2f}", style="#4ade80"),
                Text(gap_tag, style=gap_style),
            )

    def _update_positions_table(self, positions_data):
        pos_val_elem = self.query_one("#kpi_position_val", Static)
        pos_sub_elem = self.query_one("#kpi_position_sub", Static)

        if not positions_data:
            pos_val_elem.update("$0.00")
            pos_sub_elem.update("PnL: $0.00")
            if getattr(self, "_last_pos_keys", None) != []:
                table = self.query_one("#positions_table", DataTable)
                table.clear()
                table.add_row("-", "No positions active", "-", "-", "-", "-")
                self._last_pos_keys = []
            return

        pos_keys = [
            f"{m.get('marketId')}_{o.get('outcomeName')}_{opt.get('optionTitle')}_{opt.get('currentValue')}"
            for m in positions_data for o in m.get("outcomes", []) for opt in o.get("options", [])
        ]
        if getattr(self, "_last_pos_keys", None) == pos_keys:
            return
        self._last_pos_keys = pos_keys

        table = self.query_one("#positions_table", DataTable)
        table.clear()

        total_init = 0.0
        total_curr = 0.0

        for market in positions_data:
            market_title = market.get('marketTitle', 'Unknown')
            outcomes = market.get("outcomes", [])
            for outcome in outcomes:
                outcome_name = outcome.get("outcomeName", "Unknown")
                options = outcome.get("options", [])

                for i, option in enumerate(options):
                    opt_title = option.get("optionTitle", "N/A")
                    shares = str(option.get("shares", "0"))
                    init_val = float(option.get("initialValue", 0))
                    curr_val = float(option.get("currentValue", 0))

                    total_init += init_val
                    total_curr += curr_val

                    diff = curr_val - init_val
                    percent_diff = (diff / init_val * 100) if init_val > 0 else 0

                    if diff > 0:
                        sign = "+"
                        pnl_style = "bold #00e676"
                        icon = "▲"
                    elif diff < 0:
                        sign = ""
                        pnl_style = "bold #f43f5e"
                        icon = "▼"
                    else:
                        sign = ""
                        pnl_style = "bold white"
                        icon = "◈"

                    pnl_str = Text(f"{icon} ${curr_val:,.2f} ({sign}{percent_diff:.1f}%)", style=pnl_style)

                    disp_market = market_title if i == 0 and outcome == outcomes[0] else ""
                    disp_outcome = outcome_name if i == 0 else ""

                    table.add_row(
                        Text(disp_market[:28], style="bold cyan") if disp_market else "",
                        Text(disp_outcome[:20], style="bold #38bdf8") if disp_outcome else "",
                        Text(opt_title, style="#facc15"),
                        shares,
                        f"${init_val:,.2f}",
                        pnl_str
                    )

        total_diff = total_curr - total_init
        total_pct = (total_diff / total_init * 100) if total_init > 0 else 0
        pnl_color = "#00e676" if total_diff >= 0 else "#f43f5e"
        pos_val_elem.update(f"${total_curr:,.2f}")
        pos_sub_elem.update(f"PnL: {total_diff:+,.2f} ({total_pct:+.2f}%)")
