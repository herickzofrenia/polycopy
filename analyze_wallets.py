"""
Analise das wallets alvo - portfolio, tamanho medio de trade, % da banca
"""
import requests
import json

WALLETS = [
    {"address": "0x8e9eedf20dfa70956d49f608a205e402d9df38e4", "label": "Wallet-1"},
    {"address": "0xffb0b9b292e406fd250854a35a0c9bd5612afa37", "label": "Wallet-2"},
    {"address": "0x906f2454a777600aea6c506247566decef82371a", "label": "Wallet-3"},
    {"address": "0x45bc74efa620b45c02308acaecdff1f7c06f978b", "label": "Wallet-4"},
]

DATA_API = "https://data-api.polymarket.com"


def analyze_wallet(w):
    addr = w["address"]
    label = w["label"]

    print(f"\n{'='*60}")
    print(f"  {label} - {addr}")
    print(f"{'='*60}")

    # Portfolio value
    try:
        r = requests.get(f"{DATA_API}/value?user={addr}", timeout=10)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data:
                value = float(data[0].get("value", 0))
            elif isinstance(data, dict):
                value = float(data.get("value", 0))
            else:
                value = 0
            print(f"  Portfolio Value: ${value:.2f}")
        else:
            value = 0
            print(f"  Portfolio Value: ? (status {r.status_code})")
    except Exception as e:
        value = 0
        print(f"  Portfolio Value: erro ({e})")

    # Posicoes abertas
    try:
        r = requests.get(f"{DATA_API}/positions?user={addr}&sizeThreshold=0.1&limit=50", timeout=10)
        positions = r.json() if r.status_code == 200 else []
        if not isinstance(positions, list):
            positions = positions.get("data", [])
        total_invested = sum(float(p.get("initialValue", 0)) for p in positions)
        total_current = sum(float(p.get("currentValue", 0)) for p in positions)
        print(f"  Posicoes abertas: {len(positions)}")
        print(f"  Valor investido: ${total_invested:.2f}")
        print(f"  Valor atual: ${total_current:.2f}")
        print(f"  PnL posicoes: ${total_current - total_invested:.2f}")
    except Exception as e:
        total_invested = 0
        print(f"  Posicoes: erro ({e})")

    # Trades (paginar pra pegar 2000+)
    try:
        all_trades = []
        last_timestamp = None
        pages = 0
        target = 2000

        print(f"\n  Buscando trades (alvo: {target})...", end="", flush=True)

        while len(all_trades) < target and pages < 25:
            url = f"{DATA_API}/activity?user={addr}&type=TRADE&limit=100&sortBy=TIMESTAMP&sortDirection=DESC"
            if last_timestamp:
                url += f"&end={last_timestamp}"

            r = requests.get(url, timeout=15)
            if r.status_code != 200:
                break

            batch = r.json() if r.status_code == 200 else []
            if not isinstance(batch, list):
                batch = batch.get("data", [])

            if not batch:
                break

            all_trades.extend(batch)
            pages += 1
            print(f" {len(all_trades)}", end="", flush=True)

            # Pegar timestamp do ultimo trade pra proxima pagina
            last_ts = batch[-1].get("timestamp")
            if last_ts and last_ts != last_timestamp:
                last_timestamp = int(last_ts) - 1
            else:
                break

        trades = all_trades
        print(f" OK!")

        if not trades:
            print("  Sem trades")
            return

        buys = [t for t in trades if t.get("side", "").upper() == "BUY"]
        sells = [t for t in trades if t.get("side", "").upper() == "SELL"]

        print(f"  Total trades: {len(trades)} ({len(buys)} BUY, {len(sells)} SELL)")

        # Analisar BUYs
        if buys:
            usdc_sizes = []
            for t in buys:
                usdc = float(t.get("usdcSize", 0))
                if usdc == 0:
                    price = float(t.get("price", 0))
                    size = float(t.get("size", 0))
                    usdc = price * size
                usdc_sizes.append(usdc)

            avg_usdc = sum(usdc_sizes) / len(usdc_sizes) if usdc_sizes else 0
            min_usdc = min(usdc_sizes) if usdc_sizes else 0
            max_usdc = max(usdc_sizes) if usdc_sizes else 0
            median_usdc = sorted(usdc_sizes)[len(usdc_sizes)//2] if usdc_sizes else 0

            print(f"\n  --- Analise BUY ({len(buys)} trades) ---")
            print(f"  USDC medio por trade: ${avg_usdc:.2f}")
            print(f"  USDC mediano:         ${median_usdc:.2f}")
            print(f"  USDC minimo:          ${min_usdc:.2f}")
            print(f"  USDC maximo:          ${max_usdc:.2f}")
            print(f"  Total gasto (BUYs):   ${sum(usdc_sizes):.2f}")

            # % da banca (estimando banca = portfolio + total investido)
            estimated_bankroll = max(value, total_invested, 100)  # minimo $100 pra nao dividir por 0
            avg_pct = (avg_usdc / estimated_bankroll) * 100
            median_pct = (median_usdc / estimated_bankroll) * 100
            print(f"\n  Banca estimada: ~${estimated_bankroll:.0f}")
            print(f"  % medio por trade:  {avg_pct:.2f}%")
            print(f"  % mediano por trade: {median_pct:.2f}%")

            # Distribuicao por faixas
            ranges = {"<$1": 0, "$1-5": 0, "$5-10": 0, "$10-25": 0, "$25-50": 0, "$50-100": 0, ">$100": 0}
            for u in usdc_sizes:
                if u < 1: ranges["<$1"] += 1
                elif u < 5: ranges["$1-5"] += 1
                elif u < 10: ranges["$5-10"] += 1
                elif u < 25: ranges["$10-25"] += 1
                elif u < 50: ranges["$25-50"] += 1
                elif u < 100: ranges["$50-100"] += 1
                else: ranges[">$100"] += 1

            print(f"\n  Distribuicao de trades por tamanho:")
            for rng, cnt in ranges.items():
                if cnt > 0:
                    bar = "#" * cnt
                    print(f"    {rng:>8}: {cnt:>3} {bar}")

            # Ultimos 10 trades
            print(f"\n  Ultimos 10 BUYs:")
            for t in buys[:10]:
                usdc = float(t.get("usdcSize", 0))
                if usdc == 0:
                    usdc = float(t.get("price", 0)) * float(t.get("size", 0))
                title = t.get("title", "?")[:40]
                price = float(t.get("price", 0))
                outcome = t.get("outcome", "?")
                print(f"    ${usdc:.2f} @ {price:.4f} | {outcome} | {title}")

    except Exception as e:
        print(f"  Trades: erro ({e})")


def main():
    print("\n" + "=" * 60)
    print("  ANALISE DAS WALLETS ALVO - POLYMARKET")
    print("=" * 60)

    for w in WALLETS:
        analyze_wallet(w)

    print(f"\n{'='*60}")
    print("  ANALISE CONCLUIDA")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
