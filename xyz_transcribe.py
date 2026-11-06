#!/usr/bin/env python3
"""小宇宙逐字稿：貼小宇宙連結 → 下載音檔 → Whisper 轉寫 → OpenCC 轉台灣繁體。

不使用博主提供的文稿，一律由音檔自行轉寫。

用法：
    python xyz_transcribe.py https://www.xiaoyuzhoufm.com/episode/<id>
    python xyz_transcribe.py https://www.xiaoyuzhoufm.com/podcast/<id> --latest 3

輸出（transcripts/）：
    <標題>.m4a        音檔（不進 git）
    <標題>.txt/.srt   casts-down 產出的原始轉寫（多為簡體）
    <標題>_繁.txt/.srt  OpenCC s2twp 轉換後的台灣繁體

需要：pip install casts_down opencc-python-reimplemented
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

MAX_LATEST = 15
DEFAULT_OUT = "transcripts"
DEFAULT_MODEL = "medium"
TRAD_SUFFIX = "_繁"

# casts-down 內部用 aiohttp，預設 trust_env=False 會忽略 HTTPS_PROXY；
# 在有代理的環境（公司網路、雲端沙盒）會直接 DNS 失敗。這裡在子行程裡
# 把 ClientSession 的 trust_env 預設打開再呼叫 casts-down 的 CLI。
_LAUNCHER = (
    "import functools, sys, aiohttp\n"
    "aiohttp.ClientSession.__init__ = functools.partialmethod("
    "aiohttp.ClientSession.__init__, trust_env=True)\n"
    "from casts_down.cli import main\n"
    "sys.argv[0] = 'casts-down'\n"
    "sys.exit(main())\n"
)

_URL_RE = re.compile(r"^https?://(www\.)?xiaoyuzhoufm\.com/(episode|podcast)/[0-9a-zA-Z]+", re.I)


def classify_url(url: str) -> str:
    """回傳 'episode' 或 'podcast'；不是小宇宙連結就丟 ValueError。"""
    m = _URL_RE.match(url.strip())
    if not m:
        raise ValueError(f"不是小宇宙單集/節目連結：{url}")
    return m.group(2).lower()


def build_command(url: str, out_dir: str, model: str, latest: int | None) -> list[str]:
    kind = classify_url(url)
    # casts-down 是 click group：選項必須放在 URL 前面，否則會被當成子指令
    cmd = ["casts-down", "-o", out_dir, "--skip-existing", "-m", model]
    if kind == "podcast":
        n = latest or 1
        if not 1 <= n <= MAX_LATEST:
            raise ValueError(f"--latest 必須介於 1–{MAX_LATEST}，收到 {n}")
        cmd += ["--latest", str(n)]
    elif latest:
        print("[!] 單集連結會忽略 --latest", file=sys.stderr)
    cmd.append(url)
    return cmd


def _converter():
    try:
        import opencc
    except ImportError:
        sys.exit("缺少 OpenCC：pip install opencc-python-reimplemented")
    return opencc.OpenCC("s2twp")


def convert_to_traditional(out_dir: Path, force: bool = False) -> list[Path]:
    """把 out_dir 內原始 .txt/.srt 轉成 *_繁.txt / *_繁.srt。已轉過且較新的會跳過。"""
    cc = _converter()
    written: list[Path] = []
    for src in sorted(out_dir.glob("*")):
        if src.suffix not in (".txt", ".srt") or src.stem.endswith(TRAD_SUFFIX):
            continue
        dst = src.with_name(f"{src.stem}{TRAD_SUFFIX}{src.suffix}")
        if not force and dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
            continue
        dst.write_text(cc.convert(src.read_text(encoding="utf-8")), encoding="utf-8")
        written.append(dst)
    return written


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="小宇宙連結 → 繁體逐字稿")
    p.add_argument("url", help="小宇宙單集（/episode/）或節目（/podcast/）連結")
    p.add_argument("--latest", type=int, default=None,
                   help=f"節目連結時下載最新 N 集（1–{MAX_LATEST}，預設 1）")
    p.add_argument("-o", "--output", default=DEFAULT_OUT, help="輸出資料夾（預設 transcripts）")
    p.add_argument("-m", "--model", default=DEFAULT_MODEL, help="Whisper 模型（預設 medium）")
    p.add_argument("--force-convert", action="store_true", help="重新轉換所有繁體檔")
    args = p.parse_args(argv)

    try:
        cmd = build_command(args.url, args.output, args.model, args.latest)
    except ValueError as e:
        p.error(str(e))

    if not shutil.which("casts-down"):
        sys.exit("找不到 casts-down：pip install casts_down")

    print("[*] 執行：" + " ".join(cmd))
    rc = subprocess.call([sys.executable, "-c", _LAUNCHER, *cmd[1:]])
    if rc != 0:
        print(f"[!] casts-down 結束碼 {rc}（仍會嘗試轉換已存在的逐字稿）", file=sys.stderr)

    out_dir = Path(args.output)
    if not out_dir.is_dir():
        return rc or 1
    written = convert_to_traditional(out_dir, force=args.force_convert)
    for f in written:
        print(f"[✓] 繁體：{f}")
    if not written:
        print("[*] 沒有新的逐字稿需要轉換")
    return rc


if __name__ == "__main__":
    sys.exit(main())
