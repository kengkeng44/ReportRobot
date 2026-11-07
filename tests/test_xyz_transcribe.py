"""小宇宙逐字稿 xyz_transcribe：網址判斷、casts-down 指令、繁體轉換、可續跑轉寫。"""

import json
import sys
import types

import pytest

import xyz_transcribe as x

EP = "https://www.xiaoyuzhoufm.com/episode/6ae9d95bc96c8942ec16c810"
POD = "https://www.xiaoyuzhoufm.com/podcast/65a5fb7540d4ef949c0140ac"


def test_classify_url():
    assert x.classify_url(EP) == "episode"
    assert x.classify_url(" " + POD + "?s=abc ") == "podcast"
    assert x.classify_url("http://xiaoyuzhoufm.com/episode/abc") == "episode"


@pytest.mark.parametrize("bad", ["https://podcasts.apple.com/podcast/id1", "xiaoyuzhoufm.com/episode/abc", ""])
def test_classify_url_rejects_other_links(bad):
    with pytest.raises(ValueError):
        x.classify_url(bad)


def test_build_command_puts_options_before_url():
    # casts-down 是 click group，URL 後面的選項會被當成子指令
    cmd = x.build_command(EP, "out", "medium", None)
    assert cmd == ["casts-down", "-o", "out", "--skip-existing", "-m", "medium", EP]


def test_build_command_podcast_latest():
    assert x.build_command(POD, "out", "small", None)[-3:] == ["--latest", "1", POD]
    assert x.build_command(POD, "out", "small", 3)[-3:] == ["--latest", "3", POD]
    with pytest.raises(ValueError):
        x.build_command(POD, "out", "small", x.MAX_LATEST + 1)


def test_build_command_episode_ignores_latest():
    assert "--latest" not in x.build_command(EP, "out", "small", 3)


def test_build_command_resume_only_downloads():
    cmd = x.build_command(EP, "out", "medium", None, transcribe=False)
    assert "--no-transcribe" in cmd and "-m" not in cmd
    assert cmd[-1] == EP


def test_load_checkpoint_skips_broken_last_line(tmp_path):
    ckpt = tmp_path / "a.segments.jsonl"
    assert x.load_checkpoint(ckpt) == []
    ckpt.write_text('{"start": 0, "end": 1.5, "text": "一"}\n{"start": 1.5, "en', encoding="utf-8")
    assert x.load_checkpoint(ckpt) == [{"start": 0, "end": 1.5, "text": "一"}]


def test_pending_audio_skips_transcribed(tmp_path):
    for name in ["a.m4a", "a.txt", "b.mp3", "c.m4a", "c_繁.txt", "notes.md"]:
        (tmp_path / name).write_text("", encoding="utf-8")
    assert [p.name for p in x.pending_audio(tmp_path)] == ["b.mp3", "c.m4a"]


def test_convert_to_traditional(tmp_path):
    pytest.importorskip("opencc")
    (tmp_path / "ep.txt").write_text("软件里的信息", encoding="utf-8")
    (tmp_path / "ep.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n网络\n", encoding="utf-8")
    written = x.convert_to_traditional(tmp_path)
    assert sorted(p.name for p in written) == ["ep_繁.srt", "ep_繁.txt"]
    assert (tmp_path / "ep_繁.txt").read_text(encoding="utf-8") == "軟體裡的資訊"
    assert "網路" in (tmp_path / "ep_繁.srt").read_text(encoding="utf-8")
    # 已轉過且較新的不再轉；轉出來的 _繁 檔也不會被當成原稿
    assert x.convert_to_traditional(tmp_path) == []


def _fake_whisper(monkeypatch, segments, calls):
    seg = types.SimpleNamespace

    class FakeModel:
        def __init__(self, name, device, compute_type):
            calls.append(("model", name, device, compute_type))

        def transcribe(self, path, language, clip_timestamps):
            calls.append(("clip", clip_timestamps))
            return iter(seg(**s) for s in segments), seg(duration=100.0)

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))


def test_transcribe_resumable_continues_from_checkpoint(tmp_path, monkeypatch):
    pytest.importorskip("casts_down")
    audio = tmp_path / "ep.m4a"
    audio.write_bytes(b"")
    ckpt = tmp_path / "ep.segments.jsonl"
    # 上次停在 2.0 秒，而且最後一行只寫了一半
    ckpt.write_text('{"start": 0.0, "end": 2.0, "text": "第一句"}\n{"start": 2.0, "e', encoding="utf-8")
    calls = []
    _fake_whisper(monkeypatch, [{"start": 2.0, "end": 4.0, "text": " 第二句 "}], calls)

    txt = x.transcribe_resumable(audio, "medium")

    assert ("clip", [2.0]) in calls
    assert [d["text"] for d in x.load_checkpoint(ckpt)] == ["第一句", "第二句"]
    assert all(json.loads(line) for line in ckpt.read_text(encoding="utf-8").splitlines())
    body = txt.read_text(encoding="utf-8")
    assert "第一句" in body and "第二句" in body


def test_transcribe_resumable_fresh_start(tmp_path, monkeypatch):
    pytest.importorskip("casts_down")
    audio = tmp_path / "ep.m4a"
    audio.write_bytes(b"")
    calls = []
    _fake_whisper(monkeypatch, [{"start": 0.0, "end": 1.0, "text": "你好"}], calls)

    x.transcribe_resumable(audio, "small")

    assert ("model", "small", "cpu", "int8") in calls and ("clip", "0") in calls
    assert (tmp_path / "ep.srt").exists()
