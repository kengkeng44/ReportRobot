# 一站式山屋床位（本機真瀏覽器）：抓完送回 ReportRobot。工作排程器 06:50／16:20／23:20 叫這支。
# 比伺服器 07:00／16:30／23:30 早 10 分鐘，伺服器檢查時資料還在 60 分鐘有效期內。
# 用 Continue 不用 Stop：PS 5.1 下 python 寫 stderr 會被包成錯誤，Stop 會直接中斷。
$ErrorActionPreference = 'Continue'
$home_dir = Join-Path $HOME '.hut_monitor'
New-Item -ItemType Directory -Force $home_dir | Out-Null
$python = (Get-Command python).Source
$script = Join-Path $PSScriptRoot 'hut_browser.py'
$env:PYTHONIOENCODING = 'utf-8'
$out = & $python $script 2>&1 | Out-String
$code = $LASTEXITCODE
Add-Content -Path (Join-Path $home_dir 'log.txt') -Encoding utf8 `
    -Value ("{0} exit={1} {2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm'), $code, $out.Trim())
exit $code
