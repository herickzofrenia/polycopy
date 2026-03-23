@echo off
title PolyCopy - Dashboard
cd /d "%~dp0"
echo.
echo   Iniciando Dashboard em http://localhost:8060
echo.
python -c "from dashboard import *; from tracker import PositionTracker; from monitor import WalletMonitor; from executor import OrderExecutor; t=PositionTracker(); e=OrderExecutor(t); m=WalletMonitor(e,t); init_dashboard(m,t); app.run(host='0.0.0.0',port=8060,debug=False)"
pause
