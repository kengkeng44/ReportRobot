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


def paste(text):
    """中文用剪貼簿貼上，比逐字送按鍵穩（輸入法開著也不會亂掉）。"""
    from pywinauto import keyboard
    subprocess.run(["clip"], input=text.encode("utf-16-le"), check=True)
    keyboard.send_keys("^v")


def open_chat(win, name):
    """搜尋框打聊天室名稱 → Enter 打開第一個結果。

    LINE 的元件沒有文字標籤，只認得 class（LcTextField），所以抓主視窗第一個輸入框當搜尋框。
    TODO 登入後校正：確認第一個 LcTextField 是聊天列表的搜尋框、Enter 會打開第一筆。
    """
    from pywinauto import keyboard
    win.set_focus()
    keyboard.send_keys("^2")                       # 切到聊天分頁
    time.sleep(0.8)
    box = win.child_window(class_name="LcTextField", control_type="Edit", found_index=0)
    box.click_input()
    keyboard.send_keys("^a{BACKSPACE}")
    paste(name)
    time.sleep(1.5)
    keyboard.send_keys("{ENTER}")
    time.sleep(2.5)                                # 等訊息載入


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
    token = "" if dry_run else (HOME / "admin_token").read_text(encoding="utf-8").strip()
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
            open_chat(win, name)
            png = capture(win)
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
