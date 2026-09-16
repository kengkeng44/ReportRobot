"""
天氣模組 v2
- 中央氣象署為主
- OpenWeatherMap 輔助
- matplotlib 畫溫度折線圖
- AI 整理報告（不含來源狀態表）
"""

import os
import re
import tempfile
import requests
import anthropic
import http_utils
import usage_tracker
import sonnet_client
import matplotlib
matplotlib.use('Agg')  # 無視窗環境
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from datetime import datetime, timedelta
from prompts import WEATHER_PROMPT
from tz_utils import now_tpe


def _env(name):
    val = os.environ.get(name)
    if val:
        return val
    import config
    return getattr(config, name)


def _env_list(name):
    val = os.environ.get(name)
    if val:
        return [x.strip() for x in val.split(",") if x.strip()]
    import config
    return getattr(config, name)


CWA_API_KEY = _env("CWA_API_KEY")
OWM_API_KEY = _env("OWM_API_KEY")
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")
WEATHER_LOCATIONS = _env_list("WEATHER_LOCATIONS")


def _env_list_or(name, default):
    """有設就用設定值，沒設就用預設。_env_list 在缺 config 屬性時會炸，這裡吞掉。"""
    try:
        return _env_list(name) or default
    except (ImportError, AttributeError):
        return default


# 個人版推播的天氣地點（群組版仍用 WEATHER_LOCATIONS）。
# 板橋和淡水、金山同屬新北市，共用 F-D0047-071 這支鄉鎮預報，不用換資料集。
PERSONAL_WEATHER_LOCATIONS = _env_list_or("PERSONAL_WEATHER_LOCATIONS", ["板橋區"])

# 嘗試使用中文字體
# 先查檔案路徑再查家族名。這個順序是 2026-09-05 換來的：
# 容器裡其實有中文字型（nixpacks.toml 為了 Rich Menu 裝了 fonts-wqy-zenhei），
# 但舊清單只寫「WenQuanYi Micro Hei」—— 那是 fonts-wqy-**microhei** 的家族名，
# 跟裝的那顆對不上，於是圓餅圖的圖例整排變成豆腐方塊 □□。
#
# setup_richmenu 一直是用路徑找的（所以選單有字）。這份清單跟它同源，
# 改 nixpacks 的字型時兩邊都要動 —— tests/test_chart_font.py 會擋。
_FONT_PATHS = [
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "C:\\Windows\\Fonts\\msjh.ttc",
    "C:\\Windows\\Fonts\\NotoSansTC-VF.ttf",
]


def get_chinese_font():
    """找到可用的中文字體（fallback_to_default=False 才不會被 mpl 偷偷塞 DejaVu Sans）

    路徑優先：findfont 依賴 matplotlib 自己的字型快取，容器剛建好時
    不一定即時；os.path.exists 沒有這個問題。
    """
    for path in _FONT_PATHS:
        if os.path.exists(path):
            return fm.FontProperties(fname=path)

    font_candidates = [
        'Noto Sans CJK TC', 'Noto Sans TC', 'Noto Sans CJK SC',
        'Microsoft JhengHei', 'Microsoft YaHei', 'PingFang TC',
        'WenQuanYi Zen Hei', 'WenQuanYi Micro Hei',
        'SimHei', 'Arial Unicode MS',
    ]
    for font_name in font_candidates:
        try:
            font_path = fm.findfont(
                fm.FontProperties(family=font_name),
                fallback_to_default=False,
            )
            if font_path:
                return fm.FontProperties(fname=font_path)
        except (ValueError, RuntimeError):
            continue
    return fm.FontProperties()

def _extract_element_value(ev_list):
    """從 ElementValue/elementValue 陣列取出值（新 API 有 Temperature/WindSpeed 等各種鍵名）。"""
    if not ev_list:
        return ''
    ev = ev_list[0] if isinstance(ev_list, list) else ev_list
    if not isinstance(ev, dict):
        return ''
    if 'value' in ev:
        return ev.get('value', '')
    for v in ev.values():
        if v not in (None, ''):
            return v
    return ''


def get_cwa_weather(locations=None):
    """
    主：新北市鄉鎮逐 3 小時預報 F-D0047-071，直接用 LocationName 篩要的行政區。
    使用 v1 REST API 新版大寫欄位（LocationName / WeatherElement / ElementName / Time / ElementValue）。

    locations 不給就用 WEATHER_LOCATIONS（群組版）；個人版傳 PERSONAL_WEATHER_LOCATIONS。
    """
    # 用 wanted 而不是沿用 locations：下面 API 回傳的地點清單也叫 locations，
    # 同名會在中途被蓋掉，變成「傳了板橋卻拿到淡水金山」且不會報錯
    wanted = locations or WEATHER_LOCATIONS
    try:
        url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-071"
        params = {
            "Authorization": CWA_API_KEY,
            "LocationName": ",".join(wanted),
            # 省略 ElementName — F-D0047-071 的正確欄位是「平均溫度/最高溫度/最低溫度/
            # 平均相對濕度/最高體感溫度/最低體感溫度/風速/風向/12小時降雨機率/天氣現象/天氣預報綜合描述」等，
            # 不帶參數就一次拿全部，避免名稱不符被 CWA 靜默丟掉。
        }
        resp = http_utils.get(url, params=params, timeout=15)
        data = resp.json()

        records = data.get('records', {})
        locations_wrapper = records.get('Locations') or records.get('locations') or []
        if not locations_wrapper:
            print(f"CWA F-D0047-071 回傳無 Locations：{str(data)[:200]}")
            return {}
        first = locations_wrapper[0] if isinstance(locations_wrapper, list) else locations_wrapper
        locations = first.get('Location') or first.get('location') or []

        results = {}
        for loc in locations:
            name = loc.get('LocationName') or loc.get('locationName', '')
            if name not in wanted:
                continue
            elements = {}
            for elem in (loc.get('WeatherElement') or loc.get('weatherElement') or []):
                elem_name = elem.get('ElementName') or elem.get('elementName', '')
                time_data = []
                for t in (elem.get('Time') or elem.get('time') or []):
                    dt = (t.get('DataTime') or t.get('dataTime')
                          or t.get('StartTime') or t.get('startTime', ''))
                    value = _extract_element_value(
                        t.get('ElementValue') or t.get('elementValue')
                    )
                    time_data.append({'time': dt, 'value': value})
                elements[elem_name] = time_data
            results[name] = elements

        if results:
            print(f"CWA F-D0047-071 成功，抓到 {list(results.keys())}")
        return results
    except Exception as e:
        print(f"CWA F-D0047-071 失敗：{e}")
        return {}


def get_cwa_weather_fallback(locations=None):
    """
    備用：F-C0032-001（36小時預報）— 只有縣市層級，新北市共用給所有行政區。
    回傳結構跟主方案一致，方便下游共用。
    """
    wanted = locations or WEATHER_LOCATIONS
    try:
        url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001"
        params = {"Authorization": CWA_API_KEY, "locationName": "新北市"}
        resp = http_utils.get(url, params=params, timeout=15)
        data = resp.json()

        locations = data.get('records', {}).get('location', [])
        if not locations:
            print(f"CWA F-C0032-001 回傳空：{str(data)[:200]}")
            return {}

        loc = locations[0]
        elements = {}
        for elem in loc.get('weatherElement', []):
            elem_name = elem.get('elementName', '')
            time_data = []
            for t in elem.get('time', []):
                param = t.get('parameter', {}) or {}
                time_data.append({
                    'time': t.get('startTime', ''),
                    'value': param.get('parameterName', ''),
                })
            elements[elem_name] = time_data

        # 新北市的預報同時套用到要顯示的每個行政區
        results = {name: elements for name in wanted}
        print(f"CWA F-C0032-001 備用成功，共用給 {list(results.keys())}")
        return results
    except Exception as e:
        print(f"CWA F-C0032-001 備用也失敗：{e}")
        return {}

def get_owm_weather(locations=None):
    """抓 OpenWeatherMap 天氣。座標查不到的地點就只靠中央氣象署那份。"""
    wanted = locations or WEATHER_LOCATIONS
    coords = {
        "淡水區": {"lat": 25.1692, "lon": 121.4418},
        "金山區": {"lat": 25.2025, "lon": 121.6418},
        "板橋區": {"lat": 25.0143, "lon": 121.4672},
    }
    results = {}
    for name, coord in coords.items():
        if name not in wanted:
            continue
        try:
            url = "https://api.openweathermap.org/data/2.5/forecast"
            params = {
                "lat": coord["lat"], "lon": coord["lon"],
                "appid": OWM_API_KEY, "units": "metric",
                "lang": "zh_tw", "cnt": 8
            }
            resp = http_utils.get(url, params=params, timeout=10)
            results[name] = resp.json()
        except Exception as e:
            print(f"OWM 失敗 ({name})：{e}")
    return results

def _parse_cwa_time(time_str):
    """CWA 通常給帶 +08:00 的本地時間；都當本地時間解析。"""
    if not time_str:
        return None
    s = time_str.replace('Z', '').split('+')[0].strip()
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _pop_for(point_time, pop_data, window_hours=12):
    """給一個 datetime，從 12 小時降雨機率資料找對應段；找不到回 None。"""
    if not point_time or not pop_data:
        return None
    for p in pop_data:
        start = _parse_cwa_time(p.get('time', ''))
        if start is None:
            continue
        end = start + timedelta(hours=window_hours)
        if start <= point_time < end:
            try:
                return int(float(p.get('value')))
            except (TypeError, ValueError):
                return None
    return None


def generate_temp_chart(cwa_data):
    """畫淡水區未來 24 小時氣溫 + 降雨機率，雙 y 軸；回傳圖片路徑。"""
    chart_path = os.path.join(tempfile.gettempdir(), 'weather_chart.png')
    font_prop = get_chinese_font()

    POINTS = 8  # 8 × 3hr = 24hr

    # 只畫一個地點：優先淡水區，沒有就拿第一個有資料的
    target = '淡水區' if '淡水區' in cwa_data else next(iter(cwa_data), None)
    if not target:
        return None
    elements = cwa_data[target]

    temps = elements.get('平均溫度') or elements.get('溫度') or elements.get('T') or []
    if not temps:
        return None
    pop_data = elements.get('12小時降雨機率') or elements.get('PoP12h') or []

    times, values = [], []
    for t in temps[:POINTS]:
        dt = _parse_cwa_time(t.get('time', ''))
        try:
            v = float(t.get('value'))
        except (TypeError, ValueError):
            continue
        if dt is None:
            continue
        times.append(dt)
        values.append(v)
    if not times:
        return None

    pop_series = [_pop_for(dt, pop_data) for dt in times]

    BG = '#0f1424'
    TEMP_COLOR = '#00d2ff'   # 青色（氣溫線）
    POP_COLOR = '#3a7bd5'    # 藍色（降雨機率柱）

    fig, ax = plt.subplots(figsize=(11, 5.4), dpi=120)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)

    x_idx = list(range(len(times)))
    x_labels = []
    last_day = None
    for dt in times:
        day = dt.strftime('%m/%d')
        if day != last_day:
            x_labels.append(f"{day}\n{dt.strftime('%H:%M')}")
            last_day = day
        else:
            x_labels.append(dt.strftime('%H:%M'))

    # 降雨機率：第二 y 軸，半透明柱狀圖（在氣溫線下層）
    ax2 = ax.twinx()
    bar_x = [i for i, p in enumerate(pop_series) if p is not None]
    bar_h = [pop_series[i] for i in bar_x]
    if bar_x:
        ax2.bar(bar_x, bar_h, width=0.85, color=POP_COLOR, alpha=0.35,
                label='降雨機率', zorder=1)
        for i, p in zip(bar_x, bar_h):
            if p > 0:
                ax2.annotate(f'{p}%', (i, p), textcoords="offset points",
                             xytext=(0, 4), ha='center', fontsize=10,
                             fontweight='bold', color='#9ec5ff',
                             fontproperties=font_prop, zorder=2)
    ax2.set_ylim(0, 110)  # 多留 10% 給數字標籤
    ax2.set_ylabel('降雨機率 (%)', fontsize=12, color='#9ec5ff',
                   fontproperties=font_prop)
    ax2.tick_params(axis='y', colors='#9ec5ff', labelsize=10)
    ax2.spines['top'].set_visible(False)
    ax2.spines['right'].set_color('#333')
    ax2.grid(False)

    # 氣溫：主 y 軸折線
    ax.plot(x_idx, values, marker='o', color=TEMP_COLOR,
            linewidth=3.2, markersize=11, label=f'{target} 氣溫', zorder=3)
    for i, v in enumerate(values):
        ax.annotate(f'{v:.0f}°', (i, v), textcoords="offset points",
                    xytext=(0, 13), ha='center', fontsize=12,
                    fontweight='bold', color=TEMP_COLOR,
                    fontproperties=font_prop, zorder=4)

    ymin, ymax = min(values), max(values)
    ax.set_ylim(ymin - 2.5, ymax + 4.0)

    ax.set_xticks(x_idx)
    ax.set_xticklabels(x_labels, fontproperties=font_prop, fontsize=13,
                       fontweight='bold', color='#f0f0f0')

    ax.set_title(f'{target} 未來 24 小時氣溫與降雨機率',
                 fontsize=17, color='white', pad=16,
                 fontweight='bold', fontproperties=font_prop)
    ax.set_ylabel('氣溫 (°C)', fontsize=12, color=TEMP_COLOR,
                  fontproperties=font_prop)

    ax.tick_params(axis='x', colors='#f0f0f0', labelsize=13, pad=8)
    ax.tick_params(axis='y', colors=TEMP_COLOR, labelsize=10)
    for s in ('top',):
        ax.spines[s].set_visible(False)
    for s in ('bottom', 'left'):
        ax.spines[s].set_color('#333')
    ax.grid(True, alpha=0.18, color='white', linestyle='--', linewidth=0.5)

    # 合併兩軸 legend
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    leg = ax.legend(h1 + h2, l1 + l2, prop=font_prop, facecolor=BG,
                    edgecolor='#555', labelcolor='white', fontsize=14,
                    loc='upper right', markerscale=1.3,
                    handlelength=2.2, borderpad=0.8, labelspacing=0.6)
    leg.get_frame().set_linewidth(1.2)
    for text in leg.get_texts():
        text.set_fontweight('bold')

    plt.tight_layout()
    plt.savefig(chart_path, facecolor=BG, bbox_inches='tight')
    plt.close()
    return chart_path


def _strip_to_bullets(text):
    """只留 • / ・ / - / * 開頭的 bullet 行；沒 bullet 一律回空字串
    （AI 回「無」、開場白、結語、純敘述等通通視為「沒有活動」）。"""
    if not text:
        return ""
    lines = [l.rstrip() for l in text.splitlines()]
    bullets = []
    started = False
    for line in lines:
        stripped = line.lstrip()
        if not stripped:
            # 空行跳過:Sonnet 5 習慣在 bullet 之間空一行,遇空行就停會只剩第一條
            continue
        if stripped.startswith(("•", "・", "-", "*")):
            bullets.append(stripped)
            started = True
        elif started:
            # 已開始列 bullet 後遇到非 bullet 行 → 視為結語，停止
            break
    return "\n".join(bullets)


# 近期活動一週才查一次:活動不會天天變,而 web_search 是天氣段最貴的部分。
# 快取在記憶體,重新部署會清掉(清掉就重查一次,不影響功能)。
# 每次查未來 14 天,之後幾天從快取拿,顯示前把已結束的活動濾掉。
EVENTS_CACHE_DAYS = 7
EVENTS_LOOKAHEAD_DAYS = 14
_EVENTS_CACHE = {}  # {tuple(locations): (查詢日 date, bullets 文字)}

_EVENT_DATE = re.compile(r"(?:(\d{4})[-/.])?(\d{1,2})[-/.](\d{1,2})")


def _event_end_date(line, today):
    """從「• 名稱｜日期｜地點｜URL」取出結束日;解析不到回 None。

    日期欄可能是 2026-09-20、09/20-09/22、2026-09-20~2026-09-22,取最後一個日期當結束日。
    沒寫年份時用今年;若因此落在半年前,視為跨年(明年)。
    """
    fields = line.split("｜")
    if len(fields) < 2:
        return None
    matches = _EVENT_DATE.findall(fields[1])
    if not matches:
        return None
    year, month, day = matches[-1]
    try:
        end = datetime(int(year) if year else today.year, int(month), int(day)).date()
    except ValueError:
        return None
    if not year and (today - end).days > 180:
        end = end.replace(year=end.year + 1)
    return end


def _drop_past_events(text, today):
    """濾掉結束日早於今天的活動;日期解析不到的保留(寧可多列不要漏)。"""
    kept = []
    for line in text.splitlines():
        end = _event_end_date(line, today)
        if end is None or end >= today:
            kept.append(line)
    return "\n".join(kept)


# 同名但不在新北的地點。2026-09-16 實測 Haiku 被明講要排除「金山灣區」仍會列出來,改用程式擋。
EVENT_EXCLUDE_WORDS = ("灣區", "舊金山", "僑社", "僑胞")


def _clean_event_lines(text):
    """統一分隔符號成全形「｜」、濾掉海外同名地點、同名活動只留第一筆。"""
    kept, seen = [], set()
    for line in text.splitlines():
        line = line.replace("|", "｜")
        if any(w in line for w in EVENT_EXCLUDE_WORDS):
            continue
        name = re.sub(r"\W+", "", line.split("｜")[0])
        if name in seen:
            continue
        seen.add(name)
        kept.append(line)
    return "\n".join(kept)


def _fetch_local_events(locations, today):
    """抓新聞標題給 Haiku 篩活動。成功回 bullets 文字(沒活動回 ""),失敗回 None(不寫快取)。

    不用 web_search:程式自己抓 Google News RSS(免費),模型只做篩選 + 排版。
    標題常有同名雜訊(例:「金山灣區」是舊金山),交給模型依地點判斷。
    """
    from stock_news import _google_news_rss

    today_s = today.strftime("%Y-%m-%d")
    locs = "、".join(locations)
    days = EVENTS_LOOKAHEAD_DAYS
    seen, lines = set(), []
    for loc in locations:
        name = loc[:-1] if loc.endswith("區") else loc
        for q in (f"{name} 活動 when:{days}d", f"{name} 市集 OR 展覽 OR 音樂節 OR 藝術節 when:{days}d"):
            for it in _google_news_rss(q, limit=8):
                title = (it.get("title") or "").strip()
                if not title or title in seen:
                    continue
                seen.add(title)
                ts = it.get("published") or 0
                pub = datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else "日期不明"
                lines.append(f"- （{pub} 發布）{title}")
    if not lines:
        return ""

    prompt = (
        f"今天是 {today_s}（台北時間）。以下是跟「{locs}」（新北市）有關的新聞標題：\n"
        + "\n".join(lines) + "\n\n"
        f"請從中挑出「在{locs}舉辦、且 {today_s} 起未來 {days} 天內仍會舉辦」的活動"
        f"（節慶、市集、表演、展覽、廟會、馬拉松等）。\n\n"
        f"嚴格規則（必遵守）：\n"
        f"1. 只能根據上面的標題，禁止補充標題裡沒有的活動。地點不在{locs}的（例：金山灣區是舊金山）一律排除。\n"
        f"2. 已結束（結束日早於 {today_s}）的活動絕對不能列；只是新聞評論、不是活動的也不列。\n"
        f"3. 第一個字元必須是「•」或「無」。禁止任何開場白、解釋過程。\n"
        f"4. 沒有符合的 → 只輸出兩個字：「無」（不加句點、不加其他字）。\n"
        f"5. 有的話最多 5 個，依日期先後排序，每個一行，格式：\n"
        f"   • 活動名稱｜日期（YYYY-MM-DD 或 MM/DD-MM/DD，標題沒寫日期就寫「日期見新聞」）｜地點\n"
        f"6. 禁止結語（不要寫「希望對你有幫助」「請查證」等）。"
    )
    try:
        # 只是篩選 + 排版,用 Haiku、不給工具
        message = sonnet_client.create(
            ANTHROPIC_API_KEY, prompt, max_tokens=800, model=sonnet_client.HAIKU,
        )
        # web_search 是 server-side tool；content 含多個 block，取最後一個 text
        text = ""
        for block in message.content:
            if getattr(block, 'type', None) == 'text':
                text = block.text
        return _clean_event_lines(_strip_to_bullets(text.strip()))
    except Exception as e:
        print(f"近期活動查詢失敗：{e}")
        return None


def get_local_events(locations):
    """近期活動(最多 5 個)或 ""。同一組地點 7 天內只打一次 web_search。"""
    if not locations:
        return ""
    today = now_tpe().date()
    key = tuple(locations)
    cached = _EVENTS_CACHE.get(key)
    if cached and 0 <= (today - cached[0]).days < EVENTS_CACHE_DAYS:
        return _drop_past_events(cached[1], today)
    text = _fetch_local_events(locations, today)
    if text is None:
        return ""
    _EVENTS_CACHE[key] = (today, text)
    return _drop_past_events(text, today)


def get_weather_report(locations=None):
    """取得完整天氣報告（文字 + 圖片路徑）。

    locations 不給就用 WEATHER_LOCATIONS（群組版：淡水、金山）；
    個人版傳 PERSONAL_WEATHER_LOCATIONS（板橋）。
    """
    wanted = locations or WEATHER_LOCATIONS
    cwa_data = get_cwa_weather(wanted)
    if not cwa_data:
        print("詳細預報（F-D0047-071）沒資料，改用 36 小時備用預報（F-C0032-001）")
        cwa_data = get_cwa_weather_fallback(wanted)
    owm_data = get_owm_weather(wanted)

    # 畫折線圖（只有詳細預報含逐 3 小時溫度才能畫）
    chart_path = None
    has_hourly_temp = any(
        elements.get('平均溫度') or elements.get('溫度') or elements.get('T')
        for elements in cwa_data.values()
    )
    if cwa_data and has_hourly_temp:
        try:
            chart_path = generate_temp_chart(cwa_data)
        except Exception as e:
            print(f"畫圖失敗：{e}")
    elif cwa_data:
        print("備用預報沒有逐小時溫度，本次不產圖")

    # AI 整理
    today = now_tpe().strftime("%Y-%m-%d")
    prompt = WEATHER_PROMPT.format(
        date=today,
        cwa_data=str(cwa_data)[:3000],
        owm_data=str(owm_data)[:1500],
        locations="、".join(wanted)
    )

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        # 天氣整理是「按格式重組資料」型任務，不需要深度推理
        # 改 haiku 4.5 省 ~75% token cost（input $3→$0.8/M、output $15→$4/M）
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )
        usage_tracker.track("claude-haiku-4-5-20251001", message)
        weather_text = message.content[0].text
    except Exception as e:
        print(f"AI 天氣整理失敗：{e}")
        weather_text = "天氣資料暫時無法取得"

    # 接在「今日重點提醒」之後加「📅 近期活動」；AI 回「無」就整段不顯示
    events = get_local_events(wanted)
    if events and events.strip() not in ("", "無", "無。", "無.", "無\n"):
        weather_text = f"{weather_text}\n\n📅 近期活動\n{events}"

    return weather_text, chart_path
