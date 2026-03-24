"""
PolyCopy - Configuracoes e constantes
"""
import os
import json
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
COPY_SIZE_USDC = 1.0       # tamanho fixo em USDC (usado no modo FIXED)
MAX_SLIPPAGE_PCT = 2        # slippage maximo permitido (%)
MAX_OPEN_POSITIONS = 20     # maximo de posicoes abertas simultaneas

# --- Modo de copia ---
# COPY_MODE: como calcular o tamanho do trade
#   "FIXED"   = valor fixo em USDC (COPY_SIZE_USDC)
#   "PERCENT" = mesma % da banca que o trader alvo usou
# COPY_MULTIPLIER: multiplicador sobre o tamanho calculado
#   1.0 = igual ao trader, 0.5 = metade, 2.0 = dobro
# MY_BANKROLL: sua banca total pra calculo de % (em USDC)
COPY_MODE = "FIXED"       # "FIXED" ou "PERCENT"
COPY_MULTIPLIER = 1.0      # multiplicador (0.25, 0.5, 1.0, 2.0, etc)
MY_BANKROLL = 50.0          # sua banca em USDC (pra modo PERCENT)

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
        "max_market_usdc": 6.0,
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

# --- Persistencia de config ---
_OVERRIDE_FILE = os.path.join(os.path.dirname(__file__), "config_override.json")


def load_overrides():
    """Carrega overrides salvos pelo dashboard e aplica."""
    global COPY_SIZE_USDC, MAX_SLIPPAGE_PCT, MAX_OPEN_POSITIONS, POLL_INTERVAL
    global COPY_MODE, COPY_MULTIPLIER, MY_BANKROLL
    try:
        if os.path.exists(_OVERRIDE_FILE):
            with open(_OVERRIDE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Geral
            if "copy_size_usdc" in data:
                COPY_SIZE_USDC = float(data["copy_size_usdc"])
            if "max_slippage_pct" in data:
                MAX_SLIPPAGE_PCT = int(data["max_slippage_pct"])
            if "max_open_positions" in data:
                MAX_OPEN_POSITIONS = int(data["max_open_positions"])
            if "poll_interval" in data:
                POLL_INTERVAL = int(data["poll_interval"])
            if "copy_mode" in data:
                COPY_MODE = str(data["copy_mode"]).upper()
            if "copy_multiplier" in data:
                COPY_MULTIPLIER = float(data["copy_multiplier"])
            if "my_bankroll" in data:
                MY_BANKROLL = float(data["my_bankroll"])
            # Wallets
            wallet_overrides = data.get("wallets", {})
            for wcfg in WALLETS:
                label = wcfg["label"]
                if label in wallet_overrides:
                    wo = wallet_overrides[label]
                    if "price_min" in wo:
                        wcfg["price_min"] = float(wo["price_min"])
                    if "price_max" in wo:
                        wcfg["price_max"] = float(wo["price_max"])
                    if "max_market_usdc" in wo:
                        wcfg["max_market_usdc"] = float(wo["max_market_usdc"])
    except Exception:
        pass


def save_overrides():
    """Salva config atual em disco pra persistir entre restarts."""
    try:
        wallet_data = {}
        for wcfg in WALLETS:
            wallet_data[wcfg["label"]] = {
                "price_min": wcfg["price_min"],
                "price_max": wcfg["price_max"],
                "max_market_usdc": wcfg.get("max_market_usdc", 999999),
            }
        data = {
            "copy_size_usdc": COPY_SIZE_USDC,
            "max_slippage_pct": MAX_SLIPPAGE_PCT,
            "max_open_positions": MAX_OPEN_POSITIONS,
            "poll_interval": POLL_INTERVAL,
            "copy_mode": COPY_MODE,
            "copy_multiplier": COPY_MULTIPLIER,
            "my_bankroll": MY_BANKROLL,
            "wallets": wallet_data,
        }
        with open(_OVERRIDE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


# Carregar overrides ao importar
load_overrides()
