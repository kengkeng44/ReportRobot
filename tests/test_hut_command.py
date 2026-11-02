import command_router as cr
import hut_monitor


def test_admin_gets_hut_reply(monkeypatch):
    monkeypatch.setenv("ADMIN_LINE_USER_ID", "Uadmin")
    monkeypatch.setattr(hut_monitor, "line_reply", lambda: "山屋（測試）")
    ctx = {"source_type": "user", "user_id": "Uadmin"}
    assert cr.handle("山屋", ctx) == "山屋（測試）"
    assert cr.handle("/山屋空位", ctx) == "山屋（測試）"


def test_others_get_lock_not_ai(monkeypatch):
    monkeypatch.setenv("ADMIN_LINE_USER_ID", "Uadmin")
    ctx = {"source_type": "user", "user_id": "Usomeone_else"}
    assert cr.parse("山屋")[0] == "private_shortcut"
    assert "🔒" in cr.handle("山屋", ctx)
