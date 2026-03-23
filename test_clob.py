"""
PolyCopy - Teste de conexao CLOB e setup de allowance (USDC + CTF)

IMPORTANTE: Execute isso ANTES de rodar o bot em modo LIVE.
Sem allowance aprovada, ordens de SELL falham com "not enough balance/allowance".

Uso:
  python test_clob.py
"""
import sys
import os

# Adiciona diretorio do projeto ao path
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

import config


def main():
    print("=" * 60)
    print("  PolyCopy - Teste de Conexao CLOB + Allowance")
    print("=" * 60)
    print()

    # Checar credenciais
    if not config.PRIVATE_KEY:
        print("[ERRO] PRIVATE_KEY nao configurada no .env")
        print("  Copie .env.example para .env e preencha suas credenciais")
        sys.exit(1)

    if not config.POLY_SAFE_ADDRESS:
        print("[ERRO] POLY_SAFE_ADDRESS nao configurado no .env")
        sys.exit(1)

    print(f"[OK] PRIVATE_KEY: {'*' * 8}...{config.PRIVATE_KEY[-6:]}")
    print(f"[OK] POLY_SAFE_ADDRESS: {config.POLY_SAFE_ADDRESS}")
    print()

    # Importar py-clob-client
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import (
            BalanceAllowanceParams,
            AssetType,
            OrderArgs,
            OrderType,
        )
        from py_clob_client.order_builder.constants import BUY
        print("[OK] py-clob-client importado com sucesso")
    except ImportError as e:
        print(f"[ERRO] py-clob-client nao instalado: {e}")
        print("  Execute: pip install py-clob-client")
        sys.exit(1)

    # Inicializar cliente
    print()
    print("--- Inicializando ClobClient ---")
    try:
        client = ClobClient(
            config.CLOB_API_URL,
            key=config.PRIVATE_KEY,
            chain_id=config.CHAIN_ID,
            signature_type=2,
            funder=config.POLY_SAFE_ADDRESS,
        )
        print("[OK] ClobClient criado")
    except Exception as e:
        print(f"[ERRO] Falha ao criar ClobClient: {e}")
        sys.exit(1)

    # Derivar API credentials
    try:
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)
        print(f"[OK] API creds derivadas (key={creds.api_key[:16]}...)")
    except Exception as e:
        print(f"[ERRO] Falha ao derivar API creds: {e}")
        sys.exit(1)

    # Checar allowance USDC (Collateral)
    print()
    print("--- Checando Allowances ---")
    try:
        usdc_params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        usdc_allowance = client.get_balance_allowance(usdc_params)
        print(f"[INFO] Allowance USDC (COLLATERAL): {usdc_allowance}")

        if hasattr(usdc_allowance, "allowance"):
            val = float(usdc_allowance.allowance) if usdc_allowance.allowance else 0
        elif isinstance(usdc_allowance, dict):
            val = float(usdc_allowance.get("allowance", 0))
        else:
            val = 0

        if val < 100:
            print("[AVISO] Allowance USDC baixa! Atualizando...")
            resp = client.update_balance_allowance(usdc_params)
            print(f"[OK] Allowance USDC atualizada: {resp}")
        else:
            print("[OK] Allowance USDC suficiente")
    except Exception as e:
        print(f"[AVISO] Erro ao checar allowance USDC: {e}")
        print("  Pode ser necessario aprovar manualmente via Polymarket UI")

    # Checar allowance CTF (Conditional - necessario para SELL)
    try:
        ctf_params = BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL)
        ctf_allowance = client.get_balance_allowance(ctf_params)
        print(f"[INFO] Allowance CTF (CONDITIONAL): {ctf_allowance}")

        if hasattr(ctf_allowance, "allowance"):
            val = float(ctf_allowance.allowance) if ctf_allowance.allowance else 0
        elif isinstance(ctf_allowance, dict):
            val = float(ctf_allowance.get("allowance", 0))
        else:
            val = 0

        if val < 100:
            print("[AVISO] Allowance CTF baixa! Atualizando...")
            resp = client.update_balance_allowance(ctf_params)
            print(f"[OK] Allowance CTF atualizada: {resp}")
        else:
            print("[OK] Allowance CTF suficiente")
    except Exception as e:
        print(f"[AVISO] Erro ao checar allowance CTF: {e}")
        print("  Pode ser necessario aprovar manualmente via Polymarket UI")

    # Teste basico de API
    print()
    print("--- Teste de API ---")
    try:
        # Tentar buscar um mercado qualquer
        import requests
        resp = requests.get(f"{config.CLOB_API_URL}/time", timeout=10)
        print(f"[OK] CLOB API respondeu: status={resp.status_code}")
    except Exception as e:
        print(f"[AVISO] Nao conseguiu acessar CLOB API: {e}")

    print()
    print("=" * 60)
    print("  Teste concluido!")
    print("  Se tudo esta [OK], o bot pode operar em modo LIVE.")
    print("  Para DRY_RUN, nenhuma configuracao adicional necessaria.")
    print("=" * 60)


if __name__ == "__main__":
    main()
