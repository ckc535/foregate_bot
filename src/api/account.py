def fetch_balance(client):
    """
    Lấy thông tin balance/assets của tài khoản.
    Nếu session_cookie hết hạn (data returns None), tự động register lấy lại cookie mới.
    """
    try:
        if not client.session_cookie:
            client.register("ckc", "ckc@gmail.com", "101001")

        assets = client.get_assets()
        if isinstance(assets, dict) and assets.get('data') is None:
            client.session_cookie = None
            client.register("ckc", "ckc@gmail.com", "101001")
            assets = client.get_assets()

        return assets

    except Exception as e:
        print(f"Error fetching balance: {e}")
        return None
