/**
 * FOREGATE ULTRA-FAST LIVE DASHBOARD JAVASCRIPT
 * Single unified HTTP loop & zero CPU lag
 */

function updateClock() {
    const now = new Date();
    const clockElem = document.getElementById('live-clock');
    if (clockElem) {
        clockElem.textContent = now.toUTCString().split(' ')[4] + ' UTC';
    }
}

async function fetchLiveStream() {
    try {
        const res = await fetch('/api/live');
        if (!res.ok) return;
        const data = await res.json();

        // 1. UPDATE SUMMARY & KPI CARDS
        const bal = data.balance || {};
        document.getElementById('kpi-balance').textContent = `$${(bal.available || 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
        document.getElementById('kpi-frozen').textContent = `$${(bal.frozen || 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;

        const port = data.portfolio || {};
        document.getElementById('kpi-portfolio-val').textContent = `$${(port.total_value || 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
        const pnlSpan = document.getElementById('kpi-portfolio-pnl');
        const pnlDiff = port.pnl_usd || 0;
        const pnlPct = port.pnl_pct || 0;
        if (pnlDiff >= 0) {
            pnlSpan.className = 'positive';
            pnlSpan.textContent = `PnL: +$${pnlDiff.toFixed(2)} (+${pnlPct.toFixed(2)}%)`;
        } else {
            pnlSpan.className = 'negative';
            pnlSpan.textContent = `PnL: -$${Math.abs(pnlDiff).toFixed(2)} (${pnlPct.toFixed(2)}%)`;
        }

        const arb = data.arbitrage || {};
        document.getElementById('kpi-targets-count').textContent = `${arb.opportunities_count || 0} OPPS`;
        document.getElementById('kpi-best-gap').innerHTML = `<i class="fas fa-fire"></i> Best Gap: <strong>${(arb.best_gap_pct || 0).toFixed(2)}%</strong>`;

        const prog = data.progress || {};
        const isScanning = data.is_scanning || prog.running;
        const scanStateElem = document.getElementById('kpi-scanner-state');
        const statusText = document.getElementById('bot-status-text');

        if (isScanning && prog.total > 0) {
            scanStateElem.textContent = `${prog.done}/${prog.total} (${prog.pct}%)`;
            statusText.textContent = `LIVE SCANNING (${prog.pct}%)`;
        } else {
            scanStateElem.textContent = "LIVE STREAM";
            statusText.textContent = "STREAMING ONLINE";
        }
        document.getElementById('kpi-scanned-markets').textContent = arb.markets_scanned || (prog.total || 0);

        // 2. UPDATE MARKETS TABLE (Smart Diff Check)
        const scanResults = data.scan_results || [];
        const badge = document.getElementById('opps-badge');
        if (badge) badge.textContent = `${scanResults.length} Found`;

        const resultsHash = JSON.stringify(scanResults.map(x => [
            x.market_id, x.outcome_id, x.percent_gap, x.buyable_shares_payout, x.total_capital_spent
        ]));

        if (window._lastMarketsHash !== resultsHash) {
            window._lastMarketsHash = resultsHash;
            window._currentScanResults = scanResults;
            renderMarketsTable(scanResults);
            renderDepthBars(scanResults);
        }

        // 3. UPDATE POSITIONS TABLE (Smart Diff Check)
        const positions = data.positions || [];
        const posHash = JSON.stringify(positions);
        if (window._lastPositionsHash !== posHash) {
            window._lastPositionsHash = posHash;
            renderPositionsTable(positions);
        }

    } catch (e) {
        console.error("Live stream error:", e);
    }
}

function renderMarketsTable(scanResults) {
    const tbody = document.getElementById('markets-tbody');
    if (!tbody) return;

    if (scanResults.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="9" class="empty-state">
                    <p>No arbitrage opportunities currently match the gap criteria.</p>
                </td>
            </tr>`;
        return;
    }

    tbody.innerHTML = scanResults.map((item, idx) => {
        const mktTitle = item.market_title || 'Unknown';
        const outcomeTitle = item.outcome_title || '';
        const shares = item.buyable_shares_payout || 0;
        const cost = item.total_capital_spent || 0;
        const gap = item.percent_gap || 0;

        const opt1 = item.option_1 || {};
        const opt2 = item.option_2 || {};
        const p1Limit = (opt1.limit_price || opt1.vwap || 0).toFixed(3);
        const p2Limit = (opt2.limit_price || opt2.vwap || 0).toFixed(3);
        const opt1Name = (opt1.name || 'Opt A').substring(0, 10);
        const opt2Name = (opt2.name || 'Opt B').substring(0, 10);

        let gapBadgeClass = 'tag-gap-negative';
        let gapSign = '';
        if (gap > 0) {
            gapBadgeClass = 'tag-gap-positive';
            gapSign = '+';
        } else if (gap >= -2) {
            gapBadgeClass = 'tag-gap-tight';
        }

        return `
            <tr>
                <td><strong style="color:var(--neon-cyan)">${idx + 1}</strong></td>
                <td title="${mktTitle}"><strong>${mktTitle.length > 25 ? mktTitle.substring(0, 25) + '...' : mktTitle}</strong></td>
                <td style="color:var(--neon-blue)">${outcomeTitle.length > 18 ? outcomeTitle.substring(0, 18) + '...' : outcomeTitle}</td>
                <td style="color:var(--neon-amber)">${Math.floor(shares).toLocaleString()}</td>
                <td style="color:var(--neon-purple)" title="Limit: $${p1Limit} | Avg: $${(opt1.vwap || 0).toFixed(3)}">$${p1Limit} <span style="font-size:0.7rem;color:var(--text-dim);">(${opt1Name})</span></td>
                <td style="color:var(--neon-purple)" title="Limit: $${p2Limit} | Avg: $${(opt2.vwap || 0).toFixed(3)}">$${p2Limit} <span style="font-size:0.7rem;color:var(--text-dim);">(${opt2Name})</span></td>
                <td style="color:var(--neon-emerald)">$${cost.toFixed(2)}</td>
                <td><span class="${gapBadgeClass}">${gapSign}${gap.toFixed(2)}%</span></td>
                <td><button class="btn-small" title="Inspect Orderbook" onclick="openOrderbookModal(${idx})"><i class="fas fa-eye"></i></button></td>
            </tr>
        `;
    }).join('');
}

function renderPositionsTable(positions) {
    const tbody = document.getElementById('positions-tbody');
    if (!tbody) return;

    if (positions.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" class="empty-state">No open positions.</td></tr>`;
        return;
    }

    let rowsHtml = '';
    positions.forEach(mkt => {
        const mktTitle = mkt.marketTitle || 'Unknown';
        (mkt.outcomes || []).forEach((out, outIdx) => {
            const outName = out.outcomeName || 'Unknown';
            (out.options || []).forEach((opt, optIdx) => {
                const optTitle = opt.optionTitle || 'N/A';
                const shares = opt.shares || 0;
                const initVal = parseFloat(opt.initialValue || 0);
                const currVal = parseFloat(opt.currentValue || 0);
                const diff = currVal - initVal;
                const pct = initVal > 0 ? (diff / initVal * 100) : 0;

                const isFirst = (outIdx === 0 && optIdx === 0);
                const isFirstInOutcome = (optIdx === 0);

                const pnlClass = diff >= 0 ? 'positive' : 'negative';
                const pnlSign = diff > 0 ? '+' : '';

                rowsHtml += `
                    <tr>
                        <td style="color:var(--neon-cyan)">${isFirst ? mktTitle.substring(0, 20) : ''}</td>
                        <td style="color:var(--neon-blue)">${isFirstInOutcome ? outName : ''}</td>
                        <td style="color:var(--neon-amber)">${optTitle}</td>
                        <td>${shares}</td>
                        <td>$${initVal.toFixed(2)}</td>
                        <td class="${pnlClass}">$${currVal.toFixed(2)} (${pnlSign}${pct.toFixed(1)}%)</td>
                    </tr>
                `;
            });
        });
    });

    tbody.innerHTML = rowsHtml;
}

function renderDepthBars(scanResults) {
    const container = document.getElementById('depth-bars');
    if (!container) return;

    if (!scanResults || scanResults.length === 0) {
        container.innerHTML = '';
        return;
    }

    let html = '';
    const items = scanResults.slice(0, 24);
    items.forEach(item => {
        const pA = (item.option_1 && item.option_1.vwap) || 0.5;
        const pB = (item.option_2 && item.option_2.vwap) || 0.5;
        const hA = Math.min(Math.max(pA * 100, 10), 85);
        const hB = Math.min(Math.max(pB * 100, 10), 85);

        html += `
            <div class="depth-bar-col" title="${item.outcome_title}: A=${pA.toFixed(3)} | B=${pB.toFixed(3)}">
                <div class="bar-segment-a" style="height:${hA}%;"></div>
                <div class="bar-segment-b" style="height:${hB}%;"></div>
            </div>
        `;
    });
    container.innerHTML = html;
}

function openOrderbookModal(idx) {
    const item = window._currentScanResults && window._currentScanResults[idx];
    if (!item) return;

    const modal = document.getElementById('inspector-modal');
    const titleElem = document.getElementById('modal-market-title');
    const bodyElem = document.getElementById('modal-content-body');

    const mktTitle = item.market_title || 'N/A';
    const outcomeTitle = item.outcome_title || '';
    const gap = item.percent_gap || 0;
    const shares = item.buyable_shares_payout || 0;
    const totalCost = item.total_capital_spent || 0;
    const opt1 = item.option_1 || {};
    const opt2 = item.option_2 || {};

    titleElem.innerHTML = `${mktTitle} &mdash; <span style="color:var(--neon-cyan)">${outcomeTitle}</span>`;

    const gapClass = gap >= 0 ? 'positive' : 'negative';
    const gapSign = gap >= 0 ? '+' : '';

    const renderOrdersTable = (orders) => {
        if (!orders || orders.length === 0) {
            return `<tr><td colspan="3" style="text-align:center;color:var(--text-dim);">No depth orders</td></tr>`;
        }
        return orders.map(o => `
            <tr>
                <td>$${Number(o.price || 0).toFixed(3)}</td>
                <td>${Math.round(Number(o.shares || 0)).toLocaleString()}</td>
                <td>$${Number(o.cost || 0).toFixed(2)}</td>
            </tr>
        `).join('');
    };

    bodyElem.innerHTML = `
        <div class="modal-kpi-summary">
            <div class="modal-kpi-box">
                <span>HEDGED BUY SHARES</span>
                <strong style="color:var(--neon-amber)">${Math.floor(shares).toLocaleString()} shares</strong>
            </div>
            <div class="modal-kpi-box">
                <span>TOTAL CAPITAL COST</span>
                <strong style="color:var(--neon-emerald)">$${Number(totalCost).toFixed(2)}</strong>
            </div>
            <div class="modal-kpi-box">
                <span>NET GAP SPREAD</span>
                <strong class="${gapClass}">${gapSign}${Number(gap).toFixed(2)}%</strong>
            </div>
        </div>

        <div class="sides-grid">
            <div class="side-card side-a">
                <div class="side-card-title">
                    <span style="color:var(--neon-purple)">OPTION A: ${opt1.name || 'Opt A'}</span>
                    <span>Limit: <strong>$${Number(opt1.limit_price || opt1.vwap || 0).toFixed(3)}</strong> (Avg: $${Number(opt1.vwap || 0).toFixed(3)})</span>
                </div>
                <table class="sub-table">
                    <thead>
                        <tr>
                            <th>Ask Price</th>
                            <th>Shares</th>
                            <th>Cost ($)</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${renderOrdersTable(opt1.orders_breakdown)}
                    </tbody>
                </table>
            </div>

            <div class="side-card side-b">
                <div class="side-card-title">
                    <span style="color:var(--neon-purple)">OPTION B: ${opt2.name || 'Opt B'}</span>
                    <span>Limit: <strong>$${Number(opt2.limit_price || opt2.vwap || 0).toFixed(3)}</strong> (Avg: $${Number(opt2.vwap || 0).toFixed(3)})</span>
                </div>
                <table class="sub-table">
                    <thead>
                        <tr>
                            <th>Ask Price</th>
                            <th>Shares</th>
                            <th>Cost ($)</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${renderOrdersTable(opt2.orders_breakdown)}
                    </tbody>
                </table>
            </div>
        </div>

        <div style="font-size:0.75rem; color:var(--text-dim); font-family:var(--font-mono); display:flex; justify-content:space-between; align-items:center; border-top:1px solid rgba(255,255,255,0.06); padding-top:0.8rem;">
            <span><i class="fas fa-fingerprint"></i> Market ID: ${item.market_id} | Outcome ID: ${item.outcome_id}</span>
            <span><i class="fas fa-info-circle"></i> Status: ${item.fail_reason || 'Hedging Qualified'}</span>
        </div>
    `;

    modal.classList.add('open');
}

function closeOrderbookModal(e) {
    const modal = document.getElementById('inspector-modal');
    if (modal) {
        modal.classList.remove('open');
    }
}

async function refreshBalance(e) {
    if (e) e.stopPropagation();
    const icon = document.getElementById('balance-spin-icon');
    if (icon) icon.classList.add('fa-spin');

    try {
        const res = await fetch('/api/balance/refresh', { method: 'POST' });
        if (res.ok) {
            const data = await res.json();
            const bal = data.balance || {};
            document.getElementById('kpi-balance').textContent = `$${(bal.available || 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
            document.getElementById('kpi-frozen').textContent = `$${(bal.frozen || 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
            showToast("✅ Đã cập nhật số dư mới nhất!");
        }
    } catch (err) {
        showToast("❌ Lỗi tải số dư: " + err.message);
    } finally {
        if (icon) {
            setTimeout(() => icon.classList.remove('fa-spin'), 600);
        }
    }
}

async function triggerManualScan() {
    showToast("⚡ Kích hoạt quét Orderbook toàn sàn...");
    try {
        await fetch('/api/scan', { method: 'POST' });
        setTimeout(fetchLiveStream, 1000);
    } catch (e) {
        showToast("❌ Lỗi kích hoạt quét: " + e.message);
    }
}

function showToast(msg) {
    const container = document.getElementById('toast-container');
    if (!container) return;
    const toast = document.createElement('div');
    toast.className = 'toast';
    toast.innerHTML = `<i class="fas fa-info-circle" style="color:var(--neon-cyan);margin-right:8px;"></i> ${msg}`;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(100%)';
        toast.style.transition = 'all 0.3s ease';
        setTimeout(() => toast.remove(), 300);
    }, 2500);
}

// =========================================================================
// INITIALIZATION
// =========================================================================
window.addEventListener('DOMContentLoaded', () => {
    updateClock();
    setInterval(updateClock, 1000);

    fetchLiveStream();
    // Ultra-lightweight single polling loop every 1.5s
    setInterval(fetchLiveStream, 1500);
});
