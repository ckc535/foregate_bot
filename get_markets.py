import os
import sys
import requests
import json
import base64
import hashlib
import hmac
import time
from datetime import datetime, timezone, timedelta

from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.spinner import Spinner
from rich.panel import Panel
from rich.layout import Layout
from rich.align import Align
from rich.text import Text


def load_env_file(filepath=".env"):
    """Tự động tải các biến môi trường từ file .env nếu có."""
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() not in os.environ:
                        os.environ[k.strip()] = v.strip().strip('"').strip("'")


# --- Tải biến môi trường từ .env ---
load_env_file(".env")

APP_KEY = os.environ.get("FOREGATE_APP_KEY", "")
APP_SECRET = os.environ.get("FOREGATE_APP_SECRET", "")
API_KEY = os.environ.get("FOREGATE_API_KEY", "")
BASE_URL = os.environ.get("FOREGATE_BASE_URL", "https://openapi.foregate.com")
MIN_VOLUME = float(os.environ.get("MIN_MARKET_VOLUME", "10000"))
MAX_ENDTIME_DAYS = float(os.environ.get("MAX_ENDTIME_DAYS", "1"))

console = Console()

def generate_headers(path):
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    string_to_sign = "\n".join([
        "GET",
        "application/json",
        "",
        "",
        date_str,
        f"x-api-key:{API_KEY}",
        path
    ])
    signature = base64.b64encode(
        hmac.new(APP_SECRET.encode('utf-8'), string_to_sign.encode('utf-8'), hashlib.sha256).digest()
    ).decode('utf-8')
    return {
        "x-ca-key": APP_KEY,
        "x-api-key": API_KEY,
        "x-ca-signature-headers": "x-api-key",
        "x-ca-signature": signature,
        "accept": "application/json",
        "date": date_str
    }

def generate_table(markets):
    table = Table(
        title="[bold magenta]LIVE MARKET TRACKER (TOP 10 THEO ENDTIME)[/bold magenta]", 
        show_header=True, 
        header_style="bold yellow", 
        show_lines=True,
        expand=True
    )
    table.add_column("STT", style="dim", width=4, justify="center", no_wrap=True)
    table.add_column("Market ID", style="cyan", justify="center", no_wrap=True)
    table.add_column("Title", style="white", ratio=3)
    table.add_column("Available Outcomes\n(Name: Chance)", style="green", ratio=2)
    table.add_column("End Time", style="bold red", justify="center", no_wrap=True)
    
    if not markets:
        for i in range(10):
            if i == 0:
                table.add_row("1", "-", "Đang tải dữ liệu thị trường...", "-", "-")
            else:
                table.add_row(str(i+1), "-", "-", "-", "-")
        return table
        
    for i in range(10):
        if i < len(markets):
            m = markets[i]
            market_id = str(m.get('marketId', 'N/A'))
            
            title = m.get('title', 'N/A')
                
            outcomes_list = m.get('outcomes', [])
            outcomes_str = []
            for oc in outcomes_list:
                name = oc.get('name', 'N/A')
                chance = oc.get('chance', 0)
                outcomes_str.append(f"• {name}: [bold]{chance}%[/bold]")
            
            if len(outcomes_str) > 3:
                outcomes_display = "\n".join(outcomes_str[:3]) + f"\n[dim italic]... (+{len(outcomes_str) - 3} outcomes khác)[/dim italic]"
            elif outcomes_str:
                outcomes_display = "\n".join(outcomes_str)
            else:
                outcomes_display = "N/A"
            end_time = str(m.get('endTime', 'N/A'))
            
            table.add_row(str(i+1), market_id, title, outcomes_display, end_time)
        else:
            # Dòng trống để giữ fixed height
            table.add_row(str(i+1), "-", "-", "-", "-")
            
    return table

def run_live_dashboard():
    # Setup Layout màn hình
    layout = Layout()
    layout.split(
        Layout(name="header", size=3),
        Layout(name="main"),
        Layout(name="footer", size=3)
    )
    
    layout["header"].update(Panel(Align.center("[bold blue]🚀 CONTINUOUS MARKET MONITOR DASHBOARD 🚀[/bold blue]")))
    spinner = Spinner("bouncingBar", text="[cyan]Khởi tạo hệ thống...")
    layout["footer"].update(Panel(spinner))
    layout["main"].update(generate_table([]))
    
    # Bật Live chế độ toàn màn hình và loop vô tận bên trong nó
    with Live(layout, refresh_per_second=4, screen=True, console=console) as live:
        with requests.Session() as session:
            while True:  # Vòng lặp Interval
                all_markets = []
                page = 1
                page_size = 100
                
                # Fetch dữ liệu
                while True:
                    spinner.text = Text.from_markup(f"[cyan]Đang tải trang {page} (Size: {page_size}) | Đã thu thập {len(all_markets)} markets...[/cyan]")
                    layout["footer"].update(Panel(spinner))
                    
                    path = f"/markets/list?page={page}&pageSize={page_size}"
                    url = f"{BASE_URL}{path}"
                    headers = generate_headers(path)
                    
                    try:
                        response = session.get(url, headers=headers)
                        response.raise_for_status() 
                        
                        data_json = response.json()
                        items = data_json.get('data', [])
                        
                        if not items:
                            break
                            
                        # Lọc ngay khi nhận data
                        for item in items:
                            # Lọc volume khác None và > MIN_VOLUME từ env
                            vol_raw = item.get('volume')
                            if vol_raw is None:
                                continue
                            try:
                                vol = float(vol_raw)
                                if vol < MIN_VOLUME:
                                    continue
                            except (ValueError, TypeError):
                                continue

                            # Lọc endTime tối đa +MAX_ENDTIME_DAYS ngày tính từ thời điểm hiện tại
                            end_time_str = item.get('endTime')
                            if not end_time_str:
                                continue
                            try:
                                if "T" in end_time_str:
                                    end_dt = datetime.fromisoformat(end_time_str.replace("Z", "+00:00"))
                                else:
                                    end_dt = datetime.strptime(end_time_str, "%Y-%m-%d %H:%M:%S")
                                
                                if end_dt.tzinfo is None:
                                    end_dt = end_dt.replace(tzinfo=timezone.utc)
                                
                                now_utc = datetime.now(timezone.utc)
                                max_end_dt = now_utc + timedelta(days=MAX_ENDTIME_DAYS)
                                
                                if end_dt > max_end_dt or end_dt < now_utc:
                                    continue
                            except Exception:
                                continue

                            valid_outcomes = []
                            for outcome in item.get('outcomes', []):
                                chance = outcome.get('chance')
                                if chance is not None and (chance <= 0.1 or chance >= 99.9):
                                    continue
                                valid_outcomes.append(outcome)
                                
                            if valid_outcomes:
                                item['outcomes'] = valid_outcomes
                                all_markets.append(item)
                        
                        # Sort liên tục để lúc fetch cũng thấy bảng thay đổi real-time
                        all_markets.sort(key=lambda x: x.get('endTime') if x.get('endTime') is not None else 0)
                        layout["main"].update(generate_table(all_markets))
                        
                        if len(items) < page_size:
                            break
                            
                        page += 1
                    except requests.exceptions.RequestException as e:
                        spinner.text = Text.from_markup(f"[bold red]Lỗi khi gọi API tại trang {page}: {e}[/bold red]")
                        layout["footer"].update(Panel(spinner))
                        time.sleep(3)
                        break
                
                # Sau khi tải xong toàn bộ các page -> lưu file json
                if all_markets:
                    file_name = 'all_markets_sorted.json'
                    with open(file_name, 'w', encoding='utf-8') as f:
                        json.dump(all_markets, f, indent=4, ensure_ascii=False)
                
                # Chế độ chờ interval (vd 10 giây)
                countdown_seconds = 10
                for i in range(countdown_seconds, 0, -1):
                    spinner.text = Text.from_markup(f"[bold green]✔ Tải xong {len(all_markets)} markets hợp lệ![/bold green] (Đã lưu JSON). Chu kỳ tiếp theo sau [bold yellow]{i}s[/bold yellow]...")
                    layout["footer"].update(Panel(spinner))
                    time.sleep(1)

def main():
    try:
        run_live_dashboard()
    except KeyboardInterrupt:
        # Nếu người dùng ấn Ctrl+C thì thoát êm ái
        console.print("[bold red]Đã dừng hệ thống theo yêu cầu.[/bold red]")

if __name__ == "__main__":
    main()