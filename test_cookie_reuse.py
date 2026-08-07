import json
import os
import sys
import time
from datetime import datetime, timezone
from foregate_client import ForeGateClient


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


def get_current_utc_str():
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def main():
    load_env_file(".env")

    app_key = os.environ.get("FOREGATE_APP_KEY", "")
    app_secret = os.environ.get("FOREGATE_APP_SECRET", "")
    api_key = os.environ.get("FOREGATE_API_KEY", "")

    if not app_key or not app_secret or not api_key:
        print("⚠️ Vui lòng cấu hình các biến môi trường FOREGATE_APP_KEY, FOREGATE_APP_SECRET, FOREGATE_API_KEY trong file .env")
        sys.exit(1)

    client = ForeGateClient(app_key, app_secret, api_key)

    # --------------------------------------------------------------------------
    # BƯỚC 1: Lấy / Thiết lập 1 Session Cookie duy nhất ban đầu
    # --------------------------------------------------------------------------
    session_cookie = os.environ.get("FOREGATE_SESSION_COOKIE")

    if session_cookie:
        print("🔑 Đã tìm thấy Session Cookie trong .env, tiến hành sử dụng cho 10 lần gọi...")
        client.set_session_cookie(session_cookie)
    else:
        merchant_user_id = os.environ.get("FOREGATE_MERCHANT_USER_ID", "mer_user_001")
        user_email = os.environ.get("FOREGATE_USER_EMAIL", "user001@gmail.com")
        merchant_id = os.environ.get("FOREGATE_MERCHANT_ID", "101001")

        print("📝 Đang khởi tạo đăng ký User 1 LẦN DUY NHẤT để lấy Session Cookie...")
        try:
            reg_res = client.register(
                marchant_user_id=merchant_user_id,
                user_email=user_email,
                merchant_id=merchant_id
            )
            print(f"✅ Đăng ký User thành công! Session Cookie: {client.session_cookie}\n")
        except Exception as err:
            print(f"⚠️ Đăng ký user thất bại: {err}")
            print("👉 Nếu tài khoản của bạn đã được cấp Session Cookie sẵn, vui lòng thêm FOREGATE_SESSION_COOKIE vào file .env\n")

    market_id = os.environ.get("MARKET_ID", "2051")
    outcome_id = os.environ.get("OUTCOME_ID", "3665")
    option_id = os.environ.get("OPTION_ID", "7574")

    total_calls = 10
    sleep_seconds = 5
    success_count = 0

    print("=========================================================================================================")
    print(f"🔄 THỬ NGHIỆM TÁI SỬ DỤNG 1 COOKIE DUY NHẤT CHO {total_calls} LẦN GỌI REST ORDERBOOK (CÁCH NHAU {sleep_seconds}S)")
    print("=========================================================================================================")
    print(f"🔑 Session Cookie đang dùng : {client.session_cookie}")
    print(f"📊 Market Target            : marketId={market_id}, outcomeId={outcome_id}, optionId={option_id}")
    print("=========================================================================================================\n")

    print(f" {'Lần gọi':<10} | {'Thời gian (UTC)':<15} | {'Trạng thái API':<18} | {'Best Ask':<10} | {'Best Bid':<10} | {'Chi tiết'}")
    print(" " + "-" * 95)

    for i in range(1, total_calls + 1):
        timestamp = get_current_utc_str()
        try:
            # Sử dụng lại đúng client (với 1 cookie duy nhất đã set)
            response = client.get_orderbook(market_id, outcome_id, option_id)
            code = response.get("code")
            data = response.get("data", {})

            if code == 0:
                raw_bids = data.get("bids", [])
                raw_asks = data.get("asks", [])
                best_ask = sorted(raw_asks, key=lambda x: float(x.get("price", 0)))[0]["price"] if raw_asks else "N/A"
                best_bid = sorted(raw_bids, key=lambda x: float(x.get("price", 0)), reverse=True)[0]["price"] if raw_bids else "N/A"

                success_count += 1
                print(
                    f" Call {i:<5}/{total_calls} | {timestamp:<15} | ✅ Thành công (200) | "
                    f"{best_ask:<10} | {best_bid:<10} | Cookie OK (Reused)"
                )
            else:
                msg = response.get("message", "Error")
                print(f" Call {i:<5}/{total_calls} | {timestamp:<15} | ❌ Lỗi code={code:<7} | {'N/A':<10} | {'N/A':<10} | {msg}")

        except Exception as e:
            err_str = str(e)
            if len(err_str) > 35:
                err_str = err_str[:32] + "..."
            print(f" Call {i:<5}/{total_calls} | {timestamp:<15} | ❌ Lỗi HTTP        | {'N/A':<10} | {'N/A':<10} | {err_str}")

        # Nghỉ 5 giây trước lần gọi tiếp theo (trừ lần cuối cùng)
        if i < total_calls:
            time.sleep(sleep_seconds)

    print("\n=========================================================================================================")
    print(f"🏁 TỔNG KẾT KẾT QUẢ THỬ NGHIỆM:")
    print(f"   ► Tổng số lần gọi          : {total_calls}")
    print(f"   ► Thành công (Dùng 1 Cookie): {success_count}/{total_calls}")
    print(f"   ► Thất bại                 : {total_calls - success_count}/{total_calls}")

    if success_count == total_calls:
        print("🎉 KẾT LUẬN: 1 Session Cookie hoạt động BỀN VỮNG và TÁI SỬ DỤNG HOÀN HẢO cho nhiều lần gọi liên tiếp!")
    else:
        print("⚠️ KẾT LUẬN: Có lần gọi bị lỗi, cần kiểm tra nguyên nhân cookie/hạn ngạch API.")
    print("=========================================================================================================\n")


if __name__ == "__main__":
    main()
