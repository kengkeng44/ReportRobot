"""國泰世華「消費彙整通知」（service@pxbillrc01.cathaybk.com.tw）。

每天一封，彙整前一日的刷卡授權，是整套財務自動化最有價值的資料源
（逐筆、有金額商店類別，且不需要解密 PDF）。

信件結構（實測 2026-08）：
    <table> 卡號後4碼： 1234 </table>       ← 卡號在獨立的 table
    <table>
      卡別 | 行動卡號後4碼 | 授權日期 | 授權時間 | 消費地區
      正卡 | 5678         | 2026/08/09 | 19:42 | TW
      消費金額 | 商店名稱 | 消費類別 | 備註
      NT$20   | 統一超商 | 超市∕量販 | 註一
      ...（每 4 個 tr 一筆，可重複）
    </table>

注意：這是**授權**不是最終入帳。外幣結匯與退款會讓實際金額不同，
所以一律標 status=授權中，等月帳單來再對帳補正（見 spec 4.3）。
"""

import hashlib
import re

from bs4 import BeautifulSoup


SOURCE = "國泰消費彙整"

_CARD_RE = re.compile(r"卡號後4碼[：:]\s*(\d{4})")
_DATE_RE = re.compile(r"^(\d{4})/(\d{2})/(\d{2})$")
_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")

_HEAD_A = ("卡別", "行動卡號後4碼", "授權日期", "授權時間", "消費地區")
_HEAD_B = ("消費金額", "商店名稱", "消費類別", "備註")


def make_fingerprint(card_last4, date, amount, shop):
    """去重鍵。

    刻意**不含時間** —— 同日同店同金額視為同一筆，這樣重跑排程不會產生
    重複。代價是同一天在同一間店刷兩次一模一樣的金額會被併成一筆，
    但那比每次排程都長出重複資料好處理。
    """
    raw = f"{card_last4}|{date}|{amount}|{shop}|{SOURCE}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _cells(tr):
    return [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]


def _is_header(cells, expected):
    return len(cells) == len(expected) and tuple(cells) == expected


def _parse_amount(text):
    """'NT$1,271' → 1271。解析不出來回 None（呼叫端跳過該筆）。"""
    digits = re.sub(r"[^\d.]", "", text or "")
    if not digits:
        return None
    try:
        val = float(digits)
    except ValueError:
        return None
    return int(val) if val == int(val) else val


def _parse_date(text):
    m = _DATE_RE.match((text or "").strip())
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def parse(html, card_last4=None):
    """回交易 dict 清單。解析不出來的個別筆會被跳過，不丟例外。"""
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")

    # 卡號在別的 table，先從全文抓（一封信通常只有一張卡）
    if not card_last4:
        m = _CARD_RE.search(soup.get_text(" ", strip=True))
        card_last4 = m.group(1) if m else ""

    rows = [_cells(tr) for tr in soup.find_all("tr")]

    out = []
    i = 0
    while i < len(rows):
        # 一筆 = header_a / data_a / header_b / data_b 四列
        if not _is_header(rows[i], _HEAD_A):
            i += 1
            continue
        if i + 3 >= len(rows):
            break

        data_a, head_b, data_b = rows[i + 1], rows[i + 2], rows[i + 3]
        i += 4

        # 欄位數不對代表信件改版或那筆殘缺 —— 跳過，不要猜著填
        if len(data_a) != len(_HEAD_A) or not _is_header(head_b, _HEAD_B):
            continue
        if len(data_b) != len(_HEAD_B):
            continue

        date = _parse_date(data_a[2])
        amount = _parse_amount(data_b[0])
        if not date or amount is None:
            continue

        time_text = data_a[3].strip()
        shop = data_b[1].strip()

        out.append({
            "date": date,
            "time": time_text if _TIME_RE.match(time_text) else "",
            "amount": amount,
            "shop": shop,
            "category": data_b[2].strip(),
            "region": data_a[4].strip(),
            "card_last4": card_last4,
            "direction": "支出",
            "status": "授權中",
            "source": SOURCE,
            "fingerprint": make_fingerprint(card_last4, date, amount, shop),
        })

    return out
