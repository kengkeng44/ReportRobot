"""把舊的「每日一句」搬進 Notion 金句庫。一次性腳本。

    infisical run -- python import_quotes.py <舊DB的ID> --dry-run
    infisical run -- python import_quotes.py <舊DB的ID>

一定要走 infisical —— 直接跑 python 讀不到 NOTION_TOKEN,會整支停在
「連不上 Notion」。預設 --dry-run 先看報告,確認沒問題再拿掉旗標。

舊資料是 Notion CSV 匯出的原始 dump(ExportBlock),欄位跟金句庫對不上:

    好句(title)      → 句子本體 + 出處混在一起,例如「…的圈子。—大叔語錄」
    Multi-select     → 主題標籤,逗號串起來的字串「休息, 力量, 思考」
    編號 / 今天 / 顯示 → 舊系統的排程機制,新的用不到,不搬

出處拆解是**啟發式的,一定會有拆不乾淨的**。所以 --dry-run 會把
「沒拆出出處」和「句子裡還留著網址」的挑出來單獨列,那些是人工要掃
一眼的。整批 370 筆逐句檢查不實際,但這兩類加起來通常只有幾十筆。

重跑安全:去重比對的是**拆完的句子**,不是原始文字 —— 不然同一句話
因為出處寫法不同(「—愛因斯坦」vs「-愛因斯坦」)會被當成兩句。
"""

import argparse
import re
import sys

# 舊資料混用這幾種破折號。全形半形都要認 —— 只認一種會漏掉一大半。
_MARKERS = "—–－-─"

# 破折號後面超過這個長度就不是人名,是句子的一部分。
# 14 是照真實資料抓的:370 筆裡最長的出處是 13 字的「書名+作者」
# (《寫作是最好的投資》陳立飛、約翰．麥斯威爾《從內做起》),
# 一般人名 2-8 字。設 20 會把「—你以為的終點往往只是…」整段當成作者。
_MAX_SOURCE_LEN = 14

# 尾巴裡出現這些代表那是句子不是人名
_SENTENCE_PUNCT = "。，,！？；"

_URL_RE = re.compile(r"https?://\S+")
_WS_RE = re.compile(r"\s+")

# 出處後面常掛一個推薦連結,連同外面那層括號一起丟。
# 只丟「括號裡整個都是網址」的那種 —— 《寫作是最好的投資》這種
# 書名號要留著,它是出處的一部分。
_BRACKETED_URL_RE = re.compile(
    r"[《【（(\[]\s*https?://[^》】）)\]]*\s*[》】）)\]]?"
)


def _clean(text):
    """把換行與連續空白壓成單一空白。

    舊資料的網址後面常帶換行(CSV 匯出的產物),原樣搬進去信裡會
    在莫名其妙的地方斷行。
    """
    return _WS_RE.sub(" ", (text or "").strip())


def split_source(text):
    """把「句子。—作者」拆成 (句子, 作者)。拆不出來就回 (原句, "")。

    刻意保守:寧可不拆,也不要把半句話塞進「出處」欄位。出處空著
    只是少一行「—— 某某」,拆錯卻會讓句子本身缺一截。

    找破折號之前先把網址遮成等長的佔位字元 —— 不然
    「…the-5-second-rule-book/》」裡面的連字號會被當成分界,
    拆出一個叫「book/」的作者。遮罩等長,索引才對得回原字串。
    """
    text = _clean(text)
    if not text:
        return "", ""

    masked = _URL_RE.sub(lambda m: " " * len(m.group()), text)
    idx = max((masked.rfind(m) for m in _MARKERS), default=-1)
    if idx <= 0:                      # 沒有,或整句以破折號開頭
        return text, ""

    tail = _BRACKETED_URL_RE.sub("", text[idx + 1:])
    tail = _clean(_URL_RE.sub("", tail))
    if not tail or len(tail) > _MAX_SOURCE_LEN:
        return text, ""
    if any(p in tail for p in _SENTENCE_PUNCT):
        return text, ""

    # 「--泰戈爾」這種雙破折號會在句子尾巴留一個孤兒符號
    quote = text[:idx].rstrip(_MARKERS + " ")
    if not quote:
        return text, ""
    return quote, tail


def split_themes(text):
    """「休息, 力量, 思考」→ ["休息", "力量", "思考"]。順序保留、去重。"""
    seen, out = set(), []
    for part in (text or "").split(","):
        tag = part.strip()
        if tag and tag not in seen:
            seen.add(tag)
            out.append(tag)
    return out


def plan_import(rows, existing):
    """回 (要寫入的, 已存在跳過的)。

    rows 是 [{"text", "themes"}](舊 DB 的原樣),
    existing 是金句庫現有的 [{"sentence", ...}]。

    去重比對拆完的句子 —— 見模組 docstring 的說明。舊資料自己就有
    重複(370 筆裡只有 368 句不同),所以同一批裡面也要去重。
    """
    seen = {_clean(e.get("sentence")) for e in existing or []}

    to_add, skipped = [], []
    for row in rows or []:
        sentence, source = split_source(row.get("text"))
        if not sentence:
            continue                  # 空白列不是資料
        if sentence in seen:
            skipped.append(sentence)
            continue
        seen.add(sentence)
        to_add.append({
            "sentence": sentence,
            "source": source,
            "themes": split_themes(row.get("themes")),
        })
    return to_add, skipped


def needs_review(planned):
    """挑出人工要掃一眼的:沒拆出出處的、以及句子裡還留著網址的。

    370 筆逐句檢查不實際,但這兩類通常只有幾十筆 —— 那是啟發式
    最可能出錯的地方。
    """
    return [r for r in planned
            if not r.get("source") or _URL_RE.search(r.get("sentence", ""))]


# ─────────────────────────────────────────────────────────
# 以下開始碰 Notion
# ─────────────────────────────────────────────────────────

def read_legacy(db_id, limit=1000):
    """從舊的「每日一句」讀出 [{"text", "themes"}]。"""
    import notion_db

    client = notion_db._get_client()
    if not client:
        return []
    rows = notion_db._query_all(db_id, client, limit)
    out = []
    for r in rows:
        props = r.get("properties", {}) or {}
        out.append({
            "text": notion_db._read_title(props, "好句"),
            "themes": notion_db._read_rich_text(props, "Multi-select"),
        })
    return out


def _print_report(to_add, skipped, flagged, preview=15):
    print(f"\n可匯入 {len(to_add)} 句，已存在跳過 {len(skipped)} 句")
    print(f"其中 {len(flagged)} 句建議人工看一眼"
          f"（沒拆出出處，或句子裡還留著網址）")

    if to_add:
        print(f"\n── 拆解結果前 {min(preview, len(to_add))} 筆 ──")
        for r in to_add[:preview]:
            src = f"　—— {r['source']}" if r["source"] else "　（無出處）"
            tags = f"　[{', '.join(r['themes'])}]" if r["themes"] else ""
            print(f"・{r['sentence'][:44]}{src}{tags}")

    if flagged:
        print(f"\n── 建議人工確認的前 {min(preview, len(flagged))} 筆 ──")
        for r in flagged[:preview]:
            print(f"・{r['sentence'][:60]}")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="把舊的「每日一句」搬進 Notion 金句庫")
    ap.add_argument("legacy_db_id", help="舊「每日一句」資料庫的 Notion ID")
    ap.add_argument("--dry-run", action="store_true",
                    help="只印報告，不實際寫入")
    ap.add_argument("--preview", type=int, default=15,
                    help="報告裡各列幾筆（預設 15）")
    args = ap.parse_args(argv)

    import notion_db

    if not notion_db.is_configured():
        print("[錯誤] 連不上 Notion —— NOTION_TOKEN 沒讀到。", file=sys.stderr)
        print("這支腳本要走 infisical：", file=sys.stderr)
        print("  infisical run -- python import_quotes.py <舊DB的ID> --dry-run",
              file=sys.stderr)
        return 1

    rows = read_legacy(args.legacy_db_id)
    print(f"舊資料讀到 {len(rows)} 筆")
    if not rows:
        print("讀不到任何資料。確認 ID 對不對，"
              "以及那一頁有沒有 Share → Connect to 你的 integration。",
              file=sys.stderr)
        return 1

    to_add, skipped = plan_import(rows, notion_db.quotes_load())
    flagged = needs_review(to_add)
    _print_report(to_add, skipped, flagged, args.preview)

    if args.dry_run:
        print("\n[dry-run] 沒有寫入任何東西。"
              "確認上面沒問題後，把 --dry-run 拿掉再跑一次。")
        return 0

    written = 0
    for r in to_add:
        if notion_db.quote_add(r["sentence"], source=r["source"],
                               themes=r["themes"]):
            written += 1
    print(f"\n寫入完成：{written}/{len(to_add)} 句")
    if written < len(to_add):
        print(f"有 {len(to_add) - written} 句寫入失敗，訊息在上面。",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
