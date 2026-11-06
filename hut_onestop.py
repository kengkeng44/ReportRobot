"""一站式（hike.taiwan.gov.tw）床位月曆解析。

解析邏輯改寫自 hiiamyes/taiwan-hiking-form-filler 的 check-paiyun-capacity
（MIT License, Copyright (c) 2025 Yes Lee），Node → Python。
https://github.com/hiiamyes/taiwan-hiking-form-filler

抓頁面要真瀏覽器（網站前有 WAF，程式直接送表單回 Request Rejected），
在 tools/hut_browser.py；這裡只做純解析，Railway 和測試都能 import。
"""

import re
from datetime import date

from bs4 import BeautifulSoup

from hut_sources import Slot

BASE = "https://hike.taiwan.gov.tw/"
# site key -> (查詢頁, 「宿營地」下拉選單裡的名稱)；名稱以 tools/hut_browser.py --recon 印出的為準
ONESTOP = {
    "onestop_paiyun": ("bed_6.aspx", "排雲山莊"),
    "onestop_99": ("bed_1.aspx", "九九山莊"),
    # 三六九山莊 2023-05-20 起改建停止申請，只剩臨時營地（24 帳位）
    "onestop_369camp": ("bed_1.aspx", "三六九臨時營地"),
    "onestop_cilai": ("bed_4.aspx", "奇萊山屋"),
    "onestop_nanhu": ("bed_4.aspx", "南湖山屋"),
}
REJECTED_MARK = "Request Rejected"

_SDATE = re.compile(r"sdate=(\d{4}-\d{2}-\d{2})")
# 排雲：「餘額 (1,0)」／「額滿」、「排隊預約 N」；雪霸、太魯閣：「餘額 4」、「待處理 N」
_REMAIN = re.compile(r"餘額\s*(?:\(\s*(\d+)\s*,\s*\d+\s*\)|(\d+))")
_QUEUE = re.compile(r"(?:排隊預約|待處理)\s*(\d+)")


class Blocked(Exception):
    """網站 WAF 擋下。看到就停，不繞。"""


def ensure_not_blocked(html):
    if REJECTED_MARK in (html or ""):
        raise Blocked("一站式回 Request Rejected")


def parse_onestop_html(html, site):
    ensure_not_blocked(html)
    unit = ONESTOP[site][1]
    kind = "tent" if "營地" in unit else "bed"
    soup = BeautifulSoup(html, "html.parser")
    slots = []
    for a in soup.select("table.table_bed a[href*='sdate=']"):
        day = date.fromisoformat(_SDATE.search(a["href"]).group(1))
        text = re.sub(r"\s+", " ", a.get_text(" "))
        rm = _REMAIN.search(text)
        if rm:
            remaining = int(rm.group(1) or rm.group(2))
        elif "額滿" in text:
            remaining = 0
        else:
            remaining = None
        q = _QUEUE.search(text)
        slots.append(Slot(site, unit, kind, day, remaining, int(q.group(1)) if q else None))
    return slots
