"""
PolyCopy - Auto-Redeem de posicoes resolvidas

Monitora posicoes via Data API, detecta mercados resolvidos,
e executa redeemPositions via Safe execTransaction.

Requer web3 e um RPC Polygon (publico ou proprio).
"""
import threading
import time
import logging
import requests

import config

log = logging.getLogger("polycopy.redeemer")

# Contratos Polymarket (Polygon Mainnet)
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"  # Conditional Tokens Framework
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDC.e on Polygon
HASH_ZERO = b'\x00' * 32

# ABI minima do CTF pra redeemPositions
CTF_REDEEM_ABI = [
    {
        "name": "redeemPositions",
        "type": "function",
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId", "type": "bytes32"},
            {"name": "indexSets", "type": "uint256[]"},
        ],
        "outputs": [],
    }
]

# ABI minima do Safe pra execTransaction
SAFE_EXEC_ABI = [
    {
        "name": "execTransaction",
        "type": "function",
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "data", "type": "bytes"},
            {"name": "operation", "type": "uint8"},
            {"name": "safeTxGas", "type": "uint256"},
            {"name": "baseGas", "type": "uint256"},
            {"name": "gasPrice", "type": "uint256"},
            {"name": "gasToken", "type": "address"},
            {"name": "refundReceiver", "type": "address"},
            {"name": "signatures", "type": "bytes"},
        ],
        "outputs": [{"name": "success", "type": "bool"}],
    },
    {
        "name": "nonce",
        "type": "function",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "getTransactionHash",
        "type": "function",
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "data", "type": "bytes"},
            {"name": "operation", "type": "uint8"},
            {"name": "safeTxGas", "type": "uint256"},
            {"name": "baseGas", "type": "uint256"},
            {"name": "gasPrice", "type": "uint256"},
            {"name": "gasToken", "type": "address"},
            {"name": "refundReceiver", "type": "address"},
            {"name": "_nonce", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bytes32"}],
    },
]

# RPC Polygon (tenta varios publicos)
POLYGON_RPCS = [
    "https://polygon.llamarpc.com",
    "https://polygon-bor-rpc.publicnode.com",
    "https://polygon.drpc.org",
    "https://polygon-rpc.com",
]

HAS_WEB3 = False
try:
    from web3 import Web3
    from eth_account.messages import encode_defunct
    HAS_WEB3 = True
except ImportError:
    pass


class AutoRedeemer:
    """Auto-redeem de posicoes em mercados resolvidos via Safe."""

    def __init__(self):
        self._lock = threading.Lock()
        self._pending_claims = []
        self._redeemed = set()  # condition_ids ja resgatados
        self._stop_event = threading.Event()
        self._thread = None
        self.CHECK_INTERVAL = 60
        self.w3 = None
        self._init_web3()

    def _init_web3(self):
        if not HAS_WEB3:
            log.warning("web3 nao instalado. Auto-redeem desabilitado.")
            log.warning("Instale com: pip install web3")
            return
        for rpc in POLYGON_RPCS:
            try:
                w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))
                if w3.is_connected():
                    self.w3 = w3
                    log.info("Web3 conectado ao Polygon via %s (auto-redeem ativo)", rpc)
                    return
            except Exception:
                continue
        log.warning("Web3 nao conseguiu conectar a nenhum RPC Polygon")

    def start(self):
        self._thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="auto-redeemer"
        )
        self._thread.start()
        log.info("Auto-redeemer iniciado (check a cada %ds)", self.CHECK_INTERVAL)

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def get_pending_claims(self):
        with self._lock:
            return list(self._pending_claims)

    def _monitor_loop(self):
        time.sleep(15)  # esperar bot inicializar
        while not self._stop_event.is_set():
            try:
                self._check_and_redeem()
            except Exception as e:
                log.error("Erro no auto-redeemer: %s", e)
            for _ in range(self.CHECK_INTERVAL):
                if self._stop_event.is_set():
                    return
                time.sleep(1)

    def _check_and_redeem(self):
        """Busca posicoes, detecta resolvidas, e tenta redeem."""
        if not config.POLY_SAFE_ADDRESS:
            return

        # Buscar posicoes via Data API
        try:
            url = (
                f"{config.DATA_API_URL}/positions"
                f"?user={config.POLY_SAFE_ADDRESS}"
                f"&sizeThreshold=0.1"
                f"&limit=50"
            )
            resp = requests.get(url, timeout=15)
            if resp.status_code != 200:
                return
            positions = resp.json()
            if not isinstance(positions, list):
                positions = positions.get("data", positions.get("results", []))
        except Exception as e:
            log.debug("Erro ao buscar posicoes: %s", e)
            return

        pending = []
        for pos in positions:
            redeemable = pos.get("redeemable", False)
            size = float(pos.get("size", 0))

            if not redeemable:
                continue
            if size < 0.1:
                continue

            condition_id = pos.get("conditionId", pos.get("condition_id", ""))
            if not condition_id:
                continue

            title = pos.get("title", pos.get("market", "?"))
            outcome = pos.get("outcome", "")
            slug = pos.get("slug", pos.get("eventSlug", ""))
            cur_value = float(pos.get("currentValue", 0))

            entry = {
                "title": title,
                "outcome": outcome,
                "size": size,
                "current_value": cur_value,
                "condition_id": condition_id,
                "slug": slug,
            }
            pending.append(entry)

            # Tentar auto-redeem se ainda nao tentou
            if condition_id not in self._redeemed:
                success = self._execute_redeem(condition_id, title)
                if success:
                    self._redeemed.add(condition_id)

        with self._lock:
            self._pending_claims = pending

    def _execute_redeem(self, condition_id, title):
        """Executa redeemPositions via Safe execTransaction."""
        if not self.w3 or not HAS_WEB3:
            log.info("Claim pendente (web3 nao disponivel): %s", title[:40])
            return False

        if not config.PRIVATE_KEY or not config.POLY_SAFE_ADDRESS:
            return False

        try:
            log.info("Tentando auto-redeem: %s (condition=%s)", title[:40], condition_id[:20])

            # Encodar calldata pra redeemPositions
            ctf = self.w3.eth.contract(
                address=Web3.to_checksum_address(CTF_ADDRESS),
                abi=CTF_REDEEM_ABI,
            )

            # condition_id precisa ser bytes32
            if condition_id.startswith("0x"):
                cid_bytes = bytes.fromhex(condition_id[2:])
            else:
                cid_bytes = bytes.fromhex(condition_id)

            # indexSets [1, 2] = ambos outcomes (Yes e No)
            calldata = ctf.encode_abi(
                "redeemPositions",
                [
                    Web3.to_checksum_address(USDC_ADDRESS),
                    HASH_ZERO,
                    cid_bytes,
                    [1, 2],
                ],
            )

            # Executar via Safe
            safe = self.w3.eth.contract(
                address=Web3.to_checksum_address(config.POLY_SAFE_ADDRESS),
                abi=SAFE_EXEC_ABI,
            )

            # Pegar nonce do Safe
            nonce = safe.functions.nonce().call()

            # Parametros do execTransaction
            to = Web3.to_checksum_address(CTF_ADDRESS)
            value = 0
            operation = 0  # Call
            safe_tx_gas = 0
            base_gas = 0
            gas_price_param = 0
            gas_token = "0x0000000000000000000000000000000000000000"
            refund_receiver = "0x0000000000000000000000000000000000000000"

            # Gerar hash da transacao Safe
            tx_hash = safe.functions.getTransactionHash(
                to, value, bytes.fromhex(calldata[2:]) if calldata.startswith("0x") else bytes.fromhex(calldata),
                operation, safe_tx_gas, base_gas, gas_price_param,
                Web3.to_checksum_address(gas_token),
                Web3.to_checksum_address(refund_receiver),
                nonce,
            ).call()

            # Assinar com a private key
            pk = config.PRIVATE_KEY
            if not pk.startswith("0x"):
                pk = "0x" + pk
            account = self.w3.eth.account.from_key(pk)

            # Assinatura pre-validated (tipo 1 do Safe)
            # Para Safe com 1 signer, a assinatura e: r=owner, s=0, v=1
            owner_addr = account.address
            owner_bytes = bytes.fromhex(owner_addr[2:].lower())
            # Formato: r (32 bytes, padded address) + s (32 bytes, zeros) + v (1 byte, = 1)
            sig = b'\x00' * 12 + bytes.fromhex(owner_addr[2:]) + b'\x00' * 32 + b'\x01'

            # Construir transacao
            calldata_bytes = bytes.fromhex(calldata[2:]) if calldata.startswith("0x") else bytes.fromhex(calldata)

            tx = safe.functions.execTransaction(
                to, value, calldata_bytes,
                operation, safe_tx_gas, base_gas, gas_price_param,
                Web3.to_checksum_address(gas_token),
                Web3.to_checksum_address(refund_receiver),
                sig,
            ).build_transaction({
                "from": account.address,
                "nonce": self.w3.eth.get_transaction_count(account.address),
                "gas": 500000,
                "gasPrice": self.w3.eth.gas_price,
                "chainId": 137,
            })

            # Assinar e enviar
            signed = self.w3.eth.account.sign_transaction(tx, pk)
            tx_hash_sent = self.w3.eth.send_raw_transaction(signed.raw_transaction)

            log.info(
                "Auto-redeem enviado! tx=%s mercado=%s",
                tx_hash_sent.hex(), title[:40]
            )

            # Esperar confirmacao
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash_sent, timeout=60)
            if receipt["status"] == 1:
                log.info("Auto-redeem SUCCESS: %s", title[:40])
                return True
            else:
                log.warning("Auto-redeem FALHOU (reverted): %s", title[:40])
                return False

        except Exception as e:
            log.error("Erro no auto-redeem de %s: %s", title[:40], e)
            return False
