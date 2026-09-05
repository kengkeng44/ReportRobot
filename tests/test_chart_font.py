"""圓餅圖 / 溫度圖的中文字型解析。

2026-09-05：使用者收到的圓餅圖，圖例的中文全是豆腐方塊 □□。

容器裡**其實有**中文字型 —— nixpacks.toml 為了 Rich Menu 裝了
fonts-wqy-zenhei（路徑 /usr/share/fonts/truetype/wqy/wqy-zenhei.ttc）。
但 get_chinese_font() 的候選清單寫的是「WenQuanYi Micro Hei」
（那是 fonts-wqy-microhei 的家族名），跟裝的那顆對不上，於是
findfont 全部失敗、退回 DejaVu Sans —— 那顆沒有任何中文字。

setup_richmenu 用**檔案路徑 + os.path.exists** 找字型，一直是對的
（選單上有字）。這份測試守的就是「兩邊用同一套辦法」。
"""

import matplotlib.font_manager as fm

import weather

_ZENHEI = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"


def _only(monkeypatch, *existing):
    """假裝檔案系統上只有這幾個字型檔（模擬 Railway 容器）。"""
    keep = set(existing)
    monkeypatch.setattr(weather.os.path, "exists", lambda p: p in keep)

    def _no_family(*a, **kw):
        raise ValueError("no such family")

    monkeypatch.setattr(fm, "findfont", _no_family)


def test_finds_zenhei_by_path_like_the_container_has(monkeypatch):
    """Railway 容器只有 wqy-zenhei。找不到它就是整張圖都豆腐方塊。"""
    _only(monkeypatch, _ZENHEI)

    prop = weather.get_chinese_font()

    assert prop.get_file() == _ZENHEI


def test_path_lookup_beats_family_lookup(monkeypatch):
    """家族名查詢全部失敗時，仍然要靠路徑找到字型。

    findfont 依賴 matplotlib 自己的字型快取，容器重建後不一定即時。
    os.path.exists 沒有這個問題。
    """
    _only(monkeypatch, _ZENHEI)

    assert weather.get_chinese_font().get_file() is not None


def test_no_chinese_font_anywhere_does_not_crash(monkeypatch):
    """一顆中文字型都沒有時回預設值，不能丟例外 —— 寧可豆腐方塊也要出圖。"""
    _only(monkeypatch)

    prop = weather.get_chinese_font()

    assert isinstance(prop, fm.FontProperties)


def test_candidate_paths_cover_what_nixpacks_installs():
    """nixpacks.toml 裝的是 fonts-wqy-zenhei —— 清單裡一定要有它。

    這條守的是「部署設定與程式碼不准漂移」：哪天 nixpacks 換字型，
    這裡會紅。
    """
    from pathlib import Path

    nixpacks = (Path(__file__).resolve().parent.parent / "nixpacks.toml").read_text(
        encoding="utf-8")

    assert "fonts-wqy-zenhei" in nixpacks
    assert any("wqy-zenhei" in p for p in weather._FONT_PATHS)
