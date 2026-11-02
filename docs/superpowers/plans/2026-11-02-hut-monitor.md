# 山屋名額監控 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每天 07:00／16:30／23:30 檢查熱門山屋未來 2 個月的空床，有變化寄 Gmail，LINE 傳「山屋」回最新摘要。

**Architecture:** 林業署三站（嘉明湖／天池／檜谷）在 Railway 用 requests 抓月曆；一站式（排雲、九九、七卡、奇萊、南湖）有 WAF，在使用者 Windows 上用 Playwright 真瀏覽器抓，POST 回 Railway。Railway 把兩邊結果併成一份快照、比對、寄信。

**Tech Stack:** Python 3.12、requests、BeautifulSoup、holidays、APScheduler、FastAPI、Playwright（只在本機）、pytest。

Spec：`docs/superpowers/specs/2026-11-02-hut-monitor-design.md`

## 紅線（每個 task 都適用）

- 不自動送出申請、不碰驗證碼、不登入、不存隊員個資。
- 不偽造 User-Agent、不裝 stealth、不換 IP。被擋（HTTP 4xx/5xx、`Request Rejected`）就停、記錯誤，不想辦法繞。
- 一天 3 次；同一站請求間隔 ≥ 2.5 秒，一站式地點間隔 4 秒。
- 不把 ADMIN_TOKEN 印到終端或寫進 repo。

## 環境前提

- 工作目錄：`C:\Users\acer\projects\ReportRobot-hut`（git worktree，分支 `feat/hut-monitor`）。
- `config.py` 被 .gitignore，已從主資料夾複製過來；基準 `python -m pytest -q` → 1630 passed。
- 樣本已存：`tests/fixtures/hut/jmlnt.html`（2026-11 月曆）、`tconline.html`（2026-11）、`kgonline.html`（首頁，只有 csrf）、`kgonline_cal.html`（ajax 回的 2026-12 月曆）。

## File Structure

| 檔案 | 責任 |
|---|---|
| `hut_sources.py`（新） | `Slot` 資料型別；林業署三站抓取＋解析 |
| `hut_onestop.py`（新） | 一站式月曆 HTML 解析（純函式，MIT 改寫） |
| `hut_calendar.py`（新） | 週末／連假判斷、未來 7 天抽籤／開放日 |
| `hut_info.py`（新） | 地點顯示名、官方查詢頁、交通卡、申請句子 |
| `hut_report.py`（新） | `Change`、重點判斷、信件與 LINE 文字 |
| `hut_monitor.py`（新） | 狀態（快照、失敗次數、ingest）、`run_check`、`ingest`、`line_reply` |
| `server.py`（改） | 3 個排程＋開機基準；`/admin/hut-ingest`、`/admin/hut-probe`、`/admin/hut-run` |
| `command_router.py`（改） | 私訊「山屋」 |
| `tools/hut_browser.py`（新） | 本機 Playwright 抓一站式、`--recon`、送回 Railway |
| `tools/hut_browser_local.ps1`、`tools/hut_browser_task.ps1`（新） | 本機執行與工作排程註冊 |
| `tests/test_hut_*.py`（新） | 各模組單元測試 |

---

### Task 1: `Slot` 與林業署月曆解析

**Files:**
- Create: `hut_sources.py`
- Test: `tests/test_hut_sources.py`

- [ ] **Step 1: Write the failing test**

`tests/test_hut_sources.py`：

```python
"""林業署三站月曆解析。樣本是 2026-11-02 實抓的頁面。"""

from datetime import date
from pathlib import Path

import hut_sources as hs

FIX = Path(__file__).parent / "fixtures" / "hut"


def _html(name):
    return (FIX / name).read_text(encoding="utf-8")


def _find(slots, unit, day):
    return next(s for s in slots if s.unit == unit and s.day == day)


def test_jmlnt_beds_and_campsite():
    slots = hs.parse_calendar(_html("jmlnt.html"), "jmlnt", 2026, 11)
    s = _find(slots, "嘉明湖山屋", date(2026, 11, 7))
    assert (s.site, s.kind, s.remaining, s.applicants) == ("jmlnt", "bed", 10, 202)
    camp = _find(slots, "嘉明湖營地", date(2026, 11, 7))
    assert (camp.kind, camp.remaining, camp.applicants) == ("tent", 0, 8)
    xy = _find(slots, "向陽山屋", date(2026, 11, 8))
    assert (xy.remaining, xy.applicants) == (22, 55)


def test_tconline_day_without_apply_button_still_parsed():
    """天池 11/6 已過申請期，沒有「線上申請」按鈕，日期要從格子裡別處拿。"""
    slots = hs.parse_calendar(_html("tconline.html"), "tconline", 2026, 11)
    s = _find(slots, "天池山莊", date(2026, 11, 6))
    assert (s.kind, s.remaining, s.applicants) == ("bed", 17, 12)
    assert _find(slots, "天池山莊", date(2026, 11, 7)).applicants == 301
    big = _find(slots, "周圍營地大營地4*4", date(2026, 11, 8))
    assert (big.kind, big.remaining) == ("tent", 3)
    assert _find(slots, "周圍營地小營地3*3", date(2026, 11, 6)).remaining == 18


def test_kgonline_ajax_calendar():
    slots = hs.parse_calendar(_html("kgonline_cal.html"), "kgonline", 2026, 12)
    s = _find(slots, "檜谷山莊", date(2026, 12, 5))
    assert (s.kind, s.remaining, s.applicants) == ("bed", 42, 52)
    tent = _find(slots, "周圍營地四人帳篷", date(2026, 12, 5))
    assert (tent.kind, tent.remaining, tent.applicants) == ("tent", 7, 3)


def test_all_days_in_requested_month():
    for name, site, month in (("jmlnt.html", "jmlnt", 11), ("tconline.html", "tconline", 11),
                              ("kgonline_cal.html", "kgonline", 12)):
        slots = hs.parse_calendar(_html(name), site, 2026, month)
        assert len(slots) > 20
        assert {s.day.month for s in slots} == {month}


def test_no_calendar_gives_no_slots():
    assert hs.parse_calendar(_html("kgonline.html"), "kgonline", 2026, 11) == []


def test_slot_dict_round_trip():
    s = hs.Slot("jmlnt", "嘉明湖山屋", "bed", date(2026, 12, 5), 3, None)
    assert hs.Slot.from_dict(s.to_dict()) == s
    assert s.key() == ("jmlnt", "嘉明湖山屋", "2026-12-05")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_hut_sources.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'hut_sources'`

- [ ] **Step 3: Write minimal implementation**

`hut_sources.py`：

```python
"""林業署山屋站（嘉明湖／天池／檜谷）月曆：抓頁面、解析成 Slot。

三站是同一套 PHP 系統，月曆格子寫法略有不同：
- 嘉明湖、檜谷：每天一個 <a id="tds_日">，格子內容包在裡面
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
    "jmlnt": {"name": "嘉明湖", "url": "https://jmlnt.forest.gov.tw/room/index.php",
              "nav": "get", "huts": ("嘉明湖山屋", "嘉明湖營地", "向陽山屋")},
    "tconline": {"name": "天池", "url": "https://tconline.forest.gov.tw/room/index.php",
                 "nav": "get", "huts": ("天池山莊", "周圍營地")},
    "kgonline": {"name": "檜谷", "url": "https://kgonline.forest.gov.tw/room/index.php",
                 "nav": "post", "huts": ("檜谷山莊", "周圍營地")},
}

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
        return [(date(year, month, int(a["id"][4:])), a.get_text(" ")) for a in anchors]
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_hut_sources.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add hut_sources.py tests/test_hut_sources.py tests/fixtures/hut/
git commit -m "山屋監控：林業署三站月曆解析（嘉明湖／天池／檜谷）"
```

---

### Task 2: 林業署抓取（換月份、重試、0 筆當錯誤）

**Files:**
- Modify: `hut_sources.py`（檔尾加函式）
- Test: `tests/test_hut_sources.py`（檔尾加測試）

- [ ] **Step 1: Write the failing test**

加到 `tests/test_hut_sources.py` 檔尾：

```python
class _Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Session:
    """index 頁給 csrf，月曆請求一律回同一份月曆樣本。"""

    def __init__(self, index, month, status=200):
        self.index, self.month, self.status = index, month, status
        self.headers, self.calls = {}, []

    def get(self, url, params=None, timeout=None):
        self.calls.append(("get", params))
        return _Resp(self.month if params else self.index, self.status)

    def post(self, url, data=None, timeout=None):
        self.calls.append(("post", data))
        return _Resp(self.month, self.status)


def test_fetch_site_get_navigation_and_csrf():
    jml = _html("jmlnt.html")
    sess = _Session(jml, jml)
    slots = hs.fetch_site("jmlnt", date(2026, 11, 2), session=sess, sleep=lambda s: None)
    months = [(p["date_set[year]"], p["date_set[month]"]) for kind, p in sess.calls if p]
    assert months == [(2026, 11), (2026, 12), (2027, 1)]
    assert all(p["csrf"] == "YXdkMjI2MDIwMjYtMTEtMDI=" for kind, p in sess.calls if p)
    assert sess.headers["User-Agent"] == hs.USER_AGENT
    assert len(slots) > 60


def test_fetch_site_kgonline_posts_ajax():
    sess = _Session(_html("kgonline.html"), _html("kgonline_cal.html"))
    hs.fetch_site("kgonline", date(2026, 12, 1), months=1, session=sess, sleep=lambda s: None)
    kind, data = sess.calls[-1]
    assert kind == "post"
    assert data["mode"] == "show_calendar" and (data["year"], data["month"]) == (2026, 12)


def test_fetch_all_zero_rows_and_http_error_become_errors():
    kg_index = _html("kgonline.html")
    bad = {"jmlnt": _Session("<html></html>", ""),                 # 沒 csrf
           "tconline": _Session(kg_index, "<html>改版</html>"),     # 0 筆
           "kgonline": _Session(kg_index, "", status=500)}
    calls = iter(["jmlnt", "jmlnt", "tconline", "tconline", "kgonline", "kgonline"])
    slots, errors = hs.fetch_all(date(2026, 11, 2),
                                 session_factory=lambda: bad[next(calls)],
                                 sleep=lambda s: None)
    assert slots == []
    assert set(errors) == {"jmlnt", "tconline", "kgonline"}
    assert "0 筆" in errors["tconline"]


def test_fetch_all_retry_once_then_ok():
    jml = _html("jmlnt.html")
    sessions = iter([_Session(jml, jml, status=503), _Session(jml, jml)])
    slots, errors = hs.fetch_all(date(2026, 11, 2), sites=["jmlnt"],
                                 session_factory=lambda: next(sessions),
                                 sleep=lambda s: None)
    assert errors == {} and slots
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_hut_sources.py -v`
Expected: 4 個新測試 FAIL，`AttributeError: module 'hut_sources' has no attribute 'fetch_site'`

- [ ] **Step 3: Write minimal implementation**

加到 `hut_sources.py` 檔尾：

```python
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


def fetch_all(today, sites=None, session_factory=requests.Session, sleep=time.sleep):
    """(slots, errors)。每站失敗重試一次；還是失敗就記在 errors[site]，不丟例外。"""
    slots, errors = [], {}
    for site in sites or SITES:
        for attempt in (1, 2):
            try:
                slots += fetch_site(site, today, session=session_factory(), sleep=sleep)
                errors.pop(site, None)
                break
            except Exception as e:
                errors[site] = f"{type(e).__name__}: {e}"[:200]
                if attempt == 1:
                    sleep(REQUEST_GAP)
    return slots, errors
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_hut_sources.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add hut_sources.py tests/test_hut_sources.py
git commit -m "山屋監控：林業署抓取（換月份、重試一次、0 筆當錯誤）"
```

---

### Task 3: 一站式月曆解析

**Files:**
- Create: `hut_onestop.py`
- Test: `tests/test_hut_onestop.py`

- [ ] **Step 1: Write the failing test**

`tests/test_hut_onestop.py`（樣本標記取自 hiiamyes/taiwan-hiking-form-filler 測試，MIT）：

```python
from datetime import date

import pytest

import hut_onestop as ho

SAMPLE = """
<table class="table_bed"><tbody><tr>
  <td data-th="星期三"><p>1</p><p>
    <a href="bed_6main.aspx?node_id=3&sdate=2026-07-01">
      <span>餘額</span> (<span>1</span>,<span>0</span>)<br>
      <span>排隊預約</span> <span>67</span><br>
      <span>審核中</span> <span>1</span><br>
      <span>核准入園</span> <span>110</span><br>
      <span>(<span>115</span>,<span>0</span>)</span>
    </a></p></td>
  <td data-th="星期四"><p>2</p><p>
    <a href="bed_6main.aspx?node_id=3&sdate=2026-07-02">
      <span>額滿</span><br>
      <span>排隊預約</span> <span>77</span><br>
      <span>核准入園</span> <span>111</span><br>
    </a></p></td>
</tr></tbody></table>
"""


def test_parse_remaining_and_full():
    slots = ho.parse_onestop_html(SAMPLE, "onestop_paiyun")
    assert [(s.day, s.remaining, s.applicants) for s in slots] == [
        (date(2026, 7, 1), 1, 67),
        (date(2026, 7, 2), 0, 77),
    ]
    assert {(s.site, s.unit, s.kind) for s in slots} == {("onestop_paiyun", "排雲山莊", "bed")}


def test_rejected_page_raises_blocked():
    html = "<html><title>Request Rejected</title>The requested URL was rejected.</html>"
    with pytest.raises(ho.Blocked):
        ho.parse_onestop_html(html, "onestop_paiyun")
    with pytest.raises(ho.Blocked):
        ho.ensure_not_blocked(html)


def test_every_target_has_page_and_label():
    for site, (path, label) in ho.ONESTOP.items():
        assert site.startswith("onestop_") and path.startswith("bed_") and label
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_hut_onestop.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'hut_onestop'`

- [ ] **Step 3: Write minimal implementation**

`hut_onestop.py`：

```python
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
    "onestop_99": ("bed_0.aspx", "九九山莊"),
    "onestop_7ka": ("bed_1.aspx", "七卡山莊"),
    "onestop_cilai": ("bed_4.aspx", "奇萊山屋"),
    "onestop_nanhu": ("bed_4.aspx", "南湖山屋"),
}
REJECTED_MARK = "Request Rejected"

_SDATE = re.compile(r"sdate=(\d{4}-\d{2}-\d{2})")
_REMAIN = re.compile(r"餘額\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)")
_QUEUE = re.compile(r"排隊預約\s*(\d+)")


class Blocked(Exception):
    """網站 WAF 擋下。看到就停，不繞。"""


def ensure_not_blocked(html):
    if REJECTED_MARK in (html or ""):
        raise Blocked("一站式回 Request Rejected")


def parse_onestop_html(html, site):
    ensure_not_blocked(html)
    unit = ONESTOP[site][1]
    soup = BeautifulSoup(html, "html.parser")
    slots = []
    for a in soup.select("table.table_bed a[href*='sdate=']"):
        day = date.fromisoformat(_SDATE.search(a["href"]).group(1))
        text = re.sub(r"\s+", " ", a.get_text(" "))
        rm = _REMAIN.search(text)
        if rm:
            remaining = int(rm.group(1))
        elif "額滿" in text:
            remaining = 0
        else:
            remaining = None
        q = _QUEUE.search(text)
        slots.append(Slot(site, unit, "bed", day, remaining, int(q.group(1)) if q else None))
    return slots
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_hut_onestop.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add hut_onestop.py tests/test_hut_onestop.py
git commit -m "山屋監控：一站式月曆解析（改寫自 hiiamyes，MIT）"
```

---

### Task 4: 週末／連假與抽籤開放日

**Files:**
- Create: `hut_calendar.py`
- Test: `tests/test_hut_calendar.py`

- [ ] **Step 1: Write the failing test**

`tests/test_hut_calendar.py`：

```python
from datetime import date

import hut_calendar as hc

TODAY = date(2026, 11, 2)  # 週一


def test_good_night_is_night_before_day_off():
    assert hc.is_good_night(date(2026, 11, 6))       # 週五晚 → 隔天週六
    assert hc.is_good_night(date(2026, 11, 7))       # 週六晚 → 隔天週日
    assert not hc.is_good_night(date(2026, 11, 8))   # 週日晚 → 隔天上班
    assert not hc.is_good_night(date(2026, 11, 4))
    assert hc.is_good_night(date(2026, 12, 31))      # 隔天元旦


def test_minus_months():
    assert hc.minus_months(date(2026, 11, 1), 2) == date(2026, 9, 1)
    assert hc.minus_months(date(2026, 1, 15), 2) == date(2025, 11, 15)
    assert hc.minus_months(date(2026, 4, 29), 2) == date(2026, 3, 1)  # 2/29 不存在 → 3/1
    assert hc.minus_months(date(2026, 12, 31), 1) == date(2026, 12, 1)  # 11/31 → 12/1


def test_fmt_day():
    assert hc.fmt_day(date(2026, 12, 5)) == "12/5(六)"


def test_upcoming_events():
    events = hc.upcoming_events(TODAY)
    # 嘉明湖住宿前 30 天抽：12/5(六) 那晚 → 11/5 抽
    assert (date(2026, 11, 5), "嘉明湖／向陽", date(2026, 12, 5), "15:00 抽籤（16:00 公布）") in events
    # 九九／雪霸／太魯閣前 2 個月 07:00 開放：2027/1/2(六) → 11/2
    assert any(e[:3] == (date(2026, 11, 2), "九九／雪霸／太魯閣", date(2027, 1, 2)) for e in events)
    assert all(TODAY <= e[0] < date(2026, 11, 9) for e in events)
    assert all(hc.is_good_night(e[2]) for e in events)
    assert events == sorted(events)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_hut_calendar.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'hut_calendar'`

- [ ] **Step 3: Write minimal implementation**

`hut_calendar.py`：

```python
"""山屋日期：哪些晚上值得搶（隔天放假）、未來幾天有哪些抽籤／開放日。

規則來源：~/.claude/skills/hut-apply/SKILL.md（2026-10-25 讀過各機關須知）。
排雲的實際抽籤日會因假日順延，這裡只算開放申請日，抽籤日請查 bed_8.aspx。
"""

from datetime import date, timedelta

import holidays

WEEKDAY = "一二三四五六日"


def is_day_off(d):
    return d.weekday() >= 5 or d in holidays.Taiwan(years=d.year)


def is_good_night(d):
    """住宿日 d 那晚隔天放假（週五、週六、連假前一晚）。"""
    return is_day_off(d + timedelta(days=1))


def fmt_day(d):
    return f"{d.month}/{d.day}({WEEKDAY[d.weekday()]})"


def minus_months(d, n):
    """同日往前推 n 個月；那天不存在就取下個月 1 號（2/29 → 3/1）。"""
    y, m = d.year, d.month - n
    while m <= 0:
        m += 12
        y -= 1
    try:
        return date(y, m, d.day)
    except ValueError:
        return date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)


# (地點, 事件, 住宿日 → 事件日)
RULES = (
    ("嘉明湖／向陽", "15:00 抽籤（16:00 公布）", lambda d: d - timedelta(days=30)),
    ("檜谷", "15:00 抽籤（15:30 公布）", lambda d: d - timedelta(days=30)),
    ("天池", "開放申請（先到先得）", lambda d: d - timedelta(days=60)),
    ("排雲", "開放申請（抽籤制，抽籤日查 bed_8.aspx）", lambda d: minus_months(d, 2)),
    ("九九／雪霸／太魯閣", "07:00 開放申請（先到先得）", lambda d: minus_months(d, 2)),
    ("眠月線", "開放申請", lambda d: d - timedelta(days=60)),
)


def upcoming_events(today, days=7):
    """[(事件日, 地點, 住宿日, 事件)]，只列隔天放假的住宿日，依日期排序。"""
    end = today + timedelta(days=days)
    out = []
    for offset in range(1, 80):
        night = today + timedelta(days=offset)
        if not is_good_night(night):
            continue
        for place, what, when in RULES:
            e = when(night)
            if today <= e < end:
                out.append((e, place, night, what))
    return sorted(out)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_hut_calendar.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add hut_calendar.py tests/test_hut_calendar.py
git commit -m "山屋監控：週末連假判斷與未來 7 天抽籤／開放日"
```

---

### Task 5: 地點資訊、交通卡、申請句子

**Files:**
- Create: `hut_info.py`
- Test: `tests/test_hut_info.py`

- [ ] **Step 1: Write the failing test**

`tests/test_hut_info.py`：

```python
from datetime import date

import hut_info as hi
import hut_onestop
import hut_sources
from hut_sources import Slot


def test_every_site_has_place_and_query_url():
    for site in list(hut_sources.SITES) + list(hut_onestop.ONESTOP):
        assert hi.place(site)
        assert hi.QUERY_URL[site].startswith("https://")


def test_every_place_has_transport_card():
    for site in list(hut_sources.SITES) + list(hut_onestop.ONESTOP):
        assert hi.transport_card(hi.place(site))


def test_unverified_card_warns():
    card = hi.transport_card("嘉明湖／向陽")
    assert "尚未核對官方資料" in card


def test_display_and_apply_phrase():
    s = Slot("tconline", "周圍營地大營地4*4", "tent", date(2026, 12, 5), 3, 0)
    assert hi.display(s) == "天池 周圍營地大營地4*4"
    b = Slot("jmlnt", "嘉明湖山屋", "bed", date(2026, 12, 5), 4, 9)
    assert hi.display(b) == "嘉明湖山屋"
    assert hi.apply_phrase(b) == "用 hut-apply 申請 嘉明湖山屋 2026-12-05 2人"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_hut_info.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'hut_info'`

- [ ] **Step 3: Write minimal implementation**

`hut_info.py`（交通卡內容是 2026-11-02 從部落格／舊報導整理的線索，`checked` 為 None 時信裡會警告；Task 13 逐一到官方頁面核對後填日期）：

```python
"""山屋地點的固定資料：顯示名、官方查詢頁、交通卡、給 Claude 的申請句子。"""

import hut_onestop
import hut_sources

PLACE = {
    "jmlnt": "嘉明湖／向陽",
    "tconline": "天池",
    "kgonline": "檜谷",
    "onestop_paiyun": "排雲",
    "onestop_99": "九九",
    "onestop_7ka": "雪霸",
    "onestop_cilai": "奇萊",
    "onestop_nanhu": "南湖",
}

QUERY_URL = {
    **{site: cfg["url"] for site, cfg in hut_sources.SITES.items()},
    **{site: hut_onestop.BASE + path for site, (path, _) in hut_onestop.ONESTOP.items()},
}

# text：交通重點；source：要去核對的官方頁；checked：核對日期（None = 還沒核對）
TRANSPORT = {
    "嘉明湖／向陽": {
        "text": "登山口：向陽國家森林遊樂區。自駕台 9 線 → 池上 → 台 20 線南橫。無定期大眾運輸，多為池上／台東包車。南橫東段（利稻—向陽）路況洽關山工務段 089-811024。",
        "source": "https://jmlnt.forest.gov.tw/", "checked": None},
    "天池": {
        "text": "登山口：屯原（南投仁愛）。埔里包車為主。屯原服務站 07:00–17:00 登記。",
        "source": "https://tconline.forest.gov.tw/", "checked": None},
    "檜谷": {
        "text": "登山口：北大武（屏東泰武）。無大眾運輸，自駕或包車；出發前確認新舊登山口與步道是否開放。",
        "source": "https://kgonline.forest.gov.tw/", "checked": None},
    "排雲": {
        "text": "登山口：塔塔加。員林客運 6739（日月潭—阿里山）停塔塔加，班次少；上東埔停車場到登山口 2.8 km，東埔山莊有接駁。自駕國 3 名間交流道 → 水里 → 新中橫。",
        "source": "https://www.ysnp.gov.tw/", "checked": None},
    "九九": {
        "text": "登山口：雪山登山口（武陵農場）。國光客運 1751、豐原客運 865 往武陵。台 7 甲思源—梨山段單車道。",
        "source": "https://www.spnp.gov.tw/", "checked": None},
    "雪霸": {
        "text": "登山口：雪山登山口（武陵農場），到七卡山莊 2 km。國光客運 1751、豐原客運 865 往武陵。",
        "source": "https://www.spnp.gov.tw/", "checked": None},
    "奇萊": {
        "text": "登山口：合歡山滑雪山莊附近（台 14 甲）。交通待核對。",
        "source": "https://www.taroko.gov.tw/", "checked": None},
    "南湖": {
        "text": "登山口：勝光／思源埡口（台 7 甲）。交通待核對。",
        "source": "https://www.taroko.gov.tw/", "checked": None},
}


def place(site):
    return PLACE.get(site, site)


def display(slot):
    """周圍營地這種各站都有的名字前面加地點。"""
    if slot.unit.startswith("周圍"):
        return f"{place(slot.site)} {slot.unit}"
    return slot.unit


def transport_card(place_name):
    t = TRANSPORT.get(place_name)
    if not t:
        return ""
    if t["checked"]:
        return f"{place_name}：{t['text']}（{t['checked']} 核對）"
    return f"{place_name}：{t['text']}\n⚠️ 尚未核對官方資料，出發前請查：{t['source']}"


def apply_phrase(slot):
    return f"用 hut-apply 申請 {display(slot)} {slot.day.isoformat()} 2人"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_hut_info.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add hut_info.py tests/test_hut_info.py
git commit -m "山屋監控：地點資訊、交通卡（待核對）、申請句子"
```

---

### Task 6: 信件與 LINE 文字

**Files:**
- Create: `hut_report.py`
- Test: `tests/test_hut_report.py`

- [ ] **Step 1: Write the failing test**

`tests/test_hut_report.py`：

```python
from datetime import date, datetime, timedelta, timezone

import hut_report as hr
from hut_sources import Slot

TODAY = date(2026, 11, 2)
SAT = date(2026, 12, 5)   # 週六晚 → 隔天週日
WED = date(2026, 12, 2)


def _c(unit, day, remaining, before, kind="bed", site="jmlnt", applicants=10):
    return hr.Change(Slot(site, unit, kind, day, remaining, applicants), before)


def test_highlight_needs_good_night_and_enough_space():
    assert hr.is_highlight(_c("嘉明湖山屋", SAT, 2, 0))
    assert not hr.is_highlight(_c("嘉明湖山屋", SAT, 1, 0))          # 床位要 ≥ 2
    assert hr.is_highlight(_c("嘉明湖營地", SAT, 1, 0, kind="tent"))  # 一頂帳 2 人
    assert not hr.is_highlight(_c("嘉明湖山屋", WED, 5, 0))           # 平日


def test_email_with_highlight():
    changes = [_c("嘉明湖山屋", SAT, 4, 0), _c("向陽山屋", WED, 3, None)]
    events = [(TODAY, "九九／雪霸／太魯閣", date(2027, 1, 2), "07:00 開放申請（先到先得）")]
    subject, text = hr.build_email(changes, events, {"onestop_99": "本次沒查到（電腦沒開）"}, TODAY)
    assert subject == "山屋：嘉明湖山屋 12/5(六) 釋出 4床"
    assert "嘉明湖山屋 12/5(六) 剩 0→4床（報名 10）" in text
    assert "向陽山屋 12/2(三) 剩 3床（新）（報名 10）" in text
    assert "用 hut-apply 申請 嘉明湖山屋 2026-12-05 2人" in text
    assert "11/2(一) 九九／雪霸／太魯閣：1/2(六) 住宿 07:00 開放申請（先到先得）" in text
    assert "嘉明湖／向陽：" in text            # 重點地點附交通卡
    assert "九九：本次沒查到（電腦沒開）" in text
    assert text.index("<b>重點") < text.index("<b>全部變化")


def test_email_subject_without_highlight_or_changes():
    subject, _ = hr.build_email([_c("向陽山屋", WED, 3, None)], [], {}, TODAY)
    assert subject == "山屋：1 筆空位變化"
    events = [(TODAY, "天池", date(2026, 12, 31), "開放申請（先到先得）")]
    subject, text = hr.build_email([], events, {}, TODAY)
    assert subject == "山屋：今天 1 個抽籤／開放日"
    assert "<b>重點" not in text


def test_line_summary():
    checked = datetime(2026, 11, 2, 16, 30, tzinfo=timezone(timedelta(hours=8)))
    slots = [Slot("jmlnt", "嘉明湖山屋", "bed", SAT, 4, 9),
             Slot("jmlnt", "嘉明湖山屋", "bed", WED, 9, 9),       # 平日不列
             Slot("tconline", "天池山莊", "bed", SAT, 1, 9)]      # 不夠 2 人
    text = hr.line_summary(slots, checked, {"kgonline": "HTTP 500"})
    assert text.startswith("山屋（11/02 16:30 查）")
    assert "嘉明湖山屋：12/5(六) 4床" in text
    assert "天池" not in text.split("沒查到")[0]
    assert "檜谷" in text
    assert text.endswith("完整內容看 Gmail")


def test_line_summary_none():
    checked = datetime(2026, 11, 2, 7, 0, tzinfo=timezone(timedelta(hours=8)))
    assert "目前沒有週末／連假夠 2 人的空位" in hr.line_summary([], checked, {})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_hut_report.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'hut_report'`

- [ ] **Step 3: Write minimal implementation**

`hut_report.py`：

```python
"""山屋監控的輸出文字：Gmail 信（純文字＋<b>，給 mailer.to_html）與 LINE 回覆。"""

from dataclasses import dataclass

import hut_info
from hut_calendar import fmt_day, is_good_night
from hut_sources import Slot

BED_MIN = 2   # 2 人
TENT_MIN = 1  # 一頂帳 2 人
LINE_MAX_PER_UNIT = 5


@dataclass(frozen=True)
class Change:
    slot: Slot
    before: int | None  # 上次的剩餘；None = 上次沒有這筆


def _enough(slot):
    need = BED_MIN if slot.kind == "bed" else TENT_MIN
    return (slot.remaining or 0) >= need


def is_highlight(change):
    return is_good_night(change.slot.day) and _enough(change.slot)


def _word(slot):
    return "床" if slot.kind == "bed" else "帳"


def _change_line(c):
    s = c.slot
    amount = f"{s.remaining}{_word(s)}（新）" if c.before is None else f"{c.before}→{s.remaining}{_word(s)}"
    return f"{hut_info.display(s)} {fmt_day(s.day)} 剩 {amount}（報名 {s.applicants}）"


def build_email(changes, events, errors, today):
    """(subject, text)。text 用 <b> 標題，交給 mailer.to_html 轉 HTML。"""
    highlights = sorted((c for c in changes if is_highlight(c)), key=lambda c: c.slot.day)
    events_today = [e for e in events if e[0] == today]
    if highlights:
        h = highlights[0].slot
        subject = f"山屋：{hut_info.display(h)} {fmt_day(h.day)} 釋出 {h.remaining}{_word(h)}"
        if len(highlights) > 1:
            subject += f" 等 {len(highlights)} 筆"
    elif changes:
        subject = f"山屋：{len(changes)} 筆空位變化"
    else:
        subject = f"山屋：今天 {len(events_today)} 個抽籤／開放日"

    parts = []
    if highlights:
        lines = []
        for c in highlights:
            lines += [_change_line(c), f"  → {hut_info.apply_phrase(c.slot)}"]
        parts.append("<b>重點（週末／連假、夠 2 人）</b>\n" + "\n".join(lines))
    if changes:
        ordered = sorted(changes, key=lambda c: (c.slot.site, c.slot.unit, c.slot.day))
        parts.append("<b>全部變化</b>\n" + "\n".join(_change_line(c) for c in ordered))
    if events:
        parts.append("<b>未來 7 天抽籤／開放日</b>\n" + "\n".join(
            f"{fmt_day(e)} {place}：{fmt_day(night)} 住宿 {what}" for e, place, night, what in events))
    places = list(dict.fromkeys(hut_info.place(c.slot.site) for c in highlights))
    if places:
        parts.append("<b>交通</b>\n" + "\n".join(hut_info.transport_card(p) for p in places))
    if errors:
        parts.append("<b>沒查到</b>\n" + "\n".join(
            f"{hut_info.place(site)}：{msg}" for site, msg in sorted(errors.items())))
    sites = list(dict.fromkeys(c.slot.site for c in changes))
    if sites:
        parts.append("<b>查詢頁</b>\n" + "\n".join(
            f"{hut_info.place(s)}：{hut_info.QUERY_URL[s]}" for s in sites))
    return subject, "\n\n".join(parts)


def line_summary(slots, checked_at, errors):
    """slots 已由呼叫端篩過「在監控範圍內」。列週末／連假夠 2 人的日子。"""
    lines = [f"山屋（{checked_at:%m/%d %H:%M} 查）"]
    by_unit = {}
    for s in sorted(slots, key=lambda s: s.day):
        if is_good_night(s.day) and _enough(s):
            by_unit.setdefault(hut_info.display(s), []).append(s)
    if by_unit:
        for unit, ss in by_unit.items():
            days = "、".join(f"{fmt_day(s.day)} {s.remaining}{_word(s)}" for s in ss[:LINE_MAX_PER_UNIT])
            lines.append(f"{unit}：{days}")
    else:
        lines.append("目前沒有週末／連假夠 2 人的空位")
    if errors:
        lines.append("沒查到：" + "、".join(hut_info.place(s) for s in sorted(errors)))
    lines.append("完整內容看 Gmail")
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_hut_report.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add hut_report.py tests/test_hut_report.py
git commit -m "山屋監控：信件與 LINE 摘要文字"
```

---

### Task 7: 監控核心（快照比對、ingest、失敗通知）

**Files:**
- Create: `hut_monitor.py`
- Test: `tests/test_hut_monitor.py`

- [ ] **Step 1: Write the failing test**

`tests/test_hut_monitor.py`：

```python
from datetime import date, datetime, timedelta, timezone

import pytest

import hut_monitor as hm
from hut_sources import Slot

TPE = timezone(timedelta(hours=8))
NOW = datetime(2026, 11, 2, 16, 30, tzinfo=TPE)
TODAY = NOW.date()
SAT = date(2026, 12, 5)   # 33 天後；先到先得在範圍內，抽籤制（≤30 天）不在
FRI = date(2026, 11, 27)  # 25 天後


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    hm.reset()
    monkeypatch.delenv("HUT_FOREST_ON_SERVER", raising=False)
    yield
    hm.reset()


def _run(slots, errors=None, now=NOW, **kw):
    sent, alerts = [], []
    out = hm.run_check(now=now, fetch=lambda today: (list(slots), dict(errors or {})),
                       send=lambda subject, text: sent.append((subject, text)) or True,
                       notify=lambda err, ctx: alerts.append(ctx["section"]), **kw)
    return out, sent, alerts


def _s(site, unit, day, remaining, kind="bed"):
    return Slot(site, unit, kind, day, remaining, 5)


def test_watchable_window():
    assert hm.watchable(_s("tconline", "天池山莊", SAT, 1), TODAY)       # 先到先得 33 天
    assert not hm.watchable(_s("jmlnt", "嘉明湖山屋", SAT, 1), TODAY)    # 抽籤制要 ≤30 天
    assert hm.watchable(_s("jmlnt", "嘉明湖山屋", FRI, 1), TODAY)
    assert not hm.watchable(_s("tconline", "天池山莊", date(2026, 11, 5), 1), TODAY)  # <5 天
    assert not hm.watchable(_s("tconline", "天池山莊", date(2027, 1, 10), 1), TODAY)  # >61 天


def test_first_run_is_baseline_then_increase_is_change():
    out, sent, _ = _run([_s("tconline", "天池山莊", SAT, 0)])
    assert out["changes"] == 0 and sent == []
    out, sent, _ = _run([_s("tconline", "天池山莊", SAT, 3)])
    assert out["changes"] == 1
    assert sent[0][0] == "山屋：天池山莊 12/5(六) 釋出 3床"


def test_decrease_and_zero_are_not_changes():
    _run([_s("tconline", "天池山莊", SAT, 5)])
    out, sent, _ = _run([_s("tconline", "天池山莊", SAT, 2)])
    assert out["changes"] == 0 and sent == []


def test_date_entering_window_with_known_value_is_not_new():
    """抽籤制的日期進入 30 天範圍時，上次已有值就照常比，不算「新」。"""
    day = date(2026, 12, 3)  # 31 天後，不在嘉明湖範圍
    _run([_s("jmlnt", "嘉明湖山屋", day, 4)])
    tomorrow = NOW + timedelta(days=1)  # 變 30 天，進範圍
    out, _, _ = _run([_s("jmlnt", "嘉明湖山屋", day, 4)], now=tomorrow)
    assert out["changes"] == 0


def test_failed_site_keeps_old_snapshot_and_alerts_on_third():
    _run([_s("tconline", "天池山莊", SAT, 0)])
    for i in range(3):
        _, _, alerts = _run([], {"tconline": "HTTP 500"})
    assert alerts == ["tconline"]
    out, _, _ = _run([_s("tconline", "天池山莊", SAT, 2)])
    assert out["changes"] == 1  # 舊快照 0 還在，2 算變多


def test_onestop_missing_is_error_and_fresh_ingest_is_used():
    out, _, _ = _run([])
    assert out["errors"]["onestop_paiyun"] == "本次沒查到（電腦沒開）"
    payload = {"slots": [_s("onestop_99", "九九山莊", SAT, 0).to_dict()],
               "errors": {"onestop_paiyun": "被網站擋下（Request Rejected），這次停止"}}
    hm.ingest(payload, now=NOW)
    out, _, _ = _run([])
    assert "onestop_99" not in out["errors"]
    assert out["errors"]["onestop_paiyun"].startswith("被網站擋下")
    hm.ingest({"slots": [_s("onestop_99", "九九山莊", SAT, 4).to_dict()]}, now=NOW)
    out, _, _ = _run([])
    assert out["changes"] == 1


def test_pc_off_never_alerts():
    for i in range(4):
        _, _, alerts = _run([])
        assert alerts == []


def test_stale_ingest_ignored():
    hm.ingest({"slots": [_s("onestop_99", "九九山莊", SAT, 4).to_dict()]},
              now=NOW - timedelta(hours=2))
    out, _, _ = _run([])
    assert out["errors"]["onestop_99"] == "本次沒查到（電腦沒開）"


def test_ingest_rejects_unknown_site():
    with pytest.raises(ValueError):
        hm.ingest({"slots": [_s("evil", "x", SAT, 1).to_dict()]}, now=NOW)


def test_forest_off_server_expects_ingest(monkeypatch):
    monkeypatch.setenv("HUT_FOREST_ON_SERVER", "0")
    called = []
    hm.run_check(now=NOW, fetch=lambda today: called.append(1) or ([], {}),
                 send=lambda s, t: True, notify=lambda e, c: None)
    assert called == []
    assert "jmlnt" in hm.status()["errors"]


def test_events_mail_only_when_asked():
    _, sent, _ = _run([])
    assert sent == []
    _, sent, _ = _run([], send_events_today=True)
    assert sent and sent[0][0].startswith("山屋：今天")


def test_line_reply():
    assert "還沒查過" in hm.line_reply(today=TODAY)
    _run([_s("tconline", "天池山莊", SAT, 4)])
    text = hm.line_reply(today=TODAY)
    assert "天池山莊：12/5(六) 4床" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_hut_monitor.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'hut_monitor'`

- [ ] **Step 3: Write minimal implementation**

`hut_monitor.py`：

```python
"""山屋名額監控：抓 → 跟上次比 → 有變化寄 Gmail；LINE「山屋」回最新摘要。

狀態只放記憶體。Railway 重新部署後第一次檢查當基準、不寄信，最多漏一次變化。
一站式由本機 tools/hut_browser.py 用真瀏覽器抓，POST /admin/hut-ingest 送進來，
60 分鐘內的才算數（本機比伺服器早 10 分鐘跑）。
HUT_FOREST_ON_SERVER=0 時林業署三站也改由本機送（Railway 國外 IP 被擋時的備案）。

設計：docs/superpowers/specs/2026-11-02-hut-monitor-design.md
"""

import os
import threading
from datetime import timedelta

import hut_calendar
import hut_onestop
import hut_report
import hut_sources
from hut_sources import Slot
from tz_utils import now_tpe, today_tpe

KEYWORDS = {"山屋", "山屋空位"}
INGEST_FRESH = timedelta(minutes=60)
FAIL_ALERT = 3
LOTTERY_SITES = {"jmlnt", "kgonline", "onestop_paiyun"}  # 抽籤前的「剩餘」只是還沒分配
MIN_AHEAD, LOTTERY_MAX_AHEAD, MAX_AHEAD = 5, 30, 61
MISSING = "本次沒查到（電腦沒開）"

_LOCK = threading.Lock()
_snapshot = {}       # Slot.key() -> remaining
_baselined = set()   # 有過快照的 site；第一次只記不報
_fails = {}          # site -> 連續失敗次數
_latest = {}         # site -> [Slot]（給 LINE 查詢；失敗的站保留上次的）
_ingest = {}         # site -> (收到時間, [Slot])
_ingest_errors = {}  # site -> (收到時間, 訊息)
_status = {"checked_at": None, "errors": {}}


def reset():
    """測試用。"""
    with _LOCK:
        for d in (_snapshot, _fails, _latest, _ingest, _ingest_errors):
            d.clear()
        _baselined.clear()
        _status.update(checked_at=None, errors={})


def status():
    with _LOCK:
        return {"checked_at": _status["checked_at"], "errors": dict(_status["errors"])}


def watchable(slot, today):
    ahead = (slot.day - today).days
    limit = LOTTERY_MAX_AHEAD if slot.site in LOTTERY_SITES else MAX_AHEAD
    return MIN_AHEAD <= ahead <= limit


def _known_sites():
    return set(hut_sources.SITES) | set(hut_onestop.ONESTOP)


def _forest_on_server():
    return os.environ.get("HUT_FOREST_ON_SERVER", "1") != "0"


def ingest(payload, now=None):
    """本機送來的 {"slots": [Slot.to_dict()], "errors": {site: 訊息}}。"""
    now = now or now_tpe()
    slots = [Slot.from_dict(d) for d in payload.get("slots") or []]
    errors = {str(k): str(v)[:200] for k, v in (payload.get("errors") or {}).items()}
    unknown = ({s.site for s in slots} | set(errors)) - _known_sites()
    if unknown:
        raise ValueError(f"不認得的 site：{sorted(unknown)}")
    by_site = {}
    for s in slots:
        by_site.setdefault(s.site, []).append(s)
    with _LOCK:
        for site, ss in by_site.items():
            _ingest[site] = (now, ss)
        for site, msg in errors.items():
            _ingest_errors[site] = (now, msg)
    return {"slots": len(slots), "sites": sorted(by_site), "errors": sorted(errors)}


def _merge_ingest(now, forest_on_server):
    expected = list(hut_onestop.ONESTOP) + ([] if forest_on_server else list(hut_sources.SITES))
    slots, errors = [], {}
    for site in expected:
        got = _ingest.get(site)
        if got and now - got[0] <= INGEST_FRESH:
            slots += got[1]
            continue
        err = _ingest_errors.get(site)
        errors[site] = err[1] if err and now - err[0] <= INGEST_FRESH else MISSING
    return slots, errors


def _diff(slots, today):
    changes = []
    for s in slots:
        if s.site not in _baselined or not watchable(s, today):
            continue
        if s.remaining is None or s.remaining <= 0:
            continue
        before = _snapshot.get(s.key())
        if before is None or s.remaining > before:
            changes.append(hut_report.Change(s, before))
    return changes


def _send_email(subject, text):
    import mailer
    return mailer.send_email(subject, text, html=mailer.to_html(text))


def _notify(err, ctx):
    from admin_notify import notify_admin
    notify_admin(err, ctx)


def run_check(send_events_today=False, now=None, fetch=None, send=None, notify=None):
    now = now or now_tpe()
    today = now.date()
    fetch = fetch or hut_sources.fetch_all
    send = send or _send_email
    notify = notify or _notify

    on_server = _forest_on_server()
    slots, errors = fetch(today) if on_server else ([], {})
    with _LOCK:
        in_slots, in_errors = _merge_ingest(now, on_server)
        slots = slots + in_slots
        errors = {**errors, **in_errors}
        ok_sites = {s.site for s in slots}
        changes = _diff(slots, today)
        for s in slots:
            _snapshot[s.key()] = s.remaining
        _baselined.update(ok_sites)
        for site in ok_sites:
            _fails[site] = 0
            _latest[site] = [s for s in slots if s.site == site]
        alerts = []
        for site in errors:
            _fails[site] = _fails.get(site, 0) + 1
            # 電腦沒開不是故障，不推 LINE（5 個一站式地點會各推一次，吃額度）
            if _fails[site] == FAIL_ALERT and errors[site] != MISSING:
                alerts.append(site)
        _status.update(checked_at=now, errors=dict(errors))

    for site in alerts:
        notify(RuntimeError(f"{site} 連續 {FAIL_ALERT} 次沒查到：{errors[site]}"),
               {"module": "hut_monitor", "section": site})

    events = hut_calendar.upcoming_events(today)
    has_events_today = any(e[0] == today for e in events)
    sent = False
    if changes or (send_events_today and has_events_today):
        subject, text = hut_report.build_email(changes, events, errors, today)
        sent = bool(send(subject, text))
    return {"changes": len(changes), "errors": errors, "sent": sent}


def line_reply(today=None):
    today = today or today_tpe()
    with _LOCK:
        checked_at = _status["checked_at"]
        errors = dict(_status["errors"])
        slots = [s for ss in _latest.values() for s in ss if watchable(s, today)]
    if checked_at is None:
        return "山屋還沒查過，下一次 07:00／16:30／23:30 會查。"
    return hut_report.line_summary(slots, checked_at, errors)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_hut_monitor.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add hut_monitor.py tests/test_hut_monitor.py
git commit -m "山屋監控：快照比對、本機 ingest、連續失敗通知"
```

---

### Task 8: 接上 server（排程＋三個 admin 端點）

**Files:**
- Modify: `server.py`（`scheduler.start()` 前加排程；`/admin/run-daily` 之後加端點）
- Test: `tests/test_hut_server.py`

- [ ] **Step 1: Write the failing test**

`tests/test_hut_server.py`：

```python
from datetime import date

import pytest
from fastapi.testclient import TestClient

import hut_monitor
import server
from hut_sources import Slot


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "t0k")
    hut_monitor.reset()
    yield TestClient(server.app)
    hut_monitor.reset()


def _payload():
    return {"slots": [Slot("onestop_99", "九九山莊", "bed", date(2026, 12, 5), 2, 7).to_dict()]}


def test_ingest_requires_token(client):
    assert client.post("/admin/hut-ingest", json=_payload()).status_code == 403


def test_ingest_ok(client):
    r = client.post("/admin/hut-ingest", json=_payload(), headers={"X-Admin-Token": "t0k"})
    assert r.status_code == 200 and r.json()["sites"] == ["onestop_99"]


def test_ingest_bad_site_400(client):
    bad = {"slots": [dict(_payload()["slots"][0], site="evil")]}
    r = client.post("/admin/hut-ingest", json=bad, headers={"X-Admin-Token": "t0k"})
    assert r.status_code == 400


def test_probe_and_run_require_token(client):
    assert client.post("/admin/hut-probe").status_code == 403
    assert client.post("/admin/hut-run").status_code == 403
```

注意：`TestClient(server.app)` 不帶 `with`，不會跑 lifespan（不啟動排程）。

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_hut_server.py -v`
Expected: FAIL，ingest 回 404 而不是 403／200

- [ ] **Step 3: Write minimal implementation**

`server.py`：在 lifespan 裡 `scheduler.start()` 那行**之前**加入（縮排 4 格，跟上面的 `scheduler.add_job(_run_owntracks_nightly, ...)` 對齊）：

```python
    # 山屋名額監控：07:00／16:30／23:30（docs/superpowers/specs/2026-11-02-hut-monitor-design.md）
    # 07:00 那班就算沒有空位變化，今天有抽籤／開放日也寄。開機 3 分鐘後先跑一次當基準（不寄信）。
    def _run_hut_check(send_events_today=False):
        try:
            import hut_monitor
            print(f"[hut] {hut_monitor.run_check(send_events_today=send_events_today)}")
        except Exception as e:
            print(f"[hut] 檢查失敗（非致命）：{e}")

    scheduler.add_job(
        _run_hut_check,
        DateTrigger(run_date=datetime.now(timezone.utc) + timedelta(minutes=3)),
        id="hut_check_boot",
        replace_existing=True,
    )
    for _hour, _minute, _events in ((7, 0, True), (16, 30, False), (23, 30, False)):
        scheduler.add_job(
            _run_hut_check,
            CronTrigger(hour=_hour, minute=_minute, timezone="Asia/Taipei"),
            kwargs={"send_events_today": _events},
            id=f"hut_check_{_hour:02d}{_minute:02d}",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=1800,
            replace_existing=True,
        )
```

`server.py`：在 `/admin/run-daily` 函式（`trigger_daily`）結束、`@app.post("/admin/run-personal")` 之前加入：

```python
def _check_admin(request: Request):
    admin_token = os.environ.get("ADMIN_TOKEN", "")
    if not admin_token:
        raise HTTPException(status_code=503, detail="Admin disabled")
    if request.headers.get("X-Admin-Token") != admin_token:
        raise HTTPException(status_code=403, detail="Forbidden")


@app.post("/admin/hut-ingest")
async def hut_ingest(request: Request):
    """本機 tools/hut_browser.py 用真瀏覽器抓一站式後送回來（一站式有 WAF，Railway 抓不到）。"""
    _check_admin(request)
    import hut_monitor
    try:
        return hut_monitor.ingest(await request.json())
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e)[:200])


@app.post("/admin/hut-probe")
async def hut_probe(request: Request):
    """從 Railway 對林業署三站首頁各 GET 一次，看國外 IP 會不會被擋。"""
    _check_admin(request)
    import requests
    import hut_sources
    out = {}
    for site, cfg in hut_sources.SITES.items():
        try:
            r = await asyncio.to_thread(requests.get, cfg["url"], timeout=20,
                                        headers={"User-Agent": hut_sources.USER_AGENT})
            out[site] = {"status": r.status_code, "bytes": len(r.content),
                         "csrf": bool(hut_sources.find_csrf(r.text))}
        except Exception as e:
            out[site] = {"error": f"{type(e).__name__}: {e}"[:200]}
        await asyncio.sleep(hut_sources.REQUEST_GAP)
    return out


@app.post("/admin/hut-run")
async def hut_run(request: Request):
    """手動跑一次山屋檢查（會照常寄信）。"""
    _check_admin(request)
    import hut_monitor
    return await asyncio.to_thread(hut_monitor.run_check, True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_hut_server.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_hut_server.py
git commit -m "山屋監控：排程 07:00／16:30／23:30 與 hut-ingest／probe／run 端點"
```

---

### Task 9: LINE 私訊「山屋」

**Files:**
- Modify: `command_router.py:312`（`_PRIVATE_SHORTCUTS`）、`command_router.py:1144` 附近（`handle()` 裡「明天行程」區塊之後）
- Test: `tests/test_hut_command.py`

- [ ] **Step 1: Write the failing test**

`tests/test_hut_command.py`：

```python
import command_router as cr
import hut_monitor


def test_admin_gets_hut_reply(monkeypatch):
    monkeypatch.setenv("ADMIN_LINE_USER_ID", "Uadmin")
    monkeypatch.setattr(hut_monitor, "line_reply", lambda: "山屋（測試）")
    ctx = {"source_type": "user", "user_id": "Uadmin"}
    assert cr.handle("山屋", ctx) == "山屋（測試）"
    assert cr.handle("/山屋空位", ctx) == "山屋（測試）"


def test_others_get_lock_not_ai(monkeypatch):
    monkeypatch.setenv("ADMIN_LINE_USER_ID", "Uadmin")
    ctx = {"source_type": "user", "user_id": "Usomeone_else"}
    assert cr.parse("山屋")[0] == "private_shortcut"
    assert "🔒" in cr.handle("山屋", ctx)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_hut_command.py -v`
Expected: FAIL（`handle` 回的不是「山屋（測試）」、parse 不是 private_shortcut）

- [ ] **Step 3: Write minimal implementation**

`command_router.py:312`，`_PRIVATE_SHORTCUTS` 的集合最後加兩個字：

```python
_PRIVATE_SHORTCUTS = {"整理定位", "定位整理", "整理今天定位", "明天行程", "明日行程", "共同明細", "共同最近", "共同帳本", "山屋", "山屋空位"}
```

`command_router.py` 的 `handle()` 裡，緊接在這兩行之後：

```python
    if (text or "").strip().lstrip("/") in couple_calendar.TOMORROW_KEYWORDS and _is_admin(ctx):
        return couple_calendar.tomorrow_reply()
```

加入：

```python
    # 私訊「山屋」：最新一次山屋空位摘要（只限本人）
    import hut_monitor
    if (text or "").strip().lstrip("/") in hut_monitor.KEYWORDS and _is_admin(ctx):
        return hut_monitor.line_reply()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_hut_command.py tests/test_richmenu.py tests/test_help_text.py -v`
Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add command_router.py tests/test_hut_command.py
git commit -m "山屋監控：LINE 私訊「山屋」回最新摘要"
```

---

### Task 10: 本機一站式抓取工具

**Files:**
- Create: `tools/hut_browser.py`
- Test: `tests/test_hut_browser.py`

- [ ] **Step 1: Write the failing test**

`tests/test_hut_browser.py`：

```python
import importlib.util
from pathlib import Path

import hut_onestop

_spec = importlib.util.spec_from_file_location(
    "hut_browser", Path(__file__).parent.parent / "tools" / "hut_browser.py")
hb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hb)

CAL = """<table class="table_bed"><tr><td><a href="bed_6main.aspx?node_id=3&sdate=2026-12-05">
<span>餘額</span> (<span>2</span>,<span>0</span>) <span>排隊預約</span> <span>9</span></a></td></tr></table>"""


class FakePage:
    def __init__(self, html_for=lambda url: CAL):
        self.html_for, self.url, self.actions = html_for, "", []

    def goto(self, url, wait_until=None):
        self.url = url
        self.actions.append(("goto", url))

    def content(self):
        return self.html_for(self.url)

    def select_option(self, selector, value=None, label=None):
        self.actions.append(("select", selector, value or label))

    def click(self, selector):
        self.actions.append(("click", selector))

    def wait_for_load_state(self, state=None):
        pass

    def eval_on_selector(self, selector, script):
        return {"#con_ddlYear": "2026", "#con_ddlMonth": "12"}[selector]


def test_collect_all_targets():
    page = FakePage()
    slots, errors = hb.collect_onestop(page, sleep=lambda s: None)
    assert errors == {}
    assert {s.site for s in slots} == set(hut_onestop.ONESTOP)
    assert ("select", "#con_ddlYear", "2027") in page.actions   # 12 月的下一個月跨年


def test_blocked_stops_everything():
    page = FakePage(html_for=lambda url: "<title>Request Rejected</title>")
    slots, errors = hb.collect_onestop(page, sleep=lambda s: None)
    assert slots == []
    assert set(errors) == set(hut_onestop.ONESTOP)
    assert all("Request Rejected" in m for m in errors.values())
    assert sum(1 for a in page.actions if a[0] == "goto") == 1   # 第一個被擋就停


def test_zero_rows_is_error():
    page = FakePage(html_for=lambda url: "<html>改版</html>")
    _, errors = hb.collect_onestop(page, sleep=lambda s: None)
    assert all("0 筆" in m for m in errors.values())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_hut_browser.py -v`
Expected: FAIL，`FileNotFoundError`（tools/hut_browser.py 不存在）

- [ ] **Step 3: Write minimal implementation**

`tools/hut_browser.py`：

```python
"""一站式山屋床位：本機開真瀏覽器（Playwright）抓，送回 ReportRobot。

一站式網站前有 WAF，程式直接送表單會回 Request Rejected，所以用真的瀏覽器
開頁面、選宿營地、按查詢，跟人操作一樣。不裝 stealth、不改 User-Agent／指紋；
被擋就整批停下、回報，不繞。
抓法改寫自 hiiamyes/taiwan-hiking-form-filler（MIT License, Copyright (c) 2025 Yes Lee）。

用法：
  python tools/hut_browser.py --recon      印各查詢頁的宿營地選項、存 HTML 樣本到 tests/fixtures/hut/，不送出
  python tools/hut_browser.py --dry-run    抓但不送，印結果
  python tools/hut_browser.py              抓一站式 → POST /admin/hut-ingest
  python tools/hut_browser.py --forest     林業署三站也在本機抓（Railway 被擋時的備案）
  加 --headed 會開看得到的瀏覽器視窗（headless 被擋時試一次用）

token：~/.hut_monitor/admin_token（一行，ReportRobot 的 ADMIN_TOKEN，從 Infisical 複製，不要貼進聊天）
"""

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import hut_onestop  # noqa: E402
import hut_sources  # noqa: E402

SERVER = "https://chengreportbot-production.up.railway.app"
HOME = Path.home() / ".hut_monitor"
FIXTURES = ROOT / "tests" / "fixtures" / "hut"
PAGE_GAP = 4   # 地點之間停 4 秒
MONTHS = 2     # 當月＋下月


def _search(page):
    page.click("#con_btnsearch")
    page.wait_for_load_state("networkidle")


def _next_month(page):
    y = int(page.eval_on_selector("#con_ddlYear", "e => e.value"))
    m = int(page.eval_on_selector("#con_ddlMonth", "e => e.value"))
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    page.select_option("#con_ddlYear", value=str(y))
    page.select_option("#con_ddlMonth", value=str(m))
    _search(page)


def _open_target(page, path, label):
    page.goto(hut_onestop.BASE + path, wait_until="networkidle")
    hut_onestop.ensure_not_blocked(page.content())
    page.select_option("#con_rooms", label=label)
    _search(page)


def collect_onestop(page, sleep=time.sleep):
    """(slots, errors)。被 WAF 擋就停，沒抓到的地點全部記成同一個錯誤。"""
    slots, errors = [], {}
    for site, (path, label) in hut_onestop.ONESTOP.items():
        try:
            _open_target(page, path, label)
            got = []
            for i in range(MONTHS):
                if i:
                    _next_month(page)
                got += hut_onestop.parse_onestop_html(page.content(), site)
            if not got:
                raise ValueError("解析出 0 筆（網站可能改版）")
            slots += got
        except hut_onestop.Blocked:
            msg = "被網站擋下（Request Rejected），這次停止"
            done = {s.site for s in slots}
            for rest in hut_onestop.ONESTOP:
                if rest not in done:
                    errors[rest] = msg
            break
        except Exception as e:
            errors[site] = f"{type(e).__name__}: {e}"[:200]
        sleep(PAGE_GAP)
    return slots, errors


def recon(page):
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for path in dict.fromkeys(p for p, _ in hut_onestop.ONESTOP.values()):
        page.goto(hut_onestop.BASE + path, wait_until="networkidle")
        hut_onestop.ensure_not_blocked(page.content())
        options = page.eval_on_selector(
            "#con_rooms", "e => [...e.options].map(o => o.value + '=' + o.text)")
        print(f"{path} 宿營地選項：{options}")
        time.sleep(PAGE_GAP)
    for site, (path, label) in hut_onestop.ONESTOP.items():
        _open_target(page, path, label)
        html = page.content()
        (FIXTURES / f"{site}.html").write_text(html, encoding="utf-8")
        print(f"{site} {label}：解析 {len(hut_onestop.parse_onestop_html(html, site))} 天，樣本已存")
        time.sleep(PAGE_GAP)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--recon", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--forest", action="store_true")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args(argv)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        page = browser.new_page(locale="zh-TW")
        try:
            if args.recon:
                recon(page)
                return 0
            slots, errors = collect_onestop(page)
        finally:
            browser.close()

    if args.forest:
        f_slots, f_errors = hut_sources.fetch_all(date.today())
        slots += f_slots
        errors.update(f_errors)
    print(json.dumps({"slots": len(slots), "errors": errors}, ensure_ascii=False))
    if args.dry_run:
        return 0

    import requests
    token = (HOME / "admin_token").read_text(encoding="utf-8-sig").strip()
    r = requests.post(f"{SERVER}/admin/hut-ingest", timeout=60,
                      json={"slots": [s.to_dict() for s in slots], "errors": errors},
                      headers={"X-Admin-Token": token})
    r.raise_for_status()
    print(r.json())
    return 0 if slots else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_hut_browser.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add tools/hut_browser.py tests/test_hut_browser.py
git commit -m "山屋監控：本機 Playwright 抓一站式（被擋就停）"
```

---

### Task 11: 本機實測一站式（決定一站式能不能做）

這個 task 會真的連一站式網站，一次約 10 個頁面。

- [ ] **Step 1: 裝瀏覽器**

Run: `python -m playwright install chromium`
Expected: 下載完成或顯示已安裝

- [ ] **Step 2: 偵察**

Run: `python tools/hut_browser.py --recon`
Expected：每個查詢頁印出宿營地選項，每個地點印「解析 N 天，樣本已存」。

依結果處理：
- 選項裡的名稱跟 `hut_onestop.ONESTOP` 不一樣（例如「七卡山莊」實際叫「七卡山莊(雪山)」）→ 改 `hut_onestop.py` 的 ONESTOP 名稱成印出來的字，重跑。
- 解析 0 天 → 打開存下的 `tests/fixtures/hut/<site>.html`，找每天的格子，改 `parse_onestop_html` 的選擇器，並在 `tests/test_hut_onestop.py` 加一個讀這份樣本的測試。
- 出現 `Blocked` → 跑一次 `python tools/hut_browser.py --recon --headed`。還是被擋 → **停下來回報使用者**，一站式改回只做日期提醒（把 `hut_monitor._merge_ingest` 的 expected 拿掉 ONESTOP），不要再試別的方法。

- [ ] **Step 3: 樣本測試**

在 `tests/test_hut_onestop.py` 檔尾加（樣本由 Step 2 產生）：

```python
from pathlib import Path

FIX = Path(__file__).parent / "fixtures" / "hut"


@pytest.mark.parametrize("site", sorted(ho.ONESTOP))
def test_real_sample_parses(site):
    html = (FIX / f"{site}.html").read_text(encoding="utf-8")
    slots = ho.parse_onestop_html(html, site)
    assert len(slots) >= 28
    assert any(s.remaining is not None for s in slots)
```

Run: `python -m pytest tests/test_hut_onestop.py -v`
Expected: 全部 passed

- [ ] **Step 4: Dry run**

Run: `python tools/hut_browser.py --dry-run`
Expected：`{"slots": >100, "errors": {}}`

- [ ] **Step 5: Commit**

```bash
git add hut_onestop.py tests/test_hut_onestop.py tests/fixtures/hut/
git commit -m "山屋監控：一站式實測樣本與名稱校正"
```

---

### Task 12: 本機排程腳本

**Files:**
- Create: `tools/hut_browser_local.ps1`、`tools/hut_browser_task.ps1`

- [ ] **Step 1: 寫執行腳本**

`tools/hut_browser_local.ps1`：

```powershell
# 一站式山屋床位（本機真瀏覽器）：抓完送回 ReportRobot。工作排程器 06:50／16:20／23:20 叫這支。
# 比伺服器 07:00／16:30／23:30 早 10 分鐘，伺服器檢查時資料還在 60 分鐘有效期內。
# 用 Continue 不用 Stop：PS 5.1 下 python 寫 stderr 會被包成錯誤，Stop 會直接中斷。
$ErrorActionPreference = 'Continue'
$home_dir = Join-Path $HOME '.hut_monitor'
New-Item -ItemType Directory -Force $home_dir | Out-Null
$python = (Get-Command python).Source
$script = Join-Path $PSScriptRoot 'hut_browser.py'
$env:PYTHONIOENCODING = 'utf-8'
$out = & $python $script 2>&1 | Out-String
$code = $LASTEXITCODE
Add-Content -Path (Join-Path $home_dir 'log.txt') -Encoding utf8 `
    -Value ("{0} exit={1} {2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm'), $code, $out.Trim())
exit $code
```

- [ ] **Step 2: 寫註冊腳本**

`tools/hut_browser_task.ps1`：

```powershell
# 註冊 Windows 排程「山屋一站式」：每天 06:50／16:20／23:20 跑 hut_browser_local.ps1。
# headless 瀏覽器，不搶滑鼠鍵盤。不設 WakeToRun：電腦睡著就跳過，伺服器信裡會標「一站式本次沒查到」。
# 用法：powershell -ExecutionPolicy Bypass -File tools\hut_browser_task.ps1
$script = Join-Path $PSScriptRoot 'hut_browser_local.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`"" `
    -WorkingDirectory $PSScriptRoot
$triggers = @('6:50', '16:20', '23:20') | ForEach-Object { New-ScheduledTaskTrigger -Daily -At $_ }
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive
Register-ScheduledTask -TaskName '山屋一站式' -Action $action -Trigger $triggers `
    -Settings $settings -Principal $principal -Force | Out-Null
Write-Host '已註冊「山屋一站式」：每天 06:50／16:20／23:20。log 在' `
    (Join-Path $HOME '.hut_monitor\log.txt')
```

- [ ] **Step 3: 轉成 UTF-8 BOM**（PS 5.1 讀沒 BOM 的檔會用 CP950，中文註解變亂碼、字串壞掉）

Run（PowerShell）：

```powershell
foreach ($f in 'tools\hut_browser_local.ps1','tools\hut_browser_task.ps1') { $t = [IO.File]::ReadAllText($f); [IO.File]::WriteAllText($f, $t, (New-Object Text.UTF8Encoding $true)) }
```

Expected：兩個檔開頭是 BOM（`Format-Hex tools\hut_browser_task.ps1 | Select-Object -First 1` 開頭 `EF BB BF`）

- [ ] **Step 4: Commit**

```bash
git add tools/hut_browser_local.ps1 tools/hut_browser_task.ps1
git commit -m "山屋監控：本機排程腳本（06:50／16:20／23:20）"
```

---

### Task 13: 交通卡核對官方資料

**Files:**
- Modify: `hut_info.py`（`TRANSPORT`）

- [ ] **Step 1:** 逐一讀 `TRANSPORT` 每個地點的 `source` 官方頁（用 WebFetch 或 Claude in Chrome 的 `get_page_text`），找「交通資訊／交通指引」。檜谷站首頁有「交通資訊」連結；嘉明湖、天池站同一套系統也有。
- [ ] **Step 2:** 官方資料跟 `text` 不同就改 `text`，核對完把 `checked` 填核對日期字串（例 `"2026-11-03"`）。官方頁查不到的地點維持 `checked: None`（信裡會繼續警告），不要用部落格內容填。
- [ ] **Step 3:** Run: `python -m pytest tests/test_hut_info.py -v` → Expected: passed（`test_unverified_card_warns` 若嘉明湖已核對，改測一個仍是 None 的地點；全部核對完就改成測 `"核對）" in card`）
- [ ] **Step 4: Commit**

```bash
git add hut_info.py tests/test_hut_info.py
git commit -m "山屋監控：交通卡核對官方資料"
```

---

### Task 14: 全套測試、上線、實測（要使用者同意）

- [ ] **Step 1:** Run: `python -m pytest -q` → Expected: `1630 + 新增` 全部 passed，0 failed
- [ ] **Step 2: 停下來問使用者**：要 push `feat/hut-monitor` 並合併到 main 讓 Railway 部署嗎？（push 前先跑 `git -C C:/Users/acer/projects/ReportRobot-hut log --oneline main..` 列出要上去的 commit 給使用者看）
- [ ] **Step 3:** 使用者同意後：

```bash
git -C C:/Users/acer/projects/ReportRobot-hut push -u origin feat/hut-monitor
```

合併方式照 superpowers:finishing-a-development-branch 問使用者（PR 或直接 merge）。

- [ ] **Step 4: 使用者自己放 token**（不要在聊天貼）：在 Infisical 複製 ReportRobot 的 ADMIN_TOKEN，存成 `C:\Users\acer\.hut_monitor\admin_token`（一行）。
- [ ] **Step 5: Railway 連線實測**（部署完成後）：

```bash
curl -s -X POST -H "X-Admin-Token: $(cat ~/.hut_monitor/admin_token)" https://chengreportbot-production.up.railway.app/admin/hut-probe
```

Expected：三站都 `"status": 200, "csrf": true`。
- 有站被擋（4xx/5xx/error）→ 不換 UA。到 Railway 設 `HUT_FOREST_ON_SERVER=0`，`tools/hut_browser_local.ps1` 的呼叫改成 `& $python $script --forest`，林業署改由本機送；回報使用者。

- [ ] **Step 6: 手動跑一次**：

```bash
curl -s -X POST -H "X-Admin-Token: $(cat ~/.hut_monitor/admin_token)" https://chengreportbot-production.up.railway.app/admin/hut-run
```

Expected：第一次是基準 `{"changes": 0, ...}`；今天有抽籤／開放日的話會收到「山屋：今天 N 個抽籤／開放日」的信。

- [ ] **Step 7: 本機實送**：`python tools/hut_browser.py` → Expected 印出 `{'slots': N, 'sites': [...onestop...]}`
- [ ] **Step 8: 註冊排程（使用者自己跑）**：`! powershell -ExecutionPolicy Bypass -File C:\Users\acer\projects\ReportRobot-hut\tools\hut_browser_task.ps1`
- [ ] **Step 9:** LINE 私訊「山屋」→ 回最新摘要。
- [ ] **Step 10: 提醒使用者建 Gmail 篩選器**：主旨含「山屋：」→ 加標籤、標為重要、不要略過收件匣。
- [ ] **Step 11:** Notion 待辦 #12 標完成。
