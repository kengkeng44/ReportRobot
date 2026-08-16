"""看今天的「今日一笑」會推出什麼,不寫 Notion、不推 LINE。

用法(需要 ANTHROPIC_API_KEY):
    infisical run --env=prod -- python preview_joke.py

會做三件事:抓 PTT 候選 → 顯示候選池 → 呼叫一次 AI 篩選並印出結果。
"""

import sys

import humor
import joke_sources
from prompts import JOKE_PICK_PROMPT

# Windows 終端機預設 CP950,印不出部分字元
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    print("抓 PTT joke 板熱門文…\n")
    jokes = joke_sources.fetch_ptt_jokes(
        pages=humor.FORUM_PAGES, limit=humor.FORUM_CANDIDATES)
    if not jokes:
        print("撈不到候選,正式跑的時候會退回 AI 生成模式")
        return

    print(f"候選池({len(jokes)} 則):")
    print("=" * 60)
    for i, j in enumerate(jokes, 1):
        preview = j["body"].replace("\n", " / ")[:90]
        print(f"{i:>2}. ({j['heat']:>3} 推) {j['title']}")
        print(f"     {preview}")
    print("=" * 60)

    print("\n送給 AI 篩選中…\n")
    listed = "\n\n".join(
        f"{i + 1}. ({j['heat']} 推) {j['title']}\n{j['body']}"
        for i, j in enumerate(jokes))
    raw = humor._ai(JOKE_PICK_PROMPT.format(candidates=listed), max_tokens=500)

    idx, body = humor._parse_pick(raw)
    if not body or body.upper() == "NONE":
        print("AI 判定全部不合格 → 正式跑的時候會退回 AI 生成模式")
        return

    if idx is not None and 0 <= idx < len(jokes):
        print(f"挑中第 {idx + 1} 則:{jokes[idx]['title']}")
        print(f"來源:{jokes[idx]['link']}\n")

    print("實際會推播的內容")
    print("-" * 60)
    print("😄 今日一笑")
    print(body)
    print("-" * 60)


if __name__ == "__main__":
    main()
