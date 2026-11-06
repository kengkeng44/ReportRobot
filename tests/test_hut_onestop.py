from datetime import date

import pytest

import hut_onestop as ho

SAMPLE = """
<table class="table_bed"><tbody><tr>
  <td data-th="星期三"><p>1</p><p>
    <a href="bed_6main.aspx?node_id=3&sdate=2026-07-01">
      <span>餘額</span> (<span>1</span>,<span>0</span>)<br>
      <span>排隊預約</span> <span>67</span><br>
      <span>審核中</span> <span>1</span><br>
      <span>核准入園</span> <span>110</span><br>
      <span>(<span>115</span>,<span>0</span>)</span>
    </a></p></td>
  <td data-th="星期四"><p>2</p><p>
    <a href="bed_6main.aspx?node_id=3&sdate=2026-07-02">
      <span>額滿</span><br>
      <span>排隊預約</span> <span>77</span><br>
      <span>核准入園</span> <span>111</span><br>
    </a></p></td>
</tr></tbody></table>
"""


def test_parse_remaining_and_full():
    slots = ho.parse_onestop_html(SAMPLE, "onestop_paiyun")
    assert [(s.day, s.remaining, s.applicants) for s in slots] == [
        (date(2026, 7, 1), 1, 67),
        (date(2026, 7, 2), 0, 77),
    ]
    assert {(s.site, s.unit, s.kind) for s in slots} == {("onestop_paiyun", "排雲山莊", "bed")}


def test_rejected_page_raises_blocked():
    html = "<html><title>Request Rejected</title>The requested URL was rejected.</html>"
    with pytest.raises(ho.Blocked):
        ho.parse_onestop_html(html, "onestop_paiyun")
    with pytest.raises(ho.Blocked):
        ho.ensure_not_blocked(html)


def test_every_target_has_page_and_label():
    for site, (path, label) in ho.ONESTOP.items():
        assert site.startswith("onestop_") and path.startswith("bed_") and label


from pathlib import Path

FIX = Path(__file__).parent / "fixtures" / "hut"


@pytest.mark.parametrize("site", sorted(ho.ONESTOP))
def test_real_sample_parses(site):
    # 本月月曆從「今天 +5 天」才開始有格子（2026-11-02 抓的樣本從 11/7 起），所以不到 28 天
    html = (FIX / f"{site}.html").read_text(encoding="utf-8")
    slots = ho.parse_onestop_html(html, site)
    assert len(slots) >= 20
    assert any(s.remaining is not None for s in slots)


def test_sheiba_style_cell():
    """雪霸／太魯閣的格子：「餘額 4 待處理 3」，沒有括號。"""
    html = ('<table class="table_bed"><tr><td><a href="x?sdate=2026-12-05">'
            "餘額 4 待處理 3 已通過 36</a></td></tr></table>")
    s = ho.parse_onestop_html(html, "onestop_cilai")[0]
    assert (s.remaining, s.applicants) == (4, 3)


def test_369_camp_fixture_is_tent():
    """三六九只剩臨時營地，用 2026-11-06 抓的真頁面；營地算帳不算床。"""
    from pathlib import Path
    html = (Path(__file__).parent / "fixtures" / "hut" / "onestop_369camp.html").read_text(encoding="utf-8")
    slots = ho.parse_onestop_html(html, "onestop_369camp")
    assert len(slots) == 20
    assert {s.kind for s in slots} == {"tent"}
    sat = {s.day: s.remaining for s in slots if s.day.weekday() == 5}
    assert sat[date(2026, 11, 21)] == 1
