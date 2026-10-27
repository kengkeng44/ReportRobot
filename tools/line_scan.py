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
  python tools\\line_scan.py             截圖並送出記帳

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


def send(png, token):
    import requests
    r = requests.post(f"{SERVER}/admin/transfer-ocr", data=png, timeout=120,
                      headers={"X-Admin-Token": token, "Content-Type": "image/png"})
    r.raise_for_status()
    return r.json().get("text", "")


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
    for name in chats:
        try:
            chat = open_chat(win, name)
            png = capture(chat)
            chat.close()
            if dry_run:
                (HOME / "shots").mkdir(exist_ok=True)
                path = HOME / "shots" / f"{datetime.now():%m%d-%H%M}-{name}.png"
                path.write_bytes(png)
                log(f"📷 {name} → {path}")
            else:
                log(f"✅ {name}：{send(png, token).replace(chr(10), ' / ')}")
        except Exception as e:
            failed += 1
            log(f"❌ {name}：{type(e).__name__} {e}")
    win.minimize()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # 排程主控台是 CP950，印 emoji 會炸
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只截圖存檔，不送出")
    sys.exit(run(ap.parse_args().dry_run))
