import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone

import requests

BASE_URL = os.environ.get("FOREGATE_BASE_URL", "https://openapi.foregate.com")


class ForeGateClient:
    def __init__(self, app_key, app_secret, api_key, base_url=BASE_URL):
        self.app_key = app_key
        self.app_secret = app_secret
        self.api_key = api_key
        self.base_url = base_url
        self.session_cookie = os.environ.get("FOREGATE_SESSION_COOKIE", None)
        self._http = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=50, pool_maxsize=50)
        self._http.mount('https://', adapter)
        self._http.mount('http://', adapter)

    def set_session_cookie(self, cookie):
        self.session_cookie = cookie

    @staticmethod
    def gateway_date():
        """'YYYY-MM-DD HH:mm:ss' in UTC."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def signed_path(path, query):
        """Path + query, keys sorted alphabetically, values NOT URL-encoded."""
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

    def request(self, method, path, query=None, body=None, cookie_required=False, _retry_count=0):
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
            raise RuntimeError(f"{path} requires a session cookie - call register() or set FOREGATE_SESSION_COOKIE first")

        res = self._http.request(
            method.upper(), self.base_url + full_path,
            headers=headers, data=raw_body if has_body else None,
            timeout=10
        )

        # Capture the session cookie / acw_tc Gateway cookie from Set-Cookie
        if res.cookies:
            self.session_cookie = "; ".join(f"{c.name}={c.value}" for c in res.cookies)

        # Nếu thiếu Header Cookie trên request 1, Gateway sẽ trả về 401 và phát hành acw_tc Cookie.
        # Tự động thử lại 1 lần duy nhất với Cookie vừa bắt được.
        if not res.ok and _retry_count == 0 and "Missing Cookie header" in res.text and self.session_cookie:
            return self.request(method, path, query=query, body=body, cookie_required=cookie_required, _retry_count=1)

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

    def get_orderbook(self, market_id, outcome_id, option_id):
        """Lấy Orderbook Snapshot từ REST API GET /orderbook/book."""
        return self.request(
            "GET",
            "/orderbook/book",
            query={
                "marketId": str(market_id),
                "outcomeId": str(outcome_id),
                "optionId": str(option_id),
            },
        )

    def get_markets(self, page=1, page_size=100):
        """Lấy danh sách thị trường."""
        return self.request(
            "GET",
            "/markets/list",
            query={
                "page": str(page),
                "pageSize": str(page_size),
            }
        )

    def get_assets(self):
        """Lấy số dư (balance) tài khoản."""
        return self.request(
            "GET",
            "/account/assets",
            cookie_required=True
        )
