import json
import os
import sys
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from src.api.client import ForeGateClient
from src.core.analyzer import analyze_dual_option_hedged_buy

results_lock = Lock()


def get_outcome_priority(outcome_title: str) -> int:
    if not outcome_title:
        return 999
    title_lower = outcome_title.lower()

    if "game 1" in title_lower or "map 1" in title_lower:
        return 1
    if "game 2" in title_lower or "map 2" in title_lower:
        return 2
    if "game 3" in title_lower or "map 3" in title_lower:
        return 3
    if "game 4" in title_lower or "map 4" in title_lower:
        return 4
    if "game 5" in title_lower or "map 5" in title_lower:
        return 5

    return 999


def sort_matched_results(results):
    """
    Sắp xếp danh sách kết quả:
    1. Ưu tiên end_time tăng dần (thời gian kết thúc gần nhất lên đầu).
    2. Gom nhóm theo market_id.
    3. Trong cùng 1 market: Ưu tiên Game 1 / Map 1 -> Game 2 / Map 2 -> các outcome còn lại (Match Winner xếp sau).
    4. Ưu tiên Gap % tốt nhất lên đầu (Gap dương -> âm ít nhất -> âm nhiều hơn).
    """
    def sort_key(x):
        end_time = str(x.get('end_time') or "")
        market_id = str(x.get('market_id') or "")
        outcome_title = str(x.get('outcome_title') or "")
        priority = get_outcome_priority(outcome_title)
        try:
            gap = float(x.get('percent_gap', 0) or 0)
        except (ValueError, TypeError):
            gap = -999.0
        return (end_time, market_id, priority, -gap)

    results.sort(key=sort_key)


def process_outcome(task_data, app_key, app_secret, api_key, session_cookie, target_max_gap_pct, client=None):
    market_id = task_data['market_id']
    market_title = task_data['market_title']
    end_time = task_data.get('end_time', '')
    outcome_id = task_data['outcome_id']
    outcome_title = task_data['outcome_title']
    opt1 = task_data['opt1']
    opt2 = task_data['opt2']

    if client is None:
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
                return {
                    "is_valid": False, 
                    "market_title": market_title, 
                    "outcome_title": outcome_title, 
                    "reason": f"Chỉ mua được tối đa {plan.get('target_shares', 0)} shares (yêu cầu > 50). Lý do: {plan.get('fail_reason', '')}"
                }
            total_ab = plan['total_investment']
            gap_val = plan['gap_between_sides_pct']

            orders_1 = plan.get('orders_higher', [])
            orders_2 = plan.get('orders_lower', [])

            return {
                "market_id": market_id,
                "market_title": market_title,
                "end_time": end_time,
                "outcome_id": outcome_id,
                "outcome_title": outcome_title,
                "option_1": {
                    "option_id": opt1['option_id'],
                    "name": plan['higher_name'],
                    "vwap": plan['avg_price_higher'],
                    "limit_price": plan.get('highest_limit_higher', plan['avg_price_higher']),
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
                    "limit_price": plan.get('highest_limit_lower', plan['avg_price_lower']),
                    "cost": plan['cost_lower'],
                    "orders_breakdown": [
                        {"price": o.get("limitPrice", 0), "shares": o.get("share", 0), "cost": o.get("value", 0)}
                        for o in orders_2
                    ]
                },
                "buyable_shares_payout": plan['target_shares'],
                "total_capital_spent": total_ab,
                "percent_gap": gap_val,
                "target_max_gap_pct": target_max_gap_pct,
                "fail_reason": plan.get("fail_reason", "Không có thông tin"),
                "is_valid": True
            }
        else:
            return {
                "is_valid": False,
                "market_title": market_title,
                "outcome_title": outcome_title,
                "reason": plan.get("reason", "Lỗi phân tích hoặc không đủ thanh khoản") if plan else "Không có kết quả phân tích"
            }
    except Exception:
        pass
    return None


def scan_markets_data(markets, app_key, app_secret, api_key, session_cookie=None, target_max_gap_pct=5.0, max_workers=25, client=None, on_progress=None):
    """
    Hàm quét orderbook tái sử dụng được với hỗ trợ callback realtime on_progress.
    Nhận danh sách markets trực tiếp, trả về (matched_results, invalid_results).
    """
    if client is None:
        client = ForeGateClient(app_key, app_secret, api_key)
        if session_cookie:
            client.set_session_cookie(session_cookie)

    tasks = []
    for m in markets:
        market_id = str(m.get('marketId', ''))
        market_title = m.get('title', 'N/A')
        end_time = m.get('endTime', '')
        for oc in m.get('outcomes', []):
            options = oc.get('options', [])
            if len(options) >= 2:
                tasks.append({
                    "market_id": market_id,
                    "market_title": market_title,
                    "end_time": end_time,
                    "outcome_id": str(oc.get('outcomeId', '')),
                    "outcome_title": oc.get('title') or oc.get('name') or "Outcome",
                    "opt1": {"option_id": str(options[0].get('optionId', '')), "name": options[0].get('title') or "Option 1"},
                    "opt2": {"option_id": str(options[1].get('optionId', '')), "name": options[1].get('title') or "Option 2"},
                })

    matched_results = []
    invalid_results = []
    total_tasks = len(tasks)
    done_tasks = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_task = {
            executor.submit(process_outcome, t, app_key, app_secret, api_key, session_cookie, target_max_gap_pct, client): t
            for t in tasks
        }
        for future in as_completed(future_to_task):
            done_tasks += 1
            try:
                res = future.result()
                if res:
                    if res.get("is_valid", True):
                        matched_results.append(res)
                    else:
                        invalid_results.append(res)
            except Exception:
                pass

            if on_progress and total_tasks > 0:
                try:
                    on_progress(done_tasks, total_tasks, list(matched_results))
                except Exception:
                    pass

    sort_matched_results(matched_results)
    return matched_results, invalid_results
