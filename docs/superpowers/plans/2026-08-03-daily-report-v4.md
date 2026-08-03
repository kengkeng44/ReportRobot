# 每日播報 v4(幽默 + 新聞)實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每日 08:00 的 LINE Flex Carousel 新增「今日一則」卡片(小知識/笑話 + 節日祝福 + 天氣颱風新聞),並在盤前卡加市場新聞。

**Architecture:** 新增獨立模組 `humor.py` 產生「今日一則」文字;把 `stock_news.py` 綁個股的 Google News 抽成通用 `_google_news_rss(query)` 供兩邊共用;`daily_report.py` 多 gather 兩段、`flex_builder.py` carousel 多收一個 bubble。不動天氣、盤前、持股核心邏輯。

**Tech Stack:** Python 3.12、anthropic SDK(claude-sonnet-4-5)、feedparser(Google News RSS)、holidays(台灣節日)、pytest 9(新增,repo 首套測試)。

**範圍:** 本計畫只涵蓋規格功能 A。功能 B(月消費 `/本月消費`)需先取得一封真實信用卡通知信才能寫 regex,另立計畫。

---

### Task 1: 相依與測試骨架

**Files:**
- Modify: `requirements.txt`
- Create: `conftest.py`(repo 根目錄,空檔,讓 pytest 把根目錄加進 import path)
- Create: `tests/__init__.py`(空檔)

- [ ] **Step 1: 加入 holidays 相依**

在 `requirements.txt` 末尾新增一行:

```
holidays==0.60
```

- [ ] **Step 2: 安裝**

Run: `pip install holidays==0.60`
Expected: 安裝成功(若 0.60 不存在,改用 `pip install holidays` 抓最新,再把裝到的版本寫回 requirements.txt)

- [ ] **Step 3: 建立測試骨架**

建 `conftest.py`(空檔即可,存在就會讓 pytest 以 repo 根為 rootdir、把根目錄加入 sys.path,使 `import humor` / `import stock_news` 可用)。
建 `tests/__init__.py`(空檔)。

- [ ] **Step 4: 驗證 pytest 跑得動**

Run: `pytest -q`
Expected: `no tests ran`(0 收集,無 error)

- [ ] **Step 5: Commit**

```bash
git add requirements.txt conftest.py tests/__init__.py
git commit -m "chore: 加 holidays 相依 + pytest 測試骨架"
```

---

### Task 2: 抽出通用 `_google_news_rss(query)`

**Files:**
- Modify: `stock_news.py`(`get_google_news`,約 184-212 行)
- Create: `tests/test_google_news.py`

- [ ] **Step 1: 寫失敗測試**

`tests/test_google_news.py`:

```python
import stock_news


class _FakeEntry(dict):
    def get(self, k, default=None):
        return dict.get(self, k, default)


def test_google_news_rss_strips_source_suffix(monkeypatch):
    fake_feed = type("F", (), {"entries": [
        {"title": "颱風逼近北台灣 - 中央社", "link": "http://a",
         "published_parsed": None, "updated_parsed": None},
        {"title": "純標題無來源", "link": "http://b",
         "published_parsed": None, "updated_parsed": None},
    ]})()
    monkeypatch.setattr(stock_news.feedparser, "parse", lambda url: fake_feed)

    out = stock_news._google_news_rss("颱風 天氣", limit=5)

    assert [n["title"] for n in out] == ["颱風逼近北台灣", "純標題無來源"]
    assert out[0]["source"] == "Google News"


def test_get_google_news_delegates_to_rss(monkeypatch):
    captured = {}
    monkeypatch.setattr(stock_news, "_google_news_rss",
                        lambda query, limit=10: captured.setdefault("query", query) or [])
    stock_news.get_google_news("2330", "台積電", limit=3)
    assert "台積電" in captured["query"]
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `pytest tests/test_google_news.py -q`
Expected: FAIL(`AttributeError: module 'stock_news' has no attribute '_google_news_rss'`)

- [ ] **Step 3: 重構 `get_google_news`**

在 `stock_news.py` 把原本 `get_google_news`(184-212 行)替換成下面兩個函式:

```python
def _google_news_rss(query, limit=10):
    """給定查詢字串搜 Google News RSS(zh-TW),回 [{title, link, published, source}]。"""
    try:
        from urllib.parse import quote_plus
        url = (f"https://news.google.com/rss/search?q={quote_plus(query)}"
               f"&hl=zh-TW&gl=TW&ceid=TW:zh-Hant")
        feed = feedparser.parse(url)
        news = []
        for entry in feed.entries[:limit]:
            title = entry.get('title', '')
            # Google News title 格式:「正文 - 來源網站」,砍掉 source 部分
            if ' - ' in title:
                title = title.rsplit(' - ', 1)[0]
            news.append({
                "title": title,
                "link": entry.get('link', ''),
                "published": _struct_time_to_unix(entry.get('published_parsed')
                                                  or entry.get('updated_parsed')),
                "source": "Google News",
            })
        return news
    except Exception as e:
        print(f"Google News 失敗:{e}")
        return []


def get_google_news(stock_id, stock_name, limit=10):
    """Google News RSS 搜中文公司名(覆蓋廣、更新快、抓得到 yahoo/cnyes 漏的新聞)。"""
    if stock_name and stock_name != stock_id:
        query = f'"{stock_name}" 股'
    else:
        query = f'"{stock_id}" 股'
    return _google_news_rss(query, limit)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `pytest tests/test_google_news.py -q`
Expected: PASS(2 passed)

- [ ] **Step 5: Commit**

```bash
git add stock_news.py tests/test_google_news.py
git commit -m "refactor(stock_news): 抽出通用 _google_news_rss 供關鍵字搜尋共用"
```

---

### Task 3: 新增四個 Prompt

**Files:**
- Modify: `prompts.py`(檔尾新增)

- [ ] **Step 1: 新增 prompt 常數**

在 `prompts.py` 檔尾追加(風格比照現有:純文字繁中、無 Markdown、明確禁止清單):

```python
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 每日「今日一則」Prompt(humor.py 用)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DAILY_TRIVIA_PROMPT = """給我一則有趣的冷知識,主題不限(自然、歷史、科學、生活都可以)。
要求:
- 繁體中文,2 句話內講完,輕鬆好懂
- 只輸出冷知識本身,不要開場白、不要「你知道嗎」這類套語、不要 emoji、不要 Markdown"""

DAILY_JOKE_PROMPT = """給我一則簡短的中文笑話或雙關,乾淨、老少咸宜。
要求:
- 繁體中文,3 句話內,有梗但不低俗
- 只輸出笑話本身,不要開場白、不要說明笑點、不要 emoji、不要 Markdown"""

FESTIVAL_GREETING_PROMPT = """今天是「{festival}」。寫一句溫暖、真誠的節日祝福。
要求:
- 繁體中文,1-2 句,口語自然、不八股
- 只輸出祝福本身,不要開場白、不要 emoji、不要 Markdown"""

FUN_NEWS_PROMPT = """以下是今天的新聞標題清單:

{titles}

請從中挑「最有趣或最該讓一般人知道的一則」(優先天氣、颱風等生活相關),用一句白話講重點。
要求:
- 繁體中文,1 句話,像朋友轉述那樣自然
- 只輸出那句話,不要編號、不要引用多則、不要 emoji、不要 Markdown"""
```

- [ ] **Step 2: 驗證可 import**

Run: `python -c "import prompts; print(prompts.DAILY_TRIVIA_PROMPT[:6], prompts.FUN_NEWS_PROMPT[:6])"`
Expected: 印出兩個 prompt 開頭字元,無 error

- [ ] **Step 3: Commit**

```bash
git add prompts.py
git commit -m "prompts: 新增小知識/笑話/節日祝福/每日新鮮事 4 個 prompt"
```

---

### Task 4: 建立 `humor.py`

**Files:**
- Create: `humor.py`
- Create: `tests/test_humor.py`

- [ ] **Step 1: 寫失敗測試**

`tests/test_humor.py`:

```python
from datetime import date
import humor


def test_trivia_on_even_yday(monkeypatch):
    # 2026-01-02 是第 2 天(偶數)→ 小知識
    monkeypatch.setattr(humor, "_ai", lambda prompt, max_tokens=300: "太陽很大")
    header, body = humor._trivia_or_joke(date(2026, 1, 2))
    assert header == "💡 今日小知識"
    assert body == "太陽很大"


def test_joke_on_odd_yday(monkeypatch):
    # 2026-01-01 是第 1 天(奇數)→ 笑話
    monkeypatch.setattr(humor, "_ai", lambda prompt, max_tokens=300: "哈哈")
    header, _ = humor._trivia_or_joke(date(2026, 1, 1))
    assert header == "😄 今日一笑"


def test_festival_greeting_none_on_ordinary_day(monkeypatch):
    # 用一個平常日(非國定假日)
    assert humor._festival_greeting(date(2026, 1, 6)) is None


def test_get_daily_extra_assembles_sections(monkeypatch):
    monkeypatch.setattr(humor, "today_tpe", lambda: date(2026, 1, 2))
    monkeypatch.setattr(humor, "_festival_greeting", lambda today: None)
    monkeypatch.setattr(humor, "_trivia_or_joke",
                        lambda today: ("💡 今日小知識", "冷知識內容"))
    monkeypatch.setattr(humor, "_fun_news", lambda: "今天下雨")

    out = humor.get_daily_extra()

    assert "💡 今日小知識\n冷知識內容" in out
    assert "📰 今日新鮮事\n今天下雨" in out


def test_get_daily_extra_returns_none_when_all_empty(monkeypatch):
    monkeypatch.setattr(humor, "today_tpe", lambda: date(2026, 1, 2))
    monkeypatch.setattr(humor, "_festival_greeting", lambda today: None)
    monkeypatch.setattr(humor, "_trivia_or_joke", lambda today: None)
    monkeypatch.setattr(humor, "_fun_news", lambda: None)
    assert humor.get_daily_extra() is None
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `pytest tests/test_humor.py -q`
Expected: FAIL(`ModuleNotFoundError: No module named 'humor'`)

- [ ] **Step 3: 建立 `humor.py`**

```python
"""每日「今日一則」內容:小知識/笑話(依日期輪流)+ 節日祝福 + 每日新鮮事。

對外單一入口 get_daily_extra() -> str | None,回傳可直接餵給
flex_builder.text_bubble 的 body(段落以 \\n\\n 分隔,每段首行為次標)。
任一子區塊失敗只略過該段,不整卡失敗;全部失敗回 None。
"""

import os

import anthropic
import holidays

import usage_tracker
from prompts import (
    DAILY_TRIVIA_PROMPT, DAILY_JOKE_PROMPT,
    FESTIVAL_GREETING_PROMPT, FUN_NEWS_PROMPT,
)
from stock_news import _google_news_rss
from tz_utils import today_tpe


def _env(name):
    val = os.environ.get(name)
    if val:
        return val
    import config
    return getattr(config, name)


AI_MODEL = "claude-sonnet-4-5"


def _ai(prompt, max_tokens=300):
    # 金鑰在此處才讀(lazy):讓 import humor 在無金鑰環境也不炸,方便測試
    client = anthropic.Anthropic(api_key=_env("ANTHROPIC_API_KEY"))
    message = client.messages.create(
        model=AI_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    usage_tracker.track(AI_MODEL, message)
    return message.content[0].text.strip()


def _festival_greeting(today):
    """今天是台灣節日就回一句祝福,否則 None。"""
    try:
        tw = holidays.Taiwan(years=today.year)
        name = tw.get(today)
        if not name:
            return None
        return _ai(FESTIVAL_GREETING_PROMPT.format(festival=name))
    except Exception as e:
        print(f"節日祝福失敗:{e}")
        return None


def _trivia_or_joke(today):
    """依當年第幾天單雙數決定小知識/笑話。回 (header, body) 或 None。"""
    is_trivia = today.timetuple().tm_yday % 2 == 0
    prompt = DAILY_TRIVIA_PROMPT if is_trivia else DAILY_JOKE_PROMPT
    header = "💡 今日小知識" if is_trivia else "😄 今日一笑"
    try:
        return header, _ai(prompt)
    except Exception as e:
        print(f"小知識/笑話失敗:{e}")
        return None


def _fun_news():
    """搜天氣/颱風新聞,AI 挑一則講重點。無新聞回 None。"""
    items = _google_news_rss("颱風 天氣", limit=8)
    if not items:
        items = _google_news_rss("台灣 生活", limit=8)
    if not items:
        return None
    titles = "\n".join(f"{i+1}. {it['title']}"
                       for i, it in enumerate(items) if it.get("title"))
    if not titles:
        return None
    try:
        return _ai(FUN_NEWS_PROMPT.format(titles=titles))
    except Exception as e:
        print(f"每日新鮮事失敗:{e}")
        return None


def get_daily_extra():
    today = today_tpe()
    sections = []

    greeting = _festival_greeting(today)
    if greeting:
        sections.append(f"🎉 節日快樂\n{greeting}")

    tj = _trivia_or_joke(today)
    if tj:
        header, body = tj
        sections.append(f"{header}\n{body}")

    news = _fun_news()
    if news:
        sections.append(f"📰 今日新鮮事\n{news}")

    if not sections:
        return None
    return "\n\n".join(sections)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `pytest tests/test_humor.py -q`
Expected: PASS(5 passed)

> 註:`_festival_greeting` 的平常日測試不會呼叫 AI(節日名為 None 時提早 return),故不需 mock `_ai`。若某天 holidays 版本把 2026-01-06 列為假日導致測試失敗,改用當年確定的平日(如 date(2026,1,7))。

- [ ] **Step 5: Commit**

```bash
git add humor.py tests/test_humor.py
git commit -m "feat(humor): 今日一則(小知識/笑話輪流 + 節日祝福 + 天氣新聞)"
```

---

### Task 5: `flex_builder` carousel 收「今日一則」bubble

**Files:**
- Modify: `flex_builder.py`(`daily_report_carousel`,312-348 行)
- Create: `tests/test_flex_carousel.py`

- [ ] **Step 1: 寫失敗測試**

`tests/test_flex_carousel.py`:

```python
import flex_builder


def _titles(msg):
    """從 carousel/bubble message 抓所有 bubble 的 header 標題文字。"""
    contents = msg["contents"]
    bubbles = contents["contents"] if contents.get("type") == "carousel" else [contents]
    out = []
    for b in bubbles:
        for el in b["header"]["contents"]:
            if el.get("type") == "text":
                out.append(el["text"])
                break
    return out


def test_extra_bubble_is_first(monkeypatch):
    msg = flex_builder.daily_report_carousel(
        extra_text="💡 今日小知識\n冷知識",
        weather_text="晴天",
        premarket_text="盤前內容",
        today_str="2026-08-03",
    )
    titles = _titles(msg)
    assert titles[0] == "💫 今日一則"
    assert "🌤️ 天氣報告" in titles
    assert "📊 盤前報告" in titles


def test_no_extra_still_works(monkeypatch):
    msg = flex_builder.daily_report_carousel(
        extra_text=None,
        weather_text="晴天",
        premarket_text=None,
        today_str="2026-08-03",
    )
    assert "🌤️ 天氣報告" in _titles(msg)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `pytest tests/test_flex_carousel.py -q`
Expected: FAIL(`TypeError: daily_report_carousel() got an unexpected keyword argument 'extra_text'`)

- [ ] **Step 3: 改 `daily_report_carousel`**

把 `flex_builder.py` 的 `daily_report_carousel`(312-348 行)整段替換為:

```python
def daily_report_carousel(extra_text, weather_text, premarket_text, today_str):
    """把每日報組成 carousel(橫滑),1 則 push 搞定。
    順序:今日一則 → 天氣 → 盤前。全部缺回 None。"""
    bubbles = []

    if extra_text:
        bubbles.append(text_bubble(
            title="💫 今日一則",
            subtitle=today_str,
            body=extra_text,
            header_color=_GREEN,
        ))

    if weather_text:
        bubbles.append(text_bubble(
            title="🌤️ 天氣報告",
            subtitle=today_str,
            body=weather_text,
            header_color=_BROWN,
        ))
    else:
        bubbles.append(text_bubble(
            title="🌤️ 天氣報告",
            subtitle=today_str,
            body="⚠️ 天氣資料暫時無法取得",
            header_color=_BROWN,
        ))

    if premarket_text:
        bubbles.append(text_bubble(
            title="📊 盤前報告",
            subtitle=today_str,
            body=premarket_text,
            header_color="#5B8DA6",
        ))
    # premarket_text 為 None 時通常是週末,這時就只有今日一則 + 天氣 bubble

    if not bubbles:
        return None

    if len(bubbles) == 1:
        return _wrap(bubbles[0], alt="📅 每日情報")
    return _wrap(
        {"type": "carousel", "contents": bubbles},
        alt=f"📅 每日情報({today_str})",
    )
```

- [ ] **Step 4: 跑測試確認通過**

Run: `pytest tests/test_flex_carousel.py -q`
Expected: PASS(2 passed)

- [ ] **Step 5: Commit**

```bash
git add flex_builder.py tests/test_flex_carousel.py
git commit -m "feat(flex): 每日 carousel 新增今日一則 bubble(置首)"
```

---

### Task 6: `daily_report` 串接今日一則 + 盤前市場新聞

**Files:**
- Modify: `daily_report.py`

- [ ] **Step 1: 改 `daily_report.py`**

改動三處:(a) 新增 import;(b) 新增市場新聞 helper;(c) `run_daily_report` 內 gather 兩段並改呼叫 carousel。

新增 import(檔案上方 import 區):

```python
import humor
from stock_news import get_cnyes_news
```

新增 helper(放在 `_safe` 之後):

```python
def _fetch_market_news(limit=3):
    """抓鉅亨台股市場新聞數則標題,組成一段文字;無則回 None。"""
    items = get_cnyes_news("台股", limit=limit)
    lines = [f"• {it['title']}" for it in items[:limit] if it.get("title")]
    return "\n".join(lines) if lines else None
```

把 `run_daily_report` 內「組 carousel」前後改成:

```python
    # 1. 天氣
    def _weather():
        weather_msg, _ = get_weather_report()  # chart_path 不用,LINE 不傳圖
        return weather_msg
    weather_text = _safe("天氣", _weather)

    # 2. 盤前報告(週末回 None)
    premarket_text = _safe(
        "盤前",
        lambda: build_premarket_report(force=force_premarket),
    )

    # 2b. 盤前有內容才附市場新聞
    if premarket_text:
        market_news = _safe("市場新聞", _fetch_market_news)
        if market_news:
            premarket_text = f"{premarket_text}\n\n📰 今日市場新聞\n{market_news}"

    # 3. 今日一則(小知識/笑話 + 節日 + 天氣新聞)
    extra_text = _safe("今日一則", humor.get_daily_extra)

    # 組 carousel 一次推(1 則 push)
    carousel = daily_report_carousel(extra_text, weather_text, premarket_text, today)
```

- [ ] **Step 2: 驗證 import 與組裝無誤(mock 掉外部呼叫)**

Run:

```bash
python -c "import daily_report, humor, flex_builder; print('imports ok'); \
print(type(flex_builder.daily_report_carousel('a','b','c','2026-08-03')))"
```

Expected: 印出 `imports ok` 與 `<class 'dict'>`,無 error

- [ ] **Step 3: Commit**

```bash
git add daily_report.py
git commit -m "feat(daily): 串今日一則卡 + 盤前附市場新聞"
```

---

### Task 7: 全套測試 + 手動驗收

**Files:** 無(驗證用)

- [ ] **Step 1: 跑全部測試**

Run: `pytest -q`
Expected: 全 PASS(9 passed:google_news 2 + humor 5 + flex 2)

- [ ] **Step 2: 手動驗收今日一則(需真 AI/網路,會實際呼叫 API)**

Run: `python -c "import humor; print(humor.get_daily_extra())"`
Expected: 印出含「💡 今日小知識」或「😄 今日一笑」的多段文字(遇節日多一段祝福、有天氣新聞多一段);任一段失敗只會少該段,不報錯

- [ ] **Step 3: 手動驗收每日報整體(觸發 08:00 那條流程)**

依現有觸發方式跑一次每日報(server 有對應 endpoint 或本機呼叫 `run_daily_report`),確認 LINE 收到含 3 張卡片的 carousel。

> 若不想真的推 LINE,可先只跑 Step 2 確認內容產得出來,LINE 推送留待 push 上線後在正式環境看。

- [ ] **Step 4: 確認未 push,回報使用者**

Run: `git -C . log --oneline origin/main..HEAD`
Expected: 列出本計畫的數個 commit(尚未 push),回報使用者決定何時 push 上 Railway。

---

## 上線備註

- 本計畫全部 commit 完成後**先不 push**,由使用者確認再推(推上 Railway 才會生效)。
- 新相依 `holidays` 已進 `requirements.txt`,Railway 重新部署會自動安裝。
- 功能 B(月消費)待使用者提供一封真實信用卡通知信後另立計畫。
