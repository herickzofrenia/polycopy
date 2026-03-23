"""
PolyCopy - Copy Trading Bot para Polymarket
Entry point principal

Uso:
  python main.py           (inicia bot + dashboard)
  python main.py --no-dash (inicia bot sem dashboard)
"""
import sys
import os
import time
import signal
import logging

# Adiciona diretorio do projeto ao path
sys.path.insert(0, os.path.dirname(__file__))

import config
from tracker import PositionTracker
from executor import OrderExecutor
from monitor import WalletMonitor
from dashboard import start_dashboard_thread


def setup_logging():
    """Configura logging global."""
    level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(fmt)

    log_dir = os.path.dirname(__file__)
    fh = logging.FileHandler(
        os.path.join(log_dir, "polycopy.log"),
        encoding="utf-8",
    )
    fh.setLevel(level)
    fh.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(ch)
    root.addHandler(fh)

    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    return logging.getLogger("polycopy.main")


def main():
    log = setup_logging()

    print()
    print("  PolyCopy - Copy Trading Bot para Polymarket")
    print("  " + "=" * 46)
    print(f"  Modo:        {'DRY RUN (simulacao)' if config.DRY_RUN else '*** LIVE ***'}")
    print(f"  Wallets:     {len(config.WALLETS)}")
    print(f"  Polling:     {config.POLL_INTERVAL}s")
    print(f"  Copy size:   ${config.COPY_SIZE_USDC} USDC")
    print(f"  Slippage:    {config.MAX_SLIPPAGE_PCT}%")
    print(f"  Max pos:     {config.MAX_OPEN_POSITIONS}")
    print()

    if not config.DRY_RUN:
        if not config.PRIVATE_KEY or not config.POLY_SAFE_ADDRESS:
            log.error("Modo LIVE requer PRIVATE_KEY e POLY_SAFE_ADDRESS no .env")
            log.error("Execute primeiro: python test_clob.py")
            sys.exit(1)

    # Inicializar componentes
    tracker = PositionTracker()
    executor = OrderExecutor(tracker)
    monitor = WalletMonitor(executor, tracker)

    # Dashboard
    no_dash = "--no-dash" in sys.argv
    if not no_dash:
        start_dashboard_thread(monitor, tracker)
        log.info("Dashboard disponivel em http://localhost:%d", config.DASHBOARD_PORT)

    # Iniciar monitor
    monitor.start()

    # Graceful shutdown
    stop_event = False

    def handle_signal(signum, frame):
        nonlocal stop_event
        if not stop_event:
            stop_event = True
            log.info("Sinal recebido, encerrando...")
            monitor.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    log.info("Bot iniciado. Pressione Ctrl+C para parar.")
    print()
    print("  Bot rodando! Ctrl+C para parar.")
    if not no_dash:
        print(f"  Dashboard: http://localhost:{config.DASHBOARD_PORT}")
    print()

    try:
        while not stop_event:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Ctrl+C detectado, encerrando...")
        monitor.stop()

    log.info("PolyCopy encerrado.")
    print()
    print("  PolyCopy encerrado. Ate a proxima!")


if __name__ == "__main__":
    main()
