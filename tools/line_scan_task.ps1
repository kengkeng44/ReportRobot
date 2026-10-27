# 註冊 Windows 排程「LINE 轉帳掃描」：跑 line_scan_local.ps1（本機讀圖，不用伺服器的 key）。
# 要操作 LINE 視窗，所以只能「使用者登入時執行」，而且桌面要解鎖。
# 原本只掛凌晨 3 點，但那時幾乎都鎖著、天天跳過；改成登入、解鎖、晚上 9 點三個觸發，
# line_scan_local.ps1 自己保證一天只成功跑一次。
# 用法：powershell -ExecutionPolicy Bypass -File tools\line_scan_task.ps1
$script = Join-Path $PSScriptRoot 'line_scan_local.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`"" `
    -WorkingDirectory $PSScriptRoot

$logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$evening = New-ScheduledTaskTrigger -Daily -At 21:00
# 解鎖觸發 New-ScheduledTaskTrigger 沒有，要用 CIM 類別；StateChange 8 = SessionUnlock
$cls = Get-CimClass -Namespace ROOT\Microsoft\Windows\TaskScheduler -ClassName MSFT_TaskSessionStateChangeTrigger
$unlock = New-CimInstance -CimClass $cls -ClientOnly -Property @{
    StateChange = 8; UserId = "$env:USERDOMAIN\$env:USERNAME"; Enabled = $true }
# 解鎖後等一下再跑：LINE 跟桌面還在重畫
$unlock.Delay = 'PT1M'

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive
Register-ScheduledTask -TaskName 'LINE 轉帳掃描' -Action $action -Trigger @($logon, $unlock, $evening) `
    -Settings $settings -Principal $principal -Force | Out-Null
Write-Host '已註冊「LINE 轉帳掃描」：登入／解鎖／21:00 觸發，一天成功一次。log 在' `
    (Join-Path $HOME '.line_scan\log.txt')
