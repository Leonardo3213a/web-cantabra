@echo off
setlocal
cd /d "%~dp0"
echo Iniciando WEB-CANTABRA. Se instalaran las dependencias en .venv si hace falta.
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 iniciar.py
) else (
  python iniciar.py
)
if errorlevel 1 goto error
exit /b 0
:error
echo.
echo No se pudo iniciar WEB-CANTABRA. Lee el mensaje de error anterior.
echo Para reparar manualmente: python -m pip install -r requirements.txt
pause
exit /b 1
