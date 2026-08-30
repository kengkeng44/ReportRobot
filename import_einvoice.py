"""把財政部載具 CSV 匯入 Notion「食材庫存」。

    infisical run -- python import_einvoice.py <csv路徑> --dry-run
    infisical run -- python import_einvoice.py <csv路徑>

一定要走 infisical —— 本機沒有 NOTION_TOKEN，直接 python 跑會連不上。
先用 --dry-run 看會寫什麼進去，確認沒問題再拿掉旗標實際寫入。

這條線**不碰交易明細**。記帳歸國泰彙整信管（那邊只有總額，本來就夠用），
載具負責回答「買了什麼菜」與營養分析。兩邊分開，同一筆消費就不會被記兩次。

管線：
    einvoice_csv.parse   解析 CSV（兩種格式自動判別）
    einvoice_pantry      過濾非食材 + 套 kitchen 的分類與營養
    plan_import          比對 Notion 既有資料去重
    notion_db.pantry_add 逐筆寫入
"""

import argparse
import io
import sys
from collections import Counter
from datetime import date


def _as_iso(value):
    """把 date 或 Notion 讀回來的字串統一成 'YYYY-MM-DD'。

    去重鍵兩邊的型別不同（本地是 date，Notion 回字串），
    不統一的話同一天會被判成兩天，重跑就寫出兩份。
    """
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def plan_import(rows, existing):
    """分成「要寫入」與「已存在跳過」兩堆。

    去重鍵是 (名稱, 購買日)。同一種菜不同天買是兩批不同的庫存，
    到期日不一樣，不能當成重複；但同一份 CSV 跑兩次，
    那兩批的鍵完全相同 —— 那才是要擋的。

    只擋「已經在 Notion 裡」的，不擋檔案內部的重複：同一天在同一家店
    買兩把青江菜，發票上本來就是兩列。
    """
    seen = {
        (e.get("name"), _as_iso(e.get("bought")))
        for e in existing or []
        if e.get("bought")      # 手動加的沒填購買日，不該擋住匯入
    }

    to_add, skipped = [], []
    for row in rows:
        key = (row.get("name"), _as_iso(row.get("bought")))
        (skipped if key in seen else to_add).append(row)
    return to_add, skipped


def format_summary(rows, skipped):
    """dry-run 的摘要。看得出分類分布才知道過濾有沒有出錯。"""
    lines = [f"待寫入 {len(rows)} 筆，已存在跳過 {len(skipped)} 筆"]

    cats = Counter(r.get("category") or "未分類" for r in rows)
    if cats:
        lines.append("分類：" + "、".join(f"{k} {v}" for k, v in cats.most_common()))

    nutri = sum(1 for r in rows if r.get("per_100g"))
    lines.append(f"有營養值：{nutri}/{len(rows)}")

    if rows:
        lines.append("")
        lines.append("前 10 筆：")
        for r in rows[:10]:
            per = r.get("per_100g") or {}
            kcal = f"　{per['kcal']} kcal/100g" if per else "　（無營養值）"
            lines.append(f"  {_as_iso(r.get('bought'))}　{r['name']}"
                         f"　x{r.get('qty') or 1}{kcal}")
        if len(rows) > 10:
            lines.append(f"  …另 {len(rows) - 10} 筆")
    return "\n".join(lines)


def load_rows(path):
    """CSV → 可寫入的列。解析與過濾的細節在那兩個模組裡。"""
    import einvoice_csv
    import einvoice_pantry

    text = io.open(path, encoding="utf-8-sig").read()
    invoices = einvoice_csv.parse(text)
    rows = einvoice_pantry.to_pantry_rows(invoices)
    return invoices, rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="把載具 CSV 匯入 Notion 食材庫存")
    ap.add_argument("csv_path", help="平台匯出或彙整通知的 CSV")
    ap.add_argument("--dry-run", action="store_true",
                    help="只印出會寫什麼，不實際寫入")
    args = ap.parse_args(argv)

    invoices, rows = load_rows(args.csv_path)
    print(f"解析：{len(invoices)} 張發票 → 過濾後 {len(rows)} 筆食材")
    sys.stdout.flush()      # 不 flush 的話下面的 stderr 會插到這行前面

    if not rows:
        print("沒有可匯入的食材。")
        return 0

    import notion_db

    connected = notion_db.is_configured()
    if not connected and not args.dry_run:
        print("\n[錯誤] 連不上 Notion —— NOTION_TOKEN 沒讀到。", file=sys.stderr)
        print("這支腳本要走 infisical：", file=sys.stderr)
        print("  infisical run -- python import_einvoice.py <csv>", file=sys.stderr)
        return 1

    # dry-run 連不上也要能看 —— 想預覽的時候通常就是還沒接好環境。
    # 只是沒有既有資料可比對，去重那欄的數字會失真，講明就好。
    existing = notion_db.pantry_load() if connected else []
    to_add, skipped = plan_import(rows, existing)
    print()
    print(format_summary(to_add, skipped))

    if args.dry_run:
        if not connected:
            print("\n⚠ 沒連上 Notion（NOTION_TOKEN 沒讀到），"
                  "所以「已存在跳過」一定是 0 —— 去重沒有實際比對。")
            print("  要看真正的去重結果：infisical run -- python "
                  "import_einvoice.py <csv> --dry-run")
        print("\n（dry-run，什麼都沒寫入。拿掉 --dry-run 才會真的寫。）")
        return 0

    print(f"\n開始寫入 {len(to_add)} 筆…")
    ok = 0
    for row in to_add:
        # 單筆失敗不該中斷整批 —— pantry_add 自己會印錯誤並回 None
        if notion_db.pantry_add(row):
            ok += 1
    print(f"完成：成功 {ok} 筆，失敗 {len(to_add) - ok} 筆。")
    return 0 if ok == len(to_add) else 1


if __name__ == "__main__":
    sys.exit(main())
