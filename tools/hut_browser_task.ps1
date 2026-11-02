# 註冊 Windows 排程「山屋一站式」：每天 06:50／16:20／23:20 跑 hut_browser_local.ps1。
# headless 瀏覽器，不搶滑鼠鍵盤。不設 WakeToRun：電腦睡著就跳過，伺服器信裡會標「一站式本次沒查到」。
# 用法：powershell -ExecutionPolicy Bypass -File tools\hut_browser_task.ps1
$script = Join-Path $PSScriptRoot 'hut_browser_local.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`"" `
    -WorkingDirectory $PSScriptRoot
$triggers = @('6:50', '16:20', '23:20') | ForEach-Object { New-ScheduledTaskTrigger -Daily -At $_ }
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive
Register-ScheduledTask -TaskName '山屋一站式' -Action $action -Trigger $triggers `
    -Settings $settings -Principal $principal -Force | Out-Null
Write-Host '已註冊「山屋一站式」：每天 06:50／16:20／23:20。log 在' `
    (Join-Path $HOME '.hut_monitor\log.txt')
