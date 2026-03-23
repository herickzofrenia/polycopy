"""
PolyCopy - Dashboard web (Flask) em localhost:8060
Com preco atual via CLOB API e calculo de PnL
"""
import time
import logging
import threading
import requests
from flask import Flask, jsonify, Response

import config

log = logging.getLogger("polycopy.dashboard")

app = Flask(__name__)

_monitor = None
_tracker = None

# Cache de precos atuais: {token_id: {"price": float, "updated": float}}
_price_cache = {}
_price_cache_lock = threading.Lock()
PRICE_CACHE_TTL = 5  # segundos


def init_dashboard(monitor, tracker):
    global _monitor, _tracker
    _monitor = monitor
    _tracker = tracker


def _fetch_current_price(token_id):
    """Busca preco atual de um token via CLOB API."""
    with _price_cache_lock:
        cached = _price_cache.get(token_id)
        if cached and (time.time() - cached["updated"]) < PRICE_CACHE_TTL:
            return cached["price"]

    try:
        # Tenta via CLOB book endpoint
        url = f"{config.CLOB_API_URL}/book?token_id={token_id}"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            book = resp.json()
            # Melhor bid como proxy do preco atual
            bids = book.get("bids", [])
            asks = book.get("asks", [])
            best_bid = float(bids[0]["price"]) if bids else 0.0
            best_ask = float(asks[0]["price"]) if asks else 0.0
            if best_bid > 0 and best_ask > 0:
                price = round((best_bid + best_ask) / 2, 4)
            elif best_bid > 0:
                price = best_bid
            elif best_ask > 0:
                price = best_ask
            else:
                price = 0.0

            with _price_cache_lock:
                _price_cache[token_id] = {"price": price, "updated": time.time()}
            return price
    except Exception as e:
        log.debug("Erro ao buscar preco de %s: %s", token_id[:16], e)

    # Fallback: tenta via midpoint endpoint
    try:
        url = f"{config.CLOB_API_URL}/midpoint?token_id={token_id}"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            price = float(data.get("mid", data.get("price", 0)))
            if price > 0:
                with _price_cache_lock:
                    _price_cache[token_id] = {"price": price, "updated": time.time()}
                return price
    except Exception:
        pass

    return 0.0


def _get_positions_with_prices():
    """Retorna posicoes com precos atuais e PnL calculado."""
    if not _tracker:
        return [], 0.0

    positions = _tracker.get_positions()
    result = []
    total_pnl = 0.0

    for token_id, pos in positions.items():
        current_price = _fetch_current_price(token_id)
        entry_price = pos.get("avg_price", 0.0)
        size = pos.get("size", 0.0)
        side = pos.get("side", "BUY")

        # Calcular PnL somente se temos preco atual
        if current_price > 0:
            if side == "BUY":
                pnl = (current_price - entry_price) * size
            else:
                pnl = (entry_price - current_price) * size
            pnl_pct = round((pnl / (entry_price * size) * 100) if (entry_price * size) > 0 else 0, 2)
            total_pnl += pnl
        else:
            pnl = None
            pnl_pct = None

        result.append({
            "token_id": token_id,
            "market": pos.get("market", ""),
            "outcome": pos.get("outcome", ""),
            "side": side,
            "size": round(size, 2),
            "entry_price": round(entry_price, 4),
            "current_price": round(current_price, 4) if current_price > 0 else None,
            "pnl": round(pnl, 4) if pnl is not None else None,
            "pnl_pct": pnl_pct,
            "wallet_source": pos.get("wallet_source", ""),
            "opened_at": pos.get("opened_at", 0),
        })

    # Ordenar por PnL (piores primeiro pra chamar atencao)
    result.sort(key=lambda x: x["pnl"])

    return result, round(total_pnl, 4)


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PolyCopy Dashboard</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Outfit:wght@300;500;700;900&display=swap');

  *{margin:0;padding:0;box-sizing:border-box}
  :root{
    --bg:#0a0a0f;
    --surface:#12121a;
    --surface2:#1a1a26;
    --border:#252536;
    --text:#e0e0ec;
    --text2:#8888a4;
    --accent:#00e5a0;
    --accent2:#00c48c;
    --red:#ff4466;
    --orange:#ffaa33;
    --blue:#4488ff;
    --mono:'JetBrains Mono',monospace;
    --sans:'Outfit',sans-serif;
  }
  body{background:var(--bg);color:var(--text);font-family:var(--sans);overflow-x:hidden}

  .topbar{
    display:flex;align-items:center;justify-content:space-between;
    padding:16px 28px;border-bottom:1px solid var(--border);
    background:var(--surface);
  }
  .topbar h1{font-size:20px;font-weight:900;letter-spacing:1px}
  .topbar h1 span{color:var(--accent)}
  .topbar .mode{
    font-family:var(--mono);font-size:12px;font-weight:700;
    padding:4px 12px;border-radius:4px;
    text-transform:uppercase;letter-spacing:2px;
  }
  .mode-dry{background:#ffaa3322;color:var(--orange);border:1px solid var(--orange)}
  .mode-live{background:#ff446622;color:var(--red);border:1px solid var(--red)}

  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;padding:20px 28px}
  .stat-card{
    background:var(--surface);border:1px solid var(--border);border-radius:8px;
    padding:16px 20px;
  }
  .stat-card .label{font-size:11px;color:var(--text2);text-transform:uppercase;letter-spacing:1.5px;font-weight:500}
  .stat-card .value{font-family:var(--mono);font-size:26px;font-weight:700;margin-top:6px;color:var(--accent)}
  .stat-card .value.red{color:var(--red)}
  .stat-card .value.blue{color:var(--blue)}
  .stat-card .value.orange{color:var(--orange)}
  .stat-card .value.green{color:var(--accent)}
  .stat-card .value.pnl-pos{color:var(--accent)}
  .stat-card .value.pnl-neg{color:var(--red)}

  .section{padding:0 28px 20px}
  .section h2{
    font-size:13px;font-weight:700;color:var(--text2);
    text-transform:uppercase;letter-spacing:2px;margin-bottom:12px;
    padding-top:20px;border-top:1px solid var(--border);
  }

  table{width:100%;border-collapse:collapse;font-size:13px}
  th{
    text-align:left;font-size:10px;font-weight:700;color:var(--text2);
    text-transform:uppercase;letter-spacing:1.5px;
    padding:8px 10px;border-bottom:1px solid var(--border);
    background:var(--surface);position:sticky;top:0;
  }
  td{padding:8px 10px;border-bottom:1px solid #1a1a24;font-family:var(--mono);font-size:12px}
  tr:hover td{background:var(--surface2)}
  .side-buy{color:var(--accent);font-weight:700}
  .side-sell{color:var(--red);font-weight:700}
  .pnl-pos{color:var(--accent);font-weight:700}
  .pnl-neg{color:var(--red);font-weight:700}
  .pnl-zero{color:var(--text2)}
  .status-active{color:var(--accent)}
  .status-error{color:var(--red)}
  .status-starting{color:var(--orange)}
  .dry-tag{
    font-size:9px;background:#ffaa3322;color:var(--orange);
    padding:2px 6px;border-radius:3px;font-weight:700;
  }
  .price-na{color:var(--text2);font-style:italic}

  .wallets-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}
  .wallet-card{
    background:var(--surface);border:1px solid var(--border);border-radius:8px;
    padding:14px 18px;
  }
  .wallet-card .wname{font-weight:700;font-size:14px;margin-bottom:4px}
  .wallet-card .waddr{font-family:var(--mono);font-size:10px;color:var(--text2);word-break:break-all}
  .wallet-card .wmeta{display:flex;gap:16px;margin-top:8px;font-size:11px;color:var(--text2)}
  .wallet-card .wmeta span{font-family:var(--mono)}

  .table-wrap{max-height:450px;overflow-y:auto;border:1px solid var(--border);border-radius:8px}

  .pulse{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:8px;animation:pulse 2s infinite}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
  .pulse-green{background:var(--accent)}
  .pulse-red{background:var(--red)}
  .pulse-orange{background:var(--orange)}

  .refresh-note{font-size:10px;color:var(--text2);text-align:right;padding:8px 28px;font-family:var(--mono)}
</style>
</head>
<body>
<div class="topbar">
  <h1>POLY<span>COPY</span></h1>
  <div class="mode" id="modeTag">...</div>
</div>
<div class="grid" id="statsGrid"></div>

<div class="section">
  <h2>Posicoes Abertas</h2>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Mercado</th>
          <th>Outcome</th>
          <th>Side</th>
          <th>Size</th>
          <th>Preco Entrada</th>
          <th>Preco Atual</th>
          <th>PnL ($)</th>
          <th>PnL (%)</th>
          <th>Wallet</th>
          <th>Aberto Em</th>
        </tr>
      </thead>
      <tbody id="positionsBody"></tbody>
    </table>
  </div>
</div>

<div class="section">
  <h2>Trades Recentes</h2>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Hora</th>
          <th>Wallet</th>
          <th>Side</th>
          <th>Mercado</th>
          <th>Outcome</th>
          <th>Preco</th>
          <th>Size</th>
          <th>USDC</th>
          <th>Order ID</th>
          <th></th>
        </tr>
      </thead>
      <tbody id="tradesBody"></tbody>
    </table>
  </div>
</div>

<div class="section">
  <h2>Wallets Monitoradas</h2>
  <div class="wallets-grid" id="walletsGrid"></div>
</div>
<div class="refresh-note" id="refreshNote">carregando...</div>

<script>
const API = '';

function fmtTime(ts) {
  if (!ts) return '-';
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString('pt-BR', {hour:'2-digit',minute:'2-digit',second:'2-digit'});
}

function fmtDate(ts) {
  if (!ts) return '-';
  const d = new Date(ts * 1000);
  return d.toLocaleDateString('pt-BR',{day:'2-digit',month:'2-digit'}) + ' ' + d.toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'});
}

function pnlClass(val) {
  if (val === null || val === undefined) return 'pnl-zero';
  if (val > 0.001) return 'pnl-pos';
  if (val < -0.001) return 'pnl-neg';
  return 'pnl-zero';
}

function pnlSign(val) {
  if (val === null || val === undefined) return '--';
  if (val > 0) return '+' + val.toFixed(4);
  return val.toFixed(4);
}

function pnlPctFmt(val) {
  if (val === null || val === undefined) return '--';
  return (val >= 0 ? '+' : '') + val.toFixed(1) + '%';
}

async function refresh() {
  try {
    const r = await fetch(API + '/api/status');
    const data = await r.json();

    // Mode tag
    const mt = document.getElementById('modeTag');
    mt.textContent = data.dry_run ? 'DRY RUN' : 'LIVE';
    mt.className = 'mode ' + (data.dry_run ? 'mode-dry' : 'mode-live');

    // Stats
    const s = data.stats || {};
    const tp = data.total_pnl || 0;
    const pnlCls = tp >= 0 ? 'pnl-pos' : 'pnl-neg';
    document.getElementById('statsGrid').innerHTML = `
      <div class="stat-card"><div class="label">PnL Total</div><div class="value ${pnlCls}">${tp >= 0 ? '+':''}$${tp.toFixed(4)}</div></div>
      <div class="stat-card"><div class="label">Trades Copiados</div><div class="value">${s.total_copied||0}</div></div>
      <div class="stat-card"><div class="label">Posicoes Abertas</div><div class="value blue">${s.open_positions||0}</div></div>
      <div class="stat-card"><div class="label">Skipped</div><div class="value orange">${s.total_skipped||0}</div></div>
      <div class="stat-card"><div class="label">Erros</div><div class="value red">${s.total_errors||0}</div></div>
      <div class="stat-card"><div class="label">Uptime</div><div class="value">${s.uptime_hours||0}h</div></div>
      <div class="stat-card"><div class="label">Trades/Hora</div><div class="value">${s.trades_per_hour||0}</div></div>
    `;

    // Positions (com precos atuais e PnL)
    const pb = document.getElementById('positionsBody');
    const positions = data.positions_detailed || [];
    pb.innerHTML = positions.map(p => {
      const sideClass = p.side === 'BUY' ? 'side-buy' : 'side-sell';
      const pClass = pnlClass(p.pnl);
      const curPrice = p.current_price > 0
        ? p.current_price.toFixed(4)
        : '<span class="price-na">--</span>';
      const marketName = (p.market || '').substring(0, 45) || '-';
      const outcomeName = (p.outcome || p.token_id.substring(0, 16)) || '-';
      return `<tr>
        <td style="font-family:var(--sans);font-size:11px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${p.market||''}">${marketName}</td>
        <td style="font-family:var(--sans);font-size:11px">${outcomeName}</td>
        <td class="${sideClass}">${p.side}</td>
        <td>${p.size.toFixed(2)}</td>
        <td>${p.entry_price.toFixed(4)}</td>
        <td>${curPrice}</td>
        <td class="${pClass}">${pnlSign(p.pnl)}</td>
        <td class="${pClass}">${pnlPctFmt(p.pnl_pct)}</td>
        <td>${p.wallet_source||'-'}</td>
        <td>${fmtDate(p.opened_at)}</td>
      </tr>`;
    }).join('');
    if (!positions.length) pb.innerHTML = '<tr><td colspan="10" style="text-align:center;color:var(--text2)">Nenhuma posicao aberta</td></tr>';

    // Trades
    const tb = document.getElementById('tradesBody');
    const trades = data.trades || [];
    tb.innerHTML = trades.map(t => {
      const sideClass = t.side === 'BUY' ? 'side-buy' : 'side-sell';
      const dryTag = t.dry_run ? '<span class="dry-tag">DRY</span>' : '';
      const usdc = (t.price * t.size).toFixed(2);
      const marketName = (t.market || '').substring(0, 35) || '-';
      const outcomeName = (t.outcome || '-').substring(0, 20);
      return `<tr>
        <td>${fmtTime(t.timestamp)}</td>
        <td>${t.wallet_source||'-'}</td>
        <td class="${sideClass}">${t.side||'-'}</td>
        <td style="font-family:var(--sans);font-size:11px;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${t.market||''}">${marketName}</td>
        <td style="font-family:var(--sans);font-size:11px">${outcomeName}</td>
        <td>${(t.price||0).toFixed(4)}</td>
        <td>${(t.size||0).toFixed(2)}</td>
        <td>$${usdc}</td>
        <td>${(t.order_id||'-').substring(0,16)}</td>
        <td>${dryTag}</td>
      </tr>`;
    }).join('');
    if (!trades.length) tb.innerHTML = '<tr><td colspan="10" style="text-align:center;color:var(--text2)">Nenhum trade ainda</td></tr>';

    // Wallets
    const wg = document.getElementById('walletsGrid');
    const wallets = data.wallets || {};
    wg.innerHTML = Object.entries(wallets).map(([name, w]) => {
      const statusClass = w.status === 'ACTIVE' ? 'status-active' : w.status === 'ERROR' ? 'status-error' : 'status-starting';
      const pulseClass = w.status === 'ACTIVE' ? 'pulse-green' : w.status === 'ERROR' ? 'pulse-red' : 'pulse-orange';
      return `<div class="wallet-card">
        <div class="wname"><span class="pulse ${pulseClass}"></span>${name}</div>
        <div class="waddr">${w.address || ''}</div>
        <div class="wmeta">
          <span class="${statusClass}">${w.status||'?'}</span>
          <span>Detectados: ${w.trades_detected||0}</span>
          <span>Erros: ${w.errors||0}</span>
          <span>Poll: ${w.last_poll ? fmtTime(w.last_poll) : '-'}</span>
        </div>
      </div>`;
    }).join('');

    document.getElementById('refreshNote').textContent = 'Atualizado: ' + new Date().toLocaleTimeString('pt-BR') + ' (auto-refresh 5s)';
  } catch(e) {
    document.getElementById('refreshNote').textContent = 'Erro ao carregar: ' + e.message;
  }
}

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>"""


@app.route("/")
def index():
    return Response(DASHBOARD_HTML, mimetype="text/html")


@app.route("/api/status")
def api_status():
    positions_detailed, total_pnl = _get_positions_with_prices()
    data = {
        "dry_run": config.DRY_RUN,
        "stats": _tracker.get_stats() if _tracker else {},
        "wallets": _monitor.get_wallet_status() if _monitor else {},
        "trades": _tracker.get_recent_trades(50) if _tracker else [],
        "positions_detailed": positions_detailed,
        "total_pnl": total_pnl,
    }
    return jsonify(data)


def run_dashboard():
    """Inicia o servidor Flask em background."""
    log.info("Dashboard iniciando em http://localhost:%d", config.DASHBOARD_PORT)
    app.run(
        host=config.DASHBOARD_HOST,
        port=config.DASHBOARD_PORT,
        debug=False,
        use_reloader=False,
    )


def start_dashboard_thread(monitor, tracker):
    """Inicia dashboard em thread separada."""
    init_dashboard(monitor, tracker)
    t = threading.Thread(target=run_dashboard, daemon=True, name="dashboard")
    t.start()
    return t
