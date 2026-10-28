"""LINE「儲存聊天」匯出檔解析：切日、抓轉帳（名字是收款人）。資料都是假的。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import line_scan as ls  # noqa: E402

SAMPLE = "\n".join([
    "2026.10.26 星期一",
    "09:00 阿明 早安",
    "09:01 小美 收到NT$ 1,250的轉帳。",
    "12:30 阿明 午餐吃什麼",
    "\t第二行接在上一則後面",
    "2026.10.27 星期二",
    "20:10 阿明 收到NT$ 300的轉帳。",
    "20:11 小美 機票大概 NT$ 2,000 ~ NT$ 2,300",
])


def test_split_days_groups_lines_by_date():
    days = ls.split_days(SAMPLE)
    assert list(days) == ["2026-10-26", "2026-10-27"]
    assert len(days["2026-10-26"]) == 4                 # 續行算在當天
    assert days["2026-10-27"][0].startswith("20:10")


def test_transfer_name_is_receiver():
    rows = ls.transfers_from_days(ls.split_days(SAMPLE), owner="阿明", partner="美")
    assert [(r["date"], r["direction"], r["amount"]) for r in rows] == [
        ("2026-10-26", "out", 1250),                    # 小美收到 → 阿明轉出
        ("2026-10-27", "in", 300),                      # 阿明收到 → 轉進來
    ]
    assert rows[0]["time"] == "09:01" and rows[0]["counterparty"] == "美"


def test_prices_in_chat_are_not_transfers():
    rows = ls.transfers_from_days(ls.split_days(SAMPLE), owner="阿明", partner="美")
    assert all(r["amount"] != 2000 for r in rows)
