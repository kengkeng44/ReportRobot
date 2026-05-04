"""
資安工具：log 脫敏。
所有對外輸出（print / 推播 / 通知）涉及 token / userId / 帳號號碼時，
請改用 mask() 包起來，避免敏感字串外洩到 Railway log 或 LINE。
"""


def mask(value, keep=4):
    """遮罩敏感字串，保留頭尾各 keep 個字元，中間以 *** 取代。

    例：
        mask("Ub1234567890abcdef") → "Ub12***cdef"
        mask("xxx", keep=4)        → "***"  （長度不夠就整段藏）
        mask(None)                 → "***"
    """
    if not value:
        return "***"
    s = str(value)
    if len(s) <= keep * 2:
        return "***"
    return f"{s[:keep]}***{s[-keep:]}"


def mask_source(source):
    """LINE webhook 的 source dict 脫敏（userId / groupId 都遮）。"""
    if not isinstance(source, dict):
        return source
    out = dict(source)
    for k in ("userId", "groupId", "roomId"):
        if k in out and out[k]:
            out[k] = mask(out[k])
    return out
