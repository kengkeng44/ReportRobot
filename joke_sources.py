"""笑話素材來源:PTT joke 板熱門文。

為什麼要抓論壇而不是叫 AI 生:AI 生出來的笑話沒有「真的有人覺得好笑」
這個訊號,而且分佈很窄(見 humor.py 開頭)。PTT 的推文數就是現成的
群眾投票,[猜謎] 跟 [耍冷] 兩個分類又剛好是諧音梗大本營。

Dcard 不納入:官方 API 已被 Cloudflare 擋(403),沒有穩定的替代路徑。

抓下來只是「素材」,推播前一定要經過 humor.py 的 AI 篩選 ——
joke 板成人梗比例不低,直接推會出事。
"""

import re

import requests
from bs4 import BeautifulSoup

BOARD_URL = "https://www.ptt.cc/bbs/joke"
_HEADERS = {"User-Agent": "Mozilla/5.0", "Cookie": "over18=1"}

# 只要純文字笑話。趣圖/影音是圖片與連結,推到 LINE 只會看到標題;
# 公告/閒聊不是笑話。全形半形 XD 都要收(板上兩種寫法都有)。
_WANTED_TAGS = ("猜謎", "耍冷", "ＸＤ", "ＸD", "XD", "Ｘd", "趣事", "笑話", "翻譯")
_BLOCKED_TAGS = ("趣圖", "影音", "公告", "閒聊", "新聞", "問題", "討論", "情報")

# 內文清乾淨後短於這個長度的多半是「如題」+ 圖片連結
_MIN_BODY = 12
# 太長的推到 LINE 會佔掉整張卡片
_MAX_BODY = 400


def _title_tag(title):
    m = re.match(r"\s*\[(.*?)\]", title or "")
    return m.group(1) if m else ""


def _wanted(title):
    """這篇標題該不該進候選池。"""
    if not title or title.startswith("Fw:") or "本文已被刪除" in title:
        return False
    tag = _title_tag(title)
    if tag in _BLOCKED_TAGS:
        return False
    # 無 tag 的文章也收(板上有一成左右沒下 tag),但有 tag 就必須在白名單裡
    return tag in _WANTED_TAGS or tag == ""


def _parse_heat(text):
    """推文數。『爆』= 100+,『XX』是被噓爆,當作負分排到最後。"""
    t = (text or "").strip()
    if t == "爆":
        return 100
    if t.startswith("X"):
        return -100
    return int(t) if t.lstrip("-").isdigit() else 0


def _latest_page():
    """joke 板目前最新的頁碼。抓不到就回 None。"""
    resp = requests.get(f"{BOARD_URL}/index.html", headers=_HEADERS, timeout=10)
    soup = BeautifulSoup(resp.text, "html.parser")
    for b in soup.select("div.btn-group-paging a"):
        if "上頁" in b.text:
            m = re.search(r"index(\d+)", b.get("href", ""))
            if m:
                return int(m.group(1)) + 1
    return None


def _list_candidates(pages):
    """翻 pages 頁,回 [(heat, title, href)],已過濾掉不要的分類。"""
    page = _latest_page()
    if not page:
        return []
    out = []
    for offset in range(pages):
        num = page - offset
        if num < 1:
            break
        try:
            resp = requests.get(f"{BOARD_URL}/index{num}.html",
                                headers=_HEADERS, timeout=10)
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception as e:
            print(f"[joke] 第 {num} 頁抓取失敗:{e}")
            continue
        for ent in soup.select("div.r-ent"):
            link = ent.select_one("div.title a")
            if not link:
                continue  # 已刪除的文章沒有 a
            title = link.text.strip()
            if not _wanted(title):
                continue
            nrec = ent.select_one("div.nrec span")
            out.append((_parse_heat(nrec.text if nrec else ""),
                        title, link.get("href", "")))
    return out


def _series_key(title):
    """系列文的共同 key:標題去掉數字。

    「推特上在夯什麼 Part.2269 / 2287 / 2277…」這種每日連載,推文數都不低,
    不去重的話光它就能把整個候選池洗版(實測 10 則有 9 則是同一個系列)。
    """
    return re.sub(r"\d+", "", title or "").strip()


def _clean_body(html):
    """把 PTT 內文洗成純笑話。回 (內文, 圖片連結數)。

    圖片數要一起回:圖片系列文洗完只剩「今天只有兩篇/大概是這樣」這種
    廢話,長度卻過得了門檻,得靠「連結多但字少」才認得出來。
    """
    soup = BeautifulSoup(html, "html.parser")
    main = soup.select_one("#main-content")
    if not main:
        return "", 0
    for tag in main.select("div.article-metaline, div.article-metaline-right, "
                           "div.push, span.f2"):
        tag.decompose()

    text = main.get_text()
    # 簽名檔:分隔線長度沒有標準(-- / ---- / -----),APP 還會自己加
    # 「Sent from JPTT on my iPhone」,兩種都要切
    text = re.split(r"\n-{2,}\s*\n", text)[0]
    text = re.split(r"\n\s*-*\s*Sent from ", text)[0]
    links = len(re.findall(r"https?://\S+", text))
    text = re.sub(r"https?://\S+", "", text)          # 圖片/引用連結
    text = re.sub(r"^\s*[.。·]\s*$", "", text, flags=re.M)  # 防雷用的點
    text = re.sub(r"^\s*※.*$", "", text, flags=re.M)   # ※ 發信站 / 引述
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(), links


def _fetch_body(href):
    try:
        resp = requests.get(f"https://www.ptt.cc{href}",
                            headers=_HEADERS, timeout=10)
        return _clean_body(resp.text)
    except Exception as e:
        print(f"[joke] 內文抓取失敗 {href}:{e}")
        return "", 0


def _is_image_post(body, links):
    """笑點在圖片裡、文字只是陪襯的文章。推到 LINE 只會看到莫名其妙的一句。"""
    return links >= 2 and len(body) < 80


def fetch_ptt_jokes(pages=8, limit=10, exclude_links=()):
    """撈 joke 板熱門純文字笑話,推文數高的優先。

    exclude_links:已經推播過的文章連結,直接跳過(同一篇不要挑第二次)。
    回 [{"title", "body", "heat", "link"}],失敗一律回 []
    —— 呼叫端會 fallback 到 AI 生成,不該讓整段消失。
    """
    try:
        rows = _list_candidates(pages)
    except Exception as e:
        print(f"[joke] PTT 列表抓取失敗:{e}")
        return []

    rows.sort(key=lambda r: -r[0])
    seen = set(exclude_links or ())
    seen_series = set()
    jokes = []
    for heat, title, href in rows:
        if len(jokes) >= limit:
            break
        link = f"https://www.ptt.cc{href}"
        if link in seen:
            continue
        # 同一個連載只留推文數最高的那篇(rows 已排序,先遇到的就是)
        series = _series_key(title)
        if series in seen_series:
            continue
        seen_series.add(series)

        body, links = _fetch_body(href)
        # 太短多半是圖片文,太長推到 LINE 會爆版
        if not (_MIN_BODY <= len(body) <= _MAX_BODY):
            continue
        if _is_image_post(body, links):
            continue
        jokes.append({"title": title, "body": body, "heat": heat, "link": link})
    return jokes
