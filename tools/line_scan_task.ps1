# 註冊 Windows 排程「LINE 轉帳掃描」：跑 line_scan_local.ps1（本機讀圖，不用伺服器的 key）。
# 要操作 LINE 視窗，所以只能「使用者登入時執行」，而且桌面要解鎖。
# 原本只掛凌晨 3 點，但那時幾乎都鎖著、天天跳過。line_scan_local.ps1 自己保證一天只成功跑一次。
# 用法：powershell -ExecutionPolicy Bypass -File tools\line_scan_task.ps1
$script = Join-Path $PSScriptRoot 'line_scan_local.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`"" `
    -WorkingDirectory $PSScriptRoot

# 只在你離開電腦時跑：操作 LINE 會搶滑鼠鍵盤，你在用電腦時跑很干擾（2026-10-28 使用者回饋）。
# 閒置觸發＋RunOnlyIfIdle：沒碰滑鼠鍵盤 10 分鐘才開始，你一回來就停（StopOnIdleEnd），
# 截到一半被停也沒關係 —— 去重靠指紋，last_ok 沒寫就是下次再試。
# 21:00 那班也套同一個設定：到時間先等你離開（最多等 3 小時）。
$cls = Get-CimClass -Namespace ROOT\Microsoft\Windows\TaskScheduler -ClassName MSFT_TaskIdleTrigger
$idle = New-CimInstance -CimClass $cls -ClientOnly -Property @{ Enabled = $true }
$evening = New-ScheduledTaskTrigger -Daily -At 21:00

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -RunOnlyIfIdle -IdleDuration (New-TimeSpan -Minutes 10) -IdleWaitTimeout (New-TimeSpan -Hours 3) `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
$settings.IdleSettings.StopOnIdleEnd = $true
$settings.IdleSettings.RestartOnIdle = $true
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive
Register-ScheduledTask -TaskName 'LINE 轉帳掃描' -Action $action -Trigger @($idle, $evening) `
    -Settings $settings -Principal $principal -Force | Out-Null
Write-Host '已註冊「LINE 轉帳掃描」：閒置 10 分鐘才跑、你回來就停，一天成功一次。log 在' `
    (Join-Path $HOME '.line_scan\log.txt')
