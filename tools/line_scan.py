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
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
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
    """給 claude -p 的指示：截圖讀轉帳寫 rows.json；diary 資料夾的每日對話寫摘要 notes.json。

    轉帳規則直接拿伺服器讀圖用的 transfer_ocr._PROMPT，兩邊永遠同一套。
    帳戶主人的名字放 config.json 的 "owner"（repo 是公開的，名字不進程式碼）。
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import transfer_ocr
    from tz_utils import today_tpe

    cfg = json.loads((HOME / "config.json").read_text(encoding="utf-8"))
    owner = cfg.get("owner") or "帳戶主人"
    nl = chr(10)          # 跟 couple_ledger 一樣用 chr(10)：字面量 \n 在編輯流程裡被轉壞過
    shots = sorted((HOME / "shots").glob("*.png"))
    diary = sorted((HOME / "diary").glob("*.txt"))
    parts = []
    if shots:
        rules = transfer_ocr._PROMPT.format(today=today_tpe().isoformat(), owner=owner)
        files = nl.join(f"- shots/{p.name}" for p in shots)
        parts.append(f"【一】讀下面每一張電腦版 LINE 聊天室截圖（用 Read 工具開圖）：{nl}{files}{nl}{nl}"
                     f"每張都照這個規則讀：{nl}{rules}{nl}{nl}"
                     "全部合併成一個 JSON 陣列，用 Write 工具寫到 rows.json（覆蓋）。一筆都沒有就寫 []。")
    else:
        parts.append("【一】這次沒有截圖：用 Write 工具把 [] 寫到 rows.json。")
    if diary:
        alias = next((e.get("alias") or e["name"] for e in cfg.get("chats") or []
                      if isinstance(e, dict) and e.get("diary")), "對方")
        files = nl.join(f"- diary/{p.name}" for p in diary)
        parts.append(
            f"【二】每日摘要：下面每個檔是{owner}和{alias}某一天完整的 LINE 對話（檔名就是日期，"
            f"每行「時間 名字 內容」）：{nl}{files}{nl}{nl}"
            f"每天寫一句話，從{owner}的角度寫，用分號分開幾件事，{alias}自己的事寫成「{alias}……」；"
            f"有轉帳就寫「收到{alias}轉帳 NT$250」「轉給{alias} NT$185」。"
            "不要引用原句、不要寫電話地址帳號。像這樣：" + nl +
            f"  「{alias}生理期不舒服；晚上搭機捷回板橋找她」" + nl +
            f"  「在淡水家，下午去剪頭髮、洗碗、收衣服；收到{alias}轉帳 NT$250。{alias}跟朋友吃川渝、逛街」" + nl +
            "只有貼圖、互道晚安、沒有具體內容的日子就不要寫。"
            f"{nl}寫成 JSON 陣列 [{{\"date\":\"YYYY-MM-DD\",\"summary\":\"...\"}}]，"
            "用 Write 工具寫到 notes.json（覆蓋）；沒有就寫 []。")
    return (nl + nl).join(parts) + f"{nl}{nl}只寫這兩個檔，不要做其他事。"


def _dialogs(title):
    """找標題是 title 的標準對話框（#32770）。LINE 的存檔視窗不在 UIA 的頂層清單裡，只能列舉。"""
    import ctypes
    import ctypes.wintypes as wt
    u, hits = ctypes.windll.user32, []

    def cb(h, _):
        n = u.GetWindowTextLengthW(h)
        b = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(h, b, n + 1)
        c = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, c, 256)
        if u.IsWindowVisible(h) and c.value == "#32770" and b.value == title:
            hits.append(h)
        return True
    u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(cb), 0)
    return hits


def export_chat(win, name, dest):
    """聊天室右上角 ⋮ →「儲存聊天」→ 另存新檔填 dest → 存檔。回 dest。

    2026-10-28 校正：選單是 LcContextMenu（項目沒有文字，只能用相對座標），
    「儲存聊天」在選單左上角往下 403、往右 80。存檔視窗是一般的「另存新檔」。
    路徑用英文：模擬打字會被中文輸入法吃掉，這裡直接設 Edit 的值。
    """
    from pywinauto import Desktop, mouse
    from pywinauto.controls.hwndwrapper import HwndWrapper

    dest = Path(dest)
    dest.unlink(missing_ok=True)
    try:
        chat = open_chat(win, name)
    except Exception:
        time.sleep(5)
        bring_front(win)
        chat = open_chat(win, name)
    try:
        bring_front(chat)
        r = chat.rectangle()
        mouse.click(coords=(r.right - 32, r.top + 87))          # ⋮
        time.sleep(1.5)
        menus = [w for w in Desktop(backend="uia").windows() if w.class_name() == "LcContextMenu"]
        if not menus:
            raise RuntimeError("聊天室選單沒有跳出來")
        m = menus[0].rectangle()
        mouse.click(coords=(m.left + 80, m.top + 403))          # 儲存聊天
        for _ in range(20):
            time.sleep(0.5)
            dlgs = _dialogs("另存新檔")
            if dlgs:
                break
        else:
            raise RuntimeError("另存新檔視窗沒有跳出來")
        dlg = HwndWrapper(dlgs[0])
        [e for e in dlg.descendants() if e.class_name() == "Edit"][0].set_edit_text(str(dest))
        time.sleep(0.4)
        [b for b in dlg.children() if b.class_name() == "Button" and "存檔" in b.window_text()][0].click()
        # LINE 存完會再抓著檔案一下（2026-10-28 實測 WinError 32）：等大小不再變、而且打得開才算好
        last = -1
        for _ in range(60):
            time.sleep(0.5)
            size = dest.stat().st_size if dest.exists() else 0
            if size and size == last:
                try:
                    with open(dest, "rb+"):
                        return dest
                except OSError:
                    pass
            last = size
        raise RuntimeError("存檔後匯出檔一直沒寫完")
    finally:
        chat.close()


_DAY_RE = re.compile(r"^(\d{4})\.(\d{2})\.(\d{2}) 星期")
_MSG_RE = re.compile(r"^(\d{2}:\d{2}) (\S+) (.*)$")
_TRANSFER_RE = re.compile(r"收到NT\$\s?([\d,]+)的轉帳")


def split_days(text):
    """匯出檔 → {"YYYY-MM-DD": [行, ...]}。日期行是「2026.02.14 星期六」，
    訊息行是「HH:MM 名字 內容」，多行訊息的續行接在前一則後面。"""
    days, cur = {}, None
    for line in text.splitlines():
        m = _DAY_RE.match(line)
        if m:
            cur = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            days.setdefault(cur, [])
        elif cur and line.strip():
            days[cur].append(line)
    return days


def transfers_from_days(days, owner, partner):
    """LINE 轉帳在匯出檔裡是「HH:MM 名字 收到NT$ 300的轉帳。」—— 名字是**收款人**
    （2026-10-28 對照舊每日紀錄確認：「鄭仁和 收到 666」＝收到小螢轉帳）。
    純程式解析、不經過 AI —— 格式固定，用規則比讀圖準。"""
    rows = []
    for day, lines in days.items():
        for line in lines:
            m = _MSG_RE.match(line)
            t = _TRANSFER_RE.search(line) if m else None
            if not t:
                continue
            received = m.group(2) == owner
            rows.append({"date": day, "time": m.group(1), "direction": "in" if received else "out",
                         "amount": int(t.group(1).replace(",", "")),
                         "counterparty": partner, "channel": "LINE Pay", "memo": "", "skip": ""})
    return rows


def clean_shots(before):
    """送出成功後刪掉截圖：圖上有金額和對象，不要一直留在硬碟。只刪 rows.json 之前截的。"""
    n = 0
    for p in (HOME / "shots").glob("*.png"):
        if p.stat().st_mtime <= before:
            p.unlink()
            n += 1
    return n


def export_entry(win, entry, cfg, token, dry_run):
    """匯出聊天記錄 → 轉帳（程式解析）寫 rows_export.json、最近幾天的對話切成 diary\日期.txt
    給 claude -p 寫摘要。完整匯出檔一讀完就刪：裡面是幾個月的完整對話。"""
    name = entry["name"]
    alias = entry.get("alias") or name
    owner = cfg.get("owner") or ""
    dest = HOME / "export" / "chat.txt"
    dest.parent.mkdir(exist_ok=True)
    export_chat(win, name, dest)
    try:
        days = split_days(dest.read_text(encoding="utf-8-sig"))
    finally:
        for _ in range(10):                       # 完整對話不能留在硬碟上：刪不掉就多試幾次
            try:
                dest.unlink(missing_ok=True)
                break
            except OSError:
                time.sleep(1)
    today = datetime.now().date()
    recent = lambda n: {(today - timedelta(days=i)).isoformat() for i in range(1, n + 1)}
    # 轉帳看最近 7 天（伺服器有指紋去重，重送安全）；摘要只寫最近 diary_days 天、不含今天
    rows = transfers_from_days({d: v for d, v in days.items() if d in recent(7)}, owner, alias)
    (HOME / "rows_export.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    n_diary = 0
    if entry.get("diary"):
        ddir = HOME / "diary"
        ddir.mkdir(exist_ok=True)
        for d in sorted(recent(int(entry.get("diary_days") or 3))):
            if days.get(d):
                (ddir / f"{d}.txt").write_text(chr(10).join(days[d]), encoding="utf-8")
                n_diary += 1
    log(f"💬 {name}：匯出 {len(days)} 天，最近 7 天轉帳 {len(rows)} 筆，摘要待寫 {n_diary} 天")
    if not dry_run and rows:
        log(f"✅ {name} 轉帳：{send_rows(HOME / 'rows_export.json', token).replace(chr(10), ' / ')}")


def audit(out):
    """對帳用：匯出 config 裡 export 的聊天室，把**全部日子**的轉帳寫到 out（只有日期、金額、方向，
    不留對話內容），拿來比對舊紀錄的轉帳方向。不送伺服器。"""
    cfg = json.loads((HOME / "config.json").read_text(encoding="utf-8"))
    entry = next(e for e in cfg.get("chats") or [] if isinstance(e, dict) and e.get("export"))
    if not desktop_unlocked():
        log("❌ 電腦鎖定中，對帳匯出跳過")
        return 3
    win = main_window()
    if win is None:
        return 2
    dest = HOME / "export" / "chat.txt"
    dest.parent.mkdir(exist_ok=True)
    export_chat(win, entry["name"], dest)
    try:
        days = split_days(dest.read_text(encoding="utf-8-sig"))
    finally:
        for _ in range(10):
            try:
                dest.unlink(missing_ok=True)
                break
            except OSError:
                time.sleep(1)
    win.minimize()
    rows = transfers_from_days(days, cfg.get("owner") or "", entry.get("alias") or entry["name"])
    Path(out).write_text(json.dumps(sorted(rows, key=lambda r: (r["date"], r["time"])), ensure_ascii=False, indent=1),
                         encoding="utf-8")
    log(f"🔎 對帳匯出：{len(days)} 天、轉帳 {len(rows)} 筆 → {out}")
    return 0


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
        # 字串＝只截最新一個畫面；{"name", "pages"}＝往上捲多截幾張；
        # {"name", "export": true}＝改用「儲存聊天」匯出文字檔（轉帳＋每日摘要都從文字讀）
        name = entry if isinstance(entry, str) else entry["name"]
        pages = 1 if isinstance(entry, str) else int(entry.get("pages") or 1)
        if isinstance(entry, dict) and entry.get("export"):
            try:
                export_entry(win, entry, cfg, token, dry_run)
            except Exception as e:
                failed += 1
                log(f"❌ {name}（匯出）：{type(e).__name__} {e}")
            continue
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
    ap.add_argument("--audit", help="對帳：匯出聊天室全部日子的轉帳寫到這個 JSON 檔，不送出")
    args = ap.parse_args()
    if args.audit:
        sys.exit(audit(args.audit))
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
        if not json.loads(Path(args.rows).read_text(encoding="utf-8-sig")) and not args.clean:
            sys.exit(0)                           # 匯出檔這幾天沒有轉帳：不用打一趟伺服器
        token = (HOME / "admin_token").read_text(encoding="utf-8-sig").strip()
        text = send_rows(args.rows, token)
        log(f"✅ 本機讀圖：{text.replace(chr(10), ' / ')}")
        if args.clean:
            log(f"🗑️ 刪掉 {clean_shots(Path(args.rows).stat().st_mtime)} 張截圖")
        sys.exit(0)
    sys.exit(run(args.dry_run))
