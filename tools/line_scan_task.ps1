# 註冊 Windows 排程「LINE 轉帳掃描」：跑 line_scan_local.ps1（本機讀圖，不用伺服器的 key）。
# 要操作 LINE 視窗，所以只能「使用者登入時執行」，而且桌面要解鎖。
# line_scan_local.ps1 自己保證一天只成功跑一次。
# 用法：powershell -ExecutionPolicy Bypass -File tools\line_scan_task.ps1
$script = Join-Path $PSScriptRoot 'line_scan_local.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`"" `
    -WorkingDirectory $PSScriptRoot

# 只在凌晨 4:00–6:00 跑，其他時間一律不跑（2026-10-29 使用者要求）。
# 拿掉閒置觸發和 21:00 那班：白天、晚上都不會再自己啟動。
# 4:00 起每 30 分鐘一班（4:00／4:30／5:00／5:30），螢幕鎖著或 LINE 沒登入就下一班再試；
# 每班最多 20 分鐘，5:30 那班最晚 5:50 被砍。WakeToRun 會把睡眠中的電腦叫醒。
# 不設 StartWhenAvailable：4 點電腦關著就當天不跑，不會開機後補跑。
# line_scan_local.ps1 自己也會檢查時間，不在 4–6 點直接結束（手動點「執行」也一樣）。
$night = New-ScheduledTaskTrigger -Once -At 4:00 `
    -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Minutes 105)
$daily = New-ScheduledTaskTrigger -Daily -At 4:00
$daily.Repetition = $night.Repetition

$settings = New-ScheduledTaskSettingsSet -WakeToRun -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive
Register-ScheduledTask -TaskName 'LINE 轉帳掃描' -Action $action -Trigger $daily `
    -Settings $settings -Principal $principal -Force | Out-Null
Write-Host '已註冊「LINE 轉帳掃描」：只在凌晨 4:00–6:00 跑（每 30 分鐘試一次），一天成功一次。log 在' `
    (Join-Path $HOME '.line_scan\log.txt')
