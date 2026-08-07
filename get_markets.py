import os
from datetime import datetime, timezone, timedelta

def fetch_and_filter_markets(client, min_volume=10000.0, max_endtime_days=1.0):
    """
    Lấy danh sách các market từ ForeGate API thông qua `client` và lọc:
    - volume >= min_volume
    - endTime không vượt quá max_endtime_days ngày so với hiện tại
    - Loại bỏ các outcome có chance <= 0.1 hoặc >= 99.9
    """
    all_markets = []
    page = 1
    page_size = 100
    
    while True:
        try:
            data_json = client.get_markets(page=page, page_size=page_size)
            items = data_json.get('data', [])
            
            if not items:
                break
                
            for item in items:
                # Lọc volume
                vol_raw = item.get('volume')
                if vol_raw is None:
                    continue
                try:
                    vol = float(vol_raw)
                    if vol < min_volume:
                        continue
                except (ValueError, TypeError):
                    continue

                # Tạm thời loại bỏ market có category = 10
                if item.get('category') == "10":
                    continue

                # Lọc endTime
                try:
                    end_time_str = item.get('endTime', '')
                    if not end_time_str:
                        continue
                    
                    if "T" in end_time_str:
                        end_dt = datetime.fromisoformat(end_time_str.replace("Z", "+00:00"))
                    else:
                        end_dt = datetime.strptime(end_time_str, "%Y-%m-%d %H:%M:%S")
                    
                    if end_dt.tzinfo is None:
                        end_dt = end_dt.replace(tzinfo=timezone.utc)
                    
                    now_utc = datetime.now(timezone.utc)
                    max_end_dt = now_utc + timedelta(days=max_endtime_days)
                    
                    if end_dt > max_end_dt or end_dt < now_utc:
                        continue
                except Exception:
                    continue

                # Lọc outcomes
                valid_outcomes = []
                for outcome in item.get('outcomes', []):
                    chance = outcome.get('chance')
                    if chance is not None and (chance <= 0.1 or chance >= 99.9):
                        continue
                    valid_outcomes.append(outcome)
                    
                if valid_outcomes:
                    item['outcomes'] = valid_outcomes
                    all_markets.append(item)
            
            if len(items) < page_size:
                break
                
            page += 1
            
        except Exception as e:
            # Có thể log lỗi ra file hoặc print, ở đây raise để main.py bắt
            print(f"[get_markets] Lỗi khi gọi API tại trang {page}: {e}")
            break
            
    # Sort theo endTime (tăng dần)
    all_markets.sort(key=lambda x: x.get('endTime') if x.get('endTime') is not None else "")
    
    # Ghi ra file để check
    try:
        import json
        with open("all_markets_sorted.json", "w", encoding="utf-8") as f:
            json.dump(all_markets, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Lỗi khi ghi file json: {e}")

    return all_markets

if __name__ == "__main__":
    import json
    from foregate_client import ForeGateClient

    # Tải test env nếu có (giả lập)
    APP_KEY = os.environ.get("FOREGATE_APP_KEY", "")
    APP_SECRET = os.environ.get("FOREGATE_APP_SECRET", "")
    API_KEY = os.environ.get("FOREGATE_API_KEY", "")
    
    client = ForeGateClient(
        app_key=APP_KEY,
        app_secret=APP_SECRET,
        api_key=API_KEY,
    )
    
    MIN_VOL = float(os.environ.get("MIN_MARKET_VOLUME", "10000"))
    MAX_DAYS = float(os.environ.get("MAX_ENDTIME_DAYS", "100"))
    
    markets = fetch_and_filter_markets(client, MIN_VOL, MAX_DAYS)
    print(f"Total valid markets: {len(markets)}")
    print(json.dumps(markets[:2], indent=2, ensure_ascii=False)) # In 2 market đầu tiên