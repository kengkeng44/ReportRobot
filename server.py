"""
長駐 web service：
- POST /line/webhook：接 LINE Messaging API 訊息事件，dispatch 到 command_router
- 背景 scheduler：每天 UTC 00:00（台北 08:00）跑 run_daily_report
- GET /：健康檢查
"""

import base64
import hashlib
import hmac
import os
from contextlib import asynccontextmanager

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
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
DAILY_CRON = os.environ.get("DAILY_CRON", "0 0 * * *")  # 預設 UTC 00:00 = 台北 08:00


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


def _periodic_todo_reminder():
    """每 4 小時提醒所有 user 未完成的待辦。同步函式（apscheduler 直接呼叫）。"""
    from personal import send_pending_reminders
    from line_sender import push_to_user_sync
    send_pending_reminders(push_to_user_sync)


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
    # 待辦定期提醒：台北時間每天 06:00 / 09:00 / 12:00 / 18:00
    # 沒有未完成待辦時 send_pending_reminders 內部會 skip，不會吵
    scheduler.add_job(
        _periodic_todo_reminder,
        CronTrigger(hour="6,9,12,18", minute=0, timezone="Asia/Taipei"),
        id="periodic_todo_reminder",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
        replace_existing=True,
    )
    scheduler.add_listener(
        _scheduler_error_listener,
        EVENT_JOB_ERROR | EVENT_JOB_MISSED,
    )
    scheduler.start()
    # 注入 scheduler 給 personal.py 用（提醒功能要排 one-shot job）
    import app_state
    app_state.set_scheduler(scheduler)
    print(f"Scheduler 啟動，每日報排程：{DAILY_CRON} (UTC)")
    print("待辦定期提醒：台北 06:00 / 09:00 / 12:00 / 18:00（沒待辦時不發）")
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
async def env_check():
    """Server 看到的環境變數狀態（只回 set/len，不洩漏值）。Debug 用。"""
    keys = [
        "ADMIN_TOKEN",
        "LINE_CHANNEL_TOKEN",
        "LINE_CHANNEL_SECRET",
        "LINE_GROUP_ID",
        "GMAIL_USER",
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
        if event.get("type") != "message":
            source = event.get("source", {}) or {}
            print(f"[webhook] event={event.get('type')} source={mask_source(source)}")
            continue
        msg = event.get("message", {}) or {}
        if msg.get("type") != "text":
            continue
        text = msg.get("text", "")
        reply_token = event.get("replyToken")
        source = event.get("source", {}) or {}
        print(f"[webhook] message text={text[:30]!r} source={mask_source(source)}")
        if not reply_token:
            continue

        # 把 source 資訊塞給 command_router，個人指令用 source_type 判斷
        ctx = {
            "source_type": source.get("type"),  # 'user' / 'group' / 'room'
            "user_id": source.get("userId"),
            "group_id": source.get("groupId"),
        }

        # 多行訊息：每行各自當獨立指令，結果合併成一則 reply
        # 限制最多 3 行，避免一次塞十個指令把 server 卡爆
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if len(lines) > 1:
            parts = []
            for line in lines[:3]:
                r = command_router.handle(line, ctx=ctx)
                if r:
                    parts.append(r)
            if parts:
                combined = "\n\n━━━━━━━━━━\n\n".join(parts)
                print(f"LINE 多行指令命中 {len(parts)} 行 → 回覆 {len(combined)} 字")
                await reply_message(reply_token, combined)
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
