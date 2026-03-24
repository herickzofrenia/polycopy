"""
PolyCopy - Dashboard App (Flask) em localhost:8060
Aplicativo completo: config de wallets, PnL por wallet, start/stop, trades por wallet
"""
import time
import logging
import threading
import json
import requests
from flask import Flask, jsonify, Response, request as flask_request

import config

log = logging.getLogger("polycopy.dashboard")

app = Flask(__name__)

_monitor = None
_tracker = None
_bot_running = False

_price_cache = {}
_price_cache_lock = threading.Lock()
PRICE_CACHE_TTL = 8


def init_dashboard(monitor, tracker):
    global _monitor, _tracker, _bot_running
    _monitor = monitor
    _tracker = tracker
    _bot_running = True


def _fetch_current_price(token_id):
    """Busca preco atual de um token via multiplos endpoints."""
    with _price_cache_lock:
        cached = _price_cache.get(token_id)
        if cached and (time.time() - cached["updated"]) < PRICE_CACHE_TTL:
            return cached["price"]

    price = 0.0

    try:
        url = f"{config.CLOB_API_URL}/midpoint?token_id={token_id}"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict):
                mid = data.get("mid", data.get("price", data.get("midpoint", 0)))
            else:
                mid = data
            p = float(mid) if mid else 0
            if 0.01 < p < 1.0:
                price = p
    except Exception:
        pass

    if price == 0:
        try:
            url = f"{config.CLOB_API_URL}/price?token_id={token_id}&side=buy"
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict):
                    p = float(data.get("price", 0))
                else:
                    p = float(data)
                if 0.01 < p < 1.0:
                    price = p
        except Exception:
            pass

    if price == 0:
        try:
            url = f"{config.CLOB_API_URL}/book?token_id={token_id}"
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                book = resp.json()
                bids = book.get("bids", [])
                asks = book.get("asks", [])
                bb = float(bids[0]["price"]) if bids else 0.0
                ba = float(asks[0]["price"]) if asks else 0.0
                if bb > 0.01 and ba > 0.01:
                    price = round((bb + ba) / 2, 4)
                elif bb > 0.01:
                    price = bb
                elif ba > 0.01:
                    price = ba
        except Exception:
            pass

    if price == 0:
        try:
            url = f"{config.GAMMA_API_URL}/markets?clob_token_ids={token_id}"
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                markets = data if isinstance(data, list) else [data]
                for m in markets:
                    for t in m.get("tokens", []):
                        if str(t.get("token_id", "")) == str(token_id):
                            p = float(t.get("price", 0))
                            if 0.01 < p < 1.0:
                                price = p
                                break
                    if price > 0:
                        break
        except Exception:
            pass

    if price > 0:
        price = round(price, 4)
        with _price_cache_lock:
            _price_cache[token_id] = {"price": price, "updated": time.time()}
    return price


def _enrich_positions(positions_dict):
    """Adiciona preco atual e PnL a posicoes."""
    result = []
    total_pnl = 0.0
    for token_id, pos in positions_dict.items():
        cp = _fetch_current_price(token_id)
        ep = pos.get("avg_price", 0.0)
        sz = pos.get("size", 0.0)
        side = pos.get("side", "BUY")
        if cp > 0:
            pnl = (cp - ep) * sz if side == "BUY" else (ep - cp) * sz
            pnl_pct = round((pnl / (ep * sz) * 100) if (ep * sz) > 0 else 0, 2)
            total_pnl += pnl
        else:
            pnl = None
            pnl_pct = None
        result.append({
            "token_id": token_id, "market": pos.get("market", ""),
            "outcome": pos.get("outcome", ""), "side": side,
            "size": round(sz, 2), "entry_price": round(ep, 4),
            "current_price": round(cp, 4) if cp > 0 else None,
            "pnl": round(pnl, 4) if pnl is not None else None,
            "pnl_pct": pnl_pct,
            "wallet_source": pos.get("wallet_source", ""),
            "opened_at": pos.get("opened_at", 0),
        })
    result.sort(key=lambda x: (x["pnl"] if x["pnl"] is not None else 0))
    return result, round(total_pnl, 4)


# ==================== HTML ====================
DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PolyCopy App</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Outfit:wght@300;400;500;700;900&display=swap');
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#07070c;--s1:#0e0e16;--s2:#15151f;--s3:#1c1c2a;
  --bdr:#262638;--txt:#ddddf0;--txt2:#7777a0;--txt3:#555570;
  --acc:#00e5a0;--acc2:#00c48c;--red:#ff4466;--org:#ffaa33;--blu:#4488ff;
  --mono:'JetBrains Mono',monospace;--sans:'Outfit',sans-serif;
}
body{background:var(--bg);color:var(--txt);font-family:var(--sans)}
a{color:var(--acc);text-decoration:none}

/* Layout */
.shell{display:flex;height:100vh;overflow:hidden}
.sidebar{width:240px;background:var(--s1);border-right:1px solid var(--bdr);display:flex;flex-direction:column;flex-shrink:0}
.main{flex:1;overflow-y:auto;background:var(--bg)}

/* Sidebar */
.logo{padding:20px 20px 16px;font-size:18px;font-weight:900;letter-spacing:1px;border-bottom:1px solid var(--bdr)}
.logo span{color:var(--acc)}
.nav{padding:12px 0;flex:1}
.nav-item{
  display:flex;align-items:center;gap:10px;padding:10px 20px;cursor:pointer;
  font-size:13px;font-weight:500;color:var(--txt2);transition:all .15s;
  border-left:3px solid transparent;
}
.nav-item:hover{background:var(--s2);color:var(--txt)}
.nav-item.active{background:var(--s2);color:var(--acc);border-left-color:var(--acc)}
.nav-item .dot{width:6px;height:6px;border-radius:50%;flex-shrink:0}
.dot-green{background:var(--acc)}
.dot-red{background:var(--red)}
.dot-org{background:var(--org)}
.nav-sep{height:1px;background:var(--bdr);margin:8px 20px}
.sidebar-footer{padding:16px 20px;border-top:1px solid var(--bdr);font-size:11px;color:var(--txt3);font-family:var(--mono)}
.mode-tag{
  display:inline-block;font-family:var(--mono);font-size:10px;font-weight:700;
  padding:3px 8px;border-radius:3px;letter-spacing:1.5px;
}
.mode-dry{background:#ffaa3322;color:var(--org);border:1px solid var(--org)}
.mode-live{background:#ff446622;color:var(--red);border:1px solid var(--red)}

/* Page header */
.page{padding:24px 28px}
.page-title{font-size:22px;font-weight:900;margin-bottom:20px;display:flex;align-items:center;gap:12px}

/* Stats grid */
.sg{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:24px}
.sc{background:var(--s1);border:1px solid var(--bdr);border-radius:8px;padding:14px 16px}
.sc .lb{font-size:10px;color:var(--txt2);text-transform:uppercase;letter-spacing:1.5px;font-weight:500}
.sc .vl{font-family:var(--mono);font-size:22px;font-weight:700;margin-top:4px;color:var(--acc)}
.vl.red{color:var(--red)}.vl.blu{color:var(--blu)}.vl.org{color:var(--org)}
.vl.pnl-pos{color:var(--acc)}.vl.pnl-neg{color:var(--red)}

/* Tables */
.tw{border:1px solid var(--bdr);border-radius:8px;overflow:hidden;margin-bottom:20px}
.tw table{width:100%;border-collapse:collapse;font-size:12px}
.tw th{
  text-align:left;font-size:9px;font-weight:700;color:var(--txt2);
  text-transform:uppercase;letter-spacing:1.5px;
  padding:8px 10px;background:var(--s1);border-bottom:1px solid var(--bdr);
  position:sticky;top:0;
}
.tw td{padding:7px 10px;border-bottom:1px solid #161622;font-family:var(--mono);font-size:11px}
.tw tr:hover td{background:var(--s2)}
.tw .empty{text-align:center;color:var(--txt3);padding:24px;font-family:var(--sans);font-size:13px}
.sb{color:var(--acc);font-weight:700}.ss{color:var(--red);font-weight:700}
.pp{color:var(--acc);font-weight:700}.pn{color:var(--red);font-weight:700}.pz{color:var(--txt3)}
.dry-t{font-size:8px;background:#ffaa3322;color:var(--org);padding:2px 5px;border-radius:3px;font-weight:700}
.mkt{font-family:var(--sans);font-size:11px;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.na{color:var(--txt3);font-style:italic}
.scrollable{max-height:360px;overflow-y:auto}

/* Config form */
.cfg-card{background:var(--s1);border:1px solid var(--bdr);border-radius:8px;padding:16px 20px;margin-bottom:12px}
.cfg-row{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin-top:8px}
.cfg-row label{font-size:11px;color:var(--txt2);min-width:70px}
.cfg-row input{
  background:var(--s2);border:1px solid var(--bdr);border-radius:4px;
  color:var(--txt);font-family:var(--mono);font-size:12px;padding:6px 10px;width:100px;
}
.cfg-row input:focus{outline:none;border-color:var(--acc)}
.cfg-row input.wide{width:420px}
.btn{
  font-family:var(--mono);font-size:11px;font-weight:700;padding:8px 16px;
  border:none;border-radius:4px;cursor:pointer;letter-spacing:0.5px;
}
.btn-acc{background:var(--acc);color:#000}.btn-acc:hover{background:var(--acc2)}
.btn-red{background:var(--red);color:#fff}.btn-red:hover{opacity:.85}
.btn-sm{padding:5px 10px;font-size:10px}
.status-bar{
  position:fixed;bottom:0;left:240px;right:0;background:var(--s1);
  border-top:1px solid var(--bdr);padding:6px 20px;font-size:10px;
  color:var(--txt3);font-family:var(--mono);display:flex;justify-content:space-between;
}
</style>
</head>
<body>
<div class="shell">
  <div class="sidebar">
    <div class="logo">POLY<span>COPY</span></div>
    <div class="nav" id="navMenu">
      <div class="nav-item active" data-page="overview">Overview</div>
      <div class="nav-sep"></div>
    </div>
    <div class="sidebar-footer">
      <div id="modeTag" class="mode-tag">...</div>
      <div style="margin-top:6px" id="uptimeText">--</div>
    </div>
  </div>
  <div class="main" id="mainContent"></div>
</div>
<div class="status-bar">
  <span id="statusLeft">Carregando...</span>
  <span id="statusRight"></span>
</div>

<script>
const API='';
let currentPage='overview';
let cachedData=null;

function fT(ts){if(!ts)return'-';const d=new Date(ts*1000);return d.toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit',second:'2-digit'})}
function fD(ts){if(!ts)return'-';const d=new Date(ts*1000);return d.toLocaleDateString('pt-BR',{day:'2-digit',month:'2-digit'})+' '+d.toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'})}
function pc(v){if(v===null||v===undefined)return'pz';return v>0.001?'pp':v<-0.001?'pn':'pz'}
function ps(v){if(v===null||v===undefined)return'--';return(v>0?'+':'')+v.toFixed(4)}
function pp(v){if(v===null||v===undefined)return'--';return(v>=0?'+':'')+v.toFixed(1)+'%'}

function nav(page){
  currentPage=page;
  document.querySelectorAll('.nav-item').forEach(n=>n.classList.toggle('active',n.dataset.page===page));
  render();
}

function buildNav(data){
  const menu=document.getElementById('navMenu');
  let h='<div class="nav-item'+(currentPage==='overview'?' active':'')+'" data-page="overview" onclick="nav(\'overview\')">Overview</div>';
  h+='<div class="nav-item'+(currentPage==='config'?' active':'')+'" data-page="config" onclick="nav(\'config\')">Configuracoes</div>';
  h+='<div class="nav-sep"></div>';
  const wallets=data.wallets||{};
  Object.entries(wallets).forEach(([name,w])=>{
    const cls=w.status==='ACTIVE'?'dot-green':w.status==='ERROR'?'dot-red':'dot-org';
    const pg='wallet:'+name;
    h+=`<div class="nav-item${currentPage===pg?' active':''}" data-page="${pg}" onclick="nav('${pg}')"><span class="dot ${cls}"></span>${name}</div>`;
  });
  menu.innerHTML=h;
}

function renderOverview(data){
  const s=data.stats||{};const tp=data.total_pnl||0;const pc2=tp>=0?'pnl-pos':'pnl-neg';
  const pos=data.positions_detailed||[];const trades=data.trades||[];
  let h=`<div class="page"><div class="page-title">Overview</div>
  <div class="sg">
    <div class="sc"><div class="lb">PnL Total</div><div class="vl ${pc2}">${tp>=0?'+':''}$${tp.toFixed(4)}</div></div>
    <div class="sc"><div class="lb">Trades</div><div class="vl">${s.total_copied||0}</div></div>
    <div class="sc"><div class="lb">Posicoes</div><div class="vl blu">${s.open_positions||0}</div></div>
    <div class="sc"><div class="lb">Skipped</div><div class="vl org">${s.total_skipped||0}</div></div>
    <div class="sc"><div class="lb">Erros</div><div class="vl red">${s.total_errors||0}</div></div>
    <div class="sc"><div class="lb">Trades/h</div><div class="vl">${s.trades_per_hour||0}</div></div>
  </div>
  <h3 style="font-size:12px;color:var(--txt2);text-transform:uppercase;letter-spacing:2px;margin-bottom:10px">Posicoes Abertas</h3>
  <div class="tw"><div class="scrollable"><table><thead><tr><th>Mercado</th><th>Outcome</th><th>Side</th><th>Size</th><th>Entrada</th><th>Atual</th><th>PnL $</th><th>PnL %</th><th>Wallet</th><th>Aberto</th></tr></thead><tbody>`;
  if(!pos.length)h+='<tr><td colspan="10" class="empty">Nenhuma posicao aberta</td></tr>';
  else pos.forEach(p=>{
    const sc2=p.side==='BUY'?'sb':'ss';const pC=pc(p.pnl);
    const cp=p.current_price?p.current_price.toFixed(4):'<span class="na">--</span>';
    h+=`<tr><td class="mkt" title="${p.market||''}">${(p.market||'-').substring(0,42)}</td><td style="font-family:var(--sans);font-size:11px">${p.outcome||'-'}</td><td class="${sc2}">${p.side}</td><td>${p.size.toFixed(2)}</td><td>${p.entry_price.toFixed(4)}</td><td>${cp}</td><td class="${pC}">${ps(p.pnl)}</td><td class="${pC}">${pp(p.pnl_pct)}</td><td>${p.wallet_source||'-'}</td><td>${fD(p.opened_at)}</td></tr>`;
  });
  h+=`</tbody></table></div></div>
  <h3 style="font-size:12px;color:var(--txt2);text-transform:uppercase;letter-spacing:2px;margin-bottom:10px">Trades Recentes</h3>
  <div class="tw"><div class="scrollable"><table><thead><tr><th>Hora</th><th>Wallet</th><th>Side</th><th>Mercado</th><th>Outcome</th><th>Preco</th><th>Size</th><th>USDC</th><th>Order ID</th><th></th></tr></thead><tbody>`;
  if(!trades.length)h+='<tr><td colspan="10" class="empty">Nenhum trade ainda</td></tr>';
  else trades.forEach(t=>{
    const sc2=t.side==='BUY'?'sb':'ss';const dry=t.dry_run?'<span class="dry-t">DRY</span>':'';
    h+=`<tr><td>${fT(t.timestamp)}</td><td>${t.wallet_source||'-'}</td><td class="${sc2}">${t.side||'-'}</td><td class="mkt" title="${t.market||''}">${(t.market||'-').substring(0,30)}</td><td style="font-family:var(--sans);font-size:11px">${(t.outcome||'-').substring(0,18)}</td><td>${(t.price||0).toFixed(4)}</td><td>${(t.size||0).toFixed(2)}</td><td>$${(t.price*t.size).toFixed(2)}</td><td>${(t.order_id||'-').substring(0,14)}</td><td>${dry}</td></tr>`;
  });
  h+=`</tbody></table></div></div></div>`;
  return h;
}

function renderWallet(data,walletName){
  const ws=data.wallet_details?data.wallet_details[walletName]:null;
  if(!ws)return`<div class="page"><div class="page-title">${walletName}</div><p>Carregando...</p></div>`;
  const wc=data.wallet_configs?data.wallet_configs[walletName]:null;
  const st=ws.stats||{};const tp=ws.total_pnl||0;const pc2=tp>=0?'pnl-pos':'pnl-neg';
  let h=`<div class="page"><div class="page-title">${walletName}`;
  if(wc)h+=`<span style="font-size:11px;color:var(--txt3);font-family:var(--mono);font-weight:400">${wc.address}</span>`;
  h+=`</div><div class="sg">
    <div class="sc"><div class="lb">PnL Wallet</div><div class="vl ${pc2}">${tp>=0?'+':''}$${tp.toFixed(4)}</div></div>
    <div class="sc"><div class="lb">Trades</div><div class="vl">${st.total_trades||0}</div></div>
    <div class="sc"><div class="lb">Posicoes</div><div class="vl blu">${st.open_positions||0}</div></div>
    <div class="sc"><div class="lb">USDC Gasto</div><div class="vl org">$${st.total_spent_usdc||0}</div></div>`;
  if(wc)h+=`<div class="sc"><div class="lb">Max/Mercado</div><div class="vl">$${wc.max_market_usdc||'--'}</div></div>
    <div class="sc"><div class="lb">Price Range</div><div class="vl" style="font-size:16px">${wc.price_min||'--'} - ${wc.price_max||'--'}</div></div>`;
  h+=`</div>
  <h3 style="font-size:12px;color:var(--txt2);text-transform:uppercase;letter-spacing:2px;margin-bottom:10px">Posicoes - ${walletName}</h3>
  <div class="tw"><div class="scrollable"><table><thead><tr><th>Mercado</th><th>Outcome</th><th>Side</th><th>Size</th><th>Entrada</th><th>Atual</th><th>PnL $</th><th>PnL %</th><th>Aberto</th></tr></thead><tbody>`;
  const pos=ws.positions||[];
  if(!pos.length)h+='<tr><td colspan="9" class="empty">Nenhuma posicao</td></tr>';
  else pos.forEach(p=>{
    const sc2=p.side==='BUY'?'sb':'ss';const pC=pc(p.pnl);
    const cp=p.current_price?p.current_price.toFixed(4):'<span class="na">--</span>';
    h+=`<tr><td class="mkt" title="${p.market||''}">${(p.market||'-').substring(0,42)}</td><td style="font-family:var(--sans);font-size:11px">${p.outcome||'-'}</td><td class="${sc2}">${p.side}</td><td>${p.size.toFixed(2)}</td><td>${p.entry_price.toFixed(4)}</td><td>${cp}</td><td class="${pC}">${ps(p.pnl)}</td><td class="${pC}">${pp(p.pnl_pct)}</td><td>${fD(p.opened_at)}</td></tr>`;
  });
  h+=`</tbody></table></div></div>
  <h3 style="font-size:12px;color:var(--txt2);text-transform:uppercase;letter-spacing:2px;margin-bottom:10px">Trades - ${walletName}</h3>
  <div class="tw"><div class="scrollable"><table><thead><tr><th>Hora</th><th>Side</th><th>Mercado</th><th>Outcome</th><th>Preco</th><th>Size</th><th>USDC</th><th>Order ID</th></tr></thead><tbody>`;
  const trades=ws.trades||[];
  if(!trades.length)h+='<tr><td colspan="8" class="empty">Nenhum trade</td></tr>';
  else trades.forEach(t=>{
    const sc2=t.side==='BUY'?'sb':'ss';
    h+=`<tr><td>${fT(t.timestamp)}</td><td class="${sc2}">${t.side||'-'}</td><td class="mkt" title="${t.market||''}">${(t.market||'-').substring(0,30)}</td><td style="font-family:var(--sans);font-size:11px">${(t.outcome||'-').substring(0,18)}</td><td>${(t.price||0).toFixed(4)}</td><td>${(t.size||0).toFixed(2)}</td><td>$${(t.price*t.size).toFixed(2)}</td><td>${(t.order_id||'-').substring(0,14)}</td></tr>`;
  });
  h+=`</tbody></table></div></div></div>`;
  return h;
}

function renderConfig(data){
  const wcs=data.wallet_configs||{};
  const g=data.config_general||{};
  const cm=g.copy_mode||'FIXED';
  let h=`<div class="page"><div class="page-title">Configuracoes</div>

  <div class="cfg-card"><div style="font-size:14px;font-weight:700;margin-bottom:8px">Modo de Copia</div>
    <div class="cfg-row">
      <label>Modo</label>
      <select id="cfgMode" style="background:var(--s2);border:1px solid var(--bdr);border-radius:4px;color:var(--txt);font-family:var(--mono);font-size:12px;padding:6px 10px">
        <option value="FIXED" ${cm==='FIXED'?'selected':''}>FIXED - Valor fixo em USDC</option>
        <option value="PERCENT" ${cm==='PERCENT'?'selected':''}>PERCENT - Mesma % da banca do trader</option>
      </select>
    </div>
    <div class="cfg-row"><label>Trade Size</label><input id="cfgSize" type="number" step="0.5" value="${g.copy_size_usdc||1}"><label style="margin-left:8px;color:var(--txt3)">USDC (modo FIXED)</label></div>
    <div class="cfg-row"><label>Multiplicador</label>
      <select id="cfgMult" style="background:var(--s2);border:1px solid var(--bdr);border-radius:4px;color:var(--txt);font-family:var(--mono);font-size:12px;padding:6px 10px;width:140px">
        <option value="0.25" ${g.copy_multiplier==0.25?'selected':''}>0.25x (1/4)</option>
        <option value="0.5" ${g.copy_multiplier==0.5?'selected':''}>0.5x (metade)</option>
        <option value="0.75" ${g.copy_multiplier==0.75?'selected':''}>0.75x (3/4)</option>
        <option value="1" ${g.copy_multiplier==1?'selected':''}>1x (igual)</option>
        <option value="1.5" ${g.copy_multiplier==1.5?'selected':''}>1.5x</option>
        <option value="2" ${g.copy_multiplier==2?'selected':''}>2x (dobro)</option>
        <option value="3" ${g.copy_multiplier==3?'selected':''}>3x (triplo)</option>
        <option value="5" ${g.copy_multiplier==5?'selected':''}>5x</option>
      </select>
      <input id="cfgMultCustom" type="number" step="0.1" value="${g.copy_multiplier||1}" style="width:70px"><label style="margin-left:8px;color:var(--txt3)">ou custom</label>
    </div>
    <div class="cfg-row"><label>Minha Banca</label><input id="cfgBank" type="number" step="1" value="${g.my_bankroll||50}"><label style="margin-left:8px;color:var(--txt3)">USDC (modo PERCENT)</label></div>
    <div style="margin-top:8px;padding:8px 12px;background:var(--s2);border-radius:4px;font-size:11px;color:var(--txt2)">
      <b>FIXED:</b> Cada trade copia $${g.copy_size_usdc||1} x ${g.copy_multiplier||1} = $${((g.copy_size_usdc||1)*(g.copy_multiplier||1)).toFixed(2)}<br>
      <b>PERCENT:</b> Se trader gasta 2% da banca dele, voce gasta 2% de $${g.my_bankroll||50} x ${g.copy_multiplier||1} = $${((g.my_bankroll||50)*0.02*(g.copy_multiplier||1)).toFixed(2)}
    </div>
  </div>

  <div class="cfg-card"><div style="font-size:14px;font-weight:700;margin-bottom:8px">Geral</div>
    <div class="cfg-row"><label>Slippage</label><input id="cfgSlip" type="number" step="1" value="${g.max_slippage_pct||2}"><label style="margin-left:8px">%</label></div>
    <div class="cfg-row"><label>Max Pos</label><input id="cfgMaxPos" type="number" step="1" value="${g.max_open_positions||20}"></div>
    <div class="cfg-row"><label>Polling</label><input id="cfgPoll" type="number" step="1" value="${g.poll_interval||2}"><label style="margin-left:8px">segundos</label></div>
    <div class="cfg-row" style="margin-top:12px"><button class="btn btn-acc" onclick="saveGeneral()">Salvar Tudo</button></div>
  </div>`;
  Object.entries(wcs).forEach(([name,wc])=>{
    h+=`<div class="cfg-card"><div style="font-size:14px;font-weight:700;margin-bottom:4px">${name}</div>
    <div style="font-size:10px;color:var(--txt3);font-family:var(--mono);margin-bottom:8px">${wc.address}</div>
    <div class="cfg-row"><label>Price Min</label><input id="cfg_${name}_pmin" type="number" step="0.01" value="${wc.price_min}">
    <label>Price Max</label><input id="cfg_${name}_pmax" type="number" step="0.01" value="${wc.price_max}">
    <label>Max/Mercado $</label><input id="cfg_${name}_mkt" type="number" step="1" value="${wc.max_market_usdc}"></div>
    <div class="cfg-row" style="margin-top:8px"><button class="btn btn-acc btn-sm" onclick="saveWallet('${name}')">Salvar ${name}</button></div>
    </div>`;
  });
  h+=`</div>`;
  return h;
}

function render(){
  if(!cachedData)return;
  const mc=document.getElementById('mainContent');
  if(currentPage==='overview')mc.innerHTML=renderOverview(cachedData);
  else if(currentPage==='config')mc.innerHTML=renderConfig(cachedData);
  else if(currentPage.startsWith('wallet:')){
    const wn=currentPage.split(':')[1];
    mc.innerHTML=renderWallet(cachedData,wn);
  }
}

async function saveGeneral(){
  const multSelect=document.getElementById('cfgMult').value;
  const multCustom=parseFloat(document.getElementById('cfgMultCustom').value);
  const mult=multCustom||parseFloat(multSelect)||1;
  const body={
    copy_size_usdc:parseFloat(document.getElementById('cfgSize').value),
    max_slippage_pct:parseInt(document.getElementById('cfgSlip').value),
    max_open_positions:parseInt(document.getElementById('cfgMaxPos').value),
    poll_interval:parseInt(document.getElementById('cfgPoll').value),
    copy_mode:document.getElementById('cfgMode').value,
    copy_multiplier:mult,
    my_bankroll:parseFloat(document.getElementById('cfgBank').value),
  };
  await fetch(API+'/api/config/general',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  refresh();
}

async function saveWallet(name){
  const body={price_min:parseFloat(document.getElementById('cfg_'+name+'_pmin').value),price_max:parseFloat(document.getElementById('cfg_'+name+'_pmax').value),max_market_usdc:parseFloat(document.getElementById('cfg_'+name+'_mkt').value)};
  await fetch(API+'/api/config/wallet/'+name,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  refresh();
}

async function refresh(){
  try{
    const r=await fetch(API+'/api/full');
    cachedData=await r.json();
    buildNav(cachedData);
    render();
    const mt=document.getElementById('modeTag');
    mt.textContent=cachedData.dry_run?'DRY RUN':'LIVE';
    mt.className='mode-tag '+(cachedData.dry_run?'mode-dry':'mode-live');
    document.getElementById('uptimeText').textContent='Uptime: '+(cachedData.stats?.uptime_hours||0)+'h';
    document.getElementById('statusLeft').textContent='Atualizado: '+new Date().toLocaleTimeString('pt-BR');
    document.getElementById('statusRight').textContent='Auto-refresh 5s';
  }catch(e){document.getElementById('statusLeft').textContent='Erro: '+e.message}
}
refresh();setInterval(refresh,5000);
</script>
</body>
</html>"""


@app.route("/")
def index():
    return Response(DASHBOARD_HTML, mimetype="text/html")


@app.route("/api/full")
def api_full():
    """Endpoint completo com tudo que o dashboard precisa."""
    all_pos = _tracker.get_positions() if _tracker else {}
    pos_detailed, total_pnl = _enrich_positions(all_pos)

    # Dados por wallet
    wallet_details = {}
    wallet_configs = {}
    for wcfg in config.WALLETS:
        name = wcfg["label"]
        w_pos = _tracker.get_positions_by_wallet(name) if _tracker else {}
        w_pos_d, w_pnl = _enrich_positions(w_pos)
        w_trades = _tracker.get_trades_by_wallet(name) if _tracker else []
        w_stats = _tracker.get_wallet_stats(name) if _tracker else {}
        wallet_details[name] = {
            "positions": w_pos_d,
            "trades": w_trades,
            "total_pnl": w_pnl,
            "stats": w_stats,
        }
        wallet_configs[name] = {
            "address": wcfg["address"],
            "price_min": wcfg["price_min"],
            "price_max": wcfg["price_max"],
            "max_market_usdc": wcfg.get("max_market_usdc", 999999),
        }

    return jsonify({
        "dry_run": config.DRY_RUN,
        "stats": _tracker.get_stats() if _tracker else {},
        "wallets": _monitor.get_wallet_status() if _monitor else {},
        "trades": _tracker.get_recent_trades(50) if _tracker else [],
        "positions_detailed": pos_detailed,
        "total_pnl": total_pnl,
        "wallet_details": wallet_details,
        "wallet_configs": wallet_configs,
        "config_general": {
            "copy_size_usdc": config.COPY_SIZE_USDC,
            "max_slippage_pct": config.MAX_SLIPPAGE_PCT,
            "max_open_positions": config.MAX_OPEN_POSITIONS,
            "poll_interval": config.POLL_INTERVAL,
            "copy_mode": config.COPY_MODE,
            "copy_multiplier": config.COPY_MULTIPLIER,
            "my_bankroll": config.MY_BANKROLL,
        },
    })


@app.route("/api/config/general", methods=["POST"])
def api_config_general():
    """Atualiza configuracoes gerais em runtime."""
    data = flask_request.get_json()
    if "copy_size_usdc" in data:
        config.COPY_SIZE_USDC = float(data["copy_size_usdc"])
    if "max_slippage_pct" in data:
        config.MAX_SLIPPAGE_PCT = int(data["max_slippage_pct"])
    if "max_open_positions" in data:
        config.MAX_OPEN_POSITIONS = int(data["max_open_positions"])
    if "poll_interval" in data:
        config.POLL_INTERVAL = int(data["poll_interval"])
    if "copy_mode" in data:
        config.COPY_MODE = str(data["copy_mode"]).upper()
    if "copy_multiplier" in data:
        config.COPY_MULTIPLIER = float(data["copy_multiplier"])
    if "my_bankroll" in data:
        config.MY_BANKROLL = float(data["my_bankroll"])
    log.info("Config atualizada: mode=%s size=%.1f mult=%.1fx bank=$%.0f slip=%d%%",
             config.COPY_MODE, config.COPY_SIZE_USDC, config.COPY_MULTIPLIER,
             config.MY_BANKROLL, config.MAX_SLIPPAGE_PCT)
    config.save_overrides()
    return jsonify({"ok": True})


@app.route("/api/config/wallet/<name>", methods=["POST"])
def api_config_wallet(name):
    """Atualiza configuracao de uma wallet em runtime."""
    data = flask_request.get_json()
    for wcfg in config.WALLETS:
        if wcfg["label"] == name:
            if "price_min" in data:
                wcfg["price_min"] = float(data["price_min"])
            if "price_max" in data:
                wcfg["price_max"] = float(data["price_max"])
            if "max_market_usdc" in data:
                wcfg["max_market_usdc"] = float(data["max_market_usdc"])
            log.info("Config wallet %s atualizada: range=%.2f-%.2f max_mkt=$%.0f",
                     name, wcfg["price_min"], wcfg["price_max"],
                     wcfg.get("max_market_usdc", 0))
            config.save_overrides()
            return jsonify({"ok": True})
    return jsonify({"error": "wallet not found"}), 404


def run_dashboard():
    log.info("Dashboard iniciando em http://localhost:%d", config.DASHBOARD_PORT)
    app.run(host=config.DASHBOARD_HOST, port=config.DASHBOARD_PORT,
            debug=False, use_reloader=False)


def start_dashboard_thread(monitor, tracker):
    init_dashboard(monitor, tracker)
    t = threading.Thread(target=run_dashboard, daemon=True, name="dashboard")
    t.start()
    return t
