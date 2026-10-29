# LINE 掃描（本機讀圖版）：
#   截圖（LINE錢包）＋匯出聊天記錄（小螢）→ claude -p 在本機讀 → 送出轉帳、每日摘要 → 刪暫存。
# 不經過伺服器的 Anthropic key，用的是 Claude 訂閱額度。
#
# 只在凌晨 4–6 點跑；一天只成功跑一次：成功後寫 last_ok（今天的日期），同一天再觸發就直接結束。
# 電腦鎖定、LINE 沒登入時 line_scan.py 會記 log 並回非 0，這次不算成功，下次觸發再試。
$ErrorActionPreference = 'Stop'
# 只准凌晨 4:00–5:59 跑，其他時間直接結束（2026-10-29 使用者要求：會搶滑鼠鍵盤）
$hour = (Get-Date).Hour
if ($hour -lt 4 -or $hour -ge 6) { exit 0 }
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

$diary_dir = Join-Path $home_dir 'diary'
$export_rows = Join-Path $home_dir 'rows_export.json'
Remove-Item $export_rows -ErrorAction SilentlyContinue
Remove-Item $diary_dir -Recurse -ErrorAction SilentlyContinue

& $python $scan --dry-run
# 2＝LINE 沒登入、3＝電腦鎖定：整個沒做到，下次觸發再試。
# 1＝有聊天室開不起來（log 裡有寫是哪個）：做到的照樣處理，不然一個壞掉全部卡住
if ($LASTEXITCODE -ge 2) { exit $LASTEXITCODE }

# 匯出檔解析出的轉帳是純程式讀的，不用等 Claude，先送
if (Test-Path $export_rows) {
    & $python $scan --rows $export_rows
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

$shots = @(Get-ChildItem (Join-Path $home_dir 'shots') -Filter *.png -ErrorAction SilentlyContinue)
$days = @(Get-ChildItem $diary_dir -Filter *.txt -ErrorAction SilentlyContinue)
if ($shots.Count -gt 0 -or $days.Count -gt 0) {
    $rows = Join-Path $home_dir 'rows.json'
    $notes = Join-Path $home_dir 'notes.json'
    Remove-Item $rows, $notes -ErrorAction SilentlyContinue
    & $python $scan --prompt
    # 在 .line_scan 裡跑：截圖、diary、prompt.md、輸出檔都在工作目錄內，只開讀檔、寫檔兩個工具。
    # 指定 opus：預設模型一次讀多張大圖時把 NT$325 讀成 0（2026-10-28 實測）。
    # 指示放 prompt.md，命令列只傳一句短的 —— 長指示當參數傳會被 PowerShell 弄壞引號
    Push-Location $home_dir
    try { & claude -p '讀 prompt.md，完全照裡面的指示做。' --model opus --allowedTools 'Read' 'Write' | Out-Null }
    finally { Pop-Location }
    if (-not (Test-Path $rows)) { Log '❌ 本機讀圖沒有產出 rows.json，暫存留著下次再讀'; exit 4 }
    # 摘要先送：--rows --clean 會刪截圖，摘要失敗的話要留著下次重讀
    if (Test-Path $notes) {
        & $python $scan --notes $notes
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        Remove-Item $notes
    }
    & $python $scan --rows $rows --clean
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
# 每日對話是完整聊天內容，送完就刪
Remove-Item $diary_dir -Recurse -ErrorAction SilentlyContinue
Remove-Item $export_rows -ErrorAction SilentlyContinue
Set-Content -Path $last_ok -Value $today -Encoding ascii
