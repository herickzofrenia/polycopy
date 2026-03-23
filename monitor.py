"""
PolyCopy - Monitor de wallets via Data API polling
"""
import threading
import time
import logging
import requests
from collections import OrderedDict

import config

log = logging.getLogger("polycopy.monitor")


class DedupCache:
    """Cache LRU para deduplicacao de trades por hash+timestamp."""

    def __init__(self, max_size=500):
        self._cache = OrderedDict()
        self._max_size = max_size
        self._lock = threading.Lock()

    def seen(self, key):
        """Retorna True se ja viu esse key, senao marca como visto."""
        with self._lock:
            if key in self._cache:
                return True
            self._cache[key] = True
            if len(self._cache) > self._max_size:
                self._cache.popitem(last=False)
            return False


class WalletMonitor:
    """Monitora multiplas wallets simultaneamente via threading."""

    def __init__(self, executor, tracker):
        self.executor = executor
        self.tracker = tracker
        self.dedup = DedupCache(max_size=config.DEDUP_HISTORY_SIZE)
        self._stop_event = threading.Event()
        self._threads = []
        self._warmup_done = {}
        # Status por wallet (para dashboard)
        self._wallet_status = {}
        self._status_lock = threading.Lock()

    def start(self):
        """Inicia uma thread de polling pra cada wallet."""
        log.info("Iniciando monitor para %d wallets", len(config.WALLETS))
        for wallet_cfg in config.WALLETS:
            t = threading.Thread(
                target=self._poll_wallet_loop,
                args=(wallet_cfg,),
                daemon=True,
                name="monitor-" + wallet_cfg["label"],
            )
            t.start()
            self._threads.append(t)
            with self._status_lock:
                self._wallet_status[wallet_cfg["label"]] = {
                    "address": wallet_cfg["address"],
                    "status": "STARTING",
                    "last_poll": None,
                    "trades_detected": 0,
                    "errors": 0,
                }
            log.info(
                "Thread iniciada: %s -> %s",
                wallet_cfg["label"], wallet_cfg["address"][:16] + "..."
            )

    def stop(self):
        """Para todas as threads de polling."""
        log.info("Parando monitor...")
        self._stop_event.set()
        for t in self._threads:
            t.join(timeout=5)
        log.info("Monitor parado")

    def get_wallet_status(self):
        """Retorna status de todas as wallets (para dashboard)."""
        with self._status_lock:
            return dict(self._wallet_status)

    def _update_status(self, label, **kwargs):
        with self._status_lock:
            if label in self._wallet_status:
                self._wallet_status[label].update(kwargs)

    def _poll_wallet_loop(self, wallet_cfg):
        """Loop de polling para uma wallet especifica."""
        address = wallet_cfg["address"]
        label = wallet_cfg["label"]
        price_min = wallet_cfg["price_min"]
        price_max = wallet_cfg["price_max"]

        log.info(
            "[%s] Iniciando polling (intervalo=%ds, preco=%.2f-%.2f)",
            label, config.POLL_INTERVAL, price_min, price_max
        )

        session = requests.Session()
        session.headers.update({
            "Accept": "application/json",
            "User-Agent": "PolyCopy/1.0",
        })

        self._update_status(label, status="POLLING")

        while not self._stop_event.is_set():
            try:
                trades = self._fetch_activity(session, address)
                self._update_status(label, last_poll=time.time())

                if trades is None:
                    time.sleep(config.POLL_INTERVAL)
                    continue

                # Warmup: primeira rodada marca tudo como visto
                if address not in self._warmup_done:
                    for trade in trades:
                        dedup_key = self._make_dedup_key(trade)
                        self.dedup.seen(dedup_key)
                    self._warmup_done[address] = True
                    self._update_status(label, status="ACTIVE")
                    log.info(
                        "[%s] Warmup completo - %d trades marcados como vistos",
                        label, len(trades)
                    )
                    time.sleep(config.POLL_INTERVAL)
                    continue

                # Processar novos trades (mais antigos primeiro)
                for trade in reversed(trades):
                    dedup_key = self._make_dedup_key(trade)
                    if self.dedup.seen(dedup_key):
                        continue

                    self._update_status(
                        label,
                        trades_detected=self._wallet_status.get(label, {}).get("trades_detected", 0) + 1,
                    )
                    self._process_new_trade(trade, wallet_cfg)

            except Exception as e:
                log.error("[%s] Erro no polling: %s", label, e, exc_info=True)
                self._update_status(
                    label,
                    status="ERROR",
                    errors=self._wallet_status.get(label, {}).get("errors", 0) + 1,
                )

            time.sleep(config.POLL_INTERVAL)

    def _fetch_activity(self, session, address):
        """Busca atividade recente de uma wallet na Data API."""
        url = (
            f"{config.DATA_API_URL}/activity"
            f"?user={address}"
            f"&type=TRADE"
            f"&limit=20"
            f"&sortBy=TIMESTAMP"
            f"&sortDirection=DESC"
        )

        try:
            resp = session.get(url, timeout=10)
            if resp.status_code == 429:
                log.warning("Rate limited na Data API, aguardando 5s...")
                time.sleep(5)
                return None
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return data
            return data.get("data", data.get("results", []))
        except requests.exceptions.RequestException as e:
            log.warning("Erro na requisicao Data API: %s", e)
            return None
        except ValueError:
            log.warning("Resposta invalida da Data API")
            return None

    def _make_dedup_key(self, trade):
        """Gera chave unica para deduplicacao."""
        tx_hash = trade.get("transactionHash", trade.get("transaction_hash", ""))
        timestamp = str(trade.get("timestamp", trade.get("createdAt", "")))
        side = trade.get("side", trade.get("type", ""))
        return f"{tx_hash}:{timestamp}:{side}"

    def _process_new_trade(self, trade, wallet_cfg):
        """Processa um trade novo detectado."""
        label = wallet_cfg["label"]
        price_min = wallet_cfg["price_min"]
        price_max = wallet_cfg["price_max"]

        # Log raw pra debug (so as chaves do trade)
        log.debug("[%s] Trade raw keys: %s", label, list(trade.keys()))
        log.debug("[%s] Trade raw: %s", label, {k: str(v)[:80] for k, v in trade.items()})

        side = self._extract_side(trade)
        price = self._extract_price(trade)
        size = self._extract_size(trade)
        token_id = self._extract_token_id(trade)
        market = trade.get("title", trade.get("market", trade.get("question", "")))
        outcome = trade.get("outcome", trade.get("asset", ""))
        tx_hash = trade.get("transactionHash", trade.get("transaction_hash", ""))

        if not token_id:
            log.warning("[%s] Trade sem token_id, pulando: %s", label, tx_hash[:16] if tx_hash else "?")
            self.tracker.record_skip()
            return

        if not side:
            log.warning("[%s] Trade sem side definido, pulando: %s", label, tx_hash[:16] if tx_hash else "?")
            self.tracker.record_skip()
            return

        log.info(
            "[%s] NOVO TRADE: %s %s @ %.4f (size=%.2f) | %s | tx=%s",
            label, side, outcome[:30] if outcome else "?",
            price, size, market[:40] if market else "?",
            tx_hash[:16] if tx_hash else "?"
        )

        # Filtro de preco
        if price < price_min or price > price_max:
            log.info(
                "[%s] Preco %.4f fora do range (%.2f-%.2f), pulando",
                label, price, price_min, price_max
            )
            self.tracker.record_skip()
            return

        # condition_id identifica o mercado (0x...), token_id e o asset numerico pro CLOB
        condition_id = self._extract_condition_id(trade)
        if not condition_id:
            condition_id = token_id

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
        }

        result = self.executor.execute_copy_trade(trade_data)
        if result:
            log.info("[%s] Copy trade executado: %s", label, result.get("order_id", "?"))
        else:
            log.warning("[%s] Copy trade falhou ou foi pulado", label)

    def _extract_side(self, trade):
        """Extrai o side (BUY/SELL) do trade."""
        side = trade.get("side", "")
        if side:
            return side.upper()

        trade_type = trade.get("type", "")
        if trade_type:
            if trade_type.upper() in ("BUY", "SELL"):
                return trade_type.upper()

        action = trade.get("action", "")
        if action:
            if "buy" in action.lower():
                return "BUY"
            if "sell" in action.lower():
                return "SELL"

        return ""

    def _extract_price(self, trade):
        """Extrai o preco do trade."""
        for field in ("price", "avgPrice", "avg_price", "usdcSize"):
            val = trade.get(field)
            if val is not None:
                try:
                    p = float(val)
                    if 0 < p <= 1.0:
                        return p
                    size = self._extract_size(trade)
                    if size > 0 and p > 1:
                        return min(p / size, 1.0)
                except (ValueError, TypeError):
                    continue
        return 0.0

    def _extract_size(self, trade):
        """Extrai o size/quantidade do trade."""
        for field in ("size", "amount", "shares", "quantity"):
            val = trade.get(field)
            if val is not None:
                try:
                    return abs(float(val))
                except (ValueError, TypeError):
                    continue
        return 0.0

    def _extract_token_id(self, trade):
        """Extrai o token_id (asset) do trade para uso no CLOB.
        O campo 'asset' da Data API e o token_id numerico que o CLOB espera.
        O 'conditionId' e o identificador do mercado, NAO o token."""
        # Prioridade: asset (numerico decimal grande) > tokenId > assetId
        # NUNCA usar conditionId aqui - ele identifica o mercado, nao o token
        for field in ("asset", "tokenId", "token_id", "assetId", "asset_id"):
            val = trade.get(field)
            if val is not None:
                val_str = str(val)
                # asset da Data API e um numero decimal grande (ex: 6539671403522...)
                # conditionId comeca com 0x - ignorar aqui
                if val_str.startswith("0x"):
                    continue
                if len(val_str) > 10:
                    return val_str
        return ""

    def _extract_condition_id(self, trade):
        """Extrai o condition_id (identifica o mercado, nao o outcome)."""
        # Prioriza campos que identificam o mercado como um todo
        for field in ("conditionId", "condition_id", "marketId", "market_id",
                       "questionId", "question_id", "eventId", "event_id", "slug"):
            val = trade.get(field)
            if val and isinstance(val, str) and len(val) > 5:
                return val
        # Fallback: usar o titulo do mercado como chave
        title = trade.get("title", trade.get("market", trade.get("question", "")))
        if title:
            return title
        return ""
