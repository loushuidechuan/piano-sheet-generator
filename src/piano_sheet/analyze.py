# -*- coding: utf-8 -*-
"""调性 / 拍号 / 速度 自动分析。

方法(均为本机实测有效处方):
    调性: chroma_cqt 平均向量 与 Krumhansl-Schmuckler 大小调轮廓相关, 取最大
    拍位: librosa.beat.beat_track, 多候选 start_bpm 分别跑并打分择优(防锁错脉冲层级),
          检验 IBI CV
    拍号: 小节内拍点强弱轮廓与理论模板做循环互相关 + 拍号先验,
          候选 2/4、3/4、4/4 (拍脉冲已统一锁为四分音符, 故 6/8 类曲子归入 3/4 记谱)
    速度: 60 / median(IBI) 折算到四分音符
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np

LogFn = Callable[[str], None]

# Krumhansl-Schmuckler 调性轮廓
_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


@dataclass
class MusicAnalysis:
    key_name: str            # 如 "A minor"
    key_sharps: int          # music21 KeySignature 参数, 正=升号 负=降号
    time_sig: str            # 如 "4/4"
    beats_per_bar: int
    bpm: float               # 四分音符速度
    beat_times: np.ndarray   # 拍位(秒)
    downbeats: np.ndarray    # 强拍位(秒)
    ibi_cv: float
    confidence: float        # 调性相关度


def _log(log: Optional[LogFn], msg: str) -> None:
    line = f"[analyze] {msg}"
    if log:
        log(line)
    else:
        print(line, flush=True)


def estimate_key(chroma: np.ndarray, log: Optional[LogFn] = None) -> tuple:
    """chroma: (12, T) -> (key_name, sharps, corr)"""
    prof = chroma.mean(axis=1)
    prof = prof / (np.linalg.norm(prof) + 1e-9)
    best = (-2.0, None, None)
    for shift in range(12):
        for mode, prof_t in (("major", _MAJOR), ("minor", _MINOR)):
            tmpl = np.roll(prof_t, shift)
            tmpl = tmpl / (np.linalg.norm(tmpl) + 1e-9)
            c = float(np.dot(prof, tmpl))
            if c > best[0]:
                best = (c, _NOTE_NAMES[shift], mode)
    corr, root, mode = best
    name = f"{root} {mode}"
    sharps = _key_sharps(root, mode)
    _log(log, f"估调: {name} (相关度 {corr:.3f}, 调号 {sharps:+d})")
    return name, sharps, corr


_SHARP_MAJOR = {"C": 0, "G": 1, "D": 2, "A": 3, "E": 4, "B": 5, "F#": 6, "C#": 7}
_FLAT_MAJOR = {"F": -1, "Bb": -2, "Eb": -3, "Ab": -4, "Db": -5, "Gb": -6, "Cb": -7}
_SHARP_MINOR = {"A": 0, "E": 1, "B": 2, "F#": 3, "C#": 4, "G#": 5, "D#": 6, "A#": 7}
_FLAT_MINOR = {"D": -1, "G": -2, "C": -3, "F": -4, "Bb": -5, "Eb": -6, "Ab": -7}


def _key_sharps(root: str, mode: str) -> int:
    table = _SHARP_MINOR if mode == "minor" else _SHARP_MAJOR
    if root in table:
        return table[root]
    table = _FLAT_MINOR if mode == "minor" else _FLAT_MAJOR
    return table.get(root, 0)


def detect_beats(y: np.ndarray, sr: int, hop: int = 512,
                 start_bpm: Optional[float] = None, log: Optional[LogFn] = None):
    """多候选 start_bpm 择优, 返回 (beat_times, onset_env, fps, ibi_cv, bpm)

    为什么要多候选: beat_track 的 start_bpm 决定它锁定哪个**脉冲层级**。
    给错层级会锁到附点四分/二分音符, 粒度过粗 -> 每小节拍数被算成 6 而非 4,
    进而拍号误判(实测 River Flows in You: 单一 start_bpm=120 锁到 143.6bpm
    附点四分, 判成 6/8; 多候选后锁到 ~96bpm 四分脉冲, 正确判为 4/4)。
    """
    import librosa

    oenv = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    fps = sr / hop

    if start_bpm:
        candidates = [float(start_bpm)]
    else:
        # 覆盖常见四分音符速度域, 让算法自己挑锁得最稳的层级
        candidates = [60.0, 76.0, 92.0, 108.0, 126.0, 148.0, 176.0]

    best = None
    for sb in candidates:
        _tempo, beats = librosa.beat.beat_track(
            onset_envelope=oenv, sr=sr, hop_length=hop,
            start_bpm=sb, tightness=100, trim=False,
        )
        bt = librosa.frames_to_time(beats, sr=sr, hop_length=hop)
        if len(bt) < 8:
            continue
        ibi = np.diff(bt)
        med = float(np.median(ibi))
        if med <= 1e-6:
            continue
        bpm = 60.0 / med
        if not (55.0 <= bpm <= 200.0):     # 只接受合理的四分脉冲速度域
            continue
        cv = float(ibi.std() / med)
        # 打分: 拍位越稳越好; 锁定结果应接近所给先验(说明层级选对了)
        score = -cv - 0.004 * abs(bpm - sb)
        if best is None or score > best[0]:
            best = (score, bt, cv, bpm, sb)

    if best is None:                        # 全部候选越界, 退回默认
        _tempo, beats = librosa.beat.beat_track(
            onset_envelope=oenv, sr=sr, hop_length=hop,
            start_bpm=120.0, tightness=100, trim=False,
        )
        bt = librosa.frames_to_time(beats, sr=sr, hop_length=hop)
        if len(bt) < 4:
            raise RuntimeError(f"节拍检测失败, 只找到 {len(bt)} 拍")
        ibi = np.diff(bt)
        best = (0.0, bt, float(ibi.std() / ibi.mean()), float(60.0 / np.median(ibi)), 120.0)

    _score, bt, cv, bpm, sb = best
    _log(log, f"节拍: {len(bt)} 拍, 四分脉冲 ≈ {bpm:.1f} BPM "
              f"(候选 start_bpm={sb:.0f} 胜出), IBI CV={cv:.3f}"
              + ("  ⚠ 节拍不稳" if cv > 0.05 else ""))
    return bt, oenv, fps, cv, bpm


# 各拍号的小节内强弱轮廓(音乐理论标准: 强-弱-次强-弱)
# 注意: 拍脉冲已统一锁定为四分音符(detect_beats), 故每小节拍数 = 四分音符数
#   2 -> 2/4, 3 -> 3/4(或 6/8 记谱), 4 -> 4/4
_BAR_PROFILE = {
    2: np.array([1.00, 0.35]),
    3: np.array([1.00, 0.35, 0.35]),
    4: np.array([1.00, 0.35, 0.60, 0.35]),
}
_BAR_SIG = {2: "2/4", 3: "3/4", 4: "4/4"}
# 先验: 4/4 在流行与钢琴曲中占绝对多数, 其它拍号需明显更强证据才胜出
_BAR_PRIOR = {2: 0.94, 3: 0.97, 4: 1.06}


def _phase_salience(bt: np.ndarray, oenv: np.ndarray, fps: float, k: int) -> Optional[np.ndarray]:
    """按 k 分组, 取每相位的 onset 强度均值。"""
    if len(bt) < k * 2:
        return None
    sal = []
    for p in range(k):
        grp = bt[p::k]
        if len(grp) == 0:
            return None
        fr = np.clip((grp * fps).astype(int), 0, len(oenv) - 1)
        sal.append(float(oenv[fr].mean()))
    return np.asarray(sal)


def _profile_corr(sal: np.ndarray, profile: np.ndarray) -> float:
    """归一化互相关, 对所有循环移位取最大值(相位未知)。"""
    if len(sal) != len(profile):
        return -2.0
    a = sal - sal.mean()
    b = profile - profile.mean()
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na < 1e-9 or nb < 1e-9:
        return -2.0
    return max(float(np.dot(a, np.roll(b, s)) / (na * nb)) for s in range(len(b)))


def detect_time_signature(bt: np.ndarray, oenv: np.ndarray, fps: float,
                          log: Optional[LogFn] = None) -> tuple:
    """小节内强弱轮廓模板匹配 -> (拍号, beats_per_bar)

    旧版用 (max-mean)/std 打分, 分组数越多该值天然越大, 导致 6/8 被系统性偏爱。
    新版改为与理论强弱轮廓做归一化互相关, 再乘拍号先验。
    """
    scores = {}
    for k, profile in _BAR_PROFILE.items():
        sal = _phase_salience(bt, oenv, fps, k)
        if sal is None:
            continue
        c = _profile_corr(sal, profile)
        if c <= -1.5:
            continue
        scores[k] = c * _BAR_PRIOR[k]

    if not scores:
        _log(log, "拍号判定: 证据不足, 回退 4/4")
        return "4/4", 4

    best_k = max(scores, key=scores.get)
    sig = _BAR_SIG[best_k]
    _log(log, f"拍号判定: {sig} (轮廓匹配分 {{"
              + ", ".join(f"{k}:{v:.2f}" for k, v in sorted(scores.items())) + "})")
    return sig, best_k


def find_downbeat_phase(bt: np.ndarray, oenv: np.ndarray, fps: float,
                        beats_per_bar: int) -> tuple:
    """返回 (phase_index, downbeat_times)。优先用强弱轮廓模板对齐相位。"""
    sal = _phase_salience(bt, oenv, fps, beats_per_bar)
    if sal is None:
        return 0, bt[::beats_per_bar]

    profile = _BAR_PROFILE.get(beats_per_bar, np.ones(beats_per_bar))
    pb = profile - profile.mean()
    norm_b = np.linalg.norm(pb)
    # 模板移位 s 表示"真实强拍落在分组相位 s 上", 取相关最大的 s
    best_s, best_c = 0, -2.0
    for s in range(beats_per_bar):
        a = sal - sal.mean()
        na = np.linalg.norm(a)
        if na < 1e-9 or norm_b < 1e-9:
            continue
        c = float(np.dot(a, np.roll(pb, s)) / (na * norm_b))
        if c > best_c:
            best_c, best_s = c, s

    phase = best_s if best_c > -1.5 else int(np.argmax(sal))
    down = bt[phase::beats_per_bar]
    return phase, down


def analyze(wav: Path, log: Optional[LogFn] = None,
            hint_bpm: Optional[float] = None) -> MusicAnalysis:
    """完整分析入口。wav 建议是钢琴轨(节奏规整), 也可用整曲。"""
    import librosa

    y, sr = librosa.load(str(wav), sr=22050, mono=True)
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    key_name, sharps, corr = estimate_key(chroma, log)

    bt, oenv, fps, cv, bpm_quarter = detect_beats(y, sr, start_bpm=hint_bpm, log=log)
    sig, bpb = detect_time_signature(bt, oenv, fps, log)
    phase, down = find_downbeat_phase(bt, oenv, fps, bpb)
    _log(log, f"强拍相位: {phase} (每 {bpb} 拍取一次 -> {len(down)} 个强拍)")
    _log(log, f"速度: 四分音符 ≈ {bpm_quarter:.1f} BPM")

    return MusicAnalysis(
        key_name=key_name, key_sharps=sharps, time_sig=sig,
        beats_per_bar=bpb, bpm=float(bpm_quarter),
        beat_times=bt, downbeats=down, ibi_cv=cv, confidence=corr,
    )
