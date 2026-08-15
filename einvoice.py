"""財政部電子發票載具 API：把「今天買了什麼菜」真的抓出來。

為什麼要這個模組
────────────────
信用卡授權電文只傳「商店代號 + 總金額」給發卡行。所以國泰彙整信永遠只有
「全聯福利中心－板橋板新　NT$361」,買了什麼銀行從來就收不到 ——
這不是國泰偷懶,換任何一張卡都一樣。

但結帳時出示手機條碼載具的話,完整品項明細會上傳到財政部整合服務平台。
財政部只寄中獎通知,不寄明細,所以要自己用 API 撈。

⚠️ 前提:那次結帳真的有出示載具。只刷卡沒掃條碼的發票不會歸戶,
平台上也查不到 —— 這是資料源的先天限制,不是程式的問題。

規格
────
電子發票應用 API 規格 v1.9(財政部財政資訊中心)第二章五、六節。
- 位址:https://api.einvoice.nat.gov.tw/[API Method]
- POST,**application/x-www-form-urlencoded**,回應 JSON
  (網路上流傳的 openapi.yaml 寫 application/json 是錯的)
- 表頭查詢 /PB2CAPIVAN/invServ/InvServ　version 0.6(113/1/1 起)
- 明細查詢 /PB2CAPIVAN/invServ/InvServ　version 0.5

設定(三個都要,缺一個就整個關掉)
────
- EINVOICE_APP_ID：向財資中心申請的 AppID
  https://einvoice.nat.gov.tw/APCONSUMER/BTC605W/
- EINVOICE_CARD_NO：手機條碼,長得像 /EK56VW
- EINVOICE_CARD_ENCRYPT：手機條碼驗證碼(等同密碼,不要外流)
"""

import calendar
import os
import time
import uuid as _uuid
from datetime import date

import requests

BASE_URL = "https://api.einvoice.nat.gov.tw"
CARRIER_PATH = "/PB2CAPIVAN/invServ/InvServ"

CARD_TYPE_MOBILE = "3J0002"      # 手機條碼(規格第一章十一)
VERSION_HEADER = "0.6"           # 載具發票表頭查詢,113/1/1 起
VERSION_DETAIL = "0.5"           # 載具發票明細查詢

# 規格第一章七:時間戳記要比現在多 10~180 秒。取中間值,
# 容器時間稍有偏差也還在範圍內。
_TIMESTAMP_OFFSET = 90
_EXP_TIMESTAMP = "2147483647"

# 規格第一章六、訊息回應碼定義。只翻譯會真的遇到的,
# 其餘走 fallback —— 財政部之後加新碼也不會炸。
_CODES = {
    "200": "執行成功",
    "500": "財政部系統執行錯誤,稍後再試",
    "900": "建立 JSON 物件失敗",
    "901": "無此期別資料",
    "902": "期別錯誤",
    "903": "參數錯誤(送出的欄位有問題)",
    "904": "錯誤的查詢種類",
    "915": "查無此發票詳細資料",
    "919": "手機條碼驗證碼錯誤,檢查 EINVOICE_CARD_ENCRYPT",
    "950": "超過最大查詢次數,今天先別再查了",
    "951": "連線逾時",
    "952": "載具有效存續時間已過",
    "953": "卡片檢查碼有誤",
    "954": "簽名有誤",
    "996": "查詢筆數超過上限,要翻下一頁",
    "997": "UUID 不符合規定(黑名單)",
    "998": "AppID 不符合規定,可能沒申請成功或被停權",
    "999": "財政部端未知錯誤",
}

_TIMEOUT = 20


class EInvoiceError(Exception):
    """財政部回非 200 時丟。訊息已經是人看得懂的中文。"""


def explain_code(code):
    """訊息碼 → 人話。回「code 919」對使用者沒有任何意義。"""
    return _CODES.get(str(code), f"財政部回了未知的訊息碼 {code}")


def is_configured():
    """三個設定齊全才算能用。缺一個就整個關掉 —— 半套設定會在
    真的送出請求時才失敗,那時已經把驗證碼送出去了。"""
    return all(os.environ.get(k) for k in
               ("EINVOICE_APP_ID", "EINVOICE_CARD_NO", "EINVOICE_CARD_ENCRYPT"))


def _timestamp(now=None):
    return str(int(now if now is not None else time.time()) + _TIMESTAMP_OFFSET)


def _base_payload():
    """所有載具 API 共用的欄位。驗證碼在這裡進去之後就不該再被印出來。"""
    return {
        "cardType": CARD_TYPE_MOBILE,
        "cardNo": os.environ.get("EINVOICE_CARD_NO", ""),
        "cardEncrypt": os.environ.get("EINVOICE_CARD_ENCRYPT", ""),
        "appID": os.environ.get("EINVOICE_APP_ID", ""),
        "expTimeStamp": _EXP_TIMESTAMP,
        "timeStamp": _timestamp(),
        # UUID 由開發者自行管控,財政部只做記錄用
        "uuid": os.environ.get("EINVOICE_UUID") or str(_uuid.uuid4()),
    }


def _carrier_chk_payload(start_date, end_date, page=1):
    payload = _base_payload()
    payload.update({
        "version": VERSION_HEADER,
        "action": "carrierInvChk",
        "startDate": start_date,
        "endDate": end_date,
        "onlyWinningInv": "N",   # 要全部,不是只要中獎的
        "page": str(page),
    })
    return payload


def _carrier_detail_payload(inv_num, inv_date):
    payload = _base_payload()
    payload.update({
        "version": VERSION_DETAIL,
        "action": "carrierInvDetail",
        "invNum": inv_num,
        "invDate": inv_date,
    })
    return payload


def _post(payload):
    r = requests.post(BASE_URL + CARRIER_PATH, data=payload, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _check(body):
    """回應碼不是 200/996 就丟例外。996 是分頁,由呼叫端處理。"""
    code = str((body or {}).get("code", ""))
    if code not in ("200", "996"):
        raise EInvoiceError(explain_code(code))
    return code


def _num(value):
    """'56.00' → 56。空字串 / '-' / None → None(缺資料,不是 0)。"""
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (ValueError, TypeError, AttributeError):
        return None


def _inv_date(raw):
    """invDate 是 {year, month, date}。壞掉回 None,由呼叫端跳過那筆。"""
    if not isinstance(raw, dict):
        return None
    try:
        return date(int(raw["year"]), int(raw["month"]), int(raw["date"]))
    except (KeyError, ValueError, TypeError):
        return None


def _parse_invoice_list(body):
    _check(body)
    rows = []
    for d in (body or {}).get("details") or []:
        day = _inv_date(d.get("invDate"))
        if day is None:
            # 日期壞掉的那筆跳過就好,不要讓整個月的資料一起消失
            continue
        rows.append({
            "inv_num": d.get("invNum") or "",
            "seller": (d.get("sellerName") or "").strip(),
            "amount": _num(d.get("amount")),
            "date": day,
        })
    return rows


def _parse_items(body):
    _check(body)
    items = []
    for d in (body or {}).get("details") or []:
        name = (d.get("description") or "").strip()
        if not name:
            continue      # 沒有品名的那列沒有任何價值
        items.append({
            "name": name,
            "qty": _num(d.get("quantity")),
            "unit_price": _num(d.get("unitPrice")),
            "amount": _num(d.get("amount")),
        })
    return items


def list_invoices(year, month, max_pages=20):
    """某個月的發票表頭。規格限制 startDate/endDate 必須同月份。

    code 996 代表這頁滿了還有下一頁 —— 不跟的話會默默少算後面所有發票,
    而且完全看不出來。max_pages 是硬性上限:平台一直回 996 的話
    不能無限打下去。
    """
    last_day = calendar.monthrange(year, month)[1]
    start = f"{year:04d}/{month:02d}/01"
    end = f"{year:04d}/{month:02d}/{last_day:02d}"

    rows = []
    for page in range(1, max_pages + 1):
        body = _post(_carrier_chk_payload(start, end, page=page))
        rows.extend(_parse_invoice_list(body))
        if str((body or {}).get("code")) != "996":
            break
    return rows


def invoice_detail(inv_num, inv_date):
    """一張發票的品項明細。inv_date 是 date 或 yyyy/MM/dd 字串。"""
    if isinstance(inv_date, date):
        inv_date = inv_date.strftime("%Y/%m/%d")
    return _parse_items(_post(_carrier_detail_payload(inv_num, inv_date)))


def fetch_month(year, month, max_detail=60):
    """某個月的發票 + 每張的品項。

    明細要一張發票打一次 API,所以有 max_detail 上限 —— 規格第一章六節有
    「950 超過最大查詢次數」,把額度一次燒完之後那天就查不動了。

    單張明細失敗不影響其他張:那張的 items 留空,其餘照常回。
    """
    invoices = list_invoices(year, month)
    for inv in invoices[:max_detail]:
        try:
            inv["items"] = invoice_detail(inv["inv_num"], inv["date"])
        except Exception as e:
            print(f"[einvoice] 明細失敗 {inv['inv_num']}：{e}")
            inv["items"] = []
    for inv in invoices[max_detail:]:
        inv["items"] = []
    return invoices


_MAX_CHARS = 4500      # LINE 單則 5000,留 buffer


def format_purchases(invoices, max_items=8):
    """排成 LINE 看得懂的樣子。新的排前面 —— 想看的通常是最近買的。

    店家沒上傳品項時要講明「店家未上傳」,不然看起來像程式壞了。
    """
    if not invoices:
        return ("查無發票紀錄。\n"
                "可能是這個月還沒消費,或結帳時沒有出示手機條碼載具 ——\n"
                "沒出示的發票不會歸戶,財政部那邊也查不到。")

    rows = sorted(invoices, key=lambda i: i.get("date") or date.min, reverse=True)

    lines = []
    for inv in rows:
        day = inv.get("date")
        head = f"{day.month}/{day.day:02d}" if day else "日期不明"
        amount = inv.get("amount")
        lines.append(f"■ {head}　{inv.get('seller') or '店名不明'}"
                     f"　NT${amount if amount is not None else '-'}")

        items = inv.get("items") or []
        if not items:
            lines.append("　（店家未上傳品項）")
        for it in items[:max_items]:
            qty = it.get("qty")
            qty_part = f" ×{qty}" if qty and qty > 1 else ""
            amt = it.get("amount")
            lines.append(f"　・{it['name']}{qty_part}"
                         f"　{('NT$' + str(amt)) if amt is not None else ''}".rstrip())
        if len(items) > max_items:
            lines.append(f"　　…另 {len(items) - max_items} 項")
        lines.append("")

        if sum(len(x) + 1 for x in lines) > _MAX_CHARS:
            lines.append("（太多了，只顯示最近這些）")
            break

    return "\n".join(lines).strip()[:_MAX_CHARS]
