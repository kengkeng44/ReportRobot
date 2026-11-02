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

token：先讀環境變數 ADMIN_TOKEN（Windows 使用者環境變數，HANDOFF 的 admin 端點也用這個），
沒有才讀 ~/.hut_monitor/admin_token（一行，從 Infisical 複製，不要貼進聊天）
"""

import argparse
import json
import os
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


def _read_token():
    """環境變數 ADMIN_TOKEN 優先，沒有才讀 ~/.hut_monitor/admin_token；都沒有回 ""。"""
    token = os.environ.get("ADMIN_TOKEN", "").strip()
    if token:
        return token
    try:
        return (HOME / "admin_token").read_text(encoding="utf-8-sig").strip()
    except OSError:
        return ""


def _wait_calendar(page, prefix="", timeout=30000):
    """等月曆的日期連結出現。網站用 ASP.NET postback／UpdatePanel，換不換整頁不一定，
    只等 networkidle 會讀到舊頁，所以直接等目標月份的 sdate 連結。"""
    page.wait_for_selector(f"table.table_bed a[href*='sdate={prefix}']", timeout=timeout)


def _show(page, prefix, sleep):
    """選單改完後：有的頁面選了就自動 postback 出月曆；沒出來才停一下按「查詢」。"""
    try:
        _wait_calendar(page, prefix, timeout=10000)
    except Exception as e:
        if type(e).__name__ != "TimeoutError":
            raise
        hut_onestop.ensure_not_blocked(page.content())   # 選單 postback 被擋就停，不再按查詢
        sleep(PAGE_GAP)
        page.click("#con_btnsearch")
        _wait_calendar(page, prefix)
    hut_onestop.ensure_not_blocked(page.content())


def _next_month(page, sleep=time.sleep):
    sleep(PAGE_GAP)
    y = int(page.eval_on_selector("#con_ddlYear", "e => e.value"))
    m = int(page.eval_on_selector("#con_ddlMonth", "e => e.value"))
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    if m == 1:
        page.select_option("#con_ddlYear", value=str(y))
        sleep(PAGE_GAP)
    page.select_option("#con_ddlMonth", value=str(m))
    _show(page, f"{y}-{m:02d}", sleep)


def _goto(page, path):
    resp = page.goto(hut_onestop.BASE + path, wait_until="networkidle")
    status = getattr(resp, "status", None)
    if isinstance(status, int) and status >= 400:
        raise hut_onestop.Blocked(f"HTTP {status}，這次停止")
    hut_onestop.ensure_not_blocked(page.content())


def _open_target(page, path, label, sleep=time.sleep):
    _goto(page, path)
    sleep(PAGE_GAP)
    page.select_option("#con_rooms", label=label)
    _show(page, "", sleep)


def _is_stop(e):
    """被擋或逾時都視為「停」，不重試。"""
    return isinstance(e, hut_onestop.Blocked) or type(e).__name__ == "TimeoutError"


def collect_onestop(page, sleep=time.sleep):
    """(slots, errors)。被擋（WAF／HTTP 錯誤／逾時）就停，沒抓到的地點全部記成同一個錯誤。"""
    slots, errors = [], {}
    sites = list(hut_onestop.ONESTOP.items())
    for n, (site, (path, label)) in enumerate(sites):
        try:
            _open_target(page, path, label, sleep)
            got = []
            for i in range(MONTHS):
                if i:
                    _next_month(page, sleep)
                got += hut_onestop.parse_onestop_html(page.content(), site)
            if not got:
                raise ValueError("解析出 0 筆（網站可能改版）")
            slots += got
        except Exception as e:
            if _is_stop(e):
                if isinstance(e, hut_onestop.Blocked):
                    msg = str(e) or "被網站擋下（Request Rejected），這次停止"
                else:
                    msg = f"逾時（{type(e).__name__}），這次停止"
                done = {s.site for s in slots}
                for rest in hut_onestop.ONESTOP:
                    if rest not in done:
                        errors[rest] = msg
                break
            errors[site] = f"{type(e).__name__}: {e}"[:200]
        if n < len(sites) - 1:
            sleep(PAGE_GAP)
    return slots, errors


def recon(page):
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for path in dict.fromkeys(p for p, _ in hut_onestop.ONESTOP.values()):
        _goto(page, path)
        options = page.eval_on_selector(
            "#con_rooms", "e => [...e.options].map(o => o.value + '=' + o.text)")
        print(f"{path} 宿營地選項：{options}")
        time.sleep(PAGE_GAP)
    for site, (path, label) in hut_onestop.ONESTOP.items():
        _open_target(page, path, label, time.sleep)
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
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    token = None
    if not (args.dry_run or args.recon):
        token = _read_token()
        if not token:
            print("找不到 ADMIN_TOKEN（環境變數或 ~/.hut_monitor/admin_token），已停止（沒有開瀏覽器）")
            return 2

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
    try:
        r = requests.post(f"{SERVER}/admin/hut-ingest", timeout=60,
                          json={"slots": [s.to_dict() for s in slots], "errors": errors},
                          headers={"X-Admin-Token": token})
        r.raise_for_status()
        print(r.json())
    except requests.RequestException as e:
        status = getattr(getattr(e, "response", None), "status_code", "")
        print(f"送回 Railway 失敗：{type(e).__name__} {status}".strip())
        return 2
    return 0 if slots else 1


if __name__ == "__main__":
    sys.exit(main())
