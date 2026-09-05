"""
長駐 web service：
- POST /line/webhook：接 LINE Messaging API 訊息事件，dispatch 到 command_router
- 背景 scheduler：每天 UTC 00:00（台北 08:00）跑 run_daily_report
- GET /：健康檢查
"""

import asyncio
import base64
import hashlib
import hmac
import os
import socket
import threading
from contextlib import asynccontextmanager

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI, Header, HTTPException, Request

import command_router
from admin_notify import notify_admin
from daily_report import run_daily_report
from line_sender import reply_message
from security_utils import mask_source


def _env(name):
    val = os.environ.get(name)
    if val:
        return val
    try:
        import config
        return getattr(config, name, "")
    except (ImportError, AttributeError):
        return ""


LINE_CHANNEL_SECRET = _env("LINE_CHANNEL_SECRET")


def _cron_or_default(env_name, default):
    """crontab 必須是 5 欄位；env 打錯不該讓 startup crash。"""
    raw = os.environ.get(env_name, default) or default
    if len(raw.split()) == 5:
        return raw
    print(f"⚠️ {env_name} 格式錯誤 ({raw!r}，需要 5 欄)，fallback 用 {default!r}")
    return default


# UTC 07:30 = 台北 15:30
FINANCE_CRON = _cron_or_default("FINANCE_CRON", "30 7 * * *")


def _run_finance_sync():
    """排程進入點。同步失敗不能拖垮同 process 的每日推播。

    交易與持倉分開 try：其中一個掛掉不該讓另一個也不跑。
    """
    import finance_sync
    try:
        finance_sync.sync()
    except Exception as e:
        print(f"[finance] 交易同步失敗：{e}")
    try:
        finance_sync.sync_portfolio()
    except Exception as e:
        print(f"[finance] 持倉同步失敗：{e}")


def _run_einvoice_sync():
    """載具發票 → 食材庫存。跟財務同步分開,失敗不互相拖累。

    這條線**不碰交易明細** —— 記帳歸國泰彙整信管,載具負責「買了什麼菜」。
    彙整通知每月才一封,但每天檢查一次:信何時寄不確定,
    而重跑有 (名稱, 購買日) 去重擋著,不會寫出重複。
    """
    import einvoice_sync
    try:
        einvoice_sync.sync()
    except Exception as e:
        print(f"[einvoice] 食材同步失敗：{e}")

# DAILY_CRON 必須是 5 欄位的 crontab（minute hour day month dow）
# 用 env override 但若格式錯亂（空字串 / 欄位數不對）→ fallback 預設值，不讓 startup crash
DAILY_CRON_DEFAULT = "0 22 * * *"  # UTC 22:00 = 台北 06:00
_daily_cron_raw = os.environ.get("DAILY_CRON", DAILY_CRON_DEFAULT) or DAILY_CRON_DEFAULT
if len(_daily_cron_raw.split()) == 5:
    DAILY_CRON = _daily_cron_raw
else:
    print(
        f"⚠️ DAILY_CRON env 格式錯誤 ({_daily_cron_raw!r}，需要 5 個空白分隔的欄位)，"
        f"fallback 用預設 {DAILY_CRON_DEFAULT!r}"
    )
    DAILY_CRON = DAILY_CRON_DEFAULT


scheduler = AsyncIOScheduler()


def _scheduler_error_listener(event):
    """APScheduler job 失敗 / 錯過 → 通知管理員。"""
    kind = "missed" if event.code == EVENT_JOB_MISSED else "error"
    err = getattr(event, "exception", None) or RuntimeError(f"job {kind}")
    notify_admin(err, {
        "module": "scheduler",
        "section": event.job_id,
        "extra": f"event={kind}",
    })


# 應用層冪等：每日報每天最多執行一次。flag 寫到 /tmp（Railway 容器內 ephemeral，
# 重啟會清空；但搭配 apscheduler 的 coalesce + misfire_grace_time，已經能擋住
# 90% 重啟剛好踩在排程點的場景）。
_DAILY_FLAG_DIR = "/tmp" if os.path.isdir("/tmp") else os.getcwd()


def _today_flag_path():
    from datetime import date
    return os.path.join(_DAILY_FLAG_DIR, f"daily_report_{date.today():%Y%m%d}.flag")


async def _idempotent_daily_report(force_premarket=False):
    """執行前檢查 today flag；已跑過則 skip。"""
    flag = _today_flag_path()
    if os.path.exists(flag):
        print(f"[daily_report] 冪等保護觸發，今日已執行（{flag}）")
        return
    try:
        await run_daily_report(force_premarket=force_premarket)
    finally:
        # 寫 flag 不論成功失敗都寫，避免失敗後立刻被排程補跑導致雙推
        try:
            with open(flag, "w") as f:
                from datetime import datetime as _dt
                f.write(_dt.utcnow().isoformat())
        except Exception as e:
            print(f"[daily_report] flag 寫入失敗（非致命）：{e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 啟動時掛排程
    minute, hour, day, month, dow = DAILY_CRON.split()
    scheduler.add_job(
        _idempotent_daily_report,
        CronTrigger(minute=minute, hour=hour, day=day, month=month, day_of_week=dow),
        id="daily_report",
        max_instances=1,
        coalesce=True,                # 多個錯過的觸發合併成 1 次
        misfire_grace_time=300,       # 錯過 5 分鐘內仍補跑，超過就放棄
        replace_existing=True,
    )
    # 財務同步：台灣 15:30。實測國泰「消費彙整通知」每天 14:2x–14:5x 送達、
    # 富邦證券成交回報盤後約 14:25，那時信都到齊、台股也收盤了。
    # 一天一次即足夠 —— 這些信一天只來一封，每小時跑有 23 次是空轉。
    f_minute, f_hour, f_day, f_month, f_dow = FINANCE_CRON.split()
    scheduler.add_job(
        _run_finance_sync,
        CronTrigger(minute=f_minute, hour=f_hour, day=f_day,
                    month=f_month, day_of_week=f_dow),
        id="finance_sync",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,      # 漏跑半小時內補跑；重跑有指紋擋著不會重複
        replace_existing=True,
    )
    # 載具發票 → 食材庫存：台灣 16:30（UTC 08:30），排在財務同步之後半小時，
    # 錯開兩者對 Notion 的寫入。彙整通知每月才一封，但每天檢查一次 ——
    # 信何時寄不確定，而重跑有 (名稱, 購買日) 去重擋著。
    scheduler.add_job(
        _run_einvoice_sync,
        CronTrigger(minute=30, hour=8),
        id="einvoice_sync",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
        replace_existing=True,
    )
    # 即時警示（颱風 / 重要 Gmail）：每 5 分鐘檢查一輪
    from alerts import check_and_push_all as _check_alerts
    scheduler.add_job(
        _check_alerts,
        IntervalTrigger(minutes=5),
        id="alerts_loop",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=120,
        replace_existing=True,
    )
    scheduler.add_listener(
        _scheduler_error_listener,
        EVENT_JOB_ERROR | EVENT_JOB_MISSED,
    )
    # LINE push 月配額警示：每天 09:00 (TPE) 檢查當月用量，超 80%/90%/100% 推 admin
    from line_quota import check_and_warn as _check_quota
    scheduler.add_job(
        _check_quota,
        CronTrigger(hour=9, minute=0, timezone="Asia/Taipei"),
        id="line_quota_warn",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
        replace_existing=True,
    )
    scheduler.start()
    # 注入 scheduler 給 personal.py 用（提醒功能要排 one-shot job）
    import app_state
    app_state.set_scheduler(scheduler)
    print(f"Scheduler 啟動，每日報排程：{DAILY_CRON} (UTC)")
    print("LINE push 月配額警示：每天 09:00 TPE")
    # Notion 持久化：把上次 deploy 前還沒 fire 的提醒重新 schedule
    try:
        from personal import restore_reminders_from_notion
        from line_sender import push_to_user_sync
        restore_reminders_from_notion(push_to_user_sync)
    except Exception as e:
        print(f"[startup] reminders restore 失敗（非致命）：{e}")

    # Notion schema 收斂：確保 _SCHEMAS 的每個 DB 都存在。
    # 丟背景 thread 而非同步跑 —— 這會打數十次 Notion API（限流 3 req/s），
    # 擋在啟動路徑上會拖長 Railway 的健康檢查時間。
    # 不用 lazy create 是因為它會被上游的提早 return 吃掉（見 notion_db.ensure_all_dbs）。
    def _ensure_notion_schema():
        try:
            import notion_db
            notion_db.ensure_all_dbs()
        except Exception as e:
            print(f"[startup] Notion schema 收斂失敗（非致命）：{e}")

    threading.Thread(target=_ensure_notion_schema,
                     name="notion-schema-ensure", daemon=True).start()
    yield
    scheduler.shutdown()


app = FastAPI(lifespan=lifespan)


def verify_line_signature(body: bytes, signature: str | None) -> bool:
    """LINE webhook 簽章驗證；沒設 secret 就 skip（dev only）。"""
    if not LINE_CHANNEL_SECRET:
        print("⚠️ LINE_CHANNEL_SECRET 未設定，跳過簽章驗證")
        return True
    if not signature:
        return False
    h = hmac.new(LINE_CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
    expected = base64.b64encode(h).decode()
    return hmac.compare_digest(expected, signature)


@app.get("/")
async def root():
    return {"status": "ok", "service": "reportrobot"}


@app.get("/admin/env-check")
async def env_check(request: Request):
    """Server 看到的環境變數狀態（只回 set/len，不洩漏值）。Debug 用。
    要 X-Admin-Token header。

    只回 set/len 所以密鑰本身不會外流，但會告訴外人這台掛了哪些服務 ——
    其他 admin 端點都擋了，這支當初漏掉。"""
    admin_token = os.environ.get("ADMIN_TOKEN", "")
    if not admin_token:
        raise HTTPException(status_code=503, detail="Admin disabled")
    if request.headers.get("X-Admin-Token") != admin_token:
        raise HTTPException(status_code=403, detail="Forbidden")
    keys = [
        "ADMIN_TOKEN",
        "LINE_CHANNEL_TOKEN",
        "LINE_CHANNEL_SECRET",
        "LINE_GROUP_ID",
        "GMAIL_USER",
        # 個人版每日報寄信用。2026-08-25 從 SMTP 應用程式密碼改成
        # Gmail API（Railway 擋 SMTP 埠，見 HANDOFF 4.6），所以這裡看的是
        # 只有 gmail.send 權限的獨立 token，不是既有那顆 readonly 的。
        "SEND_TOKEN_PICKLE_B64",
        "REPORT_EMAIL_TO",
        "TOKEN_PICKLE_B64",
        "ANTHROPIC_API_KEY",
        "CWA_API_KEY",
        "OWM_API_KEY",
        "PDF_PASSWORD_PREFIX",
        "MANUAL_STOCKS",
        "WEATHER_LOCATIONS",
        "PYTHONUNBUFFERED",
        "TZ",
    ]
    return {
        k: {
            "set": bool(os.environ.get(k)),
            "len": len(os.environ.get(k, "")),
        }
        for k in keys
    }


@app.post("/line/webhook")
async def line_webhook(
    request: Request,
    x_line_signature: str | None = Header(None),
):
    body = await request.body()

    if not verify_line_signature(body, x_line_signature):
        raise HTTPException(status_code=403, detail="Invalid signature")

    payload = await request.json()
    events = payload.get("events", []) or []

    for event in events:
        event_type = event.get("type")
        source = event.get("source", {}) or {}
        reply_token = event.get("replyToken")

        # Postback：Flex 卡片按鈕點擊事件
        if event_type == "postback":
            data = (event.get("postback", {}) or {}).get("data", "")
            user_id = source.get("userId")
            print(f"[webhook] postback data={data[:60]!r} source={mask_source(source)}")
            if not (reply_token and user_id):
                continue
            response = command_router.handle_postback(data, user_id)
            if response:
                await reply_message(reply_token, response)
            continue

        if event_type != "message":
            print(f"[webhook] event={event_type} source={mask_source(source)}")
            continue
        msg = event.get("message", {}) or {}
        if msg.get("type") != "text":
            continue
        text = msg.get("text", "")
        print(f"[webhook] message text={text[:30]!r} source={mask_source(source)}")
        if not reply_token:
            continue

        # 把 source 資訊塞給 command_router，個人指令用 source_type 判斷
        ctx = {
            "source_type": source.get("type"),  # 'user' / 'group' / 'room'
            "user_id": source.get("userId"),
            "group_id": source.get("groupId"),
        }

        # 多行訊息：每行各自當獨立指令，依序塞進同一 reply（最多 5 則 LINE 上限）
        # 限制最多 3 行，避免一次塞十個指令把 server 卡爆
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if len(lines) > 1:
            parts = []
            for line in lines[:3]:
                r = command_router.handle(line, ctx=ctx)
                if r is None:
                    continue
                # response 可能是 str / dict / list；攤平成 list
                if isinstance(r, list):
                    parts.extend(r)
                else:
                    parts.append(r)
            if parts:
                print(f"LINE 多行指令命中 {len(parts)} 則 → reply")
                await reply_message(reply_token, parts)
            continue

        response = command_router.handle(text, ctx=ctx)
        if response:
            print(f"LINE 指令命中：{text[:30]} → 回覆 {len(response)} 字")
            await reply_message(reply_token, response)
        # 沒命中就靜默不回，避免騷擾家人聊天

    return {"ok": True}


@app.get("/admin/cost-stats")
async def cost_stats(request: Request):
    """累積 AI / web_search 用量與估算成本（要 X-Admin-Token header）。
    Railway redeploy 會清空，看當期趨勢用。"""
    admin_token = os.environ.get("ADMIN_TOKEN", "")
    if not admin_token:
        raise HTTPException(status_code=503, detail="Admin disabled")
    if request.headers.get("X-Admin-Token") != admin_token:
        raise HTTPException(status_code=403, detail="Forbidden")
    import usage_tracker
    return usage_tracker.get_stats()


@app.post("/admin/run-daily")
async def trigger_daily(request: Request, force: int = 0):
    """手動觸發每日報。?force=1 會 bypass 週末略過盤前段的檢查（測試用）。"""
    admin_token = os.environ.get("ADMIN_TOKEN", "")
    if not admin_token:
        raise HTTPException(status_code=503, detail="Admin trigger disabled")
    if request.headers.get("X-Admin-Token") != admin_token:
        raise HTTPException(status_code=403, detail="Forbidden")
    # admin 手動觸發：直接呼叫 run_daily_report，跳過 flag 冪等（測試用）
    await run_daily_report(force_premarket=bool(force))
    return {"ok": True, "force_premarket": bool(force)}


@app.post("/admin/run-personal")
async def trigger_personal(request: Request):
    """只寄個人信，不推群組。

    /admin/run-daily 會連群組推播一起送，驗證信件內容時家人會在
    非正常時間多收到一則今日情報。這支只跑個人版那一半 ——
    要驗版面、圓餅圖、今日三句時用這支。

    刻意不呼叫 run_daily_report：那支的第一段就是 push_message，
    包在裡面沒辦法只跳過群組（見 tests/test_admin_personal.py）。
    """
    admin_token = os.environ.get("ADMIN_TOKEN", "")
    if not admin_token:
        raise HTTPException(status_code=503, detail="Admin trigger disabled")
    if request.headers.get("X-Admin-Token") != admin_token:
        raise HTTPException(status_code=403, detail="Forbidden")

    from daily_report import _email_personal_report
    from tz_utils import today_tpe

    today = today_tpe().strftime("%Y-%m-%d")
    _email_personal_report(today)
    return {"ok": True, "date": today, "group_push": False}


@app.post("/admin/finance-sync")
async def trigger_finance_sync(request: Request, days: int = 7):
    """手動觸發財務同步（排程是每天台灣 15:30，這個給即時驗證用）。

    ?days=N 調整往回撈幾天，預設 7。重跑安全 —— 指紋去重會擋掉已寫入的。

    PowerShell 範例：
      Invoke-RestMethod -Method Post `
        -Uri "https://<host>/admin/finance-sync?days=7" `
        -Headers @{ 'X-Admin-Token' = $env:ADMIN_TOKEN }
    """
    admin_token = os.environ.get("ADMIN_TOKEN", "")
    if not admin_token:
        raise HTTPException(status_code=503, detail="Admin disabled")
    if request.headers.get("X-Admin-Token") != admin_token:
        raise HTTPException(status_code=403, detail="Forbidden")

    days = max(1, min(days, 90))     # 夾住範圍，避免手滑打 3650 去撈整個信箱

    import finance_sync
    try:
        # sync() 是阻塞的（Gmail / Notion 都是同步 HTTP）。直接 await 會卡住
        # event loop，LINE webhook 會跟著停擺，所以丟到 thread 跑。
        stats = await asyncio.to_thread(finance_sync.sync, lookback_days=days)
        portfolio_stats = await asyncio.to_thread(finance_sync.sync_portfolio)
        return {"ok": True, "days": days, "stats": stats,
                "portfolio": portfolio_stats,
                "summary": finance_sync.format_summary(stats)}
    except Exception as e:
        print(f"[finance] 手動觸發失敗：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/setup-richmenu")
async def setup_richmenu(request: Request):
    """一次性建立 / 重建 LINE Rich Menu（分頁式：主選單 + 財務/煮飯/投資/更多）。
    Rich Menu 與分頁切換都完全不計入 push 配額。
    PowerShell 範例：
      Invoke-RestMethod -Method Post -Uri https://<host>/admin/setup-richmenu \\
        -Headers @{ 'X-Admin-Token' = $env:ADMIN_TOKEN }"""
    admin_token = os.environ.get("ADMIN_TOKEN", "")
    if not admin_token:
        raise HTTPException(status_code=503, detail="Admin disabled")
    if request.headers.get("X-Admin-Token") != admin_token:
        raise HTTPException(status_code=403, detail="Forbidden")
    from setup_richmenu import setup as _setup
    try:
        menu_ids = _setup()
        return {"ok": True, "menus": menu_ids}
    except Exception as e:
        print(f"[richmenu] setup 失敗：{e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/portfolio-debug")
async def portfolio_debug(request: Request):
    """Dump 所有 trades + 累計後 portfolio，用來對照月對帳單抓 parser bug。
    回傳 JSON：{trades: [...], portfolio: {ticker: {shares, avg_cost}}}。
    要 X-Admin-Token header。"""
    admin_token = os.environ.get("ADMIN_TOKEN", "")
    if not admin_token:
        raise HTTPException(status_code=503, detail="Admin disabled")
    if request.headers.get("X-Admin-Token") != admin_token:
        raise HTTPException(status_code=403, detail="Forbidden")
    from gmail_reader import _download_email_items, extract_trades_from_pdf, extract_trades_from_text, _is_tw_daily, _is_tw_monthly_text, _aggregate_portfolio
    items = _download_email_items()
    all_trades = []
    for it in items:
        if _is_tw_daily(it["subject"]):
            tr = extract_trades_from_text(it["body_text"], fallback_date=it["date_hint"], daily=True)
        elif _is_tw_monthly_text(it["subject"], it["body_text"]):
            tr = extract_trades_from_text(it["body_text"], fallback_date=it["date_hint"], daily=False)
        else:
            tr = []
            for pdf_path in it["pdf_paths"]:
                tr.extend(extract_trades_from_pdf(pdf_path, fallback_date=it["date_hint"]))
        for t in tr:
            t["_subject"] = it["subject"][:80]
        all_trades.extend(tr)
    portfolio = _aggregate_portfolio(all_trades)
    # date 物件 → str 方便 JSON
    for t in all_trades:
        if t.get("date"):
            t["date"] = str(t["date"])
    return {"trade_count": len(all_trades), "portfolio": portfolio, "trades": all_trades}


@app.get("/admin/statement-dump")
async def statement_dump(request: Request):
    """Dump 最近一期月對帳單的原始文字，用來寫「庫存」欄位的 parser。

    HANDOFF 4.1 的正解是拿月對帳單的庫存欄位當持倉起點，但那段長什麼樣
    沒人看過。這支只讀不寫，先把真實格式撈出來再動手寫 parser ——
    照 snippet 猜格式的下場見 HANDOFF 第 7 節。

    ⚠️ 回傳內容含帳號與持倉，只給 admin，不要貼進公開的地方。
    要 X-Admin-Token header。
    """
    admin_token = os.environ.get("ADMIN_TOKEN", "")
    if not admin_token:
        raise HTTPException(status_code=503, detail="Admin disabled")
    if request.headers.get("X-Admin-Token") != admin_token:
        raise HTTPException(status_code=403, detail="Forbidden")

    import holdings
    from gmail_reader import _download_email_items, pdf_text

    items = _download_email_items()
    latest = holdings.pick_latest_monthly([it["subject"] for it in items])

    out = []
    for it in items:
        period = holdings.monthly_statement_period(it["subject"])
        market = holdings.statement_market(it["subject"])
        if not period or latest.get(market) != period:
            continue  # 只給每個市場最新那一期，其餘是雜訊
        text = it["body_text"] or ""
        for path in it["pdf_paths"]:
            text += "\n" + pdf_text(path)
        out.append({
            "subject": it["subject"],
            "market": market,
            "period": list(period),
            "text": text[:20000],
        })
    return {"latest": {k: list(v) for k, v in latest.items()}, "statements": out}


@app.get("/admin/net-check")
def net_check(request: Request):
    """從容器內部逐一實測對外連線，判斷 SMTP 到底卡在哪一層。

    為什麼需要這支：2026-08-25 06:01 個人版寄信炸 OSError [Errno 101]
    Network is unreachable，但同一分鐘 LINE push（HTTPS 443）是通的。
    smtplib 只會把「最後一個位址」的錯誤丟出來，看不出是 IPv6 沒路由、
    還是 465 這個 port 被平台整個擋掉 —— 兩者的修法完全不同，
    所以先量再改，不要用猜的去部署。

    每個 host 的 A / AAAA 位址都分開連，回報各自結果。
    api.line.me:443 是控制組：這條每天都在用，它一定要是 ok，
    否則代表量測本身有問題而不是 SMTP 有問題。

    刻意寫成 def 而不是 async def —— socket.connect 是阻塞的，
    寫成 async 會把整個 event loop 卡住最久 30 秒（webhook 一起被卡）。
    FastAPI 看到同步函式會自動丟到 threadpool。

    要 X-Admin-Token header。用完可以整段刪掉，沒有其他程式依賴它。
    """
    admin_token = os.environ.get("ADMIN_TOKEN", "")
    if not admin_token:
        raise HTTPException(status_code=503, detail="Admin disabled")
    if request.headers.get("X-Admin-Token") != admin_token:
        raise HTTPException(status_code=403, detail="Forbidden")

    targets = [
        ("smtp.gmail.com", 465),   # 現在在用的（SSL）
        ("smtp.gmail.com", 587),   # 備案（STARTTLS），很多平台只擋其中一個
        ("api.line.me", 443),      # 控制組：這條一定通
    ]

    out = []
    for host, port in targets:
        try:
            addrs = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
        except OSError as e:
            # DNS 就掛了 —— 跟連不出去是完全不同的故障
            out.append({"target": f"{host}:{port}", "dns": f"FAIL {type(e).__name__}: {e}"})
            continue

        for fam, socktype, proto, _canon, sa in addrs:
            entry = {
                "target": f"{host}:{port}",
                "family": "IPv6" if fam == socket.AF_INET6 else "IPv4",
                "addr": sa[0],
            }
            sock = None
            try:
                sock = socket.socket(fam, socktype, proto)
                sock.settimeout(5)
                sock.connect(sa)
                entry["result"] = "ok"
            except OSError as e:
                entry["result"] = "fail"
                entry["errno"] = getattr(e, "errno", None)
                entry["error"] = f"{type(e).__name__}: {e}"
            finally:
                if sock is not None:
                    sock.close()
            out.append(entry)

    return {"probes": out}
