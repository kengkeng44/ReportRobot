# LINE 轉帳掃描（本機讀圖版）：截圖 → claude -p 在本機看圖 → 送出轉帳清單 → 刪截圖。
# 不經過伺服器的 Anthropic key，用的是 Claude 訂閱額度。
#
# 一天只成功跑一次：成功後寫 last_ok（今天的日期），同一天再觸發就直接結束。
# 所以排程可以掛很多個觸發（登入、解鎖、晚上 9 點），哪個先碰到解鎖的桌面就哪個跑。
# 電腦鎖定、LINE 沒登入時 line_scan.py 會記 log 並回非 0，這次不算成功，下次觸發再試。
$ErrorActionPreference = 'Stop'
$home_dir = Join-Path $HOME '.line_scan'
$last_ok = Join-Path $home_dir 'last_ok'
$today = Get-Date -Format 'yyyy-MM-dd'
if ((Test-Path $last_ok) -and ((Get-Content $last_ok -Raw).Trim() -eq $today)) { exit 0 }

$python = (Get-Command python).Source
$scan = Join-Path $PSScriptRoot 'line_scan.py'
function Log($msg) {
    Add-Content -Path (Join-Path $home_dir 'log.txt') -Encoding utf8 `
        -Value ("{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm'), $msg)
}

& $python $scan --dry-run
# 2＝LINE 沒登入、3＝電腦鎖定：整個沒截到，下次觸發再試。
# 1＝有聊天室開不起來（log 裡有寫是哪個）：截到的照樣讀，不然一個壞掉全部卡住
if ($LASTEXITCODE -ge 2) { exit $LASTEXITCODE }

$shots = @(Get-ChildItem (Join-Path $home_dir 'shots') -Filter *.png -ErrorAction SilentlyContinue)
if ($shots.Count -gt 0) {
    $rows = Join-Path $home_dir 'rows.json'
    Remove-Item $rows, (Join-Path $home_dir 'notes.json') -ErrorAction SilentlyContinue
    & $python $scan --prompt
    # 在 .line_scan 裡跑：截圖、prompt.md、rows.json 都在工作目錄內，只開讀圖、寫檔兩個工具。
    # 指示放 prompt.md，命令列只傳一句短的 —— 長指示當參數傳會被 PowerShell 弄壞引號
    Push-Location $home_dir
    try { & claude -p '讀 prompt.md，完全照裡面的指示做。' --allowedTools 'Read' 'Write' | Out-Null }
    finally { Pop-Location }
    if (-not (Test-Path $rows)) { Log '❌ 本機讀圖沒有產出 rows.json，截圖留著下次再讀'; exit 4 }
    # 摘要先送：--rows --clean 會刪截圖，摘要失敗的話截圖要留著下次重讀
    $notes = Join-Path $home_dir 'notes.json'
    if (Test-Path $notes) {
        & $python $scan --notes $notes
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        Remove-Item $notes
    }
    & $python $scan --rows $rows --clean
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
Set-Content -Path $last_ok -Value $today -Encoding ascii
