import json
import os
import sys
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


def get_first_dual_option_market(client):
    """Tự động tìm thị trường đầu tiên có ít nhất 2 Option đang mở VÀ CÓ THANH KHOẢN ASKS 2 BÊN."""
    try:
        res = client.request("GET", "/markets/list", query={"page": "1", "pageSize": "30"})
        markets = res.get("data", [])
        for m in markets:
            outcomes = m.get("outcomes", [])
            if outcomes and len(outcomes[0].get("options", [])) >= 2:
                market_id = str(m["marketId"])
                outcome_id = str(outcomes[0]["outcomeId"])
                opt1 = outcomes[0]["options"][0]
                opt2 = outcomes[0]["options"][1]

                try:
                    b1 = client.get_orderbook(market_id, outcome_id, str(opt1["optionId"]))
                    b2 = client.get_orderbook(market_id, outcome_id, str(opt2["optionId"]))
                    a1 = b1.get("data", {}).get("asks", [])
                    a2 = b2.get("data", {}).get("asks", [])
                    if b1.get("code") == 0 and b2.get("code") == 0 and a1 and a2:
                        print(f"🔍 Tự động chọn thị trường có đủ thanh khoản 2 bên: {m.get('title')}")
                        return (
                            market_id,
                            outcome_id,
                            str(opt1["optionId"]),
                            str(opt2["optionId"]),
                            opt1.get("title", "Option 1"),
                            opt2.get("title", "Option 2"),
                        )
                except Exception:
                    continue
    except Exception as err:
        print(f"⚠️ Không thể lấy danh sách thị trường qua REST API: {err}")
    return None, None, None, None, None, None


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


def find_max_shares_for_target_gap(higher_asks, lower_asks, max_pair_shares, target_max_gap_pct, step_shares=50.0):
    """
    Tính mốc mua tối đa bằng cách bắt đầu từ các mốc nhỏ (10, 20, 50, 100 shares...), kiểm tra % Lệch ((A+B) - S)/(A+B) * 100%.
    Nếu thỏa mãn <= target_max_gap_pct, tiếp tục cộng thêm +50 shares cho tới khi vượt ngưỡng hoặc đạt max liquidity.
    """
    if max_pair_shares <= 0:
        return 0.0, 0.0, False, 0.0, 0.0

    # Lập danh sách các mốc shares cần test bắt đầu từ nấc nhỏ (10, 20, 50) rồi tăng dần +50 (100, 150, 200...)
    candidate_shares = []
    initial_steps = [10.0, 20.0, 50.0]
    for s in initial_steps:
        if s <= max_pair_shares and (not candidate_shares or s > candidate_shares[-1]):
            candidate_shares.append(s)

    current = 100.0
    while current <= max_pair_shares:
        if not candidate_shares or current > candidate_shares[-1]:
            candidate_shares.append(current)
        current += step_shares

    if not candidate_shares or candidate_shares[-1] < max_pair_shares:
        candidate_shares.append(max_pair_shares)

    best_s = 0.0
    best_gap = 0.0
    best_cost_1 = 0.0
    best_cost_2 = 0.0

    closest_s = candidate_shares[0]
    closest_gap = float('inf')
    closest_cost_1 = 0.0
    closest_cost_2 = 0.0

    for s in candidate_shares:
        avg_h, cost_h, _, _ = calculate_vwap_buy(higher_asks, s)
        avg_l, cost_l, _, _ = calculate_vwap_buy(lower_asks, s)
        if avg_h <= 0 or avg_l <= 0:
            continue

        total_cost = cost_h + cost_l
        if total_cost <= 0:
            continue

        # Công thức % Lệch chuẩn từ User: ((A + B) - S) / (A + B) * 100%
        gap = ((total_cost - s) / total_cost) * 100.0

        if gap < closest_gap:
            closest_gap = gap
            closest_s = s
            closest_cost_1 = cost_h
            closest_cost_2 = cost_l

        if gap <= target_max_gap_pct:
            best_s = s
            best_gap = gap
            best_cost_1 = cost_h
            best_cost_2 = cost_l
        else:
            # Ngay khi tại nấc này gap vượt quá target %, dừng lại không cộng thêm nữa
            break

    if best_s > 0:
        return best_s, best_gap, True, best_cost_1, best_cost_2
    else:
        return closest_s, closest_gap, False, closest_cost_1, closest_cost_2


def analyze_dual_option_hedged_buy(sorted_asks_1, sorted_asks_2, opt1_name, opt2_name, target_max_gap_pct=5.0):
    """
    Phân tích Kế hoạch Mua Cả 2 Đầu Dual-Option theo Mức Độ Lệch TB Giá Mục Tiêu (%) thay vì Vốn Cố định.
    """
    top5_asks_1 = sorted_asks_1[:5]
    top5_asks_2 = sorted_asks_2[:5]

    best_ask_1 = float(top5_asks_1[0]["price"]) if top5_asks_1 else 0.0
    best_ask_2 = float(top5_asks_2[0]["price"]) if top5_asks_2 else 0.0

    if best_ask_1 == 0.0 or best_ask_2 == 0.0:
        return {"executable": False, "reason": "Một trong hai Option không có thanh khoản bên ASKS."}

    # Xác định bên giá cao hơn và bên giá thấp hơn
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

    # TÍNH THANH KHOẢN TÍCH LŨY THEO TỪNG NẤC TOP 1..5 DÀNH CHO CẢ 2 BÊN KÈM MAX PAIR SHARES
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

    # BƯỚC 1: Tính số lượng shares (S) tối đa đạt Độ lệch TB Giá Mục tiêu (%) theo từng mốc (100 -> +50)
    target_shares, actual_gap_pct, is_valid, cost_h_calc, cost_l_calc = find_max_shares_for_target_gap(
        higher_asks, lower_asks, max_pair_shares, target_max_gap_pct
    )

    if target_shares <= 0 or not is_valid:
        test_s = min(100.0, max_pair_shares)
        avg_h_test, test_cost_a, _, _ = calculate_vwap_buy(higher_asks, test_s)
        avg_l_test, test_cost_b, _, _ = calculate_vwap_buy(lower_asks, test_s)
        test_total = test_cost_a + test_cost_b
        initial_gap = ((test_total - test_s) / test_total) * 100.0 if test_total > 0 else 0.0

        explanation = (
            f"Orderbook hiện tại không có mốc volume nào thỏa mãn độ lệch <= {target_max_gap_pct:.2f}%.\n\n"
            f"   📐 CÔNG THỨC & CÁCH TÍNH DỪNG THEO MỐC SHARES (100 -> +50 shares):\n"
            f"      • A = Vốn USD mua S shares [{higher_name}]\n"
            f"      • B = Vốn USD mua S shares [{lower_name}]\n"
            f"      • Tiền Win (Payout) = S * 1.00 USD\n"
            f"      • % Lệch = [((A + B) - S) / (A + B)] * 100%\n\n"
            f"   🔍 KIỂM TRA MỐC BAN ĐẦU ({test_s:.2f} SHARES):\n"
            f"      • Vốn mua A [{higher_name}] ({test_s:.2f} shares) = ${test_cost_a:.2f} USD (VWAP: {avg_h_test:.4f})\n"
            f"      • Vốn mua B [{lower_name}] ({test_s:.2f} shares) = ${test_cost_b:.2f} USD (VWAP: {avg_l_test:.4f})\n"
            f"      • Tổng vốn bỏ ra (A + B): ${test_total:.2f} USD cho ${test_s:.2f} USD tiền Win\n"
            f"      • Độ lệch % tại mốc {test_s:.2f} shares: [(${test_total:.2f} - ${test_s:.2f}) / ${test_total:.2f}] * 100% = {initial_gap:+.2f}%\n"
            f"      • Mức volume nhỏ nhất kiểm tra ({target_shares:.2f} shares) có độ lệch thực tế là {actual_gap_pct:+.2f}%.\n\n"
            f"   💡 NGUYÊN NHÂN VƯỢT MỤC TIÊU:\n"
            f"      Tổng vốn mua tại mốc ban đầu {test_s:.2f} shares (${test_total:.2f} USD cho ${test_s:.2f} USD Win) đã tạo độ lệch {initial_gap:+.2f}%, "
            f"vượt quá ngưỡng chênh lệch tối đa cho phép là {target_max_gap_pct:.2f}%!"
        )
        return {
            "executable": False, 
            "reason": explanation
        }

    # BƯỚC 2: Mua S shares ở Bên Giá Cao Hơn
    avg_price_higher, cost_higher, fill_higher, orders_higher = calculate_vwap_buy(higher_asks, target_shares)

    # BƯỚC 3: Mua đúng S shares ở Bên Giá Thấp Hơn
    avg_price_lower, cost_lower, fill_lower, orders_lower = calculate_vwap_buy(lower_asks, target_shares)

    highest_limit_higher = max(o["limitPrice"] for o in orders_higher) if orders_higher else 0
    highest_limit_lower = max(o["limitPrice"] for o in orders_lower) if orders_lower else 0

    # Thanh khoản Sàn tích lũy tới mức giá quét Max bên Giá Cao
    swept_h_size = 0.0
    swept_h_val = 0.0
    for ask in higher_asks:
        p = float(ask.get("price", 0))
        s = float(ask.get("size", 0))
        if p <= highest_limit_higher:
            swept_h_size += s
            swept_h_val += s * p

    # Thanh khoản Sàn tích lũy tới mức giá quét Max bên Giá Thấp
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

    # Công thức chuẩn: ((A + B) - S) / (A + B) * 100%
    gap_between_sides_pct = ((total_investment - target_shares) / total_investment) * 100.0 if total_investment > 0 else 0.0
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

        # Thanh khoản Sàn TÍCH LŨY TỚI MỨC GIÁ QUÉT MAX
        "swept_h_size": round(swept_h_size, 4),
        "swept_h_val": round(swept_h_val, 4),
        "usage_pct_higher_swept": round(usage_pct_higher_swept, 1),

        "swept_l_size": round(swept_l_size, 4),
        "swept_l_val": round(swept_l_val, 4),
        "usage_pct_lower_swept": round(usage_pct_lower_swept, 1),

        # Bên Giá Cao Hơn
        "avg_price_higher": round(avg_price_higher, 4),
        "cost_higher": round(cost_higher, 4),
        "orders_higher": orders_higher,
        "highest_limit_higher": highest_limit_higher,

        # Bên Giá Thấp Hơn
        "avg_price_lower": round(avg_price_lower, 4),
        "cost_lower": round(cost_lower, 4),
        "orders_lower": orders_lower,
        "highest_limit_lower": highest_limit_lower,

        # Các Chỉ số So sánh & Chênh lệch
        "total_investment": round(total_investment, 4),
        "pair_price": round(pair_price, 4),
        "gap_between_sides_pct": round(gap_between_sides_pct, 2),
        "arbitrage_gap_pct": round(arbitrage_gap_pct, 2),
        "guaranteed_payout": round(guaranteed_payout, 4),
        "net_pnl": round(net_pnl, 4),
    }


def print_dual_option_analysis(plan):
    """In Kết quả Orderbook 3 mục theo % Độ lệch Mục tiêu."""
    if not plan or not plan.get("executable"):
        print(f"❌ KHÔNG THỂ THỰC HIỆN: {plan.get('reason') if plan else 'Dữ liệu không hợp lệ'}")
        return

    target_gap = plan["target_max_gap_pct"]
    print("====================================================================================================================")
    print("                                    DUAL-OPTION ASKS ORDERBOOK (TOP 5 ASKS 2 BÊN)                                   ")
    print("====================================================================================================================")
    print(f" 🔴 BÊN GIÁ CAO HƠN [{plan['higher_name']:<25}] || 🟢 BÊN GIÁ THẤP HƠN [{plan['lower_name']:<25}]")
    print(" ---------------------------------------------------||----------------------------------------------------")
    print(
        f" {'Value ($)':<14} | {'Size':<14} | {'Price ($)':>10} || "
        f"{'Price ($)':<10} | {'Size':<14} | {'Value ($)':<14}"
    )
    print(" ---------------------------------------------------||----------------------------------------------------")

    for cl in plan["cumulative_levels"]:
        val_h = f"${cl['cum_h_val']:.2f}" if cl['price_higher'] > 0 else "$0.00"
        size_h = f"{cl['size_higher']:.2f}"
        p_h = f"{cl['price_higher']}" if cl['price_higher'] > 0 else "-"

        p_l = f"{cl['price_lower']}" if cl['price_lower'] > 0 else "-"
        size_l = f"{cl['size_lower']:.2f}"
        val_l = f"${cl['cum_l_val']:.2f}" if cl['price_lower'] > 0 else "$0.00"

        left_str = f"{val_h:<14} | {size_h:<14} | {p_h:>10}"
        right_str = f"{p_l:<10} | {size_l:<14} | {val_l:<14}"

        print(f" {left_str} || {right_str}")

    print(" ---------------------------------------------------||----------------------------------------------------")

    # Dòng Khớp Thực Tế Cho Hạn Mức Độ Lệch Mục Tiêu (%)
    sz = plan["target_shares"]
    cost_h = f"${plan['cost_higher']:.2f}"
    vwap_h = f"{plan['avg_price_higher']}"
    vwap_l = f"{plan['avg_price_lower']}"
    cost_l = f"${plan['cost_lower']:.2f}"
    left_target = f"{cost_h:<14} | {sz:<14.2f} | {vwap_h:>10}"
    right_target = f"{vwap_l:<10} | {sz:<14.2f} | {cost_l:<14}"

    print(f" 🎯 BOT KHỚP THỰC TẾ (S = {sz:.2f} shares Win | % Lệch <= {target_gap:.2f}%):")
    print(f" {left_target} || {right_target}")
    print("====================================================================================================================")

    print(f"\n📊 BẢNG CHI TIẾT THANH KHOẢN SÀN TỚI MỨC GIÁ QUÉT MAX:")
    print(f"   ► Bên Giá CAO HƠN [{plan['higher_name']}]:")
    print(f"       • Lệnh Limit Quét Max (Worst Price) : {plan['highest_limit_higher']}")
    print(f"       • Size Bot Đặt Mua                   : {plan['target_shares']:.4f} shares (Vốn chi: ${plan['cost_higher']:.4f})")
    print(
        f"       • 🌊 Thanh khoản Sàn TỚI GIÁ MAX {plan['highest_limit_higher']} : "
        f"{plan['swept_h_size']:.4f} shares (Tổng vốn Sàn: ${plan['swept_h_val']:.4f} USD) -> Bot dùng {plan['usage_pct_higher_swept']:.1f}%"
    )

    print(f"\n   ► Bên Giá THẤP HƠN [{plan['lower_name']}]:")
    print(f"       • Lệnh Limit Quét Max (Worst Price) : {plan['highest_limit_lower']}")
    print(f"       • Size Bot Đặt Mua                   : {plan['target_shares']:.4f} shares (Vốn chi: ${plan['cost_lower']:.4f})")
    print(
        f"       • 🌊 Thanh khoản Sàn TỚI GIÁ MAX {plan['highest_limit_lower']} : "
        f"{plan['swept_l_size']:.4f} shares (Tổng vốn Sàn: ${plan['swept_l_val']:.4f} USD) -> Bot dùng {plan['usage_pct_lower_swept']:.1f}%"
    )

    total_ab = plan['total_investment']
    payout_win = plan['guaranteed_payout']
    gap_val = plan['gap_between_sides_pct']

    print(f"\n📈 CHỈ SỐ SO SÁNH CHÊNH LỆCH VÀ PHÒNG HỘ ARBITRAGE:")
    print(
        f"   📐 CÔNG THỨC: % Lệch = [((A + B) - Win_USD) / (A + B)] * 100%\n"
        f"      👉 Chi tiết: Vốn Mua A [{plan['higher_name']}] = ${plan['cost_higher']:.2f} USD\n"
        f"      👉 Chi tiết: Vốn Mua B [{plan['lower_name']}] = ${plan['cost_lower']:.2f} USD\n"
        f"      👉 Tổng Vốn 2 Bên (A + B) = ${total_ab:.2f} USD\n"
        f"      👉 Tiền Nhận Khi Win (Payout = S) = ${payout_win:.2f} USD\n"
        f"      👉 Tính toán: [(${total_ab:.2f} - ${payout_win:.2f}) / ${total_ab:.2f}] * 100% = {gap_val:+.2f}%"
    )
    print(f"   1. Tỉ lệ % Lệch Vốn vs Payout Win              : {gap_val:+.2f}% (Mục tiêu: <= {target_gap:.2f}%)")
    print(f"   2. Vốn Mua Bên 1 [{plan['higher_name']}] (A)        : ${plan['cost_higher']:.4f} USD")
    print(f"   3. Vốn Mua Bên 2 [{plan['lower_name']}] (B)        : ${plan['cost_lower']:.4f} USD")
    print(f"   4. Tổng Vốn Đầu Tư 2 Bên (A + B)               : ${total_ab:.4f} USD")
    print(f"   5. Tiền Nhận Chắc Chắn Khi Win (Payout S)       : ${payout_win:.4f} USD")

    pnl = plan["net_pnl"]
    if pnl >= 0:
        print(f"   🎉 LỢI NHUẬN ARBITRAGE DỰ KIẾN                  : +${pnl:.4f} USD (Dưới 1.00 = Có Lời!)")
    else:
        print(f"   ⚠️ CHI PHÍ PHÒNG HỘ / LỖ DỰ KIẾN               : -${abs(pnl):.4f} USD")
    print("====================================================================================================================\n")


def main():
    load_env_file(".env")

    app_key = os.environ.get("FOREGATE_APP_KEY", "")
    app_secret = os.environ.get("FOREGATE_APP_SECRET", "")
    api_key = os.environ.get("FOREGATE_API_KEY", "")

    if not app_key or not app_secret or not api_key:
        print("⚠️ Vui lòng cấu hình các biến môi trường FOREGATE_APP_KEY, FOREGATE_APP_SECRET, FOREGATE_API_KEY trong file .env")
        sys.exit(1)

    client = ForeGateClient(app_key, app_secret, api_key)

    session_cookie = os.environ.get("FOREGATE_SESSION_COOKIE")
    if session_cookie:
        client.set_session_cookie(session_cookie)

    # 1. Đọc thông tin từ biến môi trường
    market_id = os.environ.get("MARKET_ID")
    outcome_id = os.environ.get("OUTCOME_ID")
    option_id_1 = os.environ.get("OPTION_ID_1")
    option_id_2 = os.environ.get("OPTION_ID_2")
    opt1_name = os.environ.get("OPTION_NAME_1", "Option 1")
    opt2_name = os.environ.get("OPTION_NAME_2", "Option 2")

    # 2. Nếu chưa có trong .env, hỗ trợ nhập trực tiếp trên Terminal (nếu chạy interactive)
    if not (market_id and outcome_id and option_id_1 and option_id_2):
        if sys.stdin.isatty():
            print("=========================================================================")
            print("             NHẬP THÔNG TIN THỊ TRƯỜNG DUAL-OPTION HỦY TỰ ĐỘNG           ")
            print("=========================================================================")
            print("👉 Nhập thông tin bên dưới (hoặc Nhấn Enter để tự động chọn từ /markets/list):")
            m_in = input("   • Market ID   : ").strip()
            o_in = input("   • Outcome ID  : ").strip()
            op1_in = input("   • Option ID 1 : ").strip()
            op2_in = input("   • Option ID 2 : ").strip()

            if m_in and o_in and op1_in and op2_in:
                market_id, outcome_id, option_id_1, option_id_2 = m_in, o_in, op1_in, op2_in

    # 3. Phụ: Nếu vẫn chưa có ID nào, tự động quét từ /markets/list thị trường có đủ thanh khoản
    if not (market_id and outcome_id and option_id_1 and option_id_2):
        m_id, out_id, opt1, opt2, name1, name2 = get_first_dual_option_market(client)
        market_id = market_id or m_id
        outcome_id = outcome_id or out_id
        option_id_1 = option_id_1 or opt1
        option_id_2 = option_id_2 or opt2
        opt1_name = name1 or opt1_name
        opt2_name = name2 or opt2_name

    if not market_id or not outcome_id or not option_id_1 or not option_id_2:
        print("❌ Chưa nhập thông tin ID và không tìm thấy thị trường có đủ thanh khoản 2 bên.")
        sys.exit(1)

    target_gap_env = os.environ.get("TARGET_MAX_GAP_PCT") or os.environ.get("MM_TARGET_MAX_GAP_PCT", "5.0")
    target_max_gap_pct = float(target_gap_env)

    print(f"\n📊 Gọi REST API Orderbook Asks cho 2 Option:")
    print(f"   marketId   = {market_id}")
    print(f"   outcomeId  = {outcome_id}")
    print(f"   Option 1   = {opt1_name} (ID: {option_id_1})")
    print(f"   Option 2   = {opt2_name} (ID: {option_id_2})")
    print(f"   Target Max Gap % (Độ lệch TB Giá Tối Đa) = {target_max_gap_pct:.2f}%\n")

    try:
        res1 = client.get_orderbook(market_id, outcome_id, option_id_1)
        res2 = client.get_orderbook(market_id, outcome_id, option_id_2)

        data1 = res1.get("data", {}) if res1.get("code") == 0 else {}
        data2 = res2.get("data", {}) if res2.get("code") == 0 else {}

        raw_asks_1 = data1.get("asks", [])
        raw_asks_2 = data2.get("asks", [])

        sorted_asks_1 = sorted(raw_asks_1, key=lambda x: float(x.get("price", 0)))
        sorted_asks_2 = sorted(raw_asks_2, key=lambda x: float(x.get("price", 0)))

        plan = analyze_dual_option_hedged_buy(
            sorted_asks_1, sorted_asks_2, opt1_name, opt2_name, target_max_gap_pct=target_max_gap_pct
        )

        print_dual_option_analysis(plan)

    except Exception as e:
        print(f"❌ Lỗi khi phân tích Dual-Option Orderbook: {e}")


if __name__ == "__main__":
    main()
