# -*- coding: utf-8 -*-
"""音频预处理与钢琴轨分离。

流程:
    1. transcode()   任意输入(mp3/mp4/m4a/中文路径) -> ASCII 路径 44.1kHz WAV
       (本机实测: 部分 mp3 是 AAC 假封装, demucs/分离模型直读会炸, 必须先 ffmpeg 转码)
    2. separate_piano()  BS-RoFormer 多轨模型分离, 取 *_piano.wav
       (模型注册名见 config.MULTI_STEM_MODEL, 输出 bass/drums/other/vocals/guitar/piano)
    3. 无 GPU/无模型时的兜底: 直接对整曲(或简易谐波增强后)转写
"""
from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

LogFn = Callable[[str], None]


class AudioError(RuntimeError):
    pass


def _log(log: Optional[LogFn], msg: str) -> None:
    line = f"[audio] {msg}"
    if log:
        log(line)
    else:
        print(line, flush=True)


def transcode(src: Path, dst_wav: Path, ffmpeg: Path, log: Optional[LogFn] = None,
              sr: int = 44100, mono: bool = False) -> Path:
    """ffmpeg 转码为 WAV。mono=True 输出单声道(转写用), 否则立体声(分离用)。"""
    dst_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(ffmpeg), "-y", "-i", str(src), "-ar", str(sr)]
    cmd += ["-ac", "1"] if mono else ["-ac", "2"]
    cmd += ["-c:a", "pcm_s16le", str(dst_wav)]
    _log(log, f"转码: {src.name} -> {dst_wav.name}")
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if r.returncode != 0 or not dst_wav.exists():
        tail = (r.stderr or "")[-600:]
        raise AudioError(f"ffmpeg 转码失败: {tail}")
    _log(log, f"转码完成 ({time.time()-t0:.1f}s), {dst_wav.stat().st_size/1e6:.1f} MB")
    return dst_wav


@dataclass
class SeparationResult:
    piano_wav: Path
    used_model: bool          # True=BS-RoFormer 分离, False=兜底(直接用整曲)
    all_stems: list           # 全部分离出的音轨文件
    elapsed: float = 0.0


def separate_piano(src_wav: Path, workdir: Path, roformer_cli: Optional[Path],
                   device: str = "auto", log: Optional[LogFn] = None,
                   model: Optional[str] = None) -> SeparationResult:
    """用 BS-RoFormer 多轨模型分离钢琴轨。

    src_wav 必须是 ASCII 路径的 44.1kHz 立体声 WAV(先 transcode)。
    返回钢琴轨 WAV 路径。失败时抛出 AudioError, 由调用方决定兜底策略。
    """
    from .config import MULTI_STEM_MODEL
    model = model or MULTI_STEM_MODEL

    if roformer_cli is None:
        raise AudioError("未找到 bs-roformer-infer, 无法分离")

    in_dir = workdir / "sep_in"
    out_dir = workdir / "sep_out"
    for d in (in_dir, out_dir):
        d.mkdir(parents=True, exist_ok=True)

    stem_src = in_dir / src_wav.name
    if stem_src.resolve() != src_wav.resolve():
        shutil.copy2(src_wav, stem_src)

    cmd = [str(roformer_cli), "--model", model,
           "--input_folder", str(in_dir), "--store_dir", str(out_dir)]
    cmd += ["--device", device] if device != "auto" else []
    _log(log, f"开始音轨分离 (模型: {model}, device={device})")
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    elapsed = time.time() - t0
    if r.returncode != 0:
        tail = ((r.stderr or "") + (r.stdout or ""))[-800:]
        raise AudioError(f"音轨分离失败 (exit={r.returncode}): {tail}")

    stems = sorted(out_dir.glob("*.wav"))
    _log(log, f"分离完成 ({elapsed:.1f}s), 共 {len(stems)} 轨: "
              + ", ".join(s.name for s in stems))

    piano = next((s for s in stems if s.stem.endswith("_piano")), None)
    if piano is None:
        raise AudioError(f"分离结果中未找到钢琴轨, 实际输出: {[s.name for s in stems]}")

    # 快速质检: 钢琴轨不能是静音
    rms = _wav_rms(piano)
    _log(log, f"钢琴轨 RMS={rms:.4f}" + ("  (⚠ 接近静音, 该曲可能没有明显钢琴声部)" if rms < 0.002 else ""))

    return SeparationResult(piano_wav=piano, used_model=True, all_stems=stems, elapsed=elapsed)


def _wav_rms(wav: Path) -> float:
    import numpy as np
    import soundfile as sf
    x, _ = sf.read(str(wav), dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    return float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0


def fallback_mono(src: Path, workdir: Path, ffmpeg: Path,
                  log: Optional[LogFn] = None) -> Path:
    """兜底: 不分离, 直接输出单声道 WAV 供转写。"""
    _log(log, "未使用分离模型, 兜底: 直接对完整音频转写(含钢琴的独奏曲效果尚可)")
    dst = workdir / "direct_mono.wav"
    return transcode(src, dst, ffmpeg, log=log, mono=True)
