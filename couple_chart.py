"""
情侶群組的「圖表」：本月共同支出依品項畫成圓餅圖，以圖片訊息貼進群組。

自己用 Pillow 畫、自己的伺服器出圖，不用 QuickChart 之類的外部服務：
那些服務的字型不一定有中文，品項名會變方塊字；Railway 上已經為 Rich Menu
裝了文泉驛字型（nixpacks.toml），直接沿用。

LINE 的圖片訊息只收網址，所以畫好的 PNG 先放在記憶體，由 /chart/<token>.png
提供下載。token 是隨機的，猜不到就看不到；只留最近 20 張，重新部署就清空
—— 舊訊息裡的圖片 LINE 已經自己快取了，不受影響。
"""

import io
import os
import secrets
import threading
from collections import OrderedDict

# 依序配色。刻意避開大面積紅色：圓餅圖裡紅色會被讀成「警告」。
_PALETTE = ["#177CB0", "#F2A93B", "#2B9A66", "#D9534F", "#7E5BB5",
            "#4FB0C6", "#C98A5B", "#8A96A0"]
_MAX_SLICES = 7            # 再多一片就讀不出來了，剩下的併成「其他」

_STORE = OrderedDict()
_STORE_MAX = 20
_LOCK = threading.Lock()


def _public_base():
    domain = (os.environ.get("RAILWAY_PUBLIC_DOMAIN")
              or "chengreportbot-production.up.railway.app")
    return f"https://{domain}"


def put(png):
    token = secrets.token_urlsafe(16)
    with _LOCK:
        _STORE[token] = png
        while len(_STORE) > _STORE_MAX:
            _STORE.popitem(last=False)
    return token


def get(token):
    with _LOCK:
        return _STORE.get(token)


def slices(rows):
    """共同的列 → [(品項, 金額)]，大到小，超過 _MAX_SLICES 的併成「其他」。

    依品項不依類別：群組手打的帳類別只猜得出「餐飲 / 其他」，
    依類別畫會是一大塊「其他」，什麼都看不出來。
    """
    totals = {}
    for r in rows or []:
        if r.get("kind") == "個人" or not r.get("total"):
            continue
        name = (r.get("item") or "未命名").strip()
        # 國泰店名「全聯福利中心－板橋板新」太長，圖例放不下；取分隔號前面
        for sep in ("－", "-", "（", "("):
            if sep in name and name.index(sep) > 1:
                name = name[:name.index(sep)]
        totals[name] = totals.get(name, 0) + r["total"]
    ranked = sorted(totals.items(), key=lambda kv: -kv[1])
    if len(ranked) > _MAX_SLICES:
        rest = sum(v for _k, v in ranked[_MAX_SLICES - 1:])
        ranked = ranked[:_MAX_SLICES - 1] + [("其他", rest)]
    return ranked


def render(parts, title, footer=""):
    """畫一張 1040×1040 的 PNG：上方標題、中間圓餅、下方圖例。回 bytes。"""
    from PIL import Image, ImageDraw

    from setup_richmenu import find_font

    W = H = 1040
    img = Image.new("RGB", (W, H), "#FFFFFF")
    d = ImageDraw.Draw(img)
    title_font, legend_font, small = find_font(52), find_font(40), find_font(34)

    d.text((60, 50), title, fill="#1B2329", font=title_font)

    total = sum(v for _k, v in parts) or 1
    box = (60, 150, 560, 650)                      # 圓餅在左
    start = -90.0                                  # 從 12 點鐘方向開始，順時針
    for i, (_name, value) in enumerate(parts):
        sweep = 360.0 * value / total
        d.pieslice(box, start, start + sweep, fill=_PALETTE[i % len(_PALETTE)],
                   outline="#FFFFFF", width=4)
        start += sweep
    # 中間挖空成甜甜圈：中心放總額，比純圓餅多一個資訊位
    d.ellipse((205, 295, 415, 505), fill="#FFFFFF")
    label = f"{int(total):,}"
    tw = d.textlength(label, font=legend_font)
    d.text((310 - tw / 2, 375), label, fill="#1B2329", font=legend_font)

    y = 170                                        # 圖例在右
    for i, (name, value) in enumerate(parts):
        color = _PALETTE[i % len(_PALETTE)]
        d.rounded_rectangle((620, y + 6, 656, y + 42), radius=8, fill=color)
        d.text((675, y), name[:8], fill="#1B2329", font=legend_font)
        pct = f"{value * 100 / total:.0f}%  NT${int(value):,}"
        d.text((675, y + 48), pct, fill="#5D6B75", font=small)
        y += 108

    if footer:
        # 底部那塊本來是空的，放「誰付了多少」：看圖的人第二個問題就是這個
        d.line((60, 900, W - 60, 900), fill="#D5DEE4", width=2)
        d.text((60, 930), footer, fill="#1B2329", font=legend_font)

    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def chart_messages(rows, label):
    """結算期間的列 → [圖片訊息]。沒有共同支出回 None（呼叫端改回文字）。"""
    parts = slices(rows)
    if not parts:
        return None
    total = sum(v for _k, v in parts)
    paid = {}
    for r in rows or []:
        if r.get("kind") == "個人" or not r.get("total"):
            continue
        who = r.get("payer") or "未知"
        paid[who] = paid.get(who, 0) + r["total"]
    footer = "　".join(f"{w} 付 NT${int(v):,}"
                      for w, v in sorted(paid.items(), key=lambda kv: -kv[1])[:3])
    png = render(parts, f"{label}共同支出 NT${int(total):,}", footer)
    url = f"{_public_base()}/chart/{put(png)}.png"
    return {"type": "image", "originalContentUrl": url, "previewImageUrl": url}
