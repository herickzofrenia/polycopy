# PolyCopy - Copy Trading Bot para Polymarket

Bot de copy trading que monitora wallets alvo no Polymarket e replica trades automaticamente via py-clob-client.

## Como funciona

- Polling da Data API a cada 2s para detectar novos trades
- Deduplicacao robusta por transactionHash + timestamp
- Replica BUY e SELL via CLOB API (GTC limit order)
- Filtro de preco por wallet (price_min / price_max)
- Limite de gasto por mercado por wallet (max_market_usdc)
- Dashboard web em tempo real (localhost:8060)

## Setup

```bash
git clone https://github.com/herickzofrenia/polycopy.git
cd polycopy
pip install -r requirements.txt
copy .env.example .env
```

Edite o `.env` com suas credenciais:

```
PRIVATE_KEY=sua_chave_privada_sem_0x
POLY_SAFE_ADDRESS=endereco_proxy_wallet
```

## Uso

```bash
# Testar conexao e aprovar allowances
python test_clob.py

# Iniciar bot + dashboard
python main.py

# Dashboard
http://localhost:8060
```

## Configuracao

Tudo no `config.py`:

- `DRY_RUN` - True = simulacao, False = ordens reais
- `COPY_SIZE_USDC` - valor fixo por trade ($1 padrao)
- `MAX_SLIPPAGE_PCT` - slippage maximo (2% padrao)
- `MAX_OPEN_POSITIONS` - limite de posicoes abertas
- `WALLETS` - lista de wallets com price range e max market individuais

## Arquitetura

```
main.py       - entry point
config.py     - configuracoes e wallets
monitor.py    - polling das wallets (threading)
executor.py   - execucao via py-clob-client
tracker.py    - tracking de posicoes e PnL
dashboard.py  - dashboard web Flask
test_clob.py  - teste de conexao + allowances
```

## Aviso

Software experimental. Trading envolve risco. Use por sua conta e risco.
