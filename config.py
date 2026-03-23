"""
PolyCopy - Configuracoes e constantes
"""
import os
from dotenv import load_dotenv

load_dotenv()

# --- Credenciais ---
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")
POLY_SAFE_ADDRESS = os.getenv("POLY_SAFE_ADDRESS", "")

# --- Modo de operacao ---
DRY_RUN = False  # True = simula ordens, False = executa de verdade

# --- Polling ---
POLL_INTERVAL = 2  # segundos entre cada poll

# --- Ordens ---
COPY_SIZE_USDC = 1.0       # tamanho fixo em USDC por copy trade
MAX_SLIPPAGE_PCT = 2        # slippage maximo permitido (%)
MAX_OPEN_POSITIONS = 20     # maximo de posicoes abertas simultaneas

# --- Logging ---
LOG_LEVEL = "INFO"

# --- APIs ---
DATA_API_URL = "https://data-api.polymarket.com"
GAMMA_API_URL = "https://gamma-api.polymarket.com"
CLOB_API_URL = "https://clob.polymarket.com"
CHAIN_ID = 137

# --- Dashboard ---
DASHBOARD_HOST = "0.0.0.0"
DASHBOARD_PORT = 8060

# --- Wallets alvo ---
# max_market_usdc = maximo de USDC que o bot gasta por mercado/evento nessa wallet
WALLETS = [
    {
        "address": "0x8e9eedf20dfa70956d49f608a205e402d9df38e4",
        "label": "Wallet-1",
        "price_min": 0.50,
        "price_max": 0.98,
        "max_market_usdc": 10.0,
    },
    {
        "address": "0xffb0b9b292e406fd250854a35a0c9bd5612afa37",
        "label": "Wallet-2",
        "price_min": 0.50,
        "price_max": 0.98,
        "max_market_usdc": 10.0,
    },
    {
        "address": "0x906f2454a777600aea6c506247566decef82371a",
        "label": "Wallet-3",
        "price_min": 0.50,
        "price_max": 0.98,
        "max_market_usdc": 12.0,
    },
    {
        "address": "0x45bc74efa620b45c02308acaecdff1f7c06f978b",
        "label": "Wallet-4",
        "price_min": 0.40,
        "price_max": 0.98,
        "max_market_usdc": 25.0,
    },
]

# --- Dedup ---
DEDUP_HISTORY_SIZE = 500  # quantos hashes manter em memoria para dedup
