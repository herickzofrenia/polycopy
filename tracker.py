"""
PolyCopy - Tracker de posicoes e PnL
"""
import threading
import time
import logging
import json
import os

log = logging.getLogger("polycopy.tracker")

DATA_FILE = os.path.join(os.path.dirname(__file__), "positions.json")


class PositionTracker:
    """Rastreia posicoes abertas, trades executados e PnL."""

    def __init__(self):
        self._lock = threading.Lock()
        # positions: {token_id: {"side": "BUY"|"SELL", "size": float, "avg_price": float,
        #             "market": str, "outcome": str, "wallet_source": str, "opened_at": float}}
        self.positions = {}
        # trades: lista de todos os trades copiados
        self.trades = []
        # gasto por mercado por wallet: {"Wallet-1::condition_id": usdc_gasto}
        self.market_spend = {}
        # stats
        self.total_copied = 0
        self.total_skipped = 0
        self.total_errors = 0
        self.start_time = time.time()
        self._load()

    def _load(self):
        """Carrega estado salvo do disco."""
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.positions = data.get("positions", {})
                self.trades = data.get("trades", [])
                self.total_copied = data.get("total_copied", 0)
                self.total_skipped = data.get("total_skipped", 0)
                self.total_errors = data.get("total_errors", 0)
                self.market_spend = data.get("market_spend", {})
                log.info("Estado carregado: %d posicoes, %d trades", len(self.positions), len(self.trades))
        except Exception as e:
            log.warning("Falha ao carregar estado: %s", e)

    def _save(self):
        """Persiste estado no disco."""
        try:
            data = {
                "positions": self.positions,
                "trades": self.trades,
                "total_copied": self.total_copied,
                "total_skipped": self.total_skipped,
                "total_errors": self.total_errors,
                "market_spend": self.market_spend,
            }
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            log.warning("Falha ao salvar estado: %s", e)

    def record_trade(self, trade_info):
        """Registra um trade copiado com sucesso."""
        with self._lock:
            token_id = trade_info.get("token_id", "")
            side = trade_info.get("side", "BUY")
            size = trade_info.get("size", 0.0)
            price = trade_info.get("price", 0.0)
            market = trade_info.get("market", "")
            outcome = trade_info.get("outcome", "")
            wallet = trade_info.get("wallet_source", "")

            entry = {
                "token_id": token_id,
                "side": side,
                "size": size,
                "price": price,
                "market": market,
                "outcome": outcome,
                "wallet_source": wallet,
                "timestamp": time.time(),
                "order_id": trade_info.get("order_id", ""),
                "dry_run": trade_info.get("dry_run", True),
            }
            self.trades.append(entry)
            self.total_copied += 1

            # Atualizar posicao
            if side == "BUY":
                if token_id in self.positions:
                    pos = self.positions[token_id]
                    old_size = pos["size"]
                    old_avg = pos["avg_price"]
                    new_size = old_size + size
                    if new_size > 0:
                        pos["avg_price"] = (old_avg * old_size + price * size) / new_size
                    pos["size"] = new_size
                else:
                    self.positions[token_id] = {
                        "side": "BUY",
                        "size": size,
                        "avg_price": price,
                        "market": market,
                        "outcome": outcome,
                        "wallet_source": wallet,
                        "opened_at": time.time(),
                    }
            elif side == "SELL":
                if token_id in self.positions:
                    pos = self.positions[token_id]
                    pos["size"] -= size
                    if pos["size"] <= 0.001:
                        del self.positions[token_id]
                else:
                    # Venda sem posicao aberta (posicao curta ou de outra origem)
                    self.positions[token_id] = {
                        "side": "SELL",
                        "size": size,
                        "avg_price": price,
                        "market": market,
                        "outcome": outcome,
                        "wallet_source": wallet,
                        "opened_at": time.time(),
                    }

            self._save()
            log.info(
                "Trade registrado: %s %s %.4f @ %.4f [%s]",
                side, outcome[:30] if outcome else token_id[:16], size, price, wallet
            )

            # Atualizar gasto por mercado+wallet
            condition_id = trade_info.get("condition_id", token_id)
            spend_key = f"{wallet}::{condition_id}"
            usdc_amount = size * price
            if side == "BUY":
                self.market_spend[spend_key] = self.market_spend.get(spend_key, 0.0) + usdc_amount
            elif side == "SELL":
                self.market_spend[spend_key] = max(0.0, self.market_spend.get(spend_key, 0.0) - usdc_amount)

    def record_skip(self):
        with self._lock:
            self.total_skipped += 1

    def get_market_spend(self, wallet_label, condition_id):
        """Retorna quanto USDC ja foi gasto nesse mercado por essa wallet."""
        with self._lock:
            spend_key = f"{wallet_label}::{condition_id}"
            return self.market_spend.get(spend_key, 0.0)

    def record_error(self):
        with self._lock:
            self.total_errors += 1
            self._save()

    def get_open_position_count(self):
        with self._lock:
            return len(self.positions)

    def get_positions(self):
        with self._lock:
            return dict(self.positions)

    def get_recent_trades(self, limit=50):
        with self._lock:
            return list(reversed(self.trades[-limit:]))

    def get_stats(self):
        with self._lock:
            uptime = time.time() - self.start_time
            hours = uptime / 3600
            return {
                "total_copied": self.total_copied,
                "total_skipped": self.total_skipped,
                "total_errors": self.total_errors,
                "open_positions": len(self.positions),
                "uptime_hours": round(hours, 2),
                "trades_per_hour": round(self.total_copied / max(hours, 0.01), 2),
            }

    def get_trades_by_wallet(self, wallet_label, limit=50):
        """Retorna trades filtrados por wallet."""
        with self._lock:
            filtered = [t for t in self.trades if t.get("wallet_source") == wallet_label]
            return list(reversed(filtered[-limit:]))

    def get_positions_by_wallet(self, wallet_label):
        """Retorna posicoes filtradas por wallet."""
        with self._lock:
            return {k: v for k, v in self.positions.items()
                    if v.get("wallet_source") == wallet_label}

    def get_wallet_stats(self, wallet_label):
        """Retorna stats de uma wallet especifica."""
        with self._lock:
            trades = [t for t in self.trades if t.get("wallet_source") == wallet_label]
            positions = {k: v for k, v in self.positions.items()
                         if v.get("wallet_source") == wallet_label}
            total_spent = sum(t.get("price", 0) * t.get("size", 0)
                              for t in trades if t.get("side") == "BUY")
            return {
                "total_trades": len(trades),
                "open_positions": len(positions),
                "total_spent_usdc": round(total_spent, 2),
            }
