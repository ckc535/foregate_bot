import json
import os
from datetime import datetime, timezone, timedelta
from src.config import MIN_VOLUME, MAX_ENDTIME_DAYS


def fetch_and_filter_markets(client, min_volume=MIN_VOLUME, max_endtime_days=MAX_ENDTIME_DAYS):
    """
    Lấy danh sách các market từ ForeGate API thông qua `client` và lọc:
    - volume >= min_volume
    - category != "10"
    - endTime không vượt quá max_endtime_days ngày so với hiện tại
    - Loại bỏ các outcome có chance <= 0.1 hoặc >= 99.9
    """
    all_markets = []
    page = 1
    page_size = 100

    while True:
        try:
            data_json = client.get_markets(page=page, page_size=page_size)
            if isinstance(data_json, str):
                try:
                    data_json = json.loads(data_json)
                except Exception:
                    data_json = {}

            if not isinstance(data_json, dict):
                break

            data_obj = data_json.get('data', {})
            if isinstance(data_obj, dict):
                items = data_obj.get('records', [])
            elif isinstance(data_obj, list):
                items = data_obj
            else:
                items = []

            if not items:
                break

            for item in items:
                vol_raw = item.get('volume')
                if vol_raw is None:
                    continue
                try:
                    vol = float(vol_raw)
                    if vol < min_volume:
                        continue
                except (ValueError, TypeError):
                    continue

                if item.get('category') == "10":
                    continue

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
            print(f"[fetch_and_filter_markets] Lỗi khi gọi API tại trang {page}: {e}")
            break

    all_markets.sort(key=lambda x: x.get('endTime') if x.get('endTime') is not None else "")

    try:
        with open("all_markets_sorted.json", "w", encoding="utf-8") as f:
            json.dump(all_markets, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Lỗi khi ghi file json: {e}")

    return all_markets
