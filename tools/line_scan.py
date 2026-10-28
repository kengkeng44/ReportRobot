"""本機排程：打開電腦版 LINE，逐一打開設定的聊天室截圖，POST 給 /admin/transfer-ocr 記帳。

為什麼要這樣：個人 LINE 帳號沒有 API，LINE Pay／LINE Bank／銀行官方帳號的轉帳通知
和朋友的轉帳卡片只看得到畫面。截圖交給喵管家既有的截圖辨識（transfer_ocr），
去重靠指紋，所以每晚重掃同一個畫面不會重複記。

設定放在 %USERPROFILE%\\.line_scan\\（不進 repo，repo 是公開的）：
  config.json   {"chats": ["LINE Pay", "國泰世華銀行", ...]}
  admin_token   ReportRobot 的 ADMIN_TOKEN（一行，從 Infisical 複製）
  log.txt       每次跑的結果
  shots\\        --dry-run 時存的截圖

用法：
  python tools\\line_scan.py --dry-run   只截圖存檔，不送出（校正用）
  python tools\\line_scan.py             截圖並送出記帳（伺服器用 Anthropic key 讀圖）
  python tools\\line_scan.py --rows rows.json
                                        送出已經讀好的轉帳清單 —— Claude Code 在本機看
                                        --dry-run 的截圖讀出來的，不經過伺服器的 key

限制：電腦鎖定時 Windows 會擋掉按鍵與截圖，只能在解鎖的桌面上跑。
"""

import argparse
import io
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HOME = Path.home() / ".line_scan"
SERVER = "https://chengreportbot-production.up.railway.app"
LAUNCHER = Path.home() / "AppData/Local/LINE/bin/LineLauncher.exe"


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M} {msg}"
    print(line)
    HOME.mkdir(exist_ok=True)
    with open(HOME / "log.txt", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def desktop_unlocked():
    """鎖定畫面是 LogonUI.exe 畫的，解鎖後它就結束。

    2026-10-27 實測不能用的判斷：OpenInputDesktop／SwitchDesktop 鎖定時照樣回成功；
    截圖有時丟 BitBlt 錯誤、有時成功但截到鎖定畫面。
    """
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq LogonUI.exe", "/NH"],
                         capture_output=True, text=True, errors="replace").stdout
    return "LogonUI.exe" not in out


def line_windows():
    from pywinauto import Desktop
    return [w for w in Desktop(backend="uia").windows() if w.window_text() == "LINE"]


def main_window(timeout=90):
    """等 LINE 主視窗。停在登入頁回 None（要人登入、勾自動登入）。"""
    if not line_windows():
        subprocess.Popen([str(LAUNCHER)])
    end = time.time() + timeout
    while time.time() < end:
        wins = line_windows()
        main = [w for w in wins if w.class_name() != "LoginWindow"]
        if main:
            return main[0]
        time.sleep(3)
    return None


def bring_front(win):
    """把 LINE 拉到最前面；拉不上來就丟例外，絕不把字打進別的視窗。

    set_focus 會被 Windows 防搶焦點擋掉（2026-10-27 實測：點擊落在別的程式上），
    先縮小再還原才拉得上來。
    """
    import ctypes
    for _ in range(3):
        if ctypes.windll.user32.GetForegroundWindow() == win.handle:
            return
        win.minimize()
        time.sleep(0.5)
        win.restore()
        win.set_focus()
        time.sleep(0.8)
    raise RuntimeError("LINE 拉不到最前面，這次不打字")


def open_chat(win, name):
    """搜尋框打聊天室名稱 → 點兩下第一筆聊天結果，回傳跳出來的 ChatWindow。

    2026-10-27 校正（精簡模式的 LINE）：
    - 主視窗只有一個 LcTextField，就是「搜尋聊天和訊息」
    - Enter、方向鍵都打不開結果，要用滑鼠；單擊只會選取，要點兩下
    - 結果是一串 ListItem：區段標題（「聊天 1」）高約 51，聊天室列高約 108 → 挑第一個夠高的
    - 聊天室開在另一個 ChatWindow，標題＝聊天室名稱
    """
    from pywinauto import Desktop, mouse

    def chat_windows():
        return {w.handle: w for w in Desktop(backend="uia").windows()
                if w.class_name() == "ChatWindow" and w.process_id() == win.process_id()}

    for old in chat_windows().values():            # 先關掉開著的，免得抓錯視窗
        old.close()
    bring_front(win)
    box = [e for e in win.descendants(control_type="Edit") if e.class_name() == "LcTextField"][0]
    box.iface_value.SetValue(name)                 # 直接設值：模擬按鍵會被中文輸入法吃掉多組字
    time.sleep(1.5)
    chats = []
    for _ in range(2):                             # 搜尋結果還在重畫時點會落空，重抓再點一次
        rows = [d for d in win.descendants(control_type="ListItem") if d.rectangle().height() > 80]
        if not rows:
            raise RuntimeError("搜尋不到這個聊天室")
        rows[0].double_click_input()
        for _ in range(8):
            time.sleep(1)
            chats = list(chat_windows().values())
            if chats:
                break
        if chats:
            break
    time.sleep(2)                                  # 等訊息載入
    if len(chats) != 1:
        raise RuntimeError(f"聊天視窗應該跳出 1 個，實際 {len(chats)} 個")
    chat = chats[0]
    bring_front(chat)                              # 主視窗會擋在前面
    chat.maximize()                                # 預設視窗只放得下一張卡片
    time.sleep(1)
    r = chat.rectangle()
    for _ in range(5):                             # 打開時停在「以下為尚未閱讀的訊息」，捲到最新
        mouse.scroll(coords=((r.left + r.right) // 2, (r.top + r.bottom) // 2), wheel_dist=-20)
        time.sleep(0.3)
    time.sleep(1)
    return chat


def capture(win):
    import mss
    from PIL import Image
    r = win.rectangle()
    with mss.MSS() as s:
        raw = s.grab({"left": r.left, "top": r.top, "width": r.width(), "height": r.height()})
    buf = io.BytesIO()
    Image.frombytes("RGB", raw.size, raw.rgb).save(buf, "PNG")
    return buf.getvalue()


def capture_pages(win, pages):
    """從最新往上捲，最多截 pages 張；捲到頂（跟上一張一樣）就停。回傳舊到新。

    聊天室要摘要一整天的事，一個畫面不夠。相鄰兩張會有重疊，讀圖時自己去重。
    """
    from pywinauto import mouse
    shots = [capture(win)]
    r = win.rectangle()
    for _ in range(pages - 1):
        mouse.scroll(coords=((r.left + r.right) // 2, (r.top + r.bottom) // 2), wheel_dist=15)
        time.sleep(1.2)                            # 往上捲會載入舊訊息
        png = capture(win)
        if png == shots[-1]:
            break
        shots.append(png)
    return list(reversed(shots))


def send(png, token):
    import requests
    r = requests.post(f"{SERVER}/admin/transfer-ocr", data=png, timeout=120,
                      headers={"X-Admin-Token": token, "Content-Type": "image/png"})
    r.raise_for_status()
    return r.json().get("text", "")


def send_rows(path, token):
    """rows.json：[{"date","time","direction","amount","counterparty","channel","memo","skip"}, ...]
    跟伺服器讀圖的輸出同格式，去重也同一套，所以重送安全。"""
    import requests
    rows = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    r = requests.post(f"{SERVER}/admin/transfer-rows", json={"rows": rows}, timeout=120,
                      headers={"X-Admin-Token": token})
    r.raise_for_status()
    return r.json().get("text", "")


def send_notes(path, token):
    """notes.json：[{"date","summary"}] → 每日紀錄「今天做了什麼」（伺服器只填空白的那幾天）。"""
    import requests
    notes = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    r = requests.post(f"{SERVER}/admin/daily-notes", json={"notes": notes}, timeout=120,
                      headers={"X-Admin-Token": token})
    r.raise_for_status()
    return r.json().get("result", {})


def read_prompt():
    """給 claude -p 的讀圖指示：讀 shots 資料夾裡每張截圖，把轉帳／錢包付款寫成 rows.json。

    規則直接拿伺服器讀圖用的 transfer_ocr._PROMPT，兩邊永遠同一套。
    帳戶主人的名字放 config.json 的 "owner"（repo 是公開的，名字不進程式碼）。
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import transfer_ocr
    from tz_utils import today_tpe

    cfg = json.loads((HOME / "config.json").read_text(encoding="utf-8"))
    shots = sorted((HOME / "shots").glob("*.png"))
    rules = transfer_ocr._PROMPT.format(today=today_tpe().isoformat(),
                                        owner=cfg.get("owner") or "（未知）")
    nl = chr(10)          # 跟 couple_ledger 一樣用 chr(10)：字面量 \n 在編輯流程裡被轉壞過
    files = nl.join(f"- shots/{p.name}" for p in shots)
    prompt = (f"讀下面每一張電腦版 LINE 聊天室截圖（用 Read 工具開圖）：{nl}{files}{nl}{nl}"
              f"【一】轉帳：每張都照這個規則讀：{nl}{rules}{nl}{nl}"
              "全部合併成一個 JSON 陣列，用 Write 工具寫到 rows.json（覆蓋）。一筆都沒有就寫 []。")
    diary = [e["name"] for e in cfg.get("chats") or [] if isinstance(e, dict) and e.get("diary")]
    if diary:
        today = today_tpe()
        prompt += (
            f"{nl}{nl}【二】每日摘要：只看「{'、'.join(diary)}」的截圖（檔名有聊天室名稱，"
            "同一個聊天室多張是往上捲截的，-1 最舊，相鄰兩張會重疊）。"
            f"今天是 {today.isoformat()}，只寫今天以前、而且從當天第一則到最後一則都在截圖裡的日子"
            "（最早那天如果上面被切掉、看不到當天開頭，就不要寫那天）。"
            f"{nl}每天一句話，從{cfg.get('owner') or '帳戶主人'}的角度寫，用分號分開幾件事，"
            "對方自己的事寫成「小螢……」；有轉帳就寫「收到小螢轉帳 NT$250」「轉給小螢 NT$185」。"
            "不要引用原句、不要寫電話地址帳號。像這樣：" + nl +
            "  「小螢生理期不舒服；晚上搭機捷回板橋找她」" + nl +
            "  「在淡水家，下午去剪頭髮、洗碗、收衣服；收到小螢轉帳 NT$250。小螢跟朋友吃川渝、逛街」" + nl +
            "只是貼圖、互道晚安、沒有具體內容的日子就不要寫。"
            f"{nl}寫成 JSON 陣列 [{{\"date\":\"YYYY-MM-DD\",\"summary\":\"...\"}}]，"
            "用 Write 工具寫到 notes.json（覆蓋）；沒有就寫 []。")
    return prompt + f"{nl}{nl}只寫這兩個檔，不要做其他事。"


def clean_shots(before):
    """送出成功後刪掉截圖：圖上有金額和對象，不要一直留在硬碟。只刪 rows.json 之前截的。"""
    n = 0
    for p in (HOME / "shots").glob("*.png"):
        if p.stat().st_mtime <= before:
            p.unlink()
            n += 1
    return n


def run(dry_run):
    cfg = json.loads((HOME / "config.json").read_text(encoding="utf-8"))
    chats = cfg.get("chats") or []
    token = "" if dry_run else (HOME / "admin_token").read_text(encoding="utf-8-sig").strip()
    if not desktop_unlocked():
        log("❌ 電腦鎖定中，沒辦法操作 LINE，這次跳過")
        return 3
    win = main_window()
    if win is None:
        log("❌ LINE 停在登入畫面（或沒開起來），要手動登入並勾「自動登入」")
        return 2
    failed = 0
    for entry in chats:
        # 字串＝只截最新一個畫面；{"name", "pages"}＝往上捲多截幾張（要寫每日摘要的聊天室）
        name = entry if isinstance(entry, str) else entry["name"]
        pages = 1 if isinstance(entry, str) else int(entry.get("pages") or 1)
        try:
            try:
                chat = open_chat(win, name)
            except Exception:                      # 偶發開不起來（RuntimeError／COMError 都遇過），隔幾秒再試一次
                time.sleep(5)
                bring_front(win)
                chat = open_chat(win, name)
            pngs = capture_pages(chat, pages)
            chat.close()
            for i, png in enumerate(pngs, 1):
                tag = f"{name}-{i}" if len(pngs) > 1 else name
                if dry_run:
                    (HOME / "shots").mkdir(exist_ok=True)
                    path = HOME / "shots" / f"{datetime.now():%m%d-%H%M}-{tag}.png"
                    path.write_bytes(png)
                    log(f"📷 {tag} → {path}")
                else:
                    log(f"✅ {tag}：{send(png, token).replace(chr(10), ' / ')}")
        except Exception as e:
            failed += 1
            log(f"❌ {name}：{type(e).__name__} {e}")
    win.minimize()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # 排程主控台是 CP950，印 emoji 會炸
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只截圖存檔，不送出")
    ap.add_argument("--rows", help="送出已經讀好的轉帳清單 JSON 檔，不截圖")
    ap.add_argument("--clean", action="store_true", help="配 --rows：送出成功就刪掉讀過的截圖")
    ap.add_argument("--notes", help="送出每日摘要 notes.json，填進每日紀錄（只填空白的）")
    ap.add_argument("--prompt", action="store_true", help="把給 claude -p 的讀圖指示寫到 prompt.md")
    args = ap.parse_args()
    if args.prompt:
        # 寫檔而不是印出來：Windows PowerShell 5.1 把多行、含雙引號的字串當命令列參數
        # 傳給 claude 時會把引號弄壞（2026-10-27 實測：讀出來的格式整個不對）
        (HOME / "prompt.md").write_text(read_prompt(), encoding="utf-8")
        sys.exit(0)
    if args.notes:
        token = (HOME / "admin_token").read_text(encoding="utf-8-sig").strip()
        res = send_notes(args.notes, token)
        label = {"written": "已填", "kept": "原本就有、不蓋", "error": "失敗", "today_or_future": "今天還沒過完"}
        log("📔 每日摘要：" + ("、".join(f"{d} {label.get(v, v)}" for d, v in sorted(res.items())) or "沒有"))
        sys.exit(0)
    if args.rows:
        token = (HOME / "admin_token").read_text(encoding="utf-8-sig").strip()
        text = send_rows(args.rows, token)
        log(f"✅ 本機讀圖：{text.replace(chr(10), ' / ')}")
        if args.clean:
            log(f"🗑️ 刪掉 {clean_shots(Path(args.rows).stat().st_mtime)} 張截圖")
        sys.exit(0)
    sys.exit(run(args.dry_run))
