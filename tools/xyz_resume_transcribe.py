"""可續跑的 faster-whisper 轉寫：每個 segment 寫進 JSONL，重跑時從最後時間點接著跑。
跑完後用 casts_down 的 formatter 輸出 .txt/.srt（與 casts-down 相同格式）。"""
import json, sys, time
from pathlib import Path
from faster_whisper import WhisperModel
from casts_down.transcribe.engine import Segment
from casts_down.transcribe.formatter import write_outputs

audio = Path(sys.argv[1])
ckpt = audio.with_suffix(".segments.jsonl")
done = []
if ckpt.exists():
    done = [json.loads(l) for l in ckpt.read_text(encoding="utf-8").splitlines() if l.strip()]
start = done[-1]["end"] if done else 0.0
print(f"[*] resume from {start:.1f}s ({len(done)} segments)", flush=True)

model = WhisperModel("medium", device="cpu")
it, info = model.transcribe(str(audio), language="zh", word_timestamps=False,
                            clip_timestamps=[start] if start else "0")
t0 = time.monotonic()
with ckpt.open("a", encoding="utf-8") as f:
    for i, s in enumerate(it):
        f.write(json.dumps({"start": s.start, "end": s.end, "text": s.text.strip()}, ensure_ascii=False) + "\n")
        f.flush()
        if i % 100 == 0:
            print(f"[*] {s.end/60:.1f}m / {info.duration/60:.1f}m  {time.monotonic()-t0:.0f}s", flush=True)

segs = [Segment(**json.loads(l)) for l in ckpt.read_text(encoding="utf-8").splitlines() if l.strip()]
print(write_outputs(audio, segs), flush=True)
print(f"[+] done: {len(segs)} segments", flush=True)
