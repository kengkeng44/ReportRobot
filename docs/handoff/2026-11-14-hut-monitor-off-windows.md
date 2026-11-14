# 交接：在這台 Windows 關掉山屋監控

**給 Windows 上的 Claude：** 先把你手上的工作做完，再做這份。這份只有下面三步，不要改其他東西。

## 背景（只是讓你知道，不用處理）
使用者暫時不用山屋監控了。伺服器那邊已經改好（ReportRobot 分支 `claude/laughing-dirac-7e0qgv`，`HUT_PAUSED` 預設是暫停），不會再寄信。
這台電腦上還有一個 Windows 工作排程「山屋一站式」，每天 06:50、16:20、23:20 會開瀏覽器去搜尋。這份交接就是要把它刪掉。

## 步驟（PowerShell）

1. 如果排程正在跑，先停掉：
   ```powershell
   Stop-ScheduledTask -TaskName '山屋一站式' -ErrorAction SilentlyContinue
   ```
2. 刪除排程：
   ```powershell
   Unregister-ScheduledTask -TaskName '山屋一站式' -Confirm:$false
   ```
3. 確認已經刪掉了。下面這行要輸出 `已刪除`：
   ```powershell
   if (Get-ScheduledTask -TaskName '山屋一站式' -ErrorAction SilentlyContinue) { '還在' } else { '已刪除' }
   ```

如果第 2 步說找不到這個排程，表示本來就沒有，直接跑第 3 步確認就好。

## 不要做的事
- 不要刪 `tools/` 裡的任何檔案，也不要刪 `~/.hut_monitor` 資料夾。以後恢復功能還用得到。
- 不要動其他工作排程，例如 LINE 掃描那個。
- 不要 commit、push、改程式碼，也不要開 PR。

## 做完後怎麼回報
做完只回這一句，前後都不要加任何文字，也不要再提起或接回你原本在做的其他工作：

> 山屋監控功能已關閉，專案已完成。

如果第 3 步輸出的是 `還在`，就只回這一句：

> 山屋監控排程刪除失敗，還在。
