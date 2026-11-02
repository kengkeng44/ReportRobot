"""山屋地點的固定資料：顯示名、官方查詢頁、交通卡、給 Claude 的申請句子。"""

import hut_onestop
import hut_sources

PLACE = {
    "jmlnt": "嘉明湖／向陽",
    "tconline": "天池",
    "kgonline": "檜谷",
    "onestop_paiyun": "排雲",
    "onestop_99": "九九",
    "onestop_7ka": "雪霸",
    "onestop_cilai": "奇萊",
    "onestop_nanhu": "南湖",
}

QUERY_URL = {
    **{site: cfg["url"] for site, cfg in hut_sources.SITES.items()},
    **{site: hut_onestop.BASE + path for site, (path, _) in hut_onestop.ONESTOP.items()},
}

# text：交通重點；source：要去核對的官方頁；checked：核對日期（None = 還沒核對）
TRANSPORT = {
    "嘉明湖／向陽": {
        "text": "登山口：向陽國家森林遊樂區（台 20 線南橫 154.5K）。自駕台 9 線 → 池上 → 台 20 甲約 5 km → 右轉台 20 線。官網：無大眾運輸，須自駕或租車。",
        "source": "https://jmlnt.forest.gov.tw/", "checked": "2026-11-02"},
    "天池": {
        "text": "登山口：屯原（南投仁愛）。埔里包車為主。屯原服務站 07:00–17:00 登記。",
        "source": "https://tconline.forest.gov.tw/", "checked": None},
    "檜谷": {
        "text": "登山口：北大武新登山口（屏東泰武）。自駕國 3 林羅交流道 → 台 1 線 → 內埔昌黎祠 → 縣 187-A → 沿山公路（縣 185），三條路線皆到新登山口；舊登山口再步行約 2.8 km。官網未列大眾運輸。",
        "source": "https://kgonline.forest.gov.tw/", "checked": "2026-11-02"},
    "排雲": {
        "text": "登山口：塔塔加。員林客運 6739（日月潭—阿里山）停塔塔加與上東埔，每日僅 4 班；排雲登山服務中心在上東埔站下車。自駕水里 → 台 21 線 → 塔塔加，或嘉義 → 台 18 線 → 阿里山 → 塔塔加。台 21 線 132K+300 管制至 2027-02-16，出發前查路況。",
        "source": "https://www.ysnp.gov.tw/", "checked": "2026-11-02"},
    "九九": {
        "text": "大霸尖山線，經觀霧進。開車：竹東→縣道 122→五峰→清泉→土場→大鹿林道（約 25 km，只限中型巴士以下）→觀霧。"
                "大眾運輸：高鐵新竹站或台鐵竹東站轉台灣好行觀霧線。大鹿林道 24K 施工管制到 2027-01-31（平日 07:30–16:30 每小時半點放行）。",
        "source": "https://www.spnp.gov.tw/ch/mountaininfo/guanwu-recreation-area", "checked": "2026-11-02"},
    "雪霸": {
        "text": "登山口：雪山登山口（武陵遊憩區）。開車：國道 5 號宜蘭→台 7→台 7 甲→武陵（約 100 km），或埔里→台 14 甲→大禹嶺→梨山→武陵。"
                "大眾運輸：國光客運宜蘭 07:30、12:40／羅東 07:00 往梨山，約 2 小時 45 分；或豐原客運到梨山再轉武陵。",
        "source": "https://www.spnp.gov.tw/ch/mountaininfo/wuling-recreation-area", "checked": "2026-11-02"},
    "奇萊": {
        "text": "登山口：合歡山滑雪山莊附近（台 14 甲）。交通待核對。",
        "source": "https://www.taroko.gov.tw/", "checked": None},
    "南湖": {
        "text": "登山口：勝光／思源埡口（台 7 甲）。交通待核對。",
        "source": "https://www.taroko.gov.tw/", "checked": None},
}


def place(site):
    return PLACE.get(site, site)


def display(slot):
    """周圍營地這種各站都有的名字前面加地點。"""
    if slot.unit.startswith("周圍"):
        return f"{place(slot.site)} {slot.unit}"
    return slot.unit


def transport_card(place_name):
    t = TRANSPORT.get(place_name)
    if not t:
        return ""
    if t["checked"]:
        return f"{place_name}：{t['text']}（{t['checked']} 核對）"
    return f"{place_name}：{t['text']}\n⚠️ 尚未核對官方資料，出發前請查：{t['source']}"


def apply_phrase(slot):
    return f"用 hut-apply 申請 {display(slot)} {slot.day.isoformat()} 2人"
