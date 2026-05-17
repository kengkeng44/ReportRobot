"""
LINE Rich Menu 一次性設定腳本。

對話框下方的固定 6 格選單，按一下送對應指令訊息（觸發 webhook → reply）。
**完全不計入 push 配額**（Rich Menu 是 channel 層的設定，不是訊息）。

執行方式擇一：
1. 本機 CLI：`LINE_CHANNEL_TOKEN=... python setup_richmenu.py`
2. Railway 部署後從 admin endpoint 觸發：
   POST /admin/setup-richmenu  with X-Admin-Token header

行為（idempotent）：
- 刪除既有所有 rich menus
- 生成 2500×1686 PNG（PIL，6 格純色塊 + 中文標籤）
- 上傳給 LINE 拿 richMenuId
- 設為所有 user 的 default

要改格子內容（指令文字 / 色彩 / 標籤）改 CELLS 常數即可。
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


# 6 格按鈕：(主標, 副標 EN, 背景色, 按下送出的訊息)
# 訊息送出後走一般 webhook → command_router 解析
CELLS = [
    ("待辦",  "TODO",    "#A0826D", "/待辦"),
    ("提醒",  "REMIND",  "#88B07A", "/提醒"),
    ("持股",  "STOCK",   "#D9534F", "仁和持股"),
    ("預覽",  "PREVIEW", "#5B8DA6", "/預覽"),
    ("用量",  "QUOTA",   "#F0AD4E", "/額度"),
    ("說明",  "HELP",    "#8A7A6E", "/help"),
]


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


def _find_font(size):
    """嘗試找系統中文字型；都找不到回 default（會缺中文，但流程不炸）。"""
    from PIL import ImageFont
    for p in _CHINESE_FONT_CANDIDATES:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception as e:
                print(f"[richmenu] 字型 {p} 載入失敗：{e}")
                continue
    print("[richmenu] ⚠️ 找不到中文字型，用 default font（中文會缺字）")
    return ImageFont.load_default()


def generate_image(out_path):
    """畫 2500×1686 PNG，6 格純色塊 + 中文大字 + EN 副標。"""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)
    main_font = _find_font(280)
    sub_font = _find_font(72)

    for i, (label, sub, color, _action) in enumerate(CELLS):
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


def _create_menu_definition():
    """POST /richmenu — 建定義（尺寸 + 6 個 action area）；回 richMenuId。"""
    areas = []
    for i, (_label, _sub, _color, action_text) in enumerate(CELLS):
        r = i // COLS
        c = i % COLS
        areas.append({
            "bounds": {
                "x": c * CELL_W, "y": r * CELL_H,
                "width": CELL_W, "height": CELL_H,
            },
            "action": {
                "type": "message",
                "text": action_text,
            },
        })
    payload = {
        "size": {"width": W, "height": H},
        "selected": True,                 # 預設展開
        "name": "ReportRobot 主選單",
        "chatBarText": "選單",
        "areas": areas,
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
    """主流程；回 menu_id。"""
    if not LINE_CHANNEL_TOKEN:
        raise RuntimeError("LINE_CHANNEL_TOKEN 未設定")
    img_path = "/tmp/richmenu.png" if os.path.isdir("/tmp") else "richmenu.png"
    print(f"[richmenu] 生成圖 → {img_path}")
    generate_image(img_path)
    img_size = os.path.getsize(img_path)
    print(f"[richmenu] 圖大小 {img_size / 1024:.0f} KB")
    if img_size > 1024 * 1024:
        print("[richmenu] ⚠️ 圖大於 1MB，LINE 會拒收；考慮降畫質或縮尺寸")

    print("[richmenu] 清除舊 menu...")
    _delete_existing_menus()

    print("[richmenu] 建立 menu definition...")
    menu_id = _create_menu_definition()
    print(f"[richmenu] menu_id = {menu_id}")

    print("[richmenu] 上傳圖...")
    _upload_image(menu_id, img_path)

    print("[richmenu] 設為 default...")
    _set_default(menu_id)

    print("[richmenu] ✅ 完成")
    return menu_id


if __name__ == "__main__":
    setup()
