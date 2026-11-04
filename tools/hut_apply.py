"""山屋申請：本機真瀏覽器自動登入、填表，驗證碼截圖用 LINE 傳給本人，本人回覆後送出。

設計：docs/superpowers/specs/2026-11-04-hut-apply-design.md
驗證碼轉手在伺服器 hut_captcha.py（/admin/hut-captcha）。

帳密、隊員資料都放 Infisical，用 infisical run 注入環境變數，不寫進檔案：
  infisical run --env=prod -- python tools/hut_apply.py --recon onestop
  infisical run --env=prod -- python tools/hut_apply.py --recon jmlnt
  infisical run --env=prod -- python tools/hut_apply.py --site jmlnt --date 2026-12-12 --people 2 --dry-run
  infisical run --env=prod -- python tools/hut_apply.py --site jmlnt --date 2026-12-12 --people 2

--recon   只開頁面、存 HTML／截圖／表單欄位清單到 ~/.hut_monitor/recon/，不填、不送。
          把 *_fields.json 給 Claude，用來補下面 FLOWS 的選擇器。
--dry-run 登入、填表到驗證碼前截圖就停，不傳驗證碼、不送出。

規矩（跟真人一樣，不做的事）：
  - 一次只送一筆；你沒回驗證碼就不送；驗證碼你自己看，不自動辨識。
  - 不狂刷：步驟之間停 STEP_GAP 秒；被擋（WAF／HTTP 錯誤）就停、LINE 告訴你，不繞。
  - 不裝 stealth、不改 User-Agent。

Infisical 環境變數：
  ADMIN_TOKEN                         既有，送 Railway 用
  HUT_FOREST_ACCOUNT / _PASSWORD      林業署（嘉明湖／天池／檜谷）會員帳密
  HUT_<SITE>_ACCOUNT / _PASSWORD      某一站帳號不同時覆寫（SITE 例 JMLNT）
  HUT_TEAM_JSON                       隊伍資料，格式見 TEAM_EXAMPLE
"""

import argparse
import base64
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SERVER = "https://chengreportbot-production.up.railway.app"
HOME = Path.home() / ".hut_monitor"
RECON = HOME / "recon"
STEP_GAP = 2          # 步驟之間停 2 秒
POLL_EVERY = 3        # 每 3 秒問一次你回了沒
POLL_MAX = 5 * 60 + 15

TEAM_EXAMPLE = {
    "team_name": "隊名",
    "leader": {"name": "", "id_no": "", "birthday": "YYYY-MM-DD", "gender": "男",
               "phone": "", "email": "", "address": ""},
    "members": [{"name": "", "id_no": "", "birthday": "YYYY-MM-DD", "gender": "女",
                 "phone": "", "email": ""}],
    "emergency": {"name": "", "phone": "", "relation": ""},
}

# 每個站的入口。林業署三站是同一套訂房系統（room/index.php），一站式另一套。
SITES = {
    "jmlnt": {"family": "forest", "base": "https://jmlnt.forest.gov.tw/room/"},
    "tconline": {"family": "forest", "base": "https://tconline.forest.gov.tw/room/"},
    "kgonline": {"family": "forest", "base": "https://kgonline.forest.gov.tw/room/"},
    "onestop": {"family": "onestop", "base": "https://hike.taiwan.gov.tw/"},
}
for _k in ("onestop_paiyun", "onestop_99", "onestop_7ka", "onestop_cilai", "onestop_nanhu"):
    SITES[_k] = SITES["onestop"]

# 步驟清單。選擇器要等 --recon 的結果才能補，None = 還沒補，跑到會停下來告訴你。
# 步驟：
#   ("goto", 路徑)                        相對 base
#   ("fill", 選擇器, 值的 key)            key 見 _value()
#   ("select", 選擇器, 值的 key)
#   ("click", 選擇器)
#   ("wait", 選擇器)                      等它出現（確認換頁成功）
#   ("captcha", 圖選擇器, 輸入框選擇器, "login"|"apply")
#   ("submit", 選擇器)                    最後送出；--dry-run 停在這之前
FLOWS = {
    "forest": {
        "login": [
            ("goto", None),
            ("fill", None, "account"),
            ("fill", None, "password"),
            ("captcha", None, None, "login"),
            ("click", None),
            ("wait", None),
        ],
        "apply": [
            ("goto", None),
            ("submit", None),
        ],
    },
    # 一站式不用登入：直接填申請表，送出前要驗證碼。
    "onestop": {
        "login": [],
        "apply": [
            ("goto", None),
            ("submit", None),
        ],
    },
}


class NotReady(Exception):
    """FLOWS 還有沒補的選擇器。"""


class Stop(Exception):
    """這次停下（被擋、你取消、過期、網站跟預期不一樣）。訊息會傳到 LINE。"""


# ── 秘密與資料 ──────────────────────────────────────────

def _secret(site, field):
    """先找 HUT_<SITE>_<FIELD>，再找 HUT_<FAMILY>_<FIELD>。"""
    fam = SITES[site]["family"].upper()
    for name in (f"HUT_{site.upper()}_{field}", f"HUT_{fam}_{field}"):
        v = os.environ.get(name, "").strip()
        if v:
            return v
    return ""


def load_team():
    raw = os.environ.get("HUT_TEAM_JSON", "").strip()
    if not raw:
        raise Stop("Infisical 沒有 HUT_TEAM_JSON（隊伍資料）")
    try:
        team = json.loads(raw)
    except ValueError:
        raise Stop("HUT_TEAM_JSON 不是合法 JSON")
    if not isinstance(team, dict) or not isinstance(team.get("leader"), dict):
        raise Stop("HUT_TEAM_JSON 要有 leader")
    return team


def _value(key, ctx):
    """表單值。key 例：account、password、date、people、team_name、leader.phone、members.0.name。"""
    if key == "account":
        return _secret(ctx["site"], "ACCOUNT")
    if key == "password":
        return _secret(ctx["site"], "PASSWORD")
    if key in ("date", "people", "nights"):
        return str(ctx[key])
    cur = ctx.get("team") or {}
    for part in key.split("."):
        if isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        elif isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            raise Stop(f"隊伍資料缺 {key}")
    return str(cur)


def summary(ctx):
    """傳到 LINE 的摘要：不放身分證、帳密。"""
    team = ctx.get("team") or {}
    names = [team.get("leader", {}).get("name", "")] + [m.get("name", "") for m in team.get("members", [])]
    names = "、".join(n for n in names if n) or "（隊員未載入）"
    return (f"{ctx['site']}｜{ctx['date']} 起 {ctx['nights']} 晚｜{ctx['people']} 人\n"
            f"隊員：{names}")


# ── 跟 Railway 講話 ─────────────────────────────────────

def _token():
    t = os.environ.get("ADMIN_TOKEN", "").strip()
    if not t:
        try:
            t = (HOME / "admin_token").read_text(encoding="utf-8-sig").strip()
        except OSError:
            t = ""
    if not t:
        raise Stop("找不到 ADMIN_TOKEN")
    return t


def _api(method, path, body=None, http=None):
    import requests
    http = http or requests
    r = http.request(method, SERVER + path, json=body, timeout=30,
                     headers={"X-Admin-Token": _token()})
    r.raise_for_status()
    return r.json()


def _b64(data):
    return base64.b64encode(data).decode() if data else None


def ask_captcha(image, text, step, api=_api, sleep=time.sleep, clock=time.monotonic):
    """送驗證碼圖到 LINE，等你回。回 code；取消／過期就 Stop。"""
    cid = api("POST", "/admin/hut-captcha",
              {"image_b64": _b64(image), "summary": text, "step": step})["id"]
    start = clock()
    while clock() - start < POLL_MAX:
        sleep(POLL_EVERY)
        got = api("GET", f"/admin/hut-captcha/{cid}")
        state = got.get("state")
        if state == "answered":
            return cid, got["code"]
        if state in ("cancelled", "expired", "unknown"):
            raise Stop({"cancelled": "你取消了這筆", "expired": "驗證碼過期，沒送",
                        "unknown": "這筆被新的請求蓋掉了"}[state])
    raise Stop("等驗證碼逾時，沒送")


def tell(text, image=None, api=_api):
    try:
        api("POST", "/admin/hut-notify", {"text": text, "image_b64": _b64(image)})
    except Exception as e:  # LINE 傳不到也不能讓腳本當掉
        print(f"LINE 通知失敗：{type(e).__name__}")


# ── 瀏覽器步驟 ──────────────────────────────────────────

def check_ready(family, steps=("login", "apply")):
    missing = [f"{family}.{s}[{i}]" for s in steps
               for i, step in enumerate(FLOWS[family][s])
               if None in step[1:]]
    if missing:
        raise NotReady("選擇器還沒補：" + "、".join(missing)
                       + "。先跑 --recon，把 ~/.hut_monitor/recon/ 的 *_fields.json 給 Claude。")


def _ensure_not_blocked(page, resp=None):
    status = getattr(resp, "status", None)
    if isinstance(status, int) and status >= 400:
        raise Stop(f"網站回 HTTP {status}，這次停止")
    if "Request Rejected" in page.content():
        raise Stop("被網站 WAF 擋下（Request Rejected），這次停止，不繞")


def _shot(page, selector=None):
    if selector:
        return page.locator(selector).first.screenshot(type="png")
    return page.screenshot(type="jpeg", quality=60, full_page=False)


def run_steps(page, steps, ctx, api=_api, sleep=time.sleep):
    """回最後一個 captcha 的 id（給回報結果用）。"""
    cid = None
    base = SITES[ctx["site"]]["base"]
    for step in steps:
        op = step[0]
        if op == "goto":
            resp = page.goto(base + step[1], wait_until="networkidle")
            _ensure_not_blocked(page, resp)
        elif op == "fill":
            page.fill(step[1], _value(step[2], ctx))
        elif op == "select":
            page.select_option(step[1], label=_value(step[2], ctx))
        elif op == "click":
            page.click(step[1])
            page.wait_for_load_state("networkidle")
            _ensure_not_blocked(page)
        elif op == "wait":
            page.wait_for_selector(step[1], timeout=20000)
        elif op == "captcha":
            img_sel, input_sel, kind = step[1], step[2], step[3]
            if ctx["dry_run"] and kind == "apply":
                ctx["dry_shot"] = _shot(page)
                return None
            text = summary(ctx) if kind == "apply" else f"{ctx['site']} 登入"
            cid, code = ask_captcha(_shot(page, img_sel), text, kind, api=api, sleep=sleep)
            page.fill(input_sel, code)
        elif op == "submit":
            if ctx["dry_run"]:
                ctx["dry_shot"] = _shot(page)
                return None
            page.click(step[1])
            page.wait_for_load_state("networkidle")
            _ensure_not_blocked(page)
        else:
            raise ValueError(f"不認得的步驟 {op}")
        sleep(STEP_GAP)
    return cid


# ── recon ──────────────────────────────────────────────

_FIELDS_JS = """() => {
  const label = el => {
    if (el.id) { const l = document.querySelector(`label[for="${el.id}"]`); if (l) return l.innerText.trim(); }
    const td = el.closest('td,div'); const prev = td && td.previousElementSibling;
    return prev ? prev.innerText.trim().slice(0, 40) : '';
  };
  const pick = el => ({tag: el.tagName.toLowerCase(), type: el.type || '', id: el.id || '',
    name: el.name || '', label: label(el), text: (el.innerText || el.value || '').trim().slice(0, 40),
    options: el.tagName === 'SELECT' ? [...el.options].slice(0, 30).map(o => o.text) : undefined});
  return {
    url: location.href, title: document.title,
    forms: [...document.forms].map(f => ({id: f.id, action: f.action, method: f.method})),
    inputs: [...document.querySelectorAll('input:not([type=hidden]),select,textarea')].map(pick),
    buttons: [...document.querySelectorAll('button,input[type=submit],input[type=button],a.btn')].map(pick),
    images: [...document.images].map(i => ({id: i.id, src: (i.getAttribute('src') || '').slice(0, 120),
      alt: i.alt, in_form: !!i.closest('form')}))
      .filter(i => i.in_form || /captcha|valid|code|verify|checkcode|驗證/i.test(i.src + i.id + i.alt)),
    links: [...document.links].map(a => ({text: a.innerText.trim().slice(0, 30), href: a.getAttribute('href')}))
      .filter(a => /申請|登入|login|會員|訂房|room|apply|預約/i.test(a.text + a.href)).slice(0, 60),
  };
}"""

RECON_PAGES = {
    "forest": ["index.php", "login.php"],
    "onestop": ["", "index.aspx"],
}


def recon(page, site, out=RECON):
    """只開頁面、記下欄位，不填任何東西。"""
    out.mkdir(parents=True, exist_ok=True)
    fam = SITES[site]["family"]
    base = SITES[site]["base"]
    paths = list(RECON_PAGES[fam])
    seen = set()
    while paths and len(seen) < 6:
        path = paths.pop(0)
        if path in seen:
            continue
        seen.add(path)
        resp = page.goto(base + path, wait_until="networkidle")
        if getattr(resp, "status", 200) >= 400:
            print(f"{path or '/'}：HTTP {resp.status}，略過")
            continue
        _ensure_not_blocked(page)
        info = page.evaluate(_FIELDS_JS)
        stem = f"{site}_{(path or 'home').replace('/', '_').replace('.', '_')}"
        (out / f"{stem}.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(out / f"{stem}.png"), full_page=True)
        (out / f"{stem}_fields.json").write_text(json.dumps(info, ensure_ascii=False, indent=1),
                                                 encoding="utf-8")
        print(f"{path or '/'}：{len(info['inputs'])} 個欄位、{len(info['images'])} 張疑似驗證碼 → {stem}_fields.json")
        for link in info["links"]:
            href = (link.get("href") or "").split("#")[0]
            if href and not href.startswith(("http", "javascript", "mailto")) and href not in seen:
                paths.append(href.lstrip("/"))
        time.sleep(STEP_GAP)
    print(f"樣本在 {out}，把 *_fields.json（和截圖）給 Claude。裡面沒有你的個資。")


# ── main ───────────────────────────────────────────────

def _lock():
    HOME.mkdir(parents=True, exist_ok=True)
    lock = HOME / "apply.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if time.time() - lock.stat().st_mtime < 15 * 60:
            raise Stop("已經有一筆申請在跑（~/.hut_monitor/apply.lock），一次只送一筆")
        lock.unlink()
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.close(fd)
    return lock


def apply(page, ctx, api=_api, sleep=time.sleep):
    fam = SITES[ctx["site"]]["family"]
    check_ready(fam)
    if fam == "forest" and not (_secret(ctx["site"], "ACCOUNT") and _secret(ctx["site"], "PASSWORD")):
        raise Stop("Infisical 沒有林業署帳密（HUT_FOREST_ACCOUNT／HUT_FOREST_PASSWORD）")
    run_steps(page, FLOWS[fam]["login"], ctx, api=api, sleep=sleep)
    cid = run_steps(page, FLOWS[fam]["apply"], ctx, api=api, sleep=sleep)
    if ctx["dry_run"]:
        return None
    shot = _shot(page)
    if cid:
        api("POST", f"/admin/hut-captcha/{cid}/result",
            {"text": f"已送出：{summary(ctx)}\n請看截圖確認結果，之後也會有網站通知信。",
             "image_b64": _b64(shot)})
    return cid


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--recon", choices=sorted(SITES))
    ap.add_argument("--site", choices=sorted(SITES))
    ap.add_argument("--date", type=date.fromisoformat)
    ap.add_argument("--nights", type=int, default=1)
    ap.add_argument("--people", type=int)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if not args.recon and not (args.site and args.date and args.people):
        ap.error("要 --recon SITE，或 --site --date --people")

    ctx = None
    if not args.recon:
        try:
            check_ready(SITES[args.site]["family"])
            ctx = {"site": args.site, "date": args.date, "nights": args.nights,
                   "people": args.people, "dry_run": args.dry_run, "team": load_team()}
        except (NotReady, Stop) as e:
            print(e)
            return 2

    from playwright.sync_api import sync_playwright
    lock = None
    try:
        if ctx:
            lock = _lock()
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not args.headed)
            page = browser.new_page(locale="zh-TW")
            try:
                if args.recon:
                    recon(page, args.recon)
                    return 0
                apply(page, ctx)
                if args.dry_run:
                    out = HOME / "dry_run.jpg"
                    out.write_bytes(ctx.get("dry_shot") or b"")
                    print(f"dry-run 停在送出前，畫面存在 {out}")
                else:
                    print("已送出，結果截圖傳到 LINE")
            except Exception as e:  # Stop、逾時、網站改版都一樣：停下、LINE 告訴你
                msg = str(e) if isinstance(e, (Stop, NotReady)) else f"{type(e).__name__}: {e}"[:300]
                print(msg)
                if ctx and not args.dry_run:
                    tell(f"⚠️ 山屋申請停下：{msg}\n{summary(ctx)}", _safe_shot(page))
                return 1
            finally:
                browser.close()
    except Stop as e:
        print(e)
        return 1
    finally:
        if lock:
            lock.unlink(missing_ok=True)
    return 0


def _safe_shot(page):
    try:
        return _shot(page)
    except Exception:
        return None


if __name__ == "__main__":
    sys.exit(main())
