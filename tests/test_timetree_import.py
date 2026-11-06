"""TimeTree 匯入：欄位對應、依 TimeTree ID upsert、更新日沒變就跳過。"""

import timetree_import


class FakeNotion:
    def __init__(self, existing):
        self.existing = existing
        self.created, self.updated = [], []
        self.databases = self
        self.pages = self

    def query(self, **kw):
        results = [{"id": pid, "properties": {
            "TimeTree ID": {"rich_text": [{"plain_text": tid}]},
            "TimeTree 更新時間": {"date": {"start": upd}}}} for tid, (pid, upd) in self.existing.items()]
        return {"results": results, "has_more": False}

    def create(self, parent, properties):
        self.created.append(properties)

    def update(self, page_id, properties):
        self.updated.append(page_id)


def test_row_to_props_timed():
    p = timetree_import.row_to_props({"t": "吃飯", "c": "私人", "id": "x", "u": "2026-01-02",
                                      "s": "2026-01-03T12:00:00+08:00", "e": "2026-01-03T13:00:00+08:00"})
    assert p["日期"]["date"] == {"start": "2026-01-03T12:00:00+08:00", "end": "2026-01-03T13:00:00+08:00"}
    assert p["整天"]["checkbox"] is False
    assert p["行事曆"]["select"]["name"] == "私人"
    assert p["地點"] == {"rich_text": []}


def test_upsert_create_update_skip():
    fake = FakeNotion({"same": ("p1", "2026-01-01"), "changed": ("p2", "2026-01-01")})
    rows = [
        {"id": "same", "u": "2026-01-01", "s": "2026-01-05", "a": 1, "t": "a", "c": "私人"},
        {"id": "changed", "u": "2026-02-01", "s": "2026-01-05", "a": 1, "t": "b", "c": "私人"},
        {"id": "new", "u": "2026-02-01", "s": "2026-01-05", "a": 1, "t": "c", "c": "用力生活"},
    ]
    stats = timetree_import.upsert(rows, client=fake, sleep=0)
    assert stats == {"created": 1, "updated": 1, "unchanged": 1, "failed": 0}
    assert fake.updated == ["p2"]
