@echo off
REM Launcher webapp per il Task Scheduler: monta la share dell'amico nella stessa
REM sessione di logon (le mappature SMB sono per-sessione) e avvia la webapp.
cd /d C:\TG_TradinGo
if not exist logs mkdir logs

powershell -NoProfile -ExecutionPolicy Bypass -Command "$n = @(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" -EA SilentlyContinue | Where-Object { $_.CommandLine -match 'webapp\.app' }).Count; exit $n"
if errorlevel 1 (
    echo [%date% %time%] Webapp gia' attiva: avvio ignorato>> logs\webapp_task.log
    exit /b 0
)

echo [%date% %time%] Avvio webapp da Task Scheduler>> logs\webapp_task.log
C:\TG_TradinGo\.venv\Scripts\python.exe -m webapp.app >> logs\webapp_stdout.log 2>&1
echo [%date% %time%] Webapp terminata (exit %errorlevel%)>> logs\webapp_task.log
exit /b %errorlevel%
