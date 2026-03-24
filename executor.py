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
    from py_clob_client.clob_types import OrderArgs, MarketOrderArgs, OrderType
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
        price_max = trade_data.get("price_max", 0.99)

        log.info(
            "MAX_MKT CHECK: wallet=%s condition=%s market=%s already_spent=%.2f max=%.2f",
            wallet, condition_id[:20] if condition_id else "?",
            market[:30] if market else "?",
            self.tracker.get_market_spend(wallet, condition_id),
            max_market_usdc,
        )

        # Calcular preco com slippage, limitado pelo price_max da wallet
        if side == "BUY":
            copy_price = min(
                round(original_price * (1 + config.MAX_SLIPPAGE_PCT / 100), 4),
                price_max,  # nunca acima do price_max da wallet
                0.99,
            )
        else:
            copy_price = max(round(original_price * (1 - config.MAX_SLIPPAGE_PCT / 100), 4), 0.01)

        # Calcular size em shares baseado no modo de copia
        if side == "BUY":
            original_size = trade_data.get("size", 0)
            original_usdc = original_price * original_size

            if config.COPY_MODE == "PERCENT":
                # Calcular % que o trader usou
                # usdcSize do trade / banca estimada do trader
                # Como nao sabemos a banca exata, usamos o valor do trade
                # e aplicamos a mesma proporcao relativa na nossa banca
                # Ex: trader gastou $5 num trade, sua banca e $50
                # Se minha banca e $100 e mult=1.0: gasto $10
                if original_usdc > 0 and config.MY_BANKROLL > 0:
                    # Proporcao direta: (trade_usdc / minha_banca) * multiplicador
                    # Mas o que queremos e: mesma % relativa
                    # Se trader gastou $5 com banca de ~$500, usou 1%
                    # Na minha banca de $50 com mult 1.0 = $0.50
                    # Precisamos estimar a banca do trader
                    # Heuristica: buscar do trade_data ou usar estimativa
                    trader_banca = trade_data.get("trader_bankroll", 0)
                    if trader_banca <= 0:
                        # Estimar banca do trader pelo usdcSize do trade
                        # Se ele fez trade de $5, estimamos banca de ~$500 (1%)
                        # Melhor: usar o original_usdc direto como referencia
                        # my_usdc = (original_usdc / original_usdc) * MY_BANKROLL * mult
                        # Simplificado: copiar o mesmo valor proporcional
                        my_usdc = original_usdc * config.COPY_MULTIPLIER
                    else:
                        pct_used = original_usdc / trader_banca
                        my_usdc = config.MY_BANKROLL * pct_used * config.COPY_MULTIPLIER

                    my_usdc = max(my_usdc, 1.05)  # minimo $1.05
                    copy_size = round(my_usdc / copy_price, 2)
                    log.info(
                        "Modo PERCENT: trade=$%.2f | mult=%.2fx | meu_trade=$%.2f",
                        original_usdc, config.COPY_MULTIPLIER, my_usdc
                    )
                else:
                    # Fallback pra FIXED
                    fixed_usdc = max(config.COPY_SIZE_USDC * config.COPY_MULTIPLIER, 1.05)
                    copy_size = round(fixed_usdc / copy_price, 2)
            else:
                # Modo FIXED: valor fixo com multiplicador
                fixed_usdc = config.COPY_SIZE_USDC * config.COPY_MULTIPLIER
                min_notional = max(fixed_usdc, 1.05)
                copy_size = round(min_notional / copy_price, 2)

            # Garantir notional minimo de $1
            while copy_size * copy_price < 1.0:
                copy_size += 0.01
            copy_size = round(copy_size, 2)
        else:
            # Para SELL, vender o que temos na posicao ou tamanho fixo
            open_pos = self.tracker.get_positions().get(token_id)
            if open_pos and open_pos["size"] > 0:
                copy_size = round(open_pos["size"], 2)
            else:
                fixed_usdc = config.COPY_SIZE_USDC * config.COPY_MULTIPLIER
                min_notional = max(fixed_usdc, 1.05)
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

        # Checar limite de gasto por mercado (max_market_usdc) - check inicial
        # Nota: sera revalidado depois do ajuste de min_size
        if side == "BUY":
            already_spent = self.tracker.get_market_spend(wallet, condition_id)
            if already_spent >= max_market_usdc:
                log.info(
                    "Limite de mercado atingido para %s (gasto=%.2f, max=%.2f), pulando BUY",
                    wallet, already_spent, max_market_usdc
                )
                self.tracker.record_skip()
                return None

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
            import re
            clob_side = BUY if side == "BUY" else SELL

            # Usar min_size do cache se ja conhecido
            cached_min = self._min_size_cache.get(token_id, 0)
            if cached_min > copy_size:
                copy_size = cached_min
                log.info("Ajustando size para min cached: %.2f", copy_size)

            # REVALIDAR max_market depois do ajuste de min_size
            if side == "BUY":
                real_cost = copy_size * copy_price
                already_spent = self.tracker.get_market_spend(wallet, condition_id)
                if already_spent + real_cost > max_market_usdc:
                    remaining = max_market_usdc - already_spent
                    log.info(
                        "Limite de mercado estourado apos min_size (custo=%.2f, gasto=%.2f, max=%.2f, rest=%.2f), pulando",
                        real_cost, already_spent, max_market_usdc, remaining
                    )
                    self.tracker.record_skip()
                    return None

            # Calcular USDC amount pra market order
            usdc_amount = round(copy_size * copy_price, 2)
            usdc_amount = max(usdc_amount, 1.05)

            resp = None
            order_type_used = "FOK"

            # ESTRATEGIA 1: FOK Market Order (executa no preco de mercado ou cancela)
            # Melhor pra mercados de 5 minutos - nao fica pendurado no book
            try:
                log.info("[FOK] Tentando market order: %s $%.2f em %s", side, usdc_amount, token_id[:16] + "...")
                market_args = MarketOrderArgs(
                    token_id=token_id,
                    amount=usdc_amount,
                    side=clob_side,
                )
                signed_market = self.client.create_market_order(market_args)
                resp = self.client.post_order(signed_market, OrderType.FOK)
                log.info("[FOK] Market order executada com sucesso")
            except Exception as fok_err:
                fok_msg = str(fok_err)
                log.warning("[FOK] Falhou: %s", fok_msg[:100])

                # Se min size erro, parsear e tentar GTC
                if "lower than the minimum" in fok_msg:
                    match = re.search(r"minimum:\s*(\d+\.?\d*)", fok_msg)
                    if match:
                        min_size = float(match.group(1))
                        self._min_size_cache[token_id] = min_size
                        copy_size = min_size
                        usdc_amount = round(copy_size * copy_price, 2)
                        # Revalidar max_market
                        spent = self.tracker.get_market_spend(wallet, condition_id)
                        if side == "BUY" and spent + usdc_amount > max_market_usdc:
                            log.info("Min size=%.0f estoura limite, pulando", min_size)
                            self.tracker.record_skip()
                            return None

                # ESTRATEGIA 2: GTC Limit Order (fallback)
                try:
                    log.info("[GTC] Fallback limit order: %s %.2f @ %.4f", side, copy_size, copy_price)
                    order_args = OrderArgs(
                        price=copy_price,
                        size=copy_size,
                        side=clob_side,
                        token_id=token_id,
                    )
                    signed_order = self.client.create_order(order_args)
                    resp = self.client.post_order(signed_order, OrderType.GTC)
                    order_type_used = "GTC"
                    log.info("[GTC] Limit order postada")
                except Exception as gtc_err:
                    gtc_msg = str(gtc_err)
                    # Parsear min size e retry
                    if "lower than the minimum" in gtc_msg:
                        match = re.search(r"minimum:\s*(\d+\.?\d*)", gtc_msg)
                        if match:
                            min_size = float(match.group(1))
                            self._min_size_cache[token_id] = min_size
                            copy_size = min_size
                            spent = self.tracker.get_market_spend(wallet, condition_id)
                            new_cost = copy_size * copy_price
                            if side == "BUY" and spent + new_cost > max_market_usdc:
                                log.info("Min size=%.0f estoura limite, pulando", min_size)
                                self.tracker.record_skip()
                                return None
                            order_args = OrderArgs(
                                price=copy_price, size=copy_size,
                                side=clob_side, token_id=token_id,
                            )
                            signed_order = self.client.create_order(order_args)
                            resp = self.client.post_order(signed_order, OrderType.GTC)
                            order_type_used = "GTC-retry"
                        else:
                            raise
                    elif "min size:" in gtc_msg:
                        match = re.search(r"min size:\s*\$?(\d+\.?\d*)", gtc_msg)
                        if match:
                            min_notional = float(match.group(1))
                            copy_size = round(min_notional / copy_price + 0.1, 2)
                            spent = self.tracker.get_market_spend(wallet, condition_id)
                            if side == "BUY" and spent + copy_size * copy_price > max_market_usdc:
                                self.tracker.record_skip()
                                return None
                            order_args = OrderArgs(
                                price=copy_price, size=copy_size,
                                side=clob_side, token_id=token_id,
                            )
                            signed_order = self.client.create_order(order_args)
                            resp = self.client.post_order(signed_order, OrderType.GTC)
                            order_type_used = "GTC-notional"
                        else:
                            raise
                    else:
                        raise

            if resp is None:
                log.error("Nenhuma ordem executada")
                self.tracker.record_error()
                return None

            order_id = ""
            if isinstance(resp, dict):
                order_id = resp.get("orderID", resp.get("id", str(resp)))
            else:
                order_id = str(resp)

            log.info("[LIVE] Ordem postada [%s]: %s | resp=%s", order_type_used, side, order_id)

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
