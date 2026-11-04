# Remote Control 守護：Wi-Fi 斷了就重連，claude remote-control 掉了就重開，每件事都記 log。
# log：~/.claude_rc/watchdog.log（跑幾天看「網路斷了」「remote-control 結束了」出現在什麼時間）
#
# 用法（第一次先手動在 repo 資料夾跑一次 claude remote-control，把信任／登入的問題答完）：
#   powershell -ExecutionPolicy Bypass -File tools\rc_watchdog.ps1
#   powershell -ExecutionPolicy Bypass -File tools\rc_watchdog.ps1 -WifiOnly    # 用桌面版 Remote Control 時，只顧 Wi-Fi
#   powershell -ExecutionPolicy Bypass -File tools\rc_watchdog.ps1 -Register    # 註冊成「登入就自動跑」
param(
    [string]$Folder = (Split-Path $PSScriptRoot -Parent),
    [string]$Ssid = '',
    [switch]$WifiOnly,
    [switch]$Register
)
$ErrorActionPreference = 'Continue'

if ($Register) {
    $args_ = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File `"$PSCommandPath`""
    if ($WifiOnly) { $args_ += ' -WifiOnly' }
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $args_ -WorkingDirectory $Folder
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive
    Register-ScheduledTask -TaskName 'Claude Remote Control 守護' -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal -Force | Out-Null
    Write-Host '已註冊「Claude Remote Control 守護」：每次登入 Windows 自動開始。'
    exit 0
}

$logDir = Join-Path $HOME '.claude_rc'
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir 'watchdog.log'
function Log([string]$msg) {
    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Add-Content -Path $log -Encoding utf8 -Value $line
    Write-Host $line
}
function Test-Online {
    foreach ($h in @('1.1.1.1', '8.8.8.8')) {
        if (Test-Connection -ComputerName $h -Count 1 -Quiet -ErrorAction SilentlyContinue) { return $true }
    }
    return $false
}

if (-not $Ssid) {
    # 目前連著的 Wi-Fi 名稱（BSSID 那行不會被 ^\s*SSID 吃到）
    $m = netsh wlan show interfaces | Select-String '^\s*SSID\s*:\s*(.+)$' | Select-Object -First 1
    if ($m) { $Ssid = $m.Matches[0].Groups[1].Value.Trim() }
}

$claude = $null
if (-not $WifiOnly) {
    $cmd = Get-Command claude -ErrorAction SilentlyContinue
    if (-not $cmd) { Log '找不到 claude 指令，只顧 Wi-Fi'; $WifiOnly = $true } else { $claude = $cmd.Source }
}

Log ("啟動：資料夾 {0}，Wi-Fi「{1}」，{2}" -f $Folder, $Ssid, $(if ($WifiOnly) { '只顧 Wi-Fi' } else { '顧 Wi-Fi＋remote-control' }))
$proc = $null
$startedAt = Get-Date
$wasOnline = $true
$downSince = $null

while ($true) {
    if (-not (Test-Online)) {
        if ($wasOnline) { Log '網路斷了'; $downSince = Get-Date }
        $wasOnline = $false
        if ($Ssid) { netsh wlan connect name="$Ssid" | Out-Null }
        Start-Sleep -Seconds 15
        continue
    }
    if (-not $wasOnline) {
        Log ('網路恢復（斷了 {0:N0} 秒）' -f ((Get-Date) - $downSince).TotalSeconds)
        $wasOnline = $true
    }

    if (-not $WifiOnly -and (-not $proc -or $proc.HasExited)) {
        if ($proc) {
            $ran = ((Get-Date) - $startedAt).TotalSeconds
            Log ('remote-control 結束了（exit {0}，跑了 {1:N0} 秒），重開' -f $proc.ExitCode, $ran)
            if ($ran -lt 60) {
                Log '一分鐘內就結束：可能要重新登入或回答信任問題，先等 5 分鐘，避免一直重開'
                Start-Sleep -Seconds 300
            }
        }
        $proc = Start-Process -FilePath $claude -ArgumentList 'remote-control' -WorkingDirectory $Folder `
            -PassThru -WindowStyle Minimized
        $startedAt = Get-Date
        Log ('已啟動 remote-control（PID {0}）' -f $proc.Id)
    }
    Start-Sleep -Seconds 30
}
