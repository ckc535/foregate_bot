"""
Foregate Web Server Entry Point (Redirects to src.web.server)
"""
from src.web.server import app, background_worker, bot_state

if __name__ == "__main__":
    import threading
    t = threading.Thread(target=background_worker, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=5000, debug=False)
