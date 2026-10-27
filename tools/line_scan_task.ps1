# 註冊 Windows 排程：每天 03:00 跑 line_scan.py（會叫醒睡眠中的電腦）。
# 要操作 LINE 視窗，所以只能「使用者登入時執行」；電腦鎖定時程式會記一行 log 後跳過。
# 用法：powershell -ExecutionPolicy Bypass -File tools\line_scan_task.ps1
$python = (Get-Command python).Source
$script = Join-Path $PSScriptRoot 'line_scan.py'
$action = New-ScheduledTaskAction -Execute $python -Argument "`"$script`"" -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -Daily -At 3:00
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 15)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive
Register-ScheduledTask -TaskName 'LINE 轉帳掃描' -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null
Write-Host '已註冊「LINE 轉帳掃描」：每天 03:00，log 在' (Join-Path $HOME '.line_scan\log.txt')
