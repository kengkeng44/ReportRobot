"""/admin/run-personal —— 只寄個人信、不推群組。

這份測試讀 server.py 的原始碼文字，不 import 它：server.py 依賴
apscheduler，本機沒裝，import 直接炸。整個 repo 的 server 端點都
沒有測試，原因就是這個。

讀原始碼是這個 repo 已經在用的手法（見 test_personal_report.py 的
test_owm_knows_banqiao）。守得住的東西有限，但守得住最重要的那件：
這支端點不能把群組推播一起送出去 —— 那正是它存在的理由。
"""

import re
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent / "server.py"


def _source():
    return SERVER.read_text(encoding="utf-8")


def _handler(name):
    """抓出某個 async def 的**程式碼**（到下一個頂層 def 為止），去掉 docstring。

    去 docstring 是必要的：這支端點的 docstring 就寫著「刻意不呼叫
    run_daily_report」，留著會讓「不准出現 run_daily_report」那條
    斷言被自己的註解絆倒 —— 斷言要看程式碼，不看散文。
    """
    src = _source()
    start = src.index(f"async def {name}(")
    rest = src[start:]
    m = re.search(r"\n@app\.|\n(?:async )?def ", rest[1:])
    body = rest[: m.start() + 1] if m else rest
    return re.sub(r'""".*?"""', "", body, flags=re.DOTALL)


def test_endpoint_exists():
    assert '@app.post("/admin/run-personal")' in _source()


def test_personal_endpoint_never_pushes_to_the_group():
    """這支的存在意義就是「不要吵到家人」。

    run_daily_report 的第一段就是群組 push_message，包在裡面沒辦法
    只跳過群組 —— 所以這支必須直接呼叫 _email_personal_report。
    """
    body = _handler("trigger_personal")

    assert "run_daily_report" not in body
    assert "push_message" not in body
    assert "_email_personal_report" in body


def test_personal_endpoint_is_token_protected():
    """admin 端點沒有 token 保護等於把「寄信給我」開放給全世界。"""
    body = _handler("trigger_personal")

    assert "ADMIN_TOKEN" in body
    assert "X-Admin-Token" in body
    assert "403" in body


def test_daily_endpoint_still_pushes_to_the_group():
    """對照組：/admin/run-daily 維持原行為，不能被這次改動影響。"""
    body = _handler("trigger_daily")

    assert "run_daily_report" in body
