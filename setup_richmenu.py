"""
LINE Rich Menu 一次性設定腳本（分頁式）。

主選單 + 財務 / 煮飯 / 投資 / 更多 四個子選單，用 richmenuswitch 互相切換，
共 24 個入口，大部分需求按一按就完成，不用打字。
**Rich Menu 與分頁切換都完全不計入 push 配額**（channel 層設定，不是訊息）。

執行方式擇一：
1. 本機 CLI：`LINE_CHANNEL_TOKEN=... python setup_richmenu.py`
2. Railway 部署後從 admin endpoint 觸發：
   POST /admin/setup-richmenu  with X-Admin-Token header

行為（idempotent）：
- 刪除既有所有 alias 與 rich menus
- 每個分頁生成 2500×1686 PNG（PIL，6 格純色塊 + 中文標籤）
- 上傳給 LINE 拿 richMenuId，並建立與 MENUS key 同名的 alias
- 主選單設為所有 user 的 default

要改格子內容（標籤 / 色彩 / 動作）改 MENUS 常數即可；
新增分頁只要在 MENUS 加一個 key，並在 main 裡放一格 switch 指過去。
"""

import os

import requests


LINE_API_BASE = "https://api.line.me/v2/bot"
LINE_DATA_API_BASE = "https://api-data.line.me/v2/bot"

LINE_CHANNEL_TOKEN = os.environ.get("LINE_CHANNEL_TOKEN", "")

# 圖片尺寸（LINE 建議：2500×1686 大版型，或 2500×843 半版）
W, H = 2500, 1686
COLS, ROWS = 3, 2
CELL_W = W // COLS  # 833
CELL_H = H // ROWS  # 843


# 分頁式選單：主選單 + 4 個子選單，用 richmenuswitch 互相切換。
# 切換是 channel 層行為，**完全不計入 push 配額**，所以層數多也不花錢。
#
# 每格格式：(主標, 副標 EN, 背景色, (動作類型, 參數))
#   ("message", "/待辦")   → 送出文字訊息，走一般 webhook → command_router
#   ("switch",  "kitchen") → 切換到別的分頁（參數是 MENUS 的 key，同時也是 alias id）
#   ("prompt",  "買了 ")   → 打開鍵盤並預填文字，使用者接著補後半段就送出
#
# ⚠️ 標籤只能放中文與英文：PNG 是用 CJK 字型畫的，沒有彩色 emoji 字型，
#    放 emoji 或箭頭會變成豆腐字（test_labels_have_no_emoji 會擋）。

_BACK = ("返回", "BACK", "#8A7A6E", ("switch", "main"))

MENUS = {
    "main": {
        "name": "全能大管家 主選單",
        "chat_bar": "全能大管家",
        "cells": [
            ("財務", "FINANCE", "#A0826D", ("switch", "finance")),
            ("煮飯", "KITCHEN", "#88B07A", ("switch", "kitchen")),
            ("投資", "INVEST",  "#D9534F", ("switch", "invest")),
            ("待辦", "TODO",    "#5B8DA6", ("message", "/待辦")),
            ("今日", "TODAY",   "#F0AD4E", ("message", "/預覽")),
            ("更多", "MORE",    "#8A7A6E", ("switch", "more")),
        ],
    },
    "finance": {
        "name": "全能大管家 財務",
        "chat_bar": "財務",
        "cells": [
            ("本月支出", "SPENDING", "#A0826D", ("message", "/本月支出")),
            ("最近交易", "RECENT",   "#B08D6D", ("message", "/最近交易")),
            ("卡費",     "CARD",     "#C09A7A", ("message", "/卡費")),
            ("淨值",     "NETWORTH", "#8A6F5A", ("message", "/淨值")),
            ("記一筆",   "ADD",      "#9A7B63", ("prompt",  "記一筆 ")),
            _BACK,
        ],
    },
    "kitchen": {
        "name": "全能大管家 煮飯",
        "chat_bar": "煮飯",
        "cells": [
            ("庫存",   "PANTRY",   "#88B07A", ("message", "/庫存")),
            ("快過期", "EXPIRING", "#6F9A62", ("message", "/快過期")),
            ("煮什麼", "COOK",     "#7FA870", ("message", "/煮什麼")),
            # 送死指令：「買了」不帶東西會回一排常買清單按鈕，點一下就入庫。
            # 用 prompt 開鍵盤反而讓人卡在「要打什麼」。
            ("買了",   "BUY",      "#96BC88", ("message", "買了")),
            ("採購",   "SHOPPING", "#5F8A54", ("message", "/採購")),
            _BACK,
        ],
    },
    "invest": {
        "name": "全能大管家 投資",
        "chat_bar": "投資",
        "cells": [
            ("持股",   "HOLDINGS",  "#D9534F", ("message", "仁和持股")),
            ("查個股", "QUOTE",     "#C64540", ("prompt",  "/")),
            # 比較要帶兩個代號才有意義，裸指令沒東西可比 —— 開鍵盤讓使用者補
            ("比較",   "COMPARE",   "#E0645F", ("prompt",  "/比較 ")),
            ("盤前",   "PREMARKET", "#B33F3B", ("message", "/盤前")),  # → premarket
            ("大盤",   "MARKET",    "#EC7370", ("message", "/大盤")),  # → markets（免費）
            _BACK,
        ],
    },
    "more": {
        "name": "全能大管家 更多",
        "chat_bar": "更多",
        "cells": [
            ("提醒", "REMIND",  "#5B8DA6", ("message", "/提醒")),
            ("額度", "QUOTA",   "#4A7A92", ("message", "/額度")),
            ("成本", "COST",    "#6B9CB5", ("message", "/cost")),
            ("預覽", "PREVIEW", "#3E6A80", ("message", "/預覽")),
            ("說明", "HELP",    "#7DAEC4", ("message", "/help")),
            _BACK,
        ],
    },
}


_CHINESE_FONT_CANDIDATES = [
    # Linux（Railway/Nixpacks 通常有 noto-cjk）
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    # macOS
    "/System/Library/Fonts/PingFang.ttc",
    "/Library/Fonts/Microsoft/微軟正黑體.ttf",
    # Windows
    "C:\\Windows\\Fonts\\msjh.ttc",
    "C:\\Windows\\Fonts\\msyh.ttc",
]


class NoChineseFontError(RuntimeError):
    """找不到 CJK 字型。

    刻意丟例外而不是 fallback 到 ImageFont.load_default()：
    default 是固定尺寸的點陣字型，會忽略指定的字級，在 2500px 的圖上
    畫出來只有約 10px，等於看不見。以前的 fallback 會**默默上傳一張
    只有色塊沒有字的選單**，而且沒有任何錯誤 —— 那比直接失敗糟得多。
    """


def find_font(size):
    """找系統 CJK 字型。找不到就丟 NoChineseFontError。"""
    from PIL import ImageFont
    for p in _CHINESE_FONT_CANDIDATES:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception as e:
                print(f"[richmenu] 字型 {p} 載入失敗：{e}")
                continue
    raise NoChineseFontError(
        "找不到任何 CJK 字型，畫出來的選單會只有色塊沒有字。\n"
        "Railway/Nixpacks 請確認 nixpacks.toml 有裝 fonts-wqy-zenhei；\n"
        f"已嘗試的路徑：{_CHINESE_FONT_CANDIDATES}"
    )


# 舊名保留，避免其他地方引用到
_find_font = find_font


def generate_image(cells, out_path):
    """畫 2500×1686 PNG，6 格純色塊 + 中文大字 + EN 副標。"""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)
    # 四字標籤（「本月支出」）比兩字的窄很多，字級要跟著縮，不然會爆格
    longest = max(len(label) for label, _s, _c, _a in cells)
    main_font = _find_font(280 if longest <= 2 else 170)
    sub_font = _find_font(72)

    for i, (label, sub, color, _action) in enumerate(cells):
        r = i // COLS
        c = i % COLS
        x0, y0 = c * CELL_W, r * CELL_H
        x1, y1 = x0 + CELL_W, y0 + CELL_H

        # 區塊底色
        draw.rectangle([x0, y0, x1, y1], fill=color)
        # 內縮邊框（白色細線）讓格子之間有區隔
        draw.rectangle([x0 + 6, y0 + 6, x1 - 6, y1 - 6], outline="white", width=4)

        # 主標（置中偏上）
        bbox = draw.textbbox((0, 0), label, font=main_font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        tx = x0 + (CELL_W - tw) // 2 - bbox[0]
        ty = y0 + (CELL_H - th) // 2 - bbox[1] - 60
        draw.text((tx, ty), label, fill="white", font=main_font)

        # 副標（底部置中）
        bbox2 = draw.textbbox((0, 0), sub, font=sub_font)
        sw = bbox2[2] - bbox2[0]
        sh = bbox2[3] - bbox2[1]
        sx = x0 + (CELL_W - sw) // 2 - bbox2[0]
        sy = y0 + CELL_H - sh - 70 - bbox2[1]
        draw.text((sx, sy), sub, fill="white", font=sub_font)

    img.save(out_path, "PNG", optimize=True)
    return out_path


def _headers():
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_TOKEN}",
    }


def _delete_existing_menus():
    """LINE 允許多 menu 並存，這裡先全清避免殘留。"""
    r = requests.get(f"{LINE_API_BASE}/richmenu/list", headers=_headers(), timeout=10)
    if r.status_code != 200:
        print(f"[richmenu] 取既有 menu 列表失敗 {r.status_code}：{r.text[:200]}")
        return
    menus = r.json().get("richmenus") or []
    for m in menus:
        mid = m.get("richMenuId")
        if not mid:
            continue
        dr = requests.delete(
            f"{LINE_API_BASE}/richmenu/{mid}",
            headers=_headers(),
            timeout=10,
        )
        print(f"[richmenu] 刪 {mid[:12]}... → {dr.status_code}")


def build_areas(cells):
    """把 6 格轉成 LINE 的 action area 定義（row-major）。

    switch 用 richmenuswitch，alias id 直接沿用 MENUS 的 key —— 少一層對照表。
    """
    areas = []
    for i, (_label, _sub, _color, action) in enumerate(cells):
        kind, param = action
        if kind == "message":
            line_action = {"type": "message", "text": param}
        elif kind == "switch":
            line_action = {
                "type": "richmenuswitch",
                "richMenuAliasId": param,
                "data": f"switch={param}",
            }
        elif kind == "prompt":
            # 需要接著打字的功能（買了什麼、記多少錢、查哪支股票）。
            # 按下去會打開鍵盤並預先填好開頭，使用者只要補後半段。
            # 不帶 displayText，避免聊天室多出一則沒意義的回音。
            line_action = {
                "type": "postback",
                "data": f"prompt={param.strip()}",
                "inputOption": "openKeyboard",
                "fillInText": param,
            }
        else:
            raise ValueError(f"未知的 action 類型：{kind}")

        r, c = divmod(i, COLS)
        areas.append({
            "bounds": {
                "x": c * CELL_W, "y": r * CELL_H,
                "width": CELL_W, "height": CELL_H,
            },
            "action": line_action,
        })
    return areas


def _create_menu_definition(menu):
    """POST /richmenu — 建定義；回 richMenuId。"""
    payload = {
        "size": {"width": W, "height": H},
        "selected": True,
        "name": menu["name"],
        "chatBarText": menu["chat_bar"],
        "areas": build_areas(menu["cells"]),
    }
    r = requests.post(
        f"{LINE_API_BASE}/richmenu",
        headers=_headers(),
        json=payload,
        timeout=15,
    )
    if r.status_code != 200:
        raise RuntimeError(f"建立 richmenu 失敗 {r.status_code}: {r.text[:400]}")
    return r.json()["richMenuId"]


def _delete_existing_aliases():
    """alias 必須先清掉，否則同 id 再建會 409。"""
    r = requests.get(f"{LINE_API_BASE}/richmenu/alias/list", headers=_headers(), timeout=10)
    if r.status_code != 200:
        print(f"[richmenu] 取 alias 列表失敗 {r.status_code}：{r.text[:200]}")
        return
    for a in r.json().get("aliases") or []:
        aid = a.get("richMenuAliasId")
        if not aid:
            continue
        dr = requests.delete(
            f"{LINE_API_BASE}/richmenu/alias/{aid}",
            headers=_headers(),
            timeout=10,
        )
        print(f"[richmenu] 刪 alias {aid} → {dr.status_code}")


def _create_alias(alias_id, menu_id):
    r = requests.post(
        f"{LINE_API_BASE}/richmenu/alias",
        headers=_headers(),
        json={"richMenuAliasId": alias_id, "richMenuId": menu_id},
        timeout=10,
    )
    if r.status_code != 200:
        raise RuntimeError(f"建立 alias {alias_id} 失敗 {r.status_code}: {r.text[:300]}")


def _upload_image(menu_id, image_path):
    """上傳圖到 menu_id（注意是 api-data.line.me，不是 api.line.me）。"""
    with open(image_path, "rb") as f:
        data = f.read()
    r = requests.post(
        f"{LINE_DATA_API_BASE}/richmenu/{menu_id}/content",
        headers={
            "Content-Type": "image/png",
            "Authorization": f"Bearer {LINE_CHANNEL_TOKEN}",
        },
        data=data,
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"上傳圖失敗 {r.status_code}: {r.text[:400]}")


def _set_default(menu_id):
    """設為所有 user 的 default menu。"""
    r = requests.post(
        f"{LINE_API_BASE}/user/all/richmenu/{menu_id}",
        headers=_headers(),
        timeout=10,
    )
    if r.status_code != 200:
        raise RuntimeError(f"設為 default 失敗 {r.status_code}: {r.text[:400]}")


def setup():
    """主流程：建立所有分頁 + alias，主選單設為 default。回 {key: menu_id}。

    順序有講究 —— alias 必須在所有 menu 都建好之後才建，
    因為 alias 是指到 menu_id 的。
    """
    if not LINE_CHANNEL_TOKEN:
        raise RuntimeError("LINE_CHANNEL_TOKEN 未設定")

    tmp_dir = "/tmp" if os.path.isdir("/tmp") else "."

    print("[richmenu] 清除舊 alias 與 menu...")
    _delete_existing_aliases()
    _delete_existing_menus()

    menu_ids = {}
    for key, menu in MENUS.items():
        img_path = os.path.join(tmp_dir, f"richmenu_{key}.png")
        generate_image(menu["cells"], img_path)
        size_kb = os.path.getsize(img_path) / 1024
        if size_kb > 1024:
            print(f"[richmenu] ⚠️ {key} 圖 {size_kb:.0f} KB 超過 1MB，LINE 會拒收")

        menu_id = _create_menu_definition(menu)
        _upload_image(menu_id, img_path)
        menu_ids[key] = menu_id
        print(f"[richmenu] {key} → {menu_id[:16]}... ({size_kb:.0f} KB)")

    # alias id 直接用 MENUS 的 key，跟 build_areas 的 richMenuAliasId 對得上
    for key, menu_id in menu_ids.items():
        _create_alias(key, menu_id)
        print(f"[richmenu] alias {key} ✓")

    _set_default(menu_ids["main"])
    print(f"[richmenu] ✅ 完成，{len(menu_ids)} 個分頁，default = main")
    return menu_ids


if __name__ == "__main__":
    setup()
