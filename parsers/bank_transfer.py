"""銀行「轉帳成功」通知信 → 交易明細（方向＝轉帳）。

兩種信（實測 2026-10）：
- 台北富邦 mbank@dfm.taipeifubon.com.tw「臺幣轉帳交易「成功」通知」
    交易時間 2026/07/06 09:51:40
    轉出金額 TWD 12,870
    轉入帳號 005(土地銀行)-00001370****0594 洪○芳   手續費 TWD 15
    存摺留言（給自己） 鄭仁和 租屋   存摺留言(給對方) ...
- 國泰 CUBE「CUBE App轉帳通知」
    您於2026/10/09 17:55:31 之即時非約定轉帳結果如下：
    轉入銀行 013國泰世華  轉入帳號 4284300****16120  轉帳金額 8000  手續費 0  備註 …  轉帳結果 交易成功

只記「轉給別人」：
- 繳國泰卡費（轉入帳號是 4284300 開頭的虛擬帳號、或對方名稱寫信用卡）跳過 ——
  繳款不算花費，國泰繳款入帳通知那條線另外處理
- 轉給自己（對方名稱的頭尾字跟 OWNER_REAL_NAME 一樣，例如「鄭○和」）跳過 ——
  錢在自己口袋間移動。沒設 OWNER_REAL_NAME 就不判斷

方向一律「轉帳」：轉帳常是還錢、代墊，不該直接算進花費（儀表板只算「支出」）。
真的是花費的（房租）在 Notion 把方向改成支出就好。

指紋不含對方名稱：截圖辨識（transfer_ocr）讀到同一筆時，名稱寫法可能不同
（「洪○芳」vs「洪O芳」），但日期＋時分＋金額＋方向一樣就是同一筆。
"""

import hashlib
import os
import re

from bs4 import BeautifulSoup

FUBON = "富邦轉帳"
CATHAY = "國泰轉帳"

_CARD_VACCT_PREFIX = "4284300"     # 國泰信用卡繳款虛擬帳號開頭


def transfer_fingerprint(date, hhmm, direction, amount):
    """跨來源共用：信件、截圖讀到同一筆轉帳要算出同一個指紋。"""
    raw = f"轉帳|{date}|{hhmm or ''}|{direction}|{amount}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def is_self(name):
    owner = os.environ.get("OWNER_REAL_NAME", "").strip()
    name = (name or "").strip()
    if not owner or len(name) != len(owner) or len(owner) < 2:
        return False
    return name[0] == owner[0] and name[-1] == owner[-1]


def is_card_payment(bank_code, account, name):
    if "信用卡" in (name or ""):
        return True
    return bank_code == "013" and (account or "").startswith(_CARD_VACCT_PREFIX)


def _text(html):
    return re.sub(r"\s+", " ", BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True))


def _int(s):
    digits = re.sub(r"[^\d]", "", s or "")
    return int(digits) if digits else None


def _txn(source, date, hhmm, amount, bank_name, account, name, fee, memo):
    who = name or f"{bank_name} {account[-4:] if account else ''}".strip()
    notes = []
    if memo:
        notes.append(f"留言：{memo}")
    if fee:
        notes.append(f"手續費 {fee}")
    notes.append(f"{bank_name} {account}".strip())
    return {
        "date": date,
        "time": hhmm,
        "amount": amount,
        "currency": "TWD",
        "shop": f"↗️ 轉給 {who}",
        "direction": "轉帳",
        "source": source,
        "items": "｜".join(notes),
        "fingerprint": transfer_fingerprint(date, hhmm, "out", amount),
    }


_FUBON_TIME = re.compile(r"交易時間\s*(\d{4})/(\d{2})/(\d{2})\s+(\d{2}):(\d{2})")
_FUBON_AMT = re.compile(r"轉出金額\s*TWD\s*([\d,]+)")
_FUBON_TO = re.compile(r"轉入帳號\s*(\d{3})\(([^)]+)\)-([\d*]+)\s*(.*?)\s*手續費")
_FUBON_FEE = re.compile(r"手續費\s*TWD\s*([\d,]+)")
_FUBON_MEMO = re.compile(r"存摺留言[（(]給自己[)）]\s*(.*?)\s*存摺留言")


def parse_fubon(html):
    t = _text(html)
    if "交易成功" not in t:
        return []
    m_time, m_amt, m_to = _FUBON_TIME.search(t), _FUBON_AMT.search(t), _FUBON_TO.search(t)
    if not (m_time and m_amt and m_to):
        return []
    code, bank, acct, name = m_to.group(1), m_to.group(2), m_to.group(3), m_to.group(4).strip()
    if is_card_payment(code, acct, name) or is_self(name):
        return []
    y, mo, d, hh, mi = m_time.groups()
    m_fee, m_memo = _FUBON_FEE.search(t), _FUBON_MEMO.search(t)
    return [_txn(FUBON, f"{y}-{mo}-{d}", f"{hh}:{mi}", _int(m_amt.group(1)), bank, acct, name,
                 _int(m_fee.group(1)) if m_fee else 0, (m_memo.group(1) if m_memo else "").strip())]


_CUBE_TIME = re.compile(r"您於\s*(\d{4})/(\d{2})/(\d{2})\s+(\d{2}):(\d{2})")
_CUBE_BANK = re.compile(r"轉入銀行\s*(\d{3})\s*(\S+)")
_CUBE_ACCT = re.compile(r"轉入帳號\s*([\d*]+)")
_CUBE_AMT = re.compile(r"轉帳金額\s*([\d,]+)")
_CUBE_FEE = re.compile(r"手續費\s*([\d,]+)")
_CUBE_MEMO = re.compile(r"備註\s*(.*?)\s*轉帳結果")
# 信件開頭「轉帳結果如下」也有這四個字，要錨在「交易」，不然每封都被當成失敗跳過
_CUBE_RESULT = re.compile(r"轉帳結果\s*(交易\S*)")


def parse_cathay_cube(html):
    t = _text(html)
    m_res = _CUBE_RESULT.search(t)
    if not m_res or m_res.group(1) != "交易成功":
        return []
    m_time, m_bank, m_acct, m_amt = (_CUBE_TIME.search(t), _CUBE_BANK.search(t),
                                     _CUBE_ACCT.search(t), _CUBE_AMT.search(t))
    if not (m_time and m_bank and m_acct and m_amt):
        return []
    code, bank, acct = m_bank.group(1), m_bank.group(2), m_acct.group(1)
    if is_card_payment(code, acct, ""):
        return []
    y, mo, d, hh, mi = m_time.groups()
    m_fee, m_memo = _CUBE_FEE.search(t), _CUBE_MEMO.search(t)
    return [_txn(CATHAY, f"{y}-{mo}-{d}", f"{hh}:{mi}", _int(m_amt.group(1)), bank, acct, "",
                 _int(m_fee.group(1)) if m_fee else 0, (m_memo.group(1) if m_memo else "").strip())]


class _Parser:
    """finance_sync.SOURCES 要的是有 parse(html) 的模組；兩種信各包一個。"""

    def __init__(self, fn):
        self.parse = fn


fubon = _Parser(parse_fubon)
cathay_cube = _Parser(parse_cathay_cube)
