"""
PolyCopy - Monitor WebSocket para deteccao rapida de trades

Usa o RTDS (Real-Time Data Stream) do Polymarket pra detectar trades
das wallets alvo em tempo real, sem depender de polling.

Roda em paralelo ao polling HTTP como camada extra de velocidade.
Requer: pip install websocket-client
"""
import threading
import time
import logging
import json

import config

log = logging.getLogger("polycopy.ws_monitor")

RTDS_URL = "wss://ws-live-data.polymarket.com"

HAS_WS = False
try:
    from websocket import WebSocketApp
    HAS_WS = True
except ImportError:
    pass


class WSTradeMonitor:
    """Monitora trades em tempo real via WebSocket RTDS."""

    def __init__(self, executor, tracker, dedup_cache):
        self.executor = executor
        self.tracker = tracker
        self.dedup = dedup_cache
        self._stop_event = threading.Event()
        self._thread = None
        self._ws = None
        self._connected = False
        self._reconnect_delay = 2
        # Mapa de enderecos alvo pra config da wallet
        self._wallet_map = {}
        for w in config.WALLETS:
            self._wallet_map[w["address"].lower()] = w

    def start(self):
        if not HAS_WS:
            log.warning("websocket-client nao instalado. WS monitor desabilitado.")
            log.warning("Instale com: pip install websocket-client")
            return

        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="ws-trade-monitor"
        )
        self._thread.start()
        log.info("WebSocket trade monitor iniciado (RTDS)")

    def stop(self):
        self._stop_event.set()
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=5)

    def is_connected(self):
        return self._connected

    def _run_loop(self):
        """Loop de reconexao automatica."""
        while not self._stop_event.is_set():
            try:
                self._connect()
            except Exception as e:
                log.warning("WS erro: %s, reconectando em %ds", e, self._reconnect_delay)

            if self._stop_event.is_set():
                break

            time.sleep(self._reconnect_delay)
            self._reconnect_delay = min(self._reconnect_delay * 1.5, 30)

    def _connect(self):
        """Conecta ao RTDS e subscreve a todos os trades."""
        log.info("Conectando ao RTDS WebSocket...")

        self._ws = WebSocketApp(
            RTDS_URL,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )

        # run_forever bloqueia ate desconectar
        self._ws.run_forever(
            ping_interval=30,
            ping_timeout=10,
        )

    def _on_open(self, ws):
        """Subscreve ao feed de trades."""
        log.info("RTDS WebSocket conectado!")
        self._connected = True
        self._reconnect_delay = 2

        # Subscrever a todos os trades (filtro por wallet e feito local)
        subscribe_msg = {
            "action": "subscribe",
            "subscriptions": [
                {
                    "topic": "activity",
                    "type": "trades",
                    "filters": "",  # todos os trades
                }
            ],
        }
        ws.send(json.dumps(subscribe_msg))
        log.info("Subscrito ao feed de trades RTDS")

    def _on_message(self, ws, message):
        """Processa mensagem do RTDS."""
        try:
            data = json.loads(message)
        except (json.JSONDecodeError, TypeError):
            return

        # RTDS pode enviar diferentes formatos
        # Mensagens de trade tem payload com dados do trade
        if isinstance(data, dict):
            msg_type = data.get("type", data.get("event_type", ""))

            # Heartbeat/ack
            if msg_type in ("ack", "heartbeat", "ping", "pong", "connected"):
                return

            # Trade data pode estar em payload ou direto
            payload = data.get("payload", data)
            if isinstance(payload, list):
                for item in payload:
                    self._process_trade_msg(item)
            elif isinstance(payload, dict):
                self._process_trade_msg(payload)

    def _process_trade_msg(self, trade):
        """Filtra e processa um trade se for de uma wallet alvo."""
        if not isinstance(trade, dict):
            return

        # Identificar a wallet que fez o trade
        # RTDS pode usar diferentes campos: proxyWallet, maker, taker, user
        wallet_addr = ""
        for field in ("proxyWallet", "proxy_wallet", "maker", "taker", "user", "owner"):
            val = trade.get(field, "")
            if val and isinstance(val, str) and val.lower() in self._wallet_map:
                wallet_addr = val.lower()
                break

        if not wallet_addr:
            return  # nao e de uma wallet alvo

        wallet_cfg = self._wallet_map[wallet_addr]
        label = wallet_cfg["label"]

        # Dedup
        tx_hash = trade.get("transactionHash", trade.get("transaction_hash", ""))
        timestamp = str(trade.get("timestamp", trade.get("createdAt", "")))
        side = trade.get("side", trade.get("type", ""))
        dedup_key = f"{tx_hash}:{timestamp}:{side}"

        if not dedup_key or dedup_key == "::":
            # Sem dados suficientes pra dedup, gerar key alternativa
            dedup_key = f"ws:{wallet_addr}:{json.dumps(trade, sort_keys=True)[:100]}"

        if self.dedup.seen(dedup_key):
            return

        log.info("[WS][%s] Trade detectado via WebSocket!", label)
        self._process_new_trade(trade, wallet_cfg)

    def _process_new_trade(self, trade, wallet_cfg):
        """Processa um trade novo (mesma logica do monitor HTTP)."""
        label = wallet_cfg["label"]
        price_min = wallet_cfg["price_min"]
        price_max = wallet_cfg["price_max"]

        side = self._extract_side(trade)
        price = self._extract_price(trade)
        size = self._extract_size(trade)
        token_id = self._extract_token_id(trade)
        market = trade.get("title", trade.get("market", trade.get("question", "")))
        outcome = trade.get("outcome", trade.get("asset_name", ""))
        tx_hash = trade.get("transactionHash", trade.get("transaction_hash", ""))

        if not token_id or not side:
            return

        log.info(
            "[WS][%s] TRADE: %s %s @ %.4f | %s",
            label, side, outcome[:20] if outcome else "?",
            price, market[:30] if market else "?"
        )

        # Filtro de preco
        if price < price_min or price > price_max:
            log.info("[WS][%s] Preco %.4f fora do range, pulando", label, price)
            self.tracker.record_skip()
            return

        condition_id = trade.get("conditionId", trade.get("condition_id", ""))
        if not condition_id:
            title = trade.get("title", trade.get("market", ""))
            condition_id = title if title else token_id

        # usdcSize do trade original
        original_usdc = 0.0
        for field in ("usdcSize", "usdc_size"):
            val = trade.get(field)
            if val is not None:
                try:
                    original_usdc = abs(float(val))
                    break
                except (ValueError, TypeError):
                    pass
        if original_usdc == 0 and price > 0 and size > 0:
            original_usdc = price * size

        trade_data = {
            "token_id": token_id,
            "condition_id": condition_id,
            "side": side,
            "price": price,
            "size": size,
            "market": market,
            "outcome": outcome,
            "wallet_source": label,
            "transaction_hash": tx_hash,
            "max_market_usdc": wallet_cfg.get("max_market_usdc", 999999),
            "price_max": wallet_cfg.get("price_max", 0.99),
            "original_usdc": original_usdc,
        }

        result = self.executor.execute_copy_trade(trade_data)
        if result:
            log.info("[WS][%s] Copy trade executado: %s", label, result.get("order_id", "?"))

    def _on_error(self, ws, error):
        log.warning("RTDS WebSocket erro: %s", error)
        self._connected = False

    def _on_close(self, ws, close_status_code, close_msg):
        log.info("RTDS WebSocket desconectado (code=%s)", close_status_code)
        self._connected = False

    # --- Extratores (mesma logica do monitor.py) ---

    def _extract_side(self, trade):
        side = trade.get("side", "")
        if side:
            return side.upper()
        trade_type = trade.get("type", "")
        if trade_type and trade_type.upper() in ("BUY", "SELL"):
            return trade_type.upper()
        action = trade.get("action", "")
        if action:
            if "buy" in action.lower():
                return "BUY"
            if "sell" in action.lower():
                return "SELL"
        return ""

    def _extract_price(self, trade):
        for field in ("price", "avgPrice", "avg_price"):
            val = trade.get(field)
            if val is not None:
                try:
                    p = float(val)
                    if 0 < p <= 1.0:
                        return p
                except (ValueError, TypeError):
                    continue
        return 0.0

    def _extract_size(self, trade):
        for field in ("size", "amount", "shares", "quantity"):
            val = trade.get(field)
            if val is not None:
                try:
                    return abs(float(val))
                except (ValueError, TypeError):
                    continue
        return 0.0

    def _extract_token_id(self, trade):
        for field in ("asset", "tokenId", "token_id", "assetId", "asset_id"):
            val = trade.get(field)
            if val is not None:
                val_str = str(val)
                if val_str.startswith("0x"):
                    continue
                if len(val_str) > 10:
                    return val_str
        return ""
