@echo off
::
:: setup_windows_tasks.bat
:: =======================
:: Creates Windows Scheduled Tasks for all 4 Quant projects.
::
:: Saskatchewan (SK), Canada = UTC-6, NO daylight saving time.
::   Morning:   6:00 AM SK  = 12:00 UTC
::   Afternoon: 4:00 PM SK  = 22:00 UTC
::
:: KEY FEATURE: Tasks use StartWhenAvailable = true
::   -> If the laptop was asleep at the trigger time, the task runs
::      immediately when the computer wakes up.
::
:: Adjust MORNING_TIME / AFTERNOON_TIME below if your computer
:: is NOT set to Saskatchewan (UTC-6) local time:
::   Eastern  (UTC-5)  -> 07:00 and 17:00
::   Mountain (UTC-7)  -> 05:00 and 15:00
::   Pacific  (UTC-8)  -> 04:00 and 14:00
::   UTC                -> 12:00 and 22:00
::
:: Run as Administrator: right-click -> "Run as Administrator"
::

setlocal EnableDelayedExpansion

set "PROJECTS_DIR=D:\OMNP - Quant\Projetos"
set "PYTHON=python"
set "SCHEDULER=%PROJECTS_DIR%\scheduler.py"

:: Local times — assumes computer is in Saskatchewan (UTC-6)
set "MORNING_TIME=06:00"
set "AFTERNOON_TIME=16:00"

:: ── Checks ────────────────────────────────────────────────────────────────────
where python >nul 2>&1 || (echo [ERROR] Python not found. Install Python 3.8+ && pause && exit /b 1)
if not exist "%SCHEDULER%" (echo [ERROR] scheduler.py not found at %SCHEDULER% && pause && exit /b 1)

echo.
echo ============================================================
echo  Quant Scheduler -- Windows Task Setup
echo ============================================================
echo  Morning:   %MORNING_TIME% local  (6:00 AM Saskatchewan)
echo  Afternoon: %AFTERNOON_TIME% local  (4:00 PM Saskatchewan)
echo  StartWhenAvailable: YES (catches up after sleep/restart)
echo ============================================================
echo.

:: ── Use PowerShell for StartWhenAvailable support ─────────────────────────────
:: schtasks.exe can't set StartWhenAvailable directly; PowerShell can.

set "PS_SCRIPT=%TEMP%\register_quant_tasks.ps1"

(
echo $python = "%PYTHON%"
echo $script = '"%SCHEDULER%" --once'
echo $workdir = "%PROJECTS_DIR%"
echo.
echo function Register-QuantTask {
echo     param($Name, $Time)
echo     $action = New-ScheduledTaskAction -Execute $python -Argument $script -WorkingDirectory $workdir
echo     $trigger = New-ScheduledTaskTrigger -Daily -At $Time
echo     $settings = New-ScheduledTaskSettingsSet `
echo         -StartWhenAvailable `
echo         -RunOnlyIfNetworkAvailable `
echo         -ExecutionTimeLimit ^(New-TimeSpan -Hours 2^) `
echo         -MultipleInstances IgnoreNew `
echo         -WakeToRun $false
echo     $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Highest -LogonType InteractiveToken
echo     Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue
echo     Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force ^| Out-Null
echo     Write-Host "[OK] Registered: $Name at $Time ^(StartWhenAvailable=true^)"
echo }
echo.
echo Register-QuantTask "QuantScheduler_Morning"   "%MORNING_TIME%"
echo Register-QuantTask "QuantScheduler_Afternoon" "%AFTERNOON_TIME%"
echo.
echo # Startup daemon task (persistent process, also handles catch-up)
echo $daemon_action = New-ScheduledTaskAction -Execute $python -Argument '"%SCHEDULER%"' -WorkingDirectory $workdir
echo $daemon_trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
echo $daemon_settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ^(New-TimeSpan -Days 365^) -MultipleInstances IgnoreNew
echo $daemon_principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Highest -LogonType InteractiveToken
echo Unregister-ScheduledTask -TaskName "QuantScheduler_Daemon" -Confirm:$false -ErrorAction SilentlyContinue
echo Register-ScheduledTask -TaskName "QuantScheduler_Daemon" -Action $daemon_action -Trigger $daemon_trigger -Settings $daemon_settings -Principal $daemon_principal -Force ^| Out-Null
echo Write-Host "[OK] Registered: QuantScheduler_Daemon ^(starts on login, daemon mode^)"
echo.
echo Write-Host ""
echo Write-Host "All tasks registered. Summary:"
echo Get-ScheduledTask -TaskName "QuantScheduler_*" ^| Select-Object TaskName, State ^| Format-Table -AutoSize
) > "%PS_SCRIPT%"

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS_SCRIPT%"

if errorlevel 1 (
    echo.
    echo [WARN] PowerShell registration failed. Falling back to schtasks...
    echo       Note: StartWhenAvailable will NOT be set with this method.
    echo.
    schtasks /create /tn "QuantScheduler_Morning" /tr "\"%PYTHON%\" \"%SCHEDULER%\" --once" /sc DAILY /st %MORNING_TIME% /ru "%USERNAME%" /rl HIGHEST /f
    schtasks /create /tn "QuantScheduler_Afternoon" /tr "\"%PYTHON%\" \"%SCHEDULER%\" --once" /sc DAILY /st %AFTERNOON_TIME% /ru "%USERNAME%" /rl HIGHEST /f
    schtasks /create /tn "QuantScheduler_Daemon" /tr "\"%PYTHON%\" \"%SCHEDULER%\"" /sc ONLOGON /ru "%USERNAME%" /rl HIGHEST /f
)

del "%PS_SCRIPT%" >nul 2>&1

echo.
echo ============================================================
echo  DONE. Three tasks created:
echo.
echo   QuantScheduler_Morning    -- %MORNING_TIME% daily  (6 AM SK)
echo   QuantScheduler_Afternoon  -- %AFTERNOON_TIME% daily  (4 PM SK)
echo   QuantScheduler_Daemon     -- on login (persistent daemon)
echo.
echo  All tasks use StartWhenAvailable=true:
echo    If the laptop was asleep at 6 AM or 4 PM, the task runs
echo    automatically when the computer wakes up.
echo.
echo  Test now (manual run):
echo    python "%SCHEDULER%" --once
echo    python "%SCHEDULER%" --test
echo ============================================================
pause
