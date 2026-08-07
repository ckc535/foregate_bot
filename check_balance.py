import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
import requests

# --- Các thông tin xác thực của bạn ---
APP_KEY = "f0bb3oiM3nPFOn7LNBqnkUlbTS28qpzb"
APP_SECRET = "c584c9f4dabf86b8c1c2e58a494f874147c0fcc97a162d0bd750410c96c18639"
API_KEY = "72f89c34-81b2-4d71-b609-2ef18bc47391"
BASE_URL = "https://openapi.foregate.com"

class ForeGateClient:
    def __init__(self, app_key, app_secret, api_key, base_url=BASE_URL):
        self.app_key = app_key
        self.app_secret = app_secret
        self.api_key = api_key
        self.base_url = base_url
        self.session_cookie = None
        self.http = requests.Session()

    def set_session_cookie(self, cookie):
        self.session_cookie = cookie

    @staticmethod
    def gateway_date():
        """'YYYY-MM-DD HH:mm:ss' in UTC."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def signed_path(path, query):
        """Path + query, query keys sorted alphabetically, values NOT URL-encoded."""
        if not query:
            return path
        qs = "&".join(f"{k}={query[k]}" for k in sorted(query))
        return f"{path}?{qs}"

    def sign(self, method, content_md5, content_type, date, signed_headers, path):
        header_lines = [f"{k}:{signed_headers[k]}" for k in sorted(signed_headers)]
        string_to_sign = "\n".join(
            [method, "application/json", content_md5, content_type, date]
            + header_lines
            + [path]
        )
        digest = hmac.new(
            self.app_secret.encode(), string_to_sign.encode(), hashlib.sha256
        ).digest()
        return base64.b64encode(digest).decode()

    def request(self, method, path, query=None, body=None, cookie_required=False):
        has_body = body is not None
        raw_body = json.dumps(body, separators=(",", ":")) if has_body else ""
        content_md5 = (
            base64.b64encode(hashlib.md5(raw_body.encode()).digest()).decode()
            if has_body
            else ""
        )
        content_type = "application/json" if has_body else ""
        date = self.gateway_date()
        full_path = self.signed_path(path, query)

        # REST requests sign only x-api-key (the cookie is sent but not signed)
        signature = self.sign(
            method.upper(), content_md5, content_type, date,
            {"x-api-key": self.api_key}, full_path,
        )

        headers = {
            "accept": "application/json",
            "date": date,
            "x-ca-key": self.app_key,
            "x-ca-signature": signature,
            "x-ca-signature-headers": "x-api-key",
            "x-api-key": self.api_key,
        }
        if has_body:
            headers["content-type"] = "application/json"
            headers["content-md5"] = content_md5
            
        if self.session_cookie:
            headers["cookie"] = self.session_cookie
        elif cookie_required:
            raise RuntimeError(f"{path} requires a session cookie - call register() first")

        res = self.http.request(
            method.upper(), self.base_url + full_path,
            headers=headers, data=raw_body if has_body else None,
        )

        # Capture the session cookie from Set-Cookie (e.g. on /user/register)
        if res.cookies:
            self.session_cookie = "; ".join(f"{c.name}={c.value}" for c in res.cookies)

        if not res.ok:
            gateway_msg = res.headers.get("x-ca-error-message", "")
            raise RuntimeError(f"HTTP {res.status_code} ({gateway_msg}): {res.text}")
        try:
            return res.json()
        except ValueError:
            return res.text

    def register(self, marchant_user_id, user_email=None, merchant_id=None):
        body = {"marchantUserId": marchant_user_id}
        if user_email:
            body["userEmail"] = user_email
        if merchant_id:
            body["merchantId"] = merchant_id
        return self.request("POST", "/user/register", body=body)


def fetch_balance():
    client = ForeGateClient(
        app_key=APP_KEY,
        app_secret=APP_SECRET,
        api_key=API_KEY,
    )
    
    try:
        # 1. Gọi /user/register để lấy session cookie
        # Bạn có thể đổi 'mer_ciismd' thành ID của user thực tế bên bạn
        print("Registering/Logging in to get session cookie...")
        user = client.register("ckc", "ckc@gmail.com", "101001")
        print("Logged in successfully. User data:", user.get("data", {}))
        
        # 2. Check balances
        print("\nFetching balance...")
        assets = client.request("GET", "/account/assets")
        print("Balance Response:")
        print(json.dumps(assets, indent=4, ensure_ascii=False))
        
    except Exception as e:
        print(f"Error calling API: {e}")

if __name__ == "__main__":
    fetch_balance()
