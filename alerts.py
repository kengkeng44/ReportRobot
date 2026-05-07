"""
即時警示模組。
目前支援：CWA 顯著有感地震（規模 ≥ 4.0）。

機制：scheduler 每 5 分鐘呼叫 check_and_push()，比對上次抓到的 EarthquakeNo，
有新就 push 群組（公共資訊家裡都該知道）+ admin。
規模門檻可用環境變數 EQ_MIN_MAGNITUDE 覆寫（預設 4.0）。

in-memory state（_LAST_EQ_NUMBER）redeploy 後重置；首次抓到只記錄不推。
"""

import os

import http_utils


CWA_EARTHQUAKE_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/E-A0015-001"


def _env(name):
    val = os.environ.get(name)
    if val:
        return val
    try:
        import config
        return getattr(config, name, "")
    except (ImportError, AttributeError):
        return ""


CWA_API_KEY = _env("CWA_API_KEY")
EQ_MIN_MAG = float(os.environ.get("EQ_MIN_MAGNITUDE", "4.0"))

_LAST_EQ_NUMBER = None


def _format_eq(eq):
    """從 CWA E-A0015-001 一筆地震記錄組訊息字串。"""
    info = eq.get("EarthquakeInfo", {}) or {}
    epi = info.get("Epicenter", {}) or {}
    mag = info.get("EarthquakeMagnitude", {}) or {}
    depth = info.get("Depth", {}) or {}

    origin = info.get("OriginTime", "?")
    location = epi.get("Location", "?")
    mag_value = mag.get("MagnitudeValue", "?")
    depth_km = depth.get("Value", "?")

    # 最大震度
    max_intensity = "?"
    intensity = eq.get("Intensity", {}) or {}
    shaking = intensity.get("ShakingArea") or []
    if shaking:
        # ShakingArea[0] 是最強震度區
        max_intensity = shaking[0].get("AreaIntensity", "?")

    return (
        f"⚠️ CWA 顯著有感地震\n"
        f"時間：{origin}\n"
        f"震央：{location}\n"
        f"規模：M {mag_value}\n"
        f"深度：{depth_km} 公里\n"
        f"最大震度：{max_intensity}"
    )


def check_earthquake():
    """抓 CWA 最新一筆地震，比對上次的 EarthquakeNo。
    有新且規模 ≥ 門檻 → 回 (eq_number, message)；否則回 None。"""
    global _LAST_EQ_NUMBER

    if not CWA_API_KEY:
        return None

    try:
        r = http_utils.get(
            CWA_EARTHQUAKE_URL,
            params={"Authorization": CWA_API_KEY, "limit": 1},
            timeout=10,
        )
        eq_list = (r.json().get("records", {}) or {}).get("Earthquake") or []
        if not eq_list:
            return None
        eq = eq_list[0]
        no = eq.get("EarthquakeNo")
        if no is None:
            return None

        # 第一次跑，只記錄不推（避免 redeploy 後立刻推一筆很久之前的）
        if _LAST_EQ_NUMBER is None:
            _LAST_EQ_NUMBER = no
            print(f"[alert/eq] 初始化 _LAST_EQ_NUMBER={no}（不推送）")
            return None

        if no == _LAST_EQ_NUMBER:
            return None

        # 規模門檻
        try:
            mag_value = float(
                (eq.get("EarthquakeInfo", {}) or {})
                .get("EarthquakeMagnitude", {})
                .get("MagnitudeValue", 0)
            )
        except (TypeError, ValueError):
            mag_value = 0.0

        _LAST_EQ_NUMBER = no  # 不論規模大小都更新，避免下次又抓到同一筆
        if mag_value < EQ_MIN_MAG:
            print(f"[alert/eq] 新地震 {no} 規模 {mag_value} < {EQ_MIN_MAG}，不推送")
            return None

        return (no, _format_eq(eq))
    except Exception as e:
        print(f"[alert/eq] CWA 地震抓取失敗：{e}")
        return None


def check_and_push():
    """scheduler 入口：抓地震 → 推群組 + admin。"""
    result = check_earthquake()
    if not result:
        return
    eq_no, message = result
    print(f"[alert/eq] 推送地震 {eq_no}")

    # 推群組（地震是公共資訊，家裡都該收到）
    try:
        from line_sender import push_message_sync
        push_message_sync(message)
    except Exception as e:
        print(f"[alert/eq] 群組 push 失敗：{e}")

    # 推 admin（多一份備份；不會因群組失敗就漏掉）
    admin_id = os.environ.get("ADMIN_LINE_USER_ID", "")
    if admin_id:
        try:
            from line_sender import push_to_user_sync
            push_to_user_sync(admin_id, message)
        except Exception as e:
            print(f"[alert/eq] admin push 失敗：{e}")
