# -*- coding: utf-8 -*-
"""钢琴音频 -> MIDI (ByteDance piano_transcription_inference, CRNN, 钢琴转写 SOTA)。

⚠ 本机踩坑: 该库自带的 load_audio 走 audioread 会 NoBackendError,
   必须用 soundfile + librosa.resample 手动加载 (sr=16000)。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np

LogFn = Callable[[str], None]


class TranscribeError(RuntimeError):
    pass


@dataclass
class TranscribeResult:
    midi_path: Path
    n_notes: int
    duration: float
    pitch_range: tuple
    elapsed: float


def _log(log: Optional[LogFn], msg: str) -> None:
    line = f"[transcribe] {msg}"
    if log:
        log(line)
    else:
        print(line, flush=True)


def load_audio(wav: Path, target_sr: int = 16000) -> np.ndarray:
    """手动加载音频为单声道 float32, 重采样到 target_sr。"""
    import soundfile as sf
    import librosa

    x, sr = sf.read(str(wav), dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    if sr != target_sr:
        x = librosa.resample(x, orig_sr=sr, target_sr=target_sr)
    return np.ascontiguousarray(x, dtype=np.float32)


def transcribe(wav: Path, out_midi: Path, checkpoint: Optional[Path] = None,
               device: str = "cpu", log: Optional[LogFn] = None) -> TranscribeResult:
    """wav -> MIDI。CPU 推理耗时约为音频时长的 0.5~1 倍(实测 126s 音频约 100s)。"""
    from piano_transcription_inference import PianoTranscription, sample_rate

    out_midi.parent.mkdir(parents=True, exist_ok=True)
    audio = load_audio(wav, sample_rate)
    dur = len(audio) / sample_rate
    _log(log, f"音频 {dur:.1f}s @ {sample_rate}Hz, 开始 CRNN 推理 (device={device}) ...")

    t0 = time.time()
    try:
        kwargs = {"device": device, "segment_samples": 160000}
        if checkpoint is not None and Path(checkpoint).exists():
            kwargs["checkpoint_path"] = str(checkpoint)
        tr = PianoTranscription(**kwargs)
        tr.transcribe(audio, str(out_midi))
    except Exception as e:  # noqa: BLE001
        raise TranscribeError(f"钢琴转写失败: {type(e).__name__}: {e}") from e
    elapsed = time.time() - t0

    if not out_midi.exists():
        raise TranscribeError(f"转写未生成 MIDI: {out_midi}")

    import pretty_midi
    pm = pretty_midi.PrettyMIDI(str(out_midi))
    notes = [n for inst in pm.instruments for n in inst.notes]
    if not notes:
        raise TranscribeError("转写结果为空 (未检测到任何钢琴音符)")

    pitches = [n.pitch for n in notes]
    res = TranscribeResult(
        midi_path=out_midi,
        n_notes=len(notes),
        duration=dur,
        pitch_range=(int(min(pitches)), int(max(pitches))),
        elapsed=elapsed,
    )
    _log(log, f"转写完成 ({elapsed:.1f}s): {res.n_notes} 个音符, "
              f"音域 MIDI {res.pitch_range[0]}-{res.pitch_range[1]}, 音频时长 {dur:.1f}s")
    return res
