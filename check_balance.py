import json

def fetch_balance(client):
    """
    Sử dụng client đã được khởi tạo để lấy thông tin balance.
    Nêu session_cookie hết hạn (data returns None), tự động register lấy lại cookie mới.
    """
    try:
        if not client.session_cookie:
            client.register("ckc", "ckc@gmail.com", "101001")
        
        assets = client.get_assets()
        if isinstance(assets, dict) and assets.get('data') is None:
            # Cookie có thể đã hết hạn, xóa cookie cũ và register lại
            client.session_cookie = None
            client.register("ckc", "ckc@gmail.com", "101001")
            assets = client.get_assets()

        return assets
        
    except Exception as e:
        print(f"Error fetching balance: {e}")
        return None

if __name__ == "__main__":
    import os
    from foregate_client import ForeGateClient

    APP_KEY = os.environ.get("FOREGATE_APP_KEY", "")
    APP_SECRET = os.environ.get("FOREGATE_APP_SECRET", "")
    API_KEY = os.environ.get("FOREGATE_API_KEY", "")
    print(APP_KEY)
    print(APP_SECRET)
    print(API_KEY)

    client = ForeGateClient(
        app_key=APP_KEY,
        app_secret=APP_SECRET,
        api_key=API_KEY,
    )
    
    balance = fetch_balance(client)
    print(json.dumps(balance, indent=4, ensure_ascii=False))
