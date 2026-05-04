"""
外部 HTTP 請求的 retry 包裝。

對 timeout / 連線失敗 / 429 / 5xx 自動重試 3 次（exponential backoff 2→4→8 秒）。
4xx 不重試（請求本身有問題，重試也沒用）。

使用：把 `import requests; r = requests.get(...)` 改成
      `import http_utils; r = http_utils.get(...)`
其餘 keyword args 與 requests.get 完全相容（params / headers / timeout）。
"""

import requests
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception,
)


class RetryableHTTPError(Exception):
    """429 或 5xx：值得重試。"""


def _is_retryable(exc):
    """timeout / 連線錯誤 / 429 / 5xx → 重試；其他不重試。"""
    if isinstance(exc, (
        requests.exceptions.Timeout,
        requests.exceptions.ConnectionError,
    )):
        return True
    if isinstance(exc, RetryableHTTPError):
        return True
    return False


_RETRY = dict(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception(_is_retryable),
    reraise=True,
)


@retry(**_RETRY)
def get(url, **kwargs):
    r = requests.get(url, **kwargs)
    if r.status_code == 429 or 500 <= r.status_code < 600:
        raise RetryableHTTPError(f"HTTP {r.status_code} from {url[:80]}")
    return r


@retry(**_RETRY)
def post(url, **kwargs):
    r = requests.post(url, **kwargs)
    if r.status_code == 429 or 500 <= r.status_code < 600:
        raise RetryableHTTPError(f"HTTP {r.status_code} from {url[:80]}")
    return r
