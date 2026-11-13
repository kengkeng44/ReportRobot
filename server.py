"""
長駐 web service：
- POST /line/webhook：接 LINE Messaging API 訊息事件，dispatch 到 command_router
- 定時工作在 jobs.py（Railway 定時任務）；這裡閒置會休眠
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
from fastapi import FastAPI, Header, HTTPException, Request

import command_router
from admin_notify import notify_admin
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


# 定時工作都搬到 jobs.py（Railway 定時任務，跑完就關）。網頁服務閒置會休眠，
# 這裡只留「提醒我」用的 in-process scheduler：醒著時準時送；睡著時到期的，
# 下次醒來（restore）或 jobs.py 下一個時段補送。
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_listener(_scheduler_error_listener, EVENT_JOB_ERROR | EVENT_JOB_MISSED)
    scheduler.start()
    # 注入 scheduler 給 personal.py 用（提醒功能要排 one-shot job）
    import app_state
    app_state.set_scheduler(scheduler)

    # 背景跑，不擋啟動：休眠醒來第一個請求要快
    def _warm_up():
        # 提醒：未到期的重新排、睡著時到期的補送
        try:
            from personal import restore_reminders_from_notion
            from line_sender import push_to_user_sync
            restore_reminders_from_notion(push_to_user_sync)
        except Exception as e:
            print(f"[startup] reminders restore 失敗（非致命）：{e}")
        command_router.warm_caches()

    threading.Thread(target=_warm_up, name="warm-up", daemon=True).start()
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


# ── LIFF 記帳表單 ─────────────────────────────────────────
# 用 def 不用 async def：裡面全是同步的 Notion / LINE 呼叫，
# 讓 FastAPI 丟到 threadpool 跑，不卡住 webhook 那條 event loop。

_LIFF_HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "static", "liff_ledger.html")


@app.get("/liff/ledger")
def liff_ledger_page():
    from fastapi.responses import HTMLResponse

    import liff_api
    if not liff_api.liff_id():
        raise HTTPException(status_code=503, detail="LIFF not configured")
    with open(_LIFF_HTML_PATH, encoding="utf-8") as f:
        html = f.read().replace("__LIFF_ID__", liff_api.liff_id())
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


def _liff_user(authorization):
    import liff_api
    token = (authorization or "").removeprefix("Bearer ").strip()
    user_id = liff_api.verify_id_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="invalid token")
    return user_id


@app.get("/liff/api/bootstrap")
def liff_bootstrap(authorization: str | None = Header(None)):
    import liff_api
    return liff_api.bootstrap(_liff_user(authorization))


@app.post("/liff/api/record")
async def liff_record(request: Request, authorization: str | None = Header(None)):
    from fastapi.concurrency import run_in_threadpool
    from fastapi.responses import JSONResponse

    import liff_api
    payload = await request.json()
    user_id = await run_in_threadpool(_liff_user, authorization)
    status, body = await run_in_threadpool(liff_api.record, user_id, payload)
    return JSONResponse(body, status_code=status)


@app.get("/liff/api/entry")
def liff_entry_get(book: str, id: str, authorization: str | None = Header(None)):
    """編輯表單：讀一筆 + 它的異動紀錄。"""
    from fastapi.responses import JSONResponse

    import liff_api
    status, body = liff_api.get_entry(_liff_user(authorization), book, id)
    return JSONResponse(body, status_code=status)


@app.post("/liff/api/entry")
async def liff_entry_save(request: Request, authorization: str | None = Header(None)):
    """編輯表單：存修改，或 {"delete": true} 刪掉。"""
    from fastapi.concurrency import run_in_threadpool
    from fastapi.responses import JSONResponse

    import liff_api
    payload = await request.json()
    user_id = await run_in_threadpool(_liff_user, authorization)
    book, page_id = payload.get("book"), payload.get("id")
    if payload.get("delete"):
        status, body = await run_in_threadpool(liff_api.delete_entry, user_id, book, page_id)
    else:
        status, body = await run_in_threadpool(liff_api.save_entry, user_id, book,
                                               page_id, payload)
    return JSONResponse(body, status_code=status)


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
        # 第二個信箱（郵局通知寄到那個）的 readonly token，setup_post_token.py 產生
        "POST_TOKEN_PICKLE_B64",
        "ANTHROPIC_API_KEY",
        "WIDGET_TOKEN",
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
            response = command_router.handle_postback(data, user_id, ctx={
                "source_type": source.get("type"),
                "user_id": user_id,
                "group_id": source.get("groupId"),
            })
            if response:
                await reply_message(reply_token, response)
            continue

        if event_type != "message":
            print(f"[webhook] event={event_type} source={mask_source(source)}")
            continue
        msg = event.get("message", {}) or {}
        # 本人私訊傳圖片 = 轉帳截圖（入帳／LINE 轉帳沒有 Email，只能截圖，見 transfer_ocr）
        if msg.get("type") == "image" and reply_token:
            img_ctx = {"source_type": source.get("type"), "user_id": source.get("userId")}
            if command_router._is_admin(img_ctx) and msg.get("id"):
                import transfer_ocr
                print(f"[webhook] 轉帳截圖 source={mask_source(source)}")
                response = await asyncio.to_thread(transfer_ocr.handle_line_image, msg["id"])
                if response:
                    await reply_message(reply_token, response)
            continue
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
        # @提及：「午餐 120 @小美」幫別人記、「設管理員 @小美」。LINE 給的是
        # 字元位置，要原始訊息才對得上，所以一起帶著（多行訊息的位置對不上各行，不帶）
        mentionees = [m for m in ((msg.get("mention") or {}).get("mentionees") or [])
                      if m.get("type", "user") == "user" and m.get("userId")]
        if mentionees and len(text.splitlines()) <= 1:
            ctx["mentions"] = [m["userId"] for m in mentionees]
            ctx["mention_spans"] = [(m.get("index", 0), m.get("length", 0)) for m in mentionees]
            ctx["raw_text"] = text

        # 多行訊息：每行各自當獨立指令，依序塞進同一 reply（最多 5 則 LINE 上限）
        # 限制最多 3 行，避免一次塞十個指令把 server 卡爆
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        # 例外：「待辦」開頭換行貼一張清單，是一則指令不是好幾則 ——
        # 拆開的話第一行只叫出清單、其餘幾行被當成私訊指令擋掉
        if (ctx["source_type"] in ("group", "room") and lines
                and lines[0].lower() in ("待辦", "todo")):
            lines = lines[:1]
        if len(lines) > 1:
            parts = []
            for line in lines[:3]:
                r = await asyncio.to_thread(command_router.handle, line, ctx=ctx)
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

        # 丟執行緒：指令多半要查 Notion（「整理定位」要十幾秒），
        # 在事件迴圈上直接跑會卡住同時間進來的 OwnTracks 定位與其他訊息
        response = await asyncio.to_thread(command_router.handle, text, ctx=ctx)
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
    from daily_report import run_daily_report
    await run_daily_report(force_premarket=bool(force))
    return {"ok": True, "force_premarket": bool(force)}


def _check_admin(request: Request):
    admin_token = os.environ.get("ADMIN_TOKEN", "")
    if not admin_token:
        raise HTTPException(status_code=503, detail="Admin disabled")
    if request.headers.get("X-Admin-Token") != admin_token:
        raise HTTPException(status_code=403, detail="Forbidden")


@app.post("/admin/hut-ingest")
async def hut_ingest(request: Request):
    """本機 tools/hut_browser.py 用真瀏覽器抓一站式後送回來（一站式有 WAF，Railway 抓不到）。"""
    _check_admin(request)
    import hut_monitor
    try:
        out = hut_monitor.ingest(await request.json())
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise HTTPException(status_code=400, detail=str(e)[:200])
    # 網頁會休眠、伺服器端不再自己 07:00／16:30／23:30 檢查：本機送來就馬上比對
    #（林業署三站在 run_check 裡由伺服器自己抓）。背景跑，本機不用等。
    if os.environ.get("HUT_CHECK_ON_INGEST", "1") != "0":
        threading.Thread(target=_hut_check_after_ingest, name="hut-check", daemon=True).start()
    return out


def _hut_check_after_ingest():
    import hut_monitor
    try:
        n = hut_monitor.restore_from_notion()   # 剛醒來快照是空的，先從 Notion 補
        if n:
            print(f"[hut] 從 Notion 補回 {n} 筆快照")
        print(f"[hut] {hut_monitor.run_check()}")
    except Exception as e:
        print(f"[hut] 檢查失敗（非致命）：{e}")


@app.post("/admin/timetree-probe")
async def timetree_probe(request: Request):
    """從 Railway 試登 TimeTree，列出行事曆名稱（設 TIMETREE_CALENDAR 用）與明天筆數。"""
    _check_admin(request)
    import timetree_client
    return timetree_client.probe()


@app.post("/admin/payment-label-backfill")
async def payment_label_backfill(request: Request):
    """交易明細舊資料補「付款／載具」欄（背景跑）；GET 同路徑看進度。"""
    _check_admin(request)
    import notion_db
    import payment_label
    notion_db.get_or_create_db("交易明細")   # 確保欄位已建
    if not payment_label.start_backfill():
        raise HTTPException(status_code=409, detail="還在跑")
    return {"ok": True}


@app.get("/admin/payment-label-backfill")
async def payment_label_backfill_status(request: Request):
    _check_admin(request)
    import payment_label
    return payment_label.status()


@app.post("/admin/einvoice-carriers")
async def einvoice_carriers(request: Request, inv: str = ""):
    """財政部彙整信裡出現過哪些載具（名稱、號碼、張數）；inv=發票號碼 時回那張發票的載具。"""
    _check_admin(request)
    import gmail_reader
    import einvoice_sync

    def run():
        invoices = []
        einvoice_sync.fetch_rows(gmail_reader.get_gmail_service(), sink=invoices)
        counts = {}
        for i in invoices:
            k = (i.get("carrier_name") or "", i.get("carrier_id") or "")
            counts[k] = counts.get(k, 0) + 1
        hit = [{"carrier_name": i.get("carrier_name"), "carrier_id": i.get("carrier_id"),
                "date": str(i.get("date")), "seller": i.get("seller")}
               for i in invoices if inv and i.get("inv_num") == inv]
        return {"carriers": [{"name": n, "id": c, "count": v} for (n, c), v in counts.items()],
                "match": hit}
    return await asyncio.to_thread(run)


@app.post("/admin/personal-shops-backfill")
async def personal_shops_backfill(request: Request, apply: int = 0):
    """舊資料補套「一律個人的店」。預設只列出（apply=1 才寫）。"""
    _check_admin(request)
    import finance_sync
    return await asyncio.to_thread(finance_sync.backfill_personal_shops, dry_run=not apply)


@app.post("/admin/personal-morning-backfill")
async def personal_morning_backfill(request: Request, apply: int = 0):
    """舊的「平日上午全家」補標個人。預設只列出來（apply=1 才寫）。"""
    _check_admin(request)
    import finance_sync
    return await asyncio.to_thread(finance_sync.backfill_personal_morning, dry_run=not apply)


@app.post("/admin/timetree-import")
async def timetree_import_run(request: Request):
    """Claude 從使用者已登入的 TimeTree 分頁抓下 rows，送來這裡寫進 Notion（背景跑）。"""
    _check_admin(request)
    import timetree_import
    rows = await request.json()
    if not isinstance(rows, list):
        raise HTTPException(status_code=400, detail="body 要是 rows 陣列")
    if not timetree_import.start(rows):
        raise HTTPException(status_code=409, detail="上一批還在跑")
    return {"ok": True, "total": len(rows)}


@app.get("/admin/timetree-import")
async def timetree_import_status(request: Request):
    _check_admin(request)
    import timetree_import
    return timetree_import.status()


@app.post("/admin/hut-probe")
async def hut_probe(request: Request):
    """從 Railway 對林業署三站首頁各 GET 一次，看國外 IP 會不會被擋。"""
    _check_admin(request)
    import requests
    import hut_sources
    out = {}
    for site, cfg in hut_sources.SITES.items():
        try:
            r = await asyncio.to_thread(requests.get, cfg["url"], timeout=20,
                                        headers={"User-Agent": hut_sources.USER_AGENT})
            out[site] = {"status": r.status_code, "bytes": len(r.content),
                         "csrf": bool(hut_sources.find_csrf(r.text))}
        except Exception as e:
            out[site] = {"error": f"{type(e).__name__}: {e}"[:200]}
        await asyncio.sleep(hut_sources.REQUEST_GAP)
    return out


@app.post("/admin/hut-run")
async def hut_run(request: Request):
    """手動跑一次山屋檢查（會照常寄信）。"""
    _check_admin(request)
    import hut_monitor
    return await asyncio.to_thread(hut_monitor.run_check, True)


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


@app.post("/admin/transfer-ocr")
async def transfer_ocr_upload(request: Request):
    """本機 LINE 掃描（tools/line_scan.py）把聊天室截圖直接 POST 進來，
    走跟私訊傳截圖同一條路：Claude 讀圖 → 交易明細（方向＝轉帳），指紋去重所以重送安全。

    body 是圖片原始 bytes，Content-Type 帶 image/png 或 image/jpeg。
    """
    admin_token = os.environ.get("ADMIN_TOKEN", "")
    if not admin_token:
        raise HTTPException(status_code=503, detail="Admin disabled")
    if request.headers.get("X-Admin-Token") != admin_token:
        raise HTTPException(status_code=403, detail="Forbidden")
    media = (request.headers.get("Content-Type") or "").split(";")[0]
    if media not in ("image/png", "image/jpeg"):
        raise HTTPException(status_code=415, detail="image/png or image/jpeg only")
    img = await request.body()
    if not img or len(img) > 5_000_000:      # Anthropic 單張圖上限 5MB
        raise HTTPException(status_code=413, detail="image empty or over 5MB")
    import transfer_ocr
    return {"ok": True, "text": await asyncio.to_thread(transfer_ocr.handle_image, img, media)}


@app.post("/admin/transfer-rows")
async def transfer_rows_upload(request: Request):
    """本機已經讀好的轉帳清單直接記帳：Claude Code 在電腦上看截圖、讀出 JSON 送進來，
    不經過伺服器的 Anthropic key（key 失效時 LINE 掃描照樣能記）。

    body：{"rows": [...]}，每筆跟 transfer_ocr.extract 的輸出同格式。去重同截圖辨識。
    """
    admin_token = os.environ.get("ADMIN_TOKEN", "")
    if not admin_token:
        raise HTTPException(status_code=503, detail="Admin disabled")
    if request.headers.get("X-Admin-Token") != admin_token:
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="JSON body required")
    rows = body.get("rows") if isinstance(body, dict) else None
    if not isinstance(rows, list) or len(rows) > 200:
        raise HTTPException(status_code=400, detail='{"rows": [...]} with at most 200 rows')
    import transfer_ocr
    rows = [r for r in rows if isinstance(r, dict)]
    return {"ok": True, "text": await asyncio.to_thread(transfer_ocr.handle_rows, rows)}


@app.post("/admin/daily-notes")
async def daily_notes_upload(request: Request):
    """本機讀 LINE 聊天截圖寫好的每日一句話摘要 → 每日紀錄「今天做了什麼」（只填空白的）。

    body：{"notes": [{"date": "YYYY-MM-DD", "summary": "..."}]}。今天以後的日期不收 ——
    一天還沒過完，摘要只會是半天的，填進去之後就會因為「不空白」而再也補不完整。
    """
    admin_token = os.environ.get("ADMIN_TOKEN", "")
    if not admin_token:
        raise HTTPException(status_code=503, detail="Admin disabled")
    if request.headers.get("X-Admin-Token") != admin_token:
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="JSON body required")
    notes = body.get("notes") if isinstance(body, dict) else None
    if not isinstance(notes, list) or len(notes) > 31:
        raise HTTPException(status_code=400, detail='{"notes": [...]} with at most 31 days')
    import owntracks
    from datetime import date as _date
    from tz_utils import today_tpe
    out = {}
    for n in notes:
        try:
            day = _date.fromisoformat(str((n or {}).get("date") or ""))
        except ValueError:
            continue
        if day >= today_tpe():
            out[day.isoformat()] = "today_or_future"
            continue
        out[day.isoformat()] = await asyncio.to_thread(
            owntracks.set_daily_note, day, (n or {}).get("summary"))
    return {"ok": True, "result": out}


@app.get("/chart/{token}.png")
def chart_png(token: str):
    """群組圓餅圖的圖檔（LINE 圖片訊息只收網址）。token 隨機，只留最近 20 張。"""
    from fastapi.responses import Response

    import couple_chart
    png = couple_chart.get(token)
    if not png:
        raise HTTPException(status_code=404, detail="chart expired")
    return Response(png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/widget/todos")
def widget_todos(request: Request, who: str = ""):
    """手機桌面小工具（Scriptable）讀的資料：緊急待辦、專案進度。

    要 X-Widget-Token header。用獨立的 WIDGET_TOKEN 而不是 ADMIN_TOKEN：
    這把會放在手機的腳本裡，外流的話也只看得到待辦，碰不到 /admin。
    """
    token = os.environ.get("WIDGET_TOKEN", "")
    if not token:
        raise HTTPException(status_code=503, detail="Widget disabled")
    got = request.headers.get("X-Widget-Token", "")
    if not hmac.compare_digest(got.encode(), token.encode()):
        raise HTTPException(status_code=403, detail="Forbidden")
    import todo_widget
    return todo_widget.payload(who or None)


def _radar_auth(k):
    token = os.environ.get("RADAR_TOKEN", "")
    if token and not hmac.compare_digest(k.encode(), token.encode()):
        raise HTTPException(status_code=403, detail="Forbidden")


@app.get("/radar")
def radar_page(k: str = ""):
    """全球股市雷達（機器人賽道）。快照新的就秒開；舊了（過了當天 14:45）當場重抓約 20 秒。

    只有公開行情、沒有個人資料。設了 RADAR_TOKEN 就要帶 ?k=。
    """
    from fastapi.responses import HTMLResponse
    _radar_auth(k)
    import radar
    return HTMLResponse(radar.render(radar.snapshot_fresh()), headers={"Cache-Control": "no-store"})


@app.post("/radar/refresh")
def radar_refresh(k: str = ""):
    """頁面上的更新鈕：重抓一次（約 10–20 秒），回新的更新時間。"""
    _radar_auth(k)
    import radar
    d = radar.refresh()
    return {"ok": bool(d), "at": (d or {}).get("at"), "missing": len((d or {}).get("missing", []))}


@app.get("/radar/icon.png")
def radar_icon():
    """雷達的手機桌面圖示（加入主畫面時 iOS／Android 會抓這張）。"""
    from fastapi.responses import FileResponse
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "radar-icon.png"),
                        media_type="image/png", headers={"Cache-Control": "public, max-age=604800"})


@app.post("/admin/couple-backfill")
def couple_backfill(request: Request):
    """把交易明細既有的共同消費補進共同帳本（一次性；重跑安全，有去重）。"""
    admin_token = os.environ.get("ADMIN_TOKEN", "")
    if not admin_token:
        raise HTTPException(status_code=503, detail="Admin disabled")
    if request.headers.get("X-Admin-Token") != admin_token:
        raise HTTPException(status_code=403, detail="Forbidden")
    import couple_ledger
    return {"ok": True, "added": couple_ledger.backfill()}


@app.post("/owntracks")
async def owntracks_ingest(request: Request):
    """iPhone OwnTracks（HTTP 模式）送定位進來。驗證走 HTTP Basic（OWNTRACKS_PASSWORD）。

    寫 Notion 失敗回 500：OwnTracks 會把訊息留在手機佇列裡之後重送，不會掉點。
    """
    from fastapi.responses import JSONResponse
    import owntracks
    try:
        payload = await request.json()
    except Exception:
        payload = None
    status, body = await asyncio.to_thread(
        owntracks.handle_post, request.headers.get("Authorization"), payload)
    return JSONResponse(status_code=status, content=body)


@app.post("/admin/owntracks-summarize")
def owntracks_summarize(request: Request, day: str = ""):
    """手動整理某天的定位（?day=YYYY-MM-DD，預設昨天）。重跑安全。"""
    admin_token = os.environ.get("ADMIN_TOKEN", "")
    if not admin_token:
        raise HTTPException(status_code=503, detail="Admin disabled")
    if request.headers.get("X-Admin-Token") != admin_token:
        raise HTTPException(status_code=403, detail="Forbidden")
    from datetime import date as _date
    import owntracks
    return owntracks.run_for_day(_date.fromisoformat(day) if day else None)


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
