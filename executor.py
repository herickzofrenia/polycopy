"""
PolyCopy - Executor de ordens via py-clob-client
"""
import logging
import time
import config

log = logging.getLogger("polycopy.executor")

# Importa py-clob-client (pode falhar se nao instalado)
try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, OrderType
    from py_clob_client.order_builder.constants import BUY, SELL
    HAS_CLOB = True
except ImportError:
    HAS_CLOB = False
    log.warning("py-clob-client nao instalado. Apenas DRY_RUN disponivel.")


class OrderExecutor:
    """Executa ordens no Polymarket CLOB."""

    def __init__(self, tracker):
        self.tracker = tracker
        self.client = None
        self._initialized = False
        self._min_size_cache = {}  # {token_id: min_size}

        if not config.DRY_RUN and HAS_CLOB:
            self._init_client()

    def _get_min_size(self, token_id):
        """Busca o min_size do mercado via CLOB API. Cache pra nao bater toda vez."""
        if token_id in self._min_size_cache:
            return self._min_size_cache[token_id]

        min_size = 1.0  # fallback conservador
        try:
            import requests as req
            url = f"{config.CLOB_API_URL}/min-size?token_id={token_id}"
            resp = req.get(url, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict):
                    min_size = float(data.get("min_size", data.get("minimum", 1.0)))
                elif isinstance(data, (int, float)):
                    min_size = float(data)
        except Exception as e:
            log.debug("Erro ao buscar min_size: %s", e)
            # Fallback: tentar parsear do erro anterior ou usar padrao
            min_size = 5.0  # varios mercados de weather/temperature tem min 5

        self._min_size_cache[token_id] = min_size
        log.info("Min size para token %s: %.2f", token_id[:16] + "...", min_size)
        return min_size

    def _init_client(self):
        """Inicializa o ClobClient com credenciais."""
        try:
            if not config.PRIVATE_KEY or not config.POLY_SAFE_ADDRESS:
                log.error("PRIVATE_KEY ou POLY_SAFE_ADDRESS nao configurados no .env")
                return

            self.client = ClobClient(
                config.CLOB_API_URL,
                key=config.PRIVATE_KEY,
                chain_id=config.CHAIN_ID,
                signature_type=2,
                funder=config.POLY_SAFE_ADDRESS,
            )
            self.client.set_api_creds(self.client.create_or_derive_api_creds())
            self._initialized = True
            log.info("ClobClient inicializado com sucesso")
        except Exception as e:
            log.error("Falha ao inicializar ClobClient: %s", e)
            self._initialized = False

    def execute_copy_trade(self, trade_data):
        """
        Executa um copy trade baseado nos dados do trade detectado.

        trade_data = {
            "token_id": str,
            "side": "BUY" | "SELL",
            "price": float,
            "size": float,
            "market": str,
            "outcome": str,
            "wallet_source": str,
            "transaction_hash": str,
        }

        Retorna dict com resultado ou None em caso de erro.
        """
        token_id = trade_data["token_id"]
        side = trade_data["side"]
        original_price = trade_data["price"]
        market = trade_data.get("market", "")
        outcome = trade_data.get("outcome", "")
        wallet = trade_data.get("wallet_source", "")
        condition_id = trade_data.get("condition_id", token_id)
        max_market_usdc = trade_data.get("max_market_usdc", 999999)

        # Calcular preco com slippage
        if side == "BUY":
            copy_price = min(round(original_price * (1 + config.MAX_SLIPPAGE_PCT / 100), 4), 0.99)
        else:
            copy_price = max(round(original_price * (1 - config.MAX_SLIPPAGE_PCT / 100), 4), 0.01)

        # Calcular size em shares
        if side == "BUY":
            # Garantir que o notional (size * price) >= $1.05 (Polymarket min = $1)
            min_notional = max(config.COPY_SIZE_USDC, 1.05)
            copy_size = round(min_notional / copy_price, 2)
            # Verificacao final: se apos arredondamento o notional caiu abaixo de $1, ajustar
            while copy_size * copy_price < 1.0:
                copy_size += 0.01
            copy_size = round(copy_size, 2)
        else:
            # Para SELL, vender o que temos na posicao ou tamanho fixo
            open_pos = self.tracker.get_positions().get(token_id)
            if open_pos and open_pos["size"] > 0:
                copy_size = round(open_pos["size"], 2)
            else:
                min_notional = max(config.COPY_SIZE_USDC, 1.05)
                copy_size = round(min_notional / copy_price, 2)

        if copy_size < 0.1:
            log.warning("Size muito pequeno (%.2f), pulando trade", copy_size)
            self.tracker.record_skip()
            return None

        # Checar limite de posicoes
        if side == "BUY" and self.tracker.get_open_position_count() >= config.MAX_OPEN_POSITIONS:
            log.warning("Limite de posicoes abertas atingido (%d), pulando BUY", config.MAX_OPEN_POSITIONS)
            self.tracker.record_skip()
            return None

        # Checar limite de gasto por mercado (max_market_usdc)
        if side == "BUY":
            already_spent = self.tracker.get_market_spend(wallet, condition_id)
            usdc_this_trade = copy_size * copy_price
            if already_spent + usdc_this_trade > max_market_usdc:
                remaining = max_market_usdc - already_spent
                if remaining < 0.10:
                    log.info(
                        "Limite de mercado atingido para %s (gasto=%.2f, max=%.2f), pulando BUY",
                        wallet, already_spent, max_market_usdc
                    )
                    self.tracker.record_skip()
                    return None
                # Reduzir size pra caber no limite
                copy_size = round(remaining / copy_price, 2)
                if copy_size < 0.1:
                    log.info(
                        "Limite de mercado quase atingido para %s (resto=%.2f), pulando BUY",
                        wallet, remaining
                    )
                    self.tracker.record_skip()
                    return None
                log.info(
                    "Ajustando size de %.2f pra %.2f (limite mercado %.2f, gasto=%.2f)",
                    config.COPY_SIZE_USDC / copy_price, copy_size, max_market_usdc, already_spent
                )

        log.info(
            "Executando %s: %s | preco=%.4f | size=%.2f | token=%s",
            "DRY_RUN" if config.DRY_RUN else "LIVE",
            side, copy_price, copy_size, token_id[:16] + "..."
        )

        if config.DRY_RUN:
            result = {
                "order_id": "DRY_" + str(int(time.time() * 1000)),
                "status": "SIMULATED",
                "price": copy_price,
                "size": copy_size,
                "side": side,
            }
            log.info("[DRY_RUN] Ordem simulada: %s", result["order_id"])

            self.tracker.record_trade({
                "token_id": token_id,
                "side": side,
                "size": copy_size,
                "price": copy_price,
                "market": market,
                "outcome": outcome,
                "wallet_source": wallet,
                "order_id": result["order_id"],
                "dry_run": True,
                "condition_id": condition_id,
            })
            return result

        # Execucao LIVE
        if not self._initialized or not self.client:
            log.error("ClobClient nao inicializado. Configure .env e reinicie.")
            self.tracker.record_error()
            return None

        try:
            clob_side = BUY if side == "BUY" else SELL

            # Buscar min_size do mercado pra evitar rejeicao
            min_size = self._get_min_size(token_id)
            if copy_size < min_size:
                log.info(
                    "Size %.2f abaixo do minimo do mercado (%.2f), ajustando",
                    copy_size, min_size
                )
                copy_size = min_size

            order_args = OrderArgs(
                price=copy_price,
                size=copy_size,
                side=clob_side,
                token_id=token_id,
            )
            signed_order = self.client.create_order(order_args)
            resp = self.client.post_order(signed_order, OrderType.GTC)

            order_id = ""
            if isinstance(resp, dict):
                order_id = resp.get("orderID", resp.get("id", str(resp)))
            else:
                order_id = str(resp)

            log.info("[LIVE] Ordem postada: %s | resp=%s", side, order_id)

            self.tracker.record_trade({
                "token_id": token_id,
                "side": side,
                "size": copy_size,
                "price": copy_price,
                "market": market,
                "outcome": outcome,
                "wallet_source": wallet,
                "order_id": order_id,
                "dry_run": False,
                "condition_id": condition_id,
            })

            return {
                "order_id": order_id,
                "status": "POSTED",
                "price": copy_price,
                "size": copy_size,
                "side": side,
                "response": resp,
            }

        except Exception as e:
            log.error("Erro ao postar ordem: %s", e, exc_info=True)
            self.tracker.record_error()
            return None
