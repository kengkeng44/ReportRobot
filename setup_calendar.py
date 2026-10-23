"""一次性設定：授權 Google 日曆 + 建兩個共用日曆 + 分享給另一半。

跑法（要在真的終端機跑，會開瀏覽器，用 renhezheng44 登入）：

    python setup_calendar.py 對方的gmail

會做的事：
1. 只要 calendar scope 的獨立授權 → token_calendar.pickle / token_calendar_b64.txt
   （都已 gitignore；完全不碰財務同步用的 token.pickle）
2. 建「我們倆」「⭐ 特別活動」兩個日曆（已經有同名的就沿用，不重建）
3. 兩個都分享給對方，權限是「可編輯」—— 對方會收到 Google 的邀請信
4. 印出 ⭐ 特別活動 的日曆 ID（不是機密），貼到 Infisical 的 SPECIAL_CALENDAR_ID

對方信箱只從參數讀、不寫進程式碼 —— 這個 repo 是公開的。
"""

import base64
import os
import pickle
import sys

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from couple_calendar import CAL_SCOPES, CAL_TOKEN_FILE

B64_FILE = "token_calendar_b64.txt"
CALENDARS = ["我們倆", "⭐ 特別活動"]


def _ensure_calendar(service, summary):
    for cal in service.calendarList().list().execute().get("items", []):
        if cal.get("summary") == summary and cal.get("accessRole") == "owner":
            return cal["id"], False
    cal = service.calendars().insert(
        body={"summary": summary, "timeZone": "Asia/Taipei"}).execute()
    return cal["id"], True


def _share(service, calendar_id, email):
    rules = service.acl().list(calendarId=calendar_id).execute().get("items", [])
    for r in rules:
        if r.get("scope", {}).get("value", "").lower() == email.lower():
            return False
    service.acl().insert(
        calendarId=calendar_id,
        body={"role": "writer", "scope": {"type": "user", "value": email}},
        sendNotifications=True,
    ).execute()
    return True


def main():
    if len(sys.argv) != 2 or "@" not in sys.argv[1]:
        sys.exit("用法：python setup_calendar.py 對方的gmail")
    partner = sys.argv[1].strip()

    if not os.path.exists("credentials.json"):
        sys.exit("找不到 credentials.json")

    if os.path.exists(CAL_TOKEN_FILE):
        with open(CAL_TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)
        print(f"沿用既有的 {CAL_TOKEN_FILE}")
    else:
        flow = InstalledAppFlow.from_client_secrets_file("credentials.json", CAL_SCOPES)
        creds = flow.run_local_server(port=0)
        with open(CAL_TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)

    b64 = base64.b64encode(pickle.dumps(creds)).decode()
    with open(B64_FILE, "w", encoding="utf-8") as f:
        f.write(b64)

    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    ids = {}
    for name in CALENDARS:
        cid, created = _ensure_calendar(service, name)
        shared = _share(service, cid, partner)
        ids[name] = cid
        print(f"{'✅ 新建' if created else '↩️ 沿用'}「{name}」"
              f"{'，已寄邀請給對方' if shared else '，對方本來就有權限'}")

    print()
    print(f"{B64_FILE} 已寫入（{len(b64)} 字元）→ 整串貼到 Infisical 的 CAL_TOKEN_PICKLE_B64")
    print(f"SPECIAL_CALENDAR_ID = {ids['⭐ 特別活動']}")


if __name__ == "__main__":
    main()
