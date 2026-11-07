#!/usr/bin/env python3
"""小宇宙逐字稿：貼小宇宙連結 → 下載音檔 → Whisper 轉寫 → OpenCC 轉台灣繁體。

不使用博主提供的文稿，一律由音檔自行轉寫。

用法：
    python xyz_transcribe.py https://www.xiaoyuzhoufm.com/episode/<id>
    python xyz_transcribe.py https://www.xiaoyuzhoufm.com/podcast/<id> --latest 3
    python xyz_transcribe.py <連結> --resume   # 長集數：可中斷、重跑會接著轉

--resume 時 casts-down 只負責下載，轉寫改在本程式裡做：每個 segment 即時寫進
<標題>.segments.jsonl，被停掉後重跑同一行，會從最後一個時間點接著轉。

輸出（transcripts/）：
    <標題>.m4a        音檔（不進 git）
    <標題>.txt/.srt   casts-down 產出的原始轉寫（多為簡體）
    <標題>_繁.txt/.srt  OpenCC s2twp 轉換後的台灣繁體

需要：pip install casts_down opencc-python-reimplemented "av<16"
（PyAV 16 以後拿掉 metadata_errors 參數，faster-whisper 1.2.x 解碼會 TypeError）
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

MAX_LATEST = 15
DEFAULT_OUT = "transcripts"
DEFAULT_MODEL = "medium"
TRAD_SUFFIX = "_繁"
AUDIO_SUFFIXES = (".m4a", ".mp3")
CKPT_SUFFIX = ".segments.jsonl"

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


def build_command(url: str, out_dir: str, model: str, latest: int | None,
                  transcribe: bool = True) -> list[str]:
    kind = classify_url(url)
    # casts-down 是 click group：選項必須放在 URL 前面，否則會被當成子指令
    cmd = ["casts-down", "-o", out_dir, "--skip-existing"]
    cmd += ["-m", model] if transcribe else ["--no-transcribe"]
    if kind == "podcast":
        n = latest or 1
        if not 1 <= n <= MAX_LATEST:
            raise ValueError(f"--latest 必須介於 1–{MAX_LATEST}，收到 {n}")
        cmd += ["--latest", str(n)]
    elif latest:
        print("[!] 單集連結會忽略 --latest", file=sys.stderr)
    cmd.append(url)
    return cmd


def load_checkpoint(ckpt: Path) -> list[dict]:
    """讀 .segments.jsonl。最後一行可能在寫到一半時被中斷，壞掉的行直接略過。"""
    if not ckpt.exists():
        return []
    segs = []
    for line in ckpt.read_text(encoding="utf-8").splitlines():
        try:
            segs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return segs


def pending_audio(out_dir: Path) -> list[Path]:
    """還沒有 .txt 逐字稿的音檔。"""
    return [a for a in sorted(out_dir.glob("*"))
            if a.suffix in AUDIO_SUFFIXES and not a.with_suffix(".txt").exists()]


def transcribe_resumable(audio: Path, model_name: str) -> Path:
    """faster-whisper 轉寫，每個 segment 即時寫進 checkpoint；重跑時從最後時間點接著轉。
    跑完用 casts-down 的 formatter 輸出 .txt/.srt（格式與 casts-down 相同）。"""
    from casts_down.transcribe.engine import Segment
    from casts_down.transcribe.formatter import write_outputs
    from faster_whisper import WhisperModel

    ckpt = audio.with_name(audio.stem + CKPT_SUFFIX)
    done = load_checkpoint(ckpt)
    if ckpt.exists() and not ckpt.read_bytes().endswith(b"\n"):
        # 上次停在寫到一半的那行：重寫成乾淨的檔案，免得新資料接在殘行後面
        ckpt.write_text("".join(json.dumps(d, ensure_ascii=False) + "\n" for d in done),
                        encoding="utf-8")
    start = done[-1]["end"] if done else 0.0
    print(f"[*] {audio.name}：從 {start / 60:.1f} 分接著轉（已有 {len(done)} 段）", flush=True)

    # int8：記憶體約 float32 的 1/4（8 GB 筆電載 medium 會 mkl_malloc 失敗），CPU 上也較快
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    it, info = model.transcribe(str(audio), language="zh",
                                clip_timestamps=[start] if start else "0")
    with ckpt.open("a", encoding="utf-8") as f:
        for i, s in enumerate(it):
            f.write(json.dumps({"start": s.start, "end": s.end, "text": s.text.strip()},
                               ensure_ascii=False) + "\n")
            f.flush()
            if i % 100 == 0:
                print(f"[*] {s.end / 60:.1f} / {info.duration / 60:.1f} 分", flush=True)

    txt, _srt = write_outputs(audio, [Segment(**d) for d in load_checkpoint(ckpt)])
    return txt


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
    p.add_argument("--resume", action="store_true",
                   help="可續跑轉寫（長集數用）：被停掉後重跑同一行會接著轉")
    args = p.parse_args(argv)

    try:
        cmd = build_command(args.url, args.output, args.model, args.latest,
                            transcribe=not args.resume)
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
    if args.resume:
        for audio in pending_audio(out_dir):
            print(f"[✓] 原稿：{transcribe_resumable(audio, args.model)}")
    written = convert_to_traditional(out_dir, force=args.force_convert)
    for f in written:
        print(f"[✓] 繁體：{f}")
    if not written:
        print("[*] 沒有新的逐字稿需要轉換")
    return rc


if __name__ == "__main__":
    sys.exit(main())
