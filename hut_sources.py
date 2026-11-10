"""林業署山屋站（嘉明湖／天池／檜谷）月曆：抓頁面、解析成 Slot。

三站是同一套 PHP 系統，月曆格子寫法略有不同：
- 嘉明湖：每天一個 <a id="tds_日">，格子內容包在裡面
- 檜谷：每天一個 <a id="tds_流水號">（不是日期），日期取自 onclick 的 date=YYYY-MM-DD
- 天池：<a id="tds_日"> 只是「線上申請」按鈕，內容在外層最內圈的 <td>；
  過了申請期的日子沒有按鈕，日期改從格子裡的 YYYY-MM-DD 或前面的日數字拿
換月份：嘉明湖／天池 GET 帶 date_set[year]、date_set[month]、csrf；
檜谷的月曆是 ajax，POST year、month、csrf、mode=show_calendar。
做法參考 exhaJim/taiwan-cabin-crawler（沒寫授權，只學網址參數與 HTML 結構，程式自己寫）。

一天只跑 3 次、每個請求間隔 2.5 秒；User-Agent 照實寫是個人監控程式，被擋就記錯誤，不換 UA。
"""

import re
import time
from dataclasses import dataclass
from datetime import date

import requests
from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (compatible; ReportRobot-hut-monitor/1.0; personal use)"
REQUEST_GAP = 2.5
TIMEOUT = 20


def _opt_int(v):
    return None if v is None else int(v)


@dataclass(frozen=True)
class Slot:
    site: str               # SITES 的 key；一站式是 hut_onestop.ONESTOP 的 key
    unit: str               # 山屋或營地，例「嘉明湖山屋」「周圍營地大營地4*4」
    kind: str               # "bed" | "tent"
    day: date               # 住宿日
    remaining: int | None   # 剩餘；None = 看不出來（不是 0）
    applicants: int | None  # 目前報名／排隊人數

    def key(self):
        return (self.site, self.unit, self.day.isoformat())

    def to_dict(self):
        return {"site": self.site, "unit": self.unit, "kind": self.kind,
                "day": self.day.isoformat(), "remaining": self.remaining,
                "applicants": self.applicants}

    @classmethod
    def from_dict(cls, d):
        return cls(str(d["site"]), str(d["unit"]), str(d["kind"]),
                   date.fromisoformat(d["day"]), _opt_int(d.get("remaining")),
                   _opt_int(d.get("applicants")))


SITES = {
    # 2026-11 起對程式的 User-Agent 回 HTTP 500（瀏覽器 UA 回 200），等於擋掉監控程式；
    # 照「被擋不換 UA」的原則停掉自動查詢（auto=False），解析與查詢頁網址留著。
    # 嘉明湖是抽籤制，抽籤／開放日由 hut_remind 提醒。對方放行後把 auto 拿掉即可。
    "jmlnt": {"name": "嘉明湖", "url": "https://jmlnt.forest.gov.tw/room/index.php",
              "nav": "get", "huts": ("嘉明湖山屋", "嘉明湖營地", "向陽山屋"), "auto": False},
    "tconline": {"name": "天池", "url": "https://tconline.forest.gov.tw/room/index.php",
                 "nav": "get", "huts": ("天池山莊", "周圍營地")},
    "kgonline": {"name": "檜谷", "url": "https://kgonline.forest.gov.tw/room/index.php",
                 "nav": "post", "huts": ("檜谷山莊", "周圍營地")},
}

# 自動查詢的站（fetch_all 預設、hut_monitor 等本機送的站）
AUTO_SITES = tuple(site for site, cfg in SITES.items() if cfg.get("auto", True))

# 「剩餘床位 (10) 目前報名 : 202」「大營地4*4 (2) 目前報名 : 0」「四人帳篷(7) 目前報名 : 3」
_ENTRY = re.compile(r"(?P<label>[^\s()]+)\s*\(\s*(?P<n>\d+)\s*\)\s*目前報名\s*:\s*(?P<m>\d+)")
_DAY_ONLY = re.compile(r"^\s*\d{1,2}\s*$")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
_CSRF = re.compile(r'name="csrf"[^>]*value="([^"]*)"')
# 這些標籤代表「山屋／營地本身」就是單位；其他標籤（大營地4*4、四人帳篷）接在營地名後面
_WHOLE_UNIT_LABELS = ("床位", "剩餘床位", "剩餘營地")


def find_csrf(html):
    m = _CSRF.search(html or "")
    return m.group(1) if m else ""


def _day_cells(soup, year, month):
    """[(住宿日, 格子文字)]。"""
    anchors = [a for a in soup.find_all("a", id=re.compile(r"^tds_\d+$"))
               if "目前報名" in a.get_text()]
    if anchors:
        # 檜谷的 id 是流水號（tds_3 = 12/1），日期以 onclick 的 date=YYYY-MM-DD 為準
        cells = []
        for a in anchors:
            m = _ISO.search(str(a.get("onclick", "")))
            day = date.fromisoformat(m.group(0)) if m else date(year, month, int(a["id"][4:]))
            cells.append((day, a.get_text(" ")))
        return cells
    cells = []
    for td in soup.find_all("td"):
        text = td.get_text(" ")
        if "目前報名" not in text:
            continue
        if any("目前報名" in inner.get_text() for inner in td.find_all("td")):
            continue  # 外層表格，內層會再輪到
        m = _ISO.search(str(td))
        if m:
            day = date.fromisoformat(m.group(0))
        else:
            prev = td.find_previous(string=_DAY_ONLY)
            if prev is None:
                continue
            day = date(year, month, int(prev.strip()))
        cells.append((day, text))
    return cells


def parse_calendar(html, site, year, month):
    hut_re = re.compile("|".join(map(re.escape, SITES[site]["huts"])))
    soup = BeautifulSoup(html, "html.parser")
    slots = []
    for day, raw in _day_cells(soup, year, month):
        text = re.sub(r"\s+", " ", raw)
        for m in _ENTRY.finditer(text):
            names = hut_re.findall(text, 0, m.start())
            if not names:
                continue
            hut, label = names[-1], m.group("label")
            unit = hut if label in _WHOLE_UNIT_LABELS else hut + label
            kind = "bed" if "床位" in label else "tent"
            slots.append(Slot(site, unit, kind, day, int(m.group("n")), int(m.group("m"))))
    return slots


def _months(today, count):
    y, m = today.year, today.month
    for _ in range(count):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _fetch_month(session, site, year, month, csrf):
    cfg = SITES[site]
    if cfg["nav"] == "post":
        r = session.post(cfg["url"], data={"year": year, "month": month, "csrf": csrf,
                                           "mode": "show_calendar"}, timeout=TIMEOUT)
    else:
        r = session.get(cfg["url"], params={"date_set[year]": year, "date_set[month]": month,
                                            "csrf": csrf}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


def fetch_site(site, today, months=3, session=None, sleep=time.sleep):
    """當月起 months 個月的 Slot。抓不到或解析出 0 筆就丟例外（0 筆 ≠ 全滿）。"""
    s = session or requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    r = s.get(SITES[site]["url"], timeout=TIMEOUT)
    r.raise_for_status()
    csrf = find_csrf(r.text)
    if not csrf:
        raise ValueError("找不到 csrf（網站可能改版）")
    slots = []
    for y, m in _months(today, months):
        sleep(REQUEST_GAP)
        slots += parse_calendar(_fetch_month(s, site, y, m, csrf), site, y, m)
    if not slots:
        raise ValueError("解析出 0 筆（網站可能改版）")
    return slots


def _is_blocked(exc):
    resp = getattr(exc, "response", None)
    return isinstance(exc, requests.HTTPError) and getattr(resp, "status_code", None) in (403, 429)


def fetch_all(today, sites=None, session_factory=requests.Session, sleep=time.sleep):
    """(slots, errors)。每站失敗重試一次（403/429 被擋不重試）；還是失敗就記在 errors[site]，不丟例外。"""
    slots, errors = [], {}
    for site in sites or AUTO_SITES:
        for attempt in (1, 2):
            try:
                slots += fetch_site(site, today, session=session_factory(), sleep=sleep)
                errors.pop(site, None)
                break
            except Exception as e:
                errors[site] = f"{type(e).__name__}: {e}"[:200]
                if _is_blocked(e):
                    break  # 被擋就停，不重試、不繞
                if attempt == 1:
                    sleep(REQUEST_GAP)
    return slots, errors
