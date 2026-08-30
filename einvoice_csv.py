"""財政部「消費發票彙整通知」CSV：API 拿不到之後的替代資料源。

為什麼有這個模組(einvoice.py 明明已經寫好了)
────────────────────────────────────────
`einvoice.py` 走的是財政部 API,程式碼是完整的,但**個人拿不到 AppID**。
依「電子發票應用程式介面使用規範」,112/3/31 起個人不再列入開發者範圍 ——
申請門檻是 CNS27001／ISO27001 資安認證,只有企業與組織適用。
市面上那些「輸入載具查毒油」的 App 能查,是因為它們有企業 AppID,
再用**你交出去的載具帳密**代查;個資風險就在這裡。

所以資料改走財政部主動寄的「消費發票彙整通知」:登入電子發票整合服務平台
開通後,每月一封信,附件 CSV,含品項明細。

兩條路的取捨
────────
| 來源 | 延遲 | 需要 |
|------|------|------|
| API(einvoice.py) | 即時 | AppID —— 個人申請不到 |
| 彙整通知 email | 每月 | 開通一次,之後全自動 |
| 平台手動匯出 | 約 2 天 | 每次自己登入匯出 |

「約 2 天」來自統一發票使用辦法第 7 條第 4 項:營業人須於開立後 48 小時內
上傳平台。平台自行查詢上限 6 個月,更早的要備身分證與申請書專案申請。

兩種格式(來源不同,不要混淆)
────────────────────────
**A. 平台手動匯出**(登入後自己下載,檔名 `{流水號}_{時間戳}.csv`)
    載具自訂名稱,發票日期,發票號碼,發票金額,發票狀態,折讓,賣方統一編號,
    賣方名稱,賣方地址,買方統編,消費明細_數量,消費明細_單價,消費明細_金額,消費明細_品名
逗號分隔、有表頭、**扁平**(一列一個品項,發票欄位重複)、UTF-8-BOM + LF。
欄位以 2026-08-30 使用者實際下載的檔案核對過。**有數量與單價。**

**B. 彙整通知 email 附件**
    M|發票狀態|發票號碼|發票日期|商店統編|商店店名|載具名稱|載具號碼|總金額
    D|發票號碼|小計|品項名稱
`|` 分隔(店名本來就常有逗號)、UTF-8 + CRLF(109/6 前是 BIG5)。
⚠️ 此格式**尚未以真實信件驗證**,取自公開文件與社群整理。只給小計,
沒有數量單價。

`parse()` 看內容自動判別,不靠檔名。

回傳結構刻意與 `einvoice.fetch_month()` 一致,所以 `format_purchases()`
可以直接重用;格式 B 缺的數量單價留 None,顯示層接得住。
"""

import csv
import io
from datetime import date

# M 列欄位位置。CSV 沒有表頭列,只能靠順序。
_M_STATUS, _M_NUM, _M_DATE, _M_SELLER_ID = 1, 2, 3, 4
_M_SELLER, _M_CARRIER_NAME, _M_CARRIER_ID, _M_AMOUNT = 5, 6, 7, 8
_M_WIDTH = 9

# D 列:發票號碼|小計|品項名稱
_D_NUM, _D_AMOUNT, _D_NAME = 1, 2, 3
_D_WIDTH = 4

# 平台匯出格式(格式 A)的欄位位置,以真實檔案核對過。
_X_CARRIER, _X_DATE, _X_NUM, _X_AMOUNT, _X_STATUS = 0, 1, 2, 3, 4
_X_SELLER_ID, _X_SELLER, _X_ADDRESS = 6, 7, 8
_X_QTY, _X_UNIT_PRICE, _X_ITEM_AMOUNT, _X_NAME = 10, 11, 12, 13
_X_WIDTH = 14      # 品名含逗號時會更多,所以是下限不是等於

_VOID_MARK = "作廢"

# 判別格式時要掃的列數。開頭可能有說明文字或新行別,只看第一列會誤判。
_SNIFF_LINES = 20


def _num(value):
    """'42.00' → 42。轉不動回 None,由呼叫端決定要不要跳過。

    整數就回整數 —— 品項金額印成 42.0 在 LINE 上看起來像壞掉。
    """
    if value is None:
        return None
    try:
        val = float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return None
    return int(val) if val == int(val) else val


def _parse_date(raw):
    """'20260815' → date(2026, 8, 15)。壞掉回 None。"""
    text = (raw or "").strip()
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:]))
    except ValueError:
        return None      # 20261332 這種語法對但日期不存在的


def _invoice_from_row(cells):
    """M 列 → 發票 dict。缺日期就當這列壞掉。"""
    day = _parse_date(cells[_M_DATE])
    if day is None:
        return None

    return {
        "inv_num": cells[_M_NUM].strip(),
        "seller": cells[_M_SELLER].strip(),
        "amount": _num(cells[_M_AMOUNT]),
        "date": day,
        "status": cells[_M_STATUS].strip(),
        # 這兩欄 API 路徑沒有,CSV 才有 —— 統編之後要拿來把同品牌的
        # 不同分店歸成一類,別在這裡丟掉。
        "seller_id": cells[_M_SELLER_ID].strip(),
        "carrier_name": cells[_M_CARRIER_NAME].strip(),
        "carrier_id": cells[_M_CARRIER_ID].strip(),
        "items": [],
    }


def _item_from_row(cells):
    """D 列 → 品項 dict。沒有品名的那列沒有任何價值,丟掉。"""
    name = cells[_D_NAME].strip()
    if not name:
        return None

    return {
        "name": name,
        # CSV 只給小計,沒有數量與單價。留鍵是為了跟 API 路徑同形,
        # format_purchases 的 `if qty and qty > 1` 會自動略過。
        "qty": None,
        "unit_price": None,
        "amount": _num(cells[_D_AMOUNT]),
    }


def parse(text, include_voided=False):
    """把載具 CSV 解成發票清單。兩種格式自動判別。

    壞掉的個別列會被跳過,不丟例外 —— 跟 parsers/ 的共同原則一致:
    一筆金額錯誤的紀錄比缺一筆更難發現。

    include_voided:預設排除作廢發票(錢已經退了,不該算進消費);
    要對帳時打開,作廢的會帶著 status='作廢' 一起回來。
    """
    if not text:
        return []
    if detect_format(text) == "export":
        return _parse_export(text, include_voided=include_voided)
    return _parse_notification(text, include_voided=include_voided)


def detect_format(text):
    """'export'(平台匯出) 或 'notification'(彙整通知)。

    靠內容判別而不是檔名 —— 使用者下載後常會改名。
    彙整通知的資料列以 M/D 開頭且用 `|`,匯出格式兩者都沒有。

    要掃過開頭數列而不是只看第一列 —— 檔案開頭可能是財政部之後新增的
    行別或說明文字,只看第一列會整份誤判成另一種格式。
    """
    for line in (text or "").lstrip("﻿").splitlines()[:_SNIFF_LINES]:
        if "|" in line and line.split("|")[0].strip() in ("M", "D"):
            return "notification"
    return "export"


def _parse_export(text, include_voided=False):
    """平台匯出格式:扁平列依發票號碼聚合。

    dict 保序,所以輸出順序＝檔案裡第一次出現的順序。
    """
    invoices = {}

    for cells in csv.reader(io.StringIO(text.lstrip("﻿"))):
        if len(cells) < _X_WIDTH:
            continue      # 表頭以外的單欄註解列(「注意:本功能…」)
        if cells[_X_NUM].strip() == "發票號碼":
            continue      # 表頭

        day = _parse_date(cells[_X_DATE])
        num = cells[_X_NUM].strip()
        if day is None or not num:
            continue

        status = cells[_X_STATUS].strip()
        if not include_voided and _VOID_MARK in status:
            continue

        inv = invoices.get(num)
        if inv is None:
            inv = invoices[num] = {
                "inv_num": num,
                # 實測賣方名稱有前導空白,不 strip 同一家店會被當成兩家
                "seller": cells[_X_SELLER].strip(),
                # 這個格式**沒有**發票總額欄位,最後由品項加總填上。
                # 第 3 欄雖然叫「發票金額」,實測 167/167 列都等於第 12 欄
                # 的品項金額,同張發票內還會變動 —— 拿它當總額的話,
                # 多品項發票只會記到第一個品項的錢。
                "amount": 0,
                "date": day,
                "status": status,
                "seller_id": cells[_X_SELLER_ID].strip(),
                "carrier_name": cells[_X_CARRIER].strip(),
                "carrier_id": "",      # 匯出格式沒有載具號碼
                "seller_address": cells[_X_ADDRESS].strip(),
                "items": [],
            }

        # 品名裡的逗號**沒有被引號包住**,會撐出第 15 欄以後。
        # 實測案例「松露野菇歐姆蛋-薯條,軟法」。不黏回去品名會靜默截斷。
        name = ",".join(cells[_X_NAME:]).strip()
        if name:
            amount = _num(cells[_X_ITEM_AMOUNT])
            inv["items"].append({
                "name": name,
                "qty": _num(cells[_X_QTY]),
                "unit_price": _num(cells[_X_UNIT_PRICE]),
                "amount": amount,
            })
            # 折扣列是負數(實測有 -11),照加 —— 那本來就是這張發票的一部分
            if amount is not None:
                inv["amount"] += amount

    return list(invoices.values())


def _parse_notification(text, include_voided=False):
    """彙整通知格式:M 主檔 + 隨後的 D 明細。"""

    invoices = []
    current = None      # 最近一張 M,D 列靠它歸屬

    for line in text.lstrip("﻿").splitlines():
        if not line.strip():
            continue

        cells = line.split("|")
        kind = cells[0].strip()

        if kind == "M":
            current = None
            if len(cells) < _M_WIDTH:
                continue      # 欄位不足代表格式改版或殘缺,不要猜著填
            inv = _invoice_from_row(cells)
            if inv is None:
                continue
            if not include_voided and _VOID_MARK in inv["status"]:
                continue      # current 留 None,後續的 D 一併被丟掉
            invoices.append(inv)
            current = inv

        elif kind == "D":
            if current is None:
                continue      # 沒有主檔的明細(或主檔被跳過了)
            if len(cells) < _D_WIDTH:
                continue
            item = _item_from_row(cells)
            if item is not None:
                current["items"].append(item)

        # 其他行別直接忽略 —— 財政部之後加新列不該讓整批解析失敗

    return invoices
