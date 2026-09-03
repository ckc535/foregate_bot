import argparse
import os
import sys
import threading
import webbrowser


if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


def run_tui():
    from src.ui.dashboard import ForegateDashboard
    app = ForegateDashboard()
    app.run()


def run_web():
    from src.web.server import app, background_worker
    
    t = threading.Thread(target=background_worker, daemon=True)
    t.start()
    
    port = int(os.environ.get("PORT", 5000))
    url = f"http://localhost:{port}"
    print("\n" + "=" * 55)
    print("🚀 FOREGATE CYBERPUNK 3D TERMINAL (OPTIMIZED)")
    print(f"🔗 Mở trình duyệt tại: {url}")
    print("=" * 55 + "\n")
    
    # Auto open browser in background
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Foregate Arbitrage Bot & 3D Dashboard")
    parser.add_argument("--web", "-w", action="store_true", help="Chạy giao diện Web Dashboard 3D")
    parser.add_argument("--tui", "-t", action="store_true", help="Chạy giao diện Terminal 3D Depth TUI")
    args = parser.parse_args()

    if args.web:
        run_web()
    else:
        run_tui()