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
        "text": "登山口：向陽國家森林遊樂區。自駕台 9 線 → 池上 → 台 20 線南橫。無定期大眾運輸，多為池上／台東包車。南橫東段（利稻—向陽）路況洽關山工務段 089-811024。",
        "source": "https://jmlnt.forest.gov.tw/", "checked": None},
    "天池": {
        "text": "登山口：屯原（南投仁愛）。埔里包車為主。屯原服務站 07:00–17:00 登記。",
        "source": "https://tconline.forest.gov.tw/", "checked": None},
    "檜谷": {
        "text": "登山口：北大武（屏東泰武）。無大眾運輸，自駕或包車；出發前確認新舊登山口與步道是否開放。",
        "source": "https://kgonline.forest.gov.tw/", "checked": None},
    "排雲": {
        "text": "登山口：塔塔加。員林客運 6739（日月潭—阿里山）停塔塔加，班次少；上東埔停車場到登山口 2.8 km，東埔山莊有接駁。自駕國 3 名間交流道 → 水里 → 新中橫。",
        "source": "https://www.ysnp.gov.tw/", "checked": None},
    "九九": {
        "text": "登山口：雪山登山口（武陵農場）。國光客運 1751、豐原客運 865 往武陵。台 7 甲思源—梨山段單車道。",
        "source": "https://www.spnp.gov.tw/", "checked": None},
    "雪霸": {
        "text": "登山口：雪山登山口（武陵農場），到七卡山莊 2 km。國光客運 1751、豐原客運 865 往武陵。",
        "source": "https://www.spnp.gov.tw/", "checked": None},
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
