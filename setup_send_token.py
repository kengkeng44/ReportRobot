"""產生「只有 gmail.send 權限」的獨立 token，給每日個人報寄信用。

為什麼要獨立一顆：現有 token.pickle / TOKEN_PICKLE_B64 只有
gmail.readonly，財務同步、發票、Gmail 警示三個功能全靠它。在那顆
上面加 send scope 得重跑授權換掉線上那顆 —— 換壞了是連鎖故障。
這支只產新的，**完全不碰** 既有的 token.pickle。

跑法（要在真的終端機跑，會開瀏覽器）：

    python setup_send_token.py

跑完會產生兩個檔（都已 gitignore）：
- token_send.pickle    本機測試用
- token_send_b64.txt   把裡面那串貼到 Infisical 的 SEND_TOKEN_PICKLE_B64

刻意不把 base64 印到終端機：那是等同密碼的東西，印出來會留在
終端機捲動紀錄、螢幕分享、以及任何在旁邊跑的東西的輸出裡。
"""

import base64
import os
import pickle
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

from mailer import SEND_SCOPES, SEND_TOKEN_FILE

B64_FILE = "token_send_b64.txt"


def main():
    if not os.path.exists("credentials.json"):
        sys.exit("找不到 credentials.json —— 從 Google Cloud Console 下載 OAuth 用戶端憑證放到 repo 根目錄")

    if os.path.exists(SEND_TOKEN_FILE):
        ans = input(f"{SEND_TOKEN_FILE} 已存在，要覆蓋嗎？(y/N) ").strip().lower()
        if ans != "y":
            sys.exit("取消，沒有改動任何東西")

    flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SEND_SCOPES)
    creds = flow.run_local_server(port=0)

    with open(SEND_TOKEN_FILE, "wb") as f:
        pickle.dump(creds, f)

    b64 = base64.b64encode(pickle.dumps(creds)).decode()
    with open(B64_FILE, "w", encoding="utf-8") as f:
        f.write(b64)

    print()
    print(f"✅ 授權完成，scope = {creds.scopes}")
    print(f"   {SEND_TOKEN_FILE}  已寫入（本機測試用）")
    print(f"   {B64_FILE}  已寫入（{len(b64)} 字元）")
    print()
    print("接下來：打開 token_send_b64.txt，整串複製，到 Infisical 新增變數")
    print("  變數名稱：SEND_TOKEN_PICKLE_B64")
    print()
    print("設定好之後，GMAIL_APP_PASSWORD 就沒用了，可以從 Infisical 刪掉。")


if __name__ == "__main__":
    main()
