import pytest


@pytest.fixture(autouse=True)
def _reset_ledger_caches():
    """記帳按鈕的交易快取是模組層級的，不清的話上一個測試 mock 的
    交易會被下一個測試讀到 —— 測試結果就跟執行順序有關。"""
    import command_router
    import couple_ledger
    command_router._TXN_CACHE.update(at=0.0, rows=None)
    couple_ledger._CACHE.update(at=0.0, rows=None)
    yield
    command_router._TXN_CACHE.update(at=0.0, rows=None)
    couple_ledger._CACHE.update(at=0.0, rows=None)
