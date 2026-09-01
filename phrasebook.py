"""每日三句:英文 / 西班牙文走間隔重複,中文金句走隨機不重複。

為什麼是固定間隔而不是 SM-2:真正的遺忘曲線要吃「你記得嗎」的回饋,
那需要信裡放連結、server.py 開端點、Notion 存熟程度。使用者選了零互動
(見 spec 2.4),所以這裡只有一張固定的間隔表。

這個模組刻意**不碰 Notion、不碰 AI** —— 只做決策,I/O 由呼叫端負責。
測試因此不需要 mock 任何東西。
"""

import random
from datetime import timedelta

# 第 n 次出現之後,隔幾天再出現。使用者原話是「隔一個月、三個月再重傳」,
# 對應第 3、第 4 級;前兩級是標準的短期鞏固。
INTERVALS = (1, 7, 30, 90, 180)


def next_due(appeared_count, today):
    """出現過 appeared_count 次(含這次)之後,下次該哪天出現。

    超過表長就一直用最後一級(180 天一輪),不是停止出現 ——
    背過的東西還是會忘,只是慢一點。

    appeared_count 是 0 時當成 1:Notion 的「出現次數」沒填,讀回來
    就是 0。這裡吞掉那個 off-by-one,呼叫端不必特判。
    """
    index = min(max(appeared_count, 1) - 1, len(INTERVALS) - 1)
    return today + timedelta(days=INTERVALS[index])
