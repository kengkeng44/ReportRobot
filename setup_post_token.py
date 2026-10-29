"""產生第二個信箱（郵局通知寄到的 xses20030724）的 gmail.readonly token。

為什麼另開一顆：既有 token.pickle / TOKEN_PICKLE_B64 綁的是 renhezheng44，
財務同步、發票、Gmail 警示全靠它。一顆 token 只能對應一個 Google 帳號，
所以第二個信箱要自己一顆；這支只產新的，**完全不碰** 既有的 token。

跑法（要在真的終端機跑，會開瀏覽器）：

    python setup_post_token.py

瀏覽器跳出來時**選 xses20030724 那個帳號登入**。跑完會產生兩個檔（都已 gitignore）：
- token_post.pickle    本機測試用
- token_post_b64.txt   把裡面那串貼到 Infisical 的 POST_TOKEN_PICKLE_B64

刻意不把 base64 印到終端機：那是等同密碼的東西。
"""

import base64
import os
import pickle
import sys

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
TOKEN_FILE = "token_post.pickle"
B64_FILE = "token_post_b64.txt"
EXPECTED = "xses20030724@gmail.com"


def main():
    if not os.path.exists("credentials.json"):
        sys.exit("找不到 credentials.json —— 從 Google Cloud Console 下載 OAuth 用戶端憑證放到 repo 根目錄")

    if os.path.exists(TOKEN_FILE):
        ans = input(f"{TOKEN_FILE} 已存在，要覆蓋嗎？(y/N) ").strip().lower()
        if ans != "y":
            sys.exit("取消，沒有改動任何東西")

    print(f"等一下瀏覽器跳出來，請選 {EXPECTED} 登入")
    flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
    creds = flow.run_local_server(port=0, prompt="select_account")

    email = build("gmail", "v1", credentials=creds).users().getProfile(userId="me").execute()["emailAddress"]
    if email.lower() != EXPECTED:
        sys.exit(f"登入的是 {email}，不是 {EXPECTED}。沒有寫入任何檔案，重跑一次選對帳號")

    with open(TOKEN_FILE, "wb") as f:
        pickle.dump(creds, f)
    b64 = base64.b64encode(pickle.dumps(creds)).decode()
    with open(B64_FILE, "w", encoding="utf-8") as f:
        f.write(b64)

    print()
    print(f"✅ 授權完成：{email}，scope = {creds.scopes}")
    print(f"   {TOKEN_FILE}  已寫入（本機測試用）")
    print(f"   {B64_FILE}  已寫入（{len(b64)} 字元）")
    print()
    print("接下來：把 token_post_b64.txt 整串設成 Infisical 的 POST_TOKEN_PICKLE_B64")


if __name__ == "__main__":
    main()
