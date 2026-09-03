import os

def load_env_file(filepath=".env"):
    """Tự động tải các biến môi trường từ file .env nếu chưa được thiết lập."""
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k_str = k.strip()
                    if k_str not in os.environ:
                        os.environ[k_str] = v.strip().strip('"').strip("'")

# Nạp file .env ngay khi import module
load_env_file(".env")

# API Configuration
BASE_URL = os.environ.get("FOREGATE_BASE_URL", "https://openapi.foregate.com")
APP_KEY = os.environ.get("FOREGATE_APP_KEY", "")
APP_SECRET = os.environ.get("FOREGATE_APP_SECRET", "")
API_KEY = os.environ.get("FOREGATE_API_KEY", "")
SESSION_COOKIE = os.environ.get("FOREGATE_SESSION_COOKIE", None)

# Default Scanner Thresholds
MIN_VOLUME = float(os.environ.get("MIN_MARKET_VOLUME", "10000"))
MAX_ENDTIME_DAYS = float(os.environ.get("MAX_ENDTIME_DAYS", "0.5"))
TARGET_MAX_GAP_PCT = float(os.environ.get("TARGET_MAX_GAP_PCT") or os.environ.get("MM_TARGET_MAX_GAP_PCT", "5.0"))
