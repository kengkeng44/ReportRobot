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
