def calculate_vwap_buy(order_list, target_shares):
    """
    Tính VWAP (Giá trung bình có trọng số) và chi tiết các lệnh mua từ danh sách ASKS.
    Trả về: (avg_price, total_cost, filled_size, level_orders)
    """
    remaining = target_shares
    total_cost = 0.0
    filled_size = 0.0
    orders = []

    for item in order_list:
        price = float(item.get("price", 0))
        avail_size = float(item.get("size", 0))
        if avail_size <= 0 or price <= 0:
            continue

        take_size = min(remaining, avail_size)
        val = take_size * price
        avail_val = avail_size * price
        total_cost += val
        filled_size += take_size
        usage_pct = (take_size / avail_size) * 100.0 if avail_size > 0 else 0.0

        orders.append({
            "direction": "BUY",
            "orderType": "LIMIT",
            "limitPrice": price,
            "share": round(take_size, 4),
            "value": round(val, 4),
            "avail_size": round(avail_size, 4),
            "avail_val": round(avail_val, 4),
            "usage_pct": round(usage_pct, 2),
        })
        remaining -= take_size
        if remaining <= 0:
            break

    avg_price = total_cost / filled_size if filled_size > 0 else 0.0
    return avg_price, total_cost, filled_size, orders


def find_max_shares_for_target_gap(higher_asks, lower_asks, max_pair_shares, target_max_gap_pct, step_shares=10.0):
    """
    Tính mốc mua tối đa cực nhanh bằng O(1) Quick-Check + Binary Search:
    - Nếu max_pair_shares thỏa mãn gap >= -target_max_gap_pct -> Trả về ngay lập tức (1 phép tính)!
    - Nếu không: Dùng Binary Search tìm mốc shares lớn nhất trong <= 15 bước (thay vì lặp hàng nghìn lần).
    """
    if max_pair_shares <= 0:
        return 0.0, 0.0, False, 0.0, 0.0, "Không có thanh khoản"

    def evaluate_shares(s):
        avg_h, cost_h, _, _ = calculate_vwap_buy(higher_asks, s)
        avg_l, cost_l, _, _ = calculate_vwap_buy(lower_asks, s)
        if avg_h <= 0 or avg_l <= 0:
            return None
        total_cost = cost_h + cost_l
        if total_cost <= 0:
            return None
        gap = ((s - total_cost) / total_cost) * 100.0
        return gap, cost_h, cost_l

    # 1. Quick Check mốc tối đa (O(1))
    max_eval = evaluate_shares(max_pair_shares)
    if max_eval:
        gap_max, cost_h_max, cost_l_max = max_eval
        if gap_max >= -target_max_gap_pct:
            return max_pair_shares, gap_max, True, cost_h_max, cost_l_max, f"Đã đạt mốc thanh khoản tối đa khả dụng ({max_pair_shares:.2f} shares)."

    # 2. Kiểm tra mốc tối thiểu
    min_s = min(50.0, max_pair_shares)
    min_eval = evaluate_shares(min_s)
    if not min_eval or min_eval[0] < -target_max_gap_pct:
        gap_min = min_eval[0] if min_eval else -999.0
        cost_h_min = min_eval[1] if min_eval else 0.0
        cost_l_min = min_eval[2] if min_eval else 0.0
        return min_s, gap_min, False, cost_h_min, cost_l_min, f"Mốc ban đầu {min_s:.2f} shares không thỏa mãn: Lệch {gap_min:+.2f}% vượt ngưỡng -{target_max_gap_pct:.2f}%."

    # 3. Binary Search tìm mốc shares lớn nhất thỏa mãn
    low = min_s
    high = max_pair_shares
    best_s = min_s
    best_gap = min_eval[0]
    best_cost_1 = min_eval[1]
    best_cost_2 = min_eval[2]

    for _ in range(15):
        mid = (low + high) / 2.0
        res = evaluate_shares(mid)
        if res and res[0] >= -target_max_gap_pct:
            best_s = mid
            best_gap = res[0]
            best_cost_1 = res[1]
            best_cost_2 = res[2]
            low = mid + 1.0
        else:
            high = mid - 1.0

        if high < low:
            break

    return best_s, best_gap, True, best_cost_1, best_cost_2, f"Đã tối ưu mốc mua tối đa ({best_s:.2f} shares)."


def analyze_dual_option_hedged_buy(sorted_asks_1, sorted_asks_2, opt1_name, opt2_name, target_max_gap_pct=5.0):
    """
    Phân tích Kế hoạch Mua Cả 2 Đầu Dual-Option theo Mức Độ Lệch TB Giá Mục Tiêu (%).
    """
    top5_asks_1 = sorted_asks_1[:5]
    top5_asks_2 = sorted_asks_2[:5]

    best_ask_1 = float(top5_asks_1[0]["price"]) if top5_asks_1 else 0.0
    best_ask_2 = float(top5_asks_2[0]["price"]) if top5_asks_2 else 0.0

    if best_ask_1 == 0.0 or best_ask_2 == 0.0:
        return {"executable": False, "reason": "Một trong hai Option không có thanh khoản bên ASKS."}

    if best_ask_1 >= best_ask_2:
        higher_name = opt1_name
        higher_asks = top5_asks_1
        lower_name = opt2_name
        lower_asks = top5_asks_2
    else:
        higher_name = opt2_name
        higher_asks = top5_asks_2
        lower_name = opt1_name
        lower_asks = top5_asks_1

    cumulative_levels = []
    cum_h_size = 0.0
    cum_h_val = 0.0
    cum_l_size = 0.0
    cum_l_val = 0.0

    max_n = max(len(higher_asks), len(lower_asks))
    for k in range(max_n):
        if k < len(higher_asks):
            p_h = float(higher_asks[k].get("price", 0))
            s_h = float(higher_asks[k].get("size", 0))
            v_h = s_h * p_h
            cum_h_size += s_h
            cum_h_val += v_h
        else:
            p_h = 0.0
            s_h = 0.0
            v_h = 0.0

        if k < len(lower_asks):
            p_l = float(lower_asks[k].get("price", 0))
            s_l = float(lower_asks[k].get("size", 0))
            v_l = s_l * p_l
            cum_l_size += s_l
            cum_l_val += v_l
        else:
            p_l = 0.0
            s_l = 0.0
            v_l = 0.0

        max_pair_at_level = min(cum_h_size, cum_l_size)

        cumulative_levels.append({
            "level": f"Top {k+1}",
            "price_higher": p_h,
            "size_higher": round(s_h, 4),
            "val_higher": round(v_h, 4),
            "cum_h_size": round(cum_h_size, 4),
            "cum_h_val": round(cum_h_val, 4),
            "price_lower": p_l,
            "size_lower": round(s_l, 4),
            "val_lower": round(v_l, 4),
            "cum_l_size": round(cum_l_size, 4),
            "cum_l_val": round(cum_l_val, 4),
            "max_pair_shares": round(max_pair_at_level, 4),
        })

    total_avail_shares_higher = cum_h_size
    total_avail_shares_lower = cum_l_size
    max_pair_shares = min(total_avail_shares_higher, total_avail_shares_lower)

    target_shares, actual_gap_pct, is_valid, cost_h_calc, cost_l_calc, fail_reason = find_max_shares_for_target_gap(
        higher_asks, lower_asks, max_pair_shares, target_max_gap_pct
    )

    if target_shares <= 0 or not is_valid:
        test_s = min(50.0, max_pair_shares)
        avg_h_test, test_cost_a, _, _ = calculate_vwap_buy(higher_asks, test_s)
        avg_l_test, test_cost_b, _, _ = calculate_vwap_buy(lower_asks, test_s)
        test_total = test_cost_a + test_cost_b
        initial_gap = ((test_s - test_total) / test_total) * 100.0 if test_total > 0 else 0.0

        explanation = (
            f"Orderbook hiện tại không có mốc volume nào thỏa mãn độ lệch >= -{target_max_gap_pct:.2f}%.\n\n"
            f"   💡 CHI TIẾT LÝ DO: {fail_reason}\n\n"
            f"   📐 CÔNG THỨC (Dương = Lãi, Âm = Lỗ):\n"
            f"      • A = Vốn USD mua S shares [{higher_name}]\n"
            f"      • B = Vốn USD mua S shares [{lower_name}]\n"
            f"      • Tiền Win (Payout) = S * 1.00 USD\n"
            f"      • % Lệch = [(S - (A + B)) / (A + B)] * 100%\n\n"
            f"   🔍 KIỂM TRA MỐC BAN ĐẦU ({test_s:.2f} SHARES):\n"
            f"      • Vốn mua A [{higher_name}] ({test_s:.2f} shares) = ${test_cost_a:.2f} USD (VWAP: {avg_h_test:.4f})\n"
            f"      • Vốn mua B [{lower_name}] ({test_s:.2f} shares) = ${test_cost_b:.2f} USD (VWAP: {avg_l_test:.4f})\n"
            f"      • Tổng vốn bỏ ra (A + B): ${test_total:.2f} USD cho ${test_s:.2f} USD tiền Win\n"
            f"      • Độ lệch % tại mốc {test_s:.2f} shares: [(${test_s:.2f} - ${test_total:.2f}) / ${test_total:.2f}] * 100% = {initial_gap:+.2f}%\n"
        )
        return {
            "executable": False, 
            "reason": explanation
        }

    avg_price_higher, cost_higher, fill_higher, orders_higher = calculate_vwap_buy(higher_asks, target_shares)
    avg_price_lower, cost_lower, fill_lower, orders_lower = calculate_vwap_buy(lower_asks, target_shares)

    highest_limit_higher = max(o["limitPrice"] for o in orders_higher) if orders_higher else 0
    highest_limit_lower = max(o["limitPrice"] for o in orders_lower) if orders_lower else 0

    swept_h_size = 0.0
    swept_h_val = 0.0
    for ask in higher_asks:
        p = float(ask.get("price", 0))
        s = float(ask.get("size", 0))
        if p <= highest_limit_higher:
            swept_h_size += s
            swept_h_val += s * p

    swept_l_size = 0.0
    swept_l_val = 0.0
    for ask in lower_asks:
        p = float(ask.get("price", 0))
        s = float(ask.get("size", 0))
        if p <= highest_limit_lower:
            swept_l_size += s
            swept_l_val += s * p

    usage_pct_higher_swept = (target_shares / swept_h_size) * 100.0 if swept_h_size > 0 else 0.0
    usage_pct_lower_swept = (target_shares / swept_l_size) * 100.0 if swept_l_size > 0 else 0.0

    total_investment = cost_higher + cost_lower
    pair_price = avg_price_higher + avg_price_lower

    gap_between_sides_pct = ((target_shares - total_investment) / total_investment) * 100.0 if total_investment > 0 else 0.0
    arbitrage_gap_pct = (pair_price - 1.00) * 100.0
    guaranteed_payout = target_shares * 1.00
    net_pnl = guaranteed_payout - total_investment

    return {
        "executable": True,
        "higher_name": higher_name,
        "lower_name": lower_name,
        "target_max_gap_pct": target_max_gap_pct,
        "target_shares": target_shares,
        "is_valid_gap": is_valid,
        "cumulative_levels": cumulative_levels,
        "swept_h_size": round(swept_h_size, 4),
        "swept_h_val": round(swept_h_val, 4),
        "usage_pct_higher_swept": round(usage_pct_higher_swept, 1),
        "swept_l_size": round(swept_l_size, 4),
        "swept_l_val": round(swept_l_val, 4),
        "usage_pct_lower_swept": round(usage_pct_lower_swept, 1),
        "avg_price_higher": round(avg_price_higher, 4),
        "cost_higher": round(cost_higher, 4),
        "orders_higher": orders_higher,
        "highest_limit_higher": highest_limit_higher,
        "avg_price_lower": round(avg_price_lower, 4),
        "cost_lower": round(cost_lower, 4),
        "orders_lower": orders_lower,
        "highest_limit_lower": highest_limit_lower,
        "total_investment": round(total_investment, 4),
        "pair_price": round(pair_price, 4),
        "gap_between_sides_pct": round(gap_between_sides_pct, 2),
        "arbitrage_gap_pct": round(arbitrage_gap_pct, 2),
        "guaranteed_payout": round(guaranteed_payout, 4),
        "net_pnl": round(net_pnl, 4),
        "fail_reason": fail_reason,
    }
