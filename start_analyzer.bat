@echo off
echo ========================================
echo   Iniciando Servidores del Analizador
echo ========================================
echo.

:: Inicia candle_service que sincroniza automaticamente antes de servir
:: (sin --serve para que sincronice todos los simbolos del trades.json)
start "Candle API (5001)" cmd /k "cd /d %~dp0 && python candle_service.py"

:: Espera 5 segundos para que la sincronizacion inicial termine
echo Esperando sincronizacion de velas...
timeout /t 5 /nobreak > nul

:: Inicia http.server en una nueva ventana
start "HTTP Server (8000)" cmd /k "cd /d %~dp0 && python -m http.server 8000"

:: Espera 1 segundo
timeout /t 1 /nobreak > nul

:: Abre el navegador
start http://localhost:8000/analisis_bot_v3.html

echo.
echo Servidores iniciados:
echo   - Candle API:    http://localhost:5001 (sincroniza automaticamente)
echo   - HTTP Server:   http://localhost:8000
echo   - Analizador:    http://localhost:8000/analisis_bot_v3.html
echo.
echo Para cerrar, cierra las ventanas de comandos.
pause
