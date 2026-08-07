import base64
import hashlib
import hmac
import json
import os
import sys
from datetime import datetime, timezone

try:
    import websockets
    import asyncio
except ImportError:
    websockets = None

WS_BASE_URL = os.environ.get("FOREGATE_WS_BASE", "wss://openapi.foregate.com")


def load_env_file(filepath=".env"):
    """Tự động tải biến môi trường từ file .env nếu có."""
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() not in os.environ:
                        os.environ[k.strip()] = v.strip().strip('"').strip("'")


def get_gateway_date():
    """Returns 'YYYY-MM-DD HH:mm:ss' in UTC."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def build_ws_headers_and_path(app_key, app_secret, api_key, path, query):
    """
    Builds signed headers and full path (with sorted query string) 
    for ForeGate Gateway WebSocket HTTP Upgrade handshake.
    """
    date = get_gateway_date()

    # Query keys MUST be sorted alphabetically, values not URL-encoded
    qs = "&".join(f"{k}={query[k]}" for k in sorted(query)) if query else ""
    full_path = f"{path}?{qs}" if qs else path

    # Market channels sign x-api-key
    signed_headers = {"x-api-key": api_key}
    header_lines = [f"{k}:{signed_headers[k]}" for k in sorted(signed_headers)]

    # StringToSign for GET request:
    # GET \n application/json \n \n \n date \n x-api-key:<key> \n full_path
    string_to_sign = "\n".join(
        ["GET", "application/json", "", "", date]
        + header_lines
        + [full_path]
    )

    signature = base64.b64encode(
        hmac.new(
            app_secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).digest()
    ).decode("utf-8")

    headers = {
        "accept": "application/json",
        "date": date,
        "x-ca-key": app_key,
        "x-ca-signature": signature,
        "x-ca-signature-headers": "x-api-key",
        "x-api-key": api_key,
    }

    return full_path, headers


async def listen_orderbook_ws(app_key, app_secret, api_key, market_id, outcome_id, option_id, ws_base=WS_BASE_URL):
    """
    Asyncio generator/listener for ForeGate Orderbook (CLOB price update) WebSocket.
    """
    if websockets is None:
        raise RuntimeError("Thư viện 'websockets' chưa được cài đặt. Vui lòng chạy: pip install websockets")

    path = "/socket/clob-price-update"
    query = {
        "marketId": str(market_id),
        "outcomeId": str(outcome_id),
        "optionId": str(option_id),
    }

    full_path, headers = build_ws_headers_and_path(app_key, app_secret, api_key, path, query)
    ws_url = ws_base + full_path

    print(f"📡 Connecting to WebSocket: {ws_url}")

    try:
        async with websockets.connect(ws_url, additional_headers=headers) as ws:
            print("✅ Kết nối WebSocket ForeGate thành công!")
            async for message in ws:
                try:
                    data = json.loads(message)
                    yield data
                except json.JSONDecodeError:
                    yield message
    except websockets.exceptions.ConnectionClosed as e:
        print(f"⚠️ Connection closed by server: code={e.code}, reason={e.reason}")
        raise RuntimeError(
            f"Kết nối bị ngắt do thị trường (marketId={market_id}) không tồn tại, đã đóng hoặc upstream không khả thi. "
            f"Chi tiết lỗi: {e}"
        ) from e


def get_first_active_market_ids(client):
    """Lấy bộ ID (marketId, outcomeId, optionId) của thị trường đang mở đầu tiên từ REST API."""
    try:
        res = client.request("GET", "/markets/list", query={"page": "1", "pageSize": "5"})
        markets = res.get("data", [])
        for m in markets:
            if m.get("status") == "open" and m.get("outcomes"):
                market_id = m["marketId"]
                outcome = m["outcomes"][0]
                outcome_id = outcome["outcomeId"]
                if outcome.get("options"):
                    option_id = outcome["options"][0]["optionId"]
                    print(f"🔍 Tìm thấy market đang mở: {m.get('title')}")
                    return str(market_id), str(outcome_id), str(option_id)
    except Exception as err:
        print(f"⚠️ Không thể lấy danh sách market qua REST API: {err}")
    return None, None, None


if __name__ == "__main__":
    load_env_file(".env")

    app_key = os.environ.get("FOREGATE_APP_KEY", "")
    app_secret = os.environ.get("FOREGATE_APP_SECRET", "")
    api_key = os.environ.get("FOREGATE_API_KEY", "")

    if not app_key or not app_secret or not api_key:
        print("⚠️ Vui lòng cài đặt các biến môi trường trong file .env hoặc export shell:")
        print("export FOREGATE_APP_KEY='your_app_key'")
        print("export FOREGATE_APP_SECRET='your_app_secret'")
        print("export FOREGATE_API_KEY='your_api_key'")
        sys.exit(1)

    # Thử tự động lấy ID thực tế từ REST API nếu chưa đặt biến môi trường MARKET_ID
    market_id = os.environ.get("MARKET_ID")
    outcome_id = os.environ.get("OUTCOME_ID")
    option_id = os.environ.get("OPTION_ID")

    if not market_id or not outcome_id or not option_id:
        from foregate_client import ForeGateClient
        client = ForeGateClient(app_key, app_secret, api_key)
        m_id, out_id, opt_id = get_first_active_market_ids(client)
        if m_id and out_id and opt_id:
            market_id, outcome_id, option_id = m_id, out_id, opt_id
        else:
            market_id = market_id or "441696984689021917"
            outcome_id = outcome_id or "441696984701605272"
            option_id = option_id or "441696984714189288"

    print(f"🎯 Sử dụng ID: marketId={market_id}, outcomeId={outcome_id}, optionId={option_id}")

    async def main():
        try:
            async for msg in listen_orderbook_ws(app_key, app_secret, api_key, market_id, outcome_id, option_id):
                print("📩 Nhận Orderbook Delta Update:")
                print(json.dumps(msg, indent=2))
        except Exception as e:
            print(f"❌ Lỗi WebSocket: {e}")

    asyncio.run(main())
