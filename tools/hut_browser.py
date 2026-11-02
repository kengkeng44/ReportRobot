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
