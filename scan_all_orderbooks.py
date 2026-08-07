import json
import os
import sys
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from foregate_client import ForeGateClient
from get_orderbook_rest import load_env_file, analyze_dual_option_hedged_buy

# Lock để đồng bộ tiến trình và lưu file an toàn
results_lock = Lock()


def process_outcome(task_data, app_key, app_secret, api_key, session_cookie, target_max_gap_pct):
    market_id = task_data['market_id']
    market_title = task_data['market_title']
    outcome_id = task_data['outcome_id']
    outcome_title = task_data['outcome_title']
    opt1 = task_data['opt1']
    opt2 = task_data['opt2']

    client = ForeGateClient(app_key, app_secret, api_key)
    if session_cookie:
        client.set_session_cookie(session_cookie)

    try:
        res1 = client.get_orderbook(market_id, outcome_id, opt1['option_id'])
        res2 = client.get_orderbook(market_id, outcome_id, opt2['option_id'])

        data1 = res1.get("data", {}) if isinstance(res1, dict) and res1.get("code") == 0 else {}
        data2 = res2.get("data", {}) if isinstance(res2, dict) and res2.get("code") == 0 else {}

        raw_asks_1 = data1.get("asks", [])
        raw_asks_2 = data2.get("asks", [])

        if not raw_asks_1 or not raw_asks_2:
            return None

        sorted_asks_1 = sorted(raw_asks_1, key=lambda x: float(x.get("price", 0)))
        sorted_asks_2 = sorted(raw_asks_2, key=lambda x: float(x.get("price", 0)))

        plan = analyze_dual_option_hedged_buy(
            sorted_asks_1, sorted_asks_2, opt1['name'], opt2['name'], target_max_gap_pct=target_max_gap_pct
        )

        if plan and plan.get("executable"):
            if plan.get("target_shares", 0) <= 50.0:
                return None
            total_ab = plan['total_investment']
            payout_win = plan['guaranteed_payout']
            gap_val = plan['gap_between_sides_pct']

            orders_1 = plan.get('orders_higher', [])
            orders_2 = plan.get('orders_lower', [])

            return {
                "market_id": market_id,
                "market_title": market_title,
                "outcome_id": outcome_id,
                "outcome_title": outcome_title,
                "option_1": {
                    "option_id": opt1['option_id'],
                    "name": plan['higher_name'],
                    "vwap": plan['avg_price_higher'],
                    "cost": plan['cost_higher'],
                    "orders_breakdown": [
                        {"price": o.get("limitPrice", 0), "shares": o.get("share", 0), "cost": o.get("value", 0)}
                        for o in orders_1
                    ]
                },
                "option_2": {
                    "option_id": opt2['option_id'],
                    "name": plan['lower_name'],
                    "vwap": plan['avg_price_lower'],
                    "cost": plan['cost_lower'],
                    "orders_breakdown": [
                        {"price": o.get("limitPrice", 0), "shares": o.get("share", 0), "cost": o.get("value", 0)}
                        for o in orders_2
                    ]
                },
                "buyable_shares_payout": plan['target_shares'],
                "total_capital_spent": total_ab,
                "percent_gap": gap_val,
                "target_max_gap_pct": target_max_gap_pct
            }
    except Exception as e:
        # Debug error if any occurs
        # print(f"Error processing {market_id}: {e}")
        pass
    return None


def scan_all_markets(json_file='all_markets_sorted.json', output_json='scan_results.json', output_txt='scan_results.txt'):
    load_env_file(".env")

    app_key = os.environ.get("FOREGATE_APP_KEY", "")
    app_secret = os.environ.get("FOREGATE_APP_SECRET", "")
    api_key = os.environ.get("FOREGATE_API_KEY", "")

    if not app_key or not app_secret or not api_key:
        print("⚠️ Vui lòng cấu hình các biến môi trường FOREGATE_APP_KEY, FOREGATE_APP_SECRET, FOREGATE_API_KEY trong file .env")
        sys.exit(1)

    session_cookie = os.environ.get("FOREGATE_SESSION_COOKIE")
    target_gap_env = os.environ.get("TARGET_MAX_GAP_PCT") or os.environ.get("MM_TARGET_MAX_GAP_PCT", "5.0")
    target_max_gap_pct = float(target_gap_env)

    max_workers_env = os.environ.get("MAX_WORKERS", "15")
    max_workers = int(max_workers_env)

    if not os.path.exists(json_file):
        print(f"❌ File {json_file} không tồn tại. Vui lòng chạy get_markets.py trước để tạo file json.")
        sys.exit(1)

    with open(json_file, 'r', encoding='utf-8') as f:
        markets = json.load(f)

    # Chuẩn bị danh sách công việc
    tasks = []
    for m in markets:
        market_id = str(m.get('marketId', ''))
        market_title = m.get('title', 'N/A')
        for oc in m.get('outcomes', []):
            options = oc.get('options', [])
            if len(options) >= 2:
                tasks.append({
                    "market_id": market_id,
                    "market_title": market_title,
                    "outcome_id": str(oc.get('outcomeId', '')),
                    "outcome_title": oc.get('title') or oc.get('name') or "Outcome",
                    "opt1": {"option_id": str(options[0].get('optionId', '')), "name": options[0].get('title') or "Option 1"},
                    "opt2": {"option_id": str(options[1].get('optionId', '')), "name": options[1].get('title') or "Option 2"},
                })

    total_tasks = len(tasks)
    print("====================================================================================================")
    print(f"🚀 BẮT ĐẦU QUÉT ĐA THREAD ({max_workers} THREADS) TẤT CẢ MARKETS & OUTCOMES")
    print(f"   • Mức Độ Lệch Tối Đa Yêu Cầu (Target Max Gap %): <= {target_max_gap_pct:.2f}%")
    print(f"   • Tổng số Outcomes cần kiểm tra: {total_tasks}")
    print(f"   • Kết quả sẽ ghi vào: {output_json} & {output_txt}")
    print("====================================================================================================\n")

    start_time = time.time()
    matched_results = []
    completed_count = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_task = {
            executor.submit(process_outcome, t, app_key, app_secret, api_key, session_cookie, target_max_gap_pct): t
            for t in tasks
        }

        for future in as_completed(future_to_task):
            res = future.result()
            completed_count += 1

            if res:
                with results_lock:
                    matched_results.append(res)

            # In thanh tiến trình 1 dòng duy nhất trên terminal (không bị spam màn hình)
            elapsed = time.time() - start_time
            speed = completed_count / elapsed if elapsed > 0 else 0
            sys.stdout.write(
                f"\r⏳ Tiến độ: {completed_count}/{total_tasks} Outcomes ({(completed_count/total_tasks)*100:.1f}%) | "
                f"✅ Khớp: {len(matched_results)} | ⚡ Tốc độ: {speed:.1f} op/s"
            )
            sys.stdout.flush()

    elapsed_total = time.time() - start_time
    print("\n\n====================================================================================================")
    print(f"🏁 HOÀN THÀNH QUÉT ĐA THREAD TRONG {elapsed_total:.2f} GIÂY")
    print(f"   • Tổng số Outcomes đã quét : {total_tasks}")
    print(f"   • Số Outcomes MUA ĐƯỢC     : {len(matched_results)}")
    print("====================================================================================================\n")

    # Sắp xếp kết quả theo % Lệch tốt nhất (thấp nhất) lên đầu
    matched_results.sort(key=lambda x: x['percent_gap'])

    # 1. Ghi kết quả dạng JSON
    with open(output_json, 'w', encoding='utf-8') as f:
        json.dump({
            "scanned_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "target_max_gap_pct": target_max_gap_pct,
            "total_scanned_outcomes": total_tasks,
            "total_matched_outcomes": len(matched_results),
            "matched_results": matched_results
        }, f, indent=4, ensure_ascii=False)

    # 2. Ghi kết quả dạng Báo cáo Text dễ đọc
    with open(output_txt, 'w', encoding='utf-8') as f:
        f.write("====================================================================================================\n")
        f.write(f"📊 BÁO CÁO KẾT QUẢ QUÉT OUTCOMES MUA ĐƯỢC (TARGET MAX GAP: <= {target_max_gap_pct:.2f}%)\n")
        f.write(f"   Thời gian quét: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
        f.write(f"   Tổng số Outcomes đã quét: {total_tasks} | Số lượng Mua Được: {len(matched_results)}\n")
        f.write("====================================================================================================\n\n")

        for idx, item in enumerate(matched_results, 1):
            f.write(f"[{idx}] Market: {item['market_title']}\n")
            f.write(f"    • Outcome        : {item['outcome_title']} (Outcome ID: {item['outcome_id']})\n")
            f.write(f"    • Shares Win Mua  : {item['buyable_shares_payout']:.2f} shares (Payout: ${item['buyable_shares_payout']:.2f} USD)\n")
            
            # Formatted Option 1 Breakdown
            opt1_orders_str = " | ".join(
                [f"{o['shares']:.2f} shares @ ${o['price']:.4f} (${o['cost']:.2f})" for o in item['option_1'].get('orders_breakdown', [])]
            )
            f.write(f"    • Vốn Mua Bên A  : ${item['option_1']['cost']:.2f} USD [{item['option_1']['name']}] (VWAP: {item['option_1']['vwap']:.4f})\n")
            f.write(f"      └─ Chi tiết nấc giá A: {opt1_orders_str}\n")
            
            # Formatted Option 2 Breakdown
            opt2_orders_str = " | ".join(
                [f"{o['shares']:.2f} shares @ ${o['price']:.4f} (${o['cost']:.2f})" for o in item['option_2'].get('orders_breakdown', [])]
            )
            f.write(f"    • Vốn Mua Bên B  : ${item['option_2']['cost']:.2f} USD [{item['option_2']['name']}] (VWAP: {item['option_2']['vwap']:.4f})\n")
            f.write(f"      └─ Chi tiết nấc giá B: {opt2_orders_str}\n")

            f.write(f"    • Tổng Vốn (A+B)  : ${item['total_capital_spent']:.2f} USD\n")
            f.write(f"    • % Lệch Vốn/Win  : {item['percent_gap']:+.2f}%\n")
            f.write("----------------------------------------------------------------------------------------------------\n")

    print(f"📁 Đã lưu kết quả thành công vào:\n   1. {output_json}\n   2. {output_txt}\n")


if __name__ == "__main__":
    scan_all_markets()
