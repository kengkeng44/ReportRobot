"""
跨模組共享的執行時狀態：
- scheduler：由 server.py 啟動時注入，personal.py 加提醒時用
避免 server.py / personal.py / command_router.py 互相 import 造成循環。
"""

scheduler = None


def set_scheduler(s):
    global scheduler
    scheduler = s


def get_scheduler():
    return scheduler
