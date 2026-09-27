# -*- coding: utf-8 -*-
"""MIDI -> MusicXML 建谱 (music21)。

采用 v3 标准流程(本机实测最有效):
    1. t_to_bar 分段线性映射(真实拍位对齐, 不用固定网格 — 固定网格会随 rubato 累积漂移)
    2. C4 分大小谱表, RH 1/8 网格 / LH 1/4 网格(6/8 拍为附点四分), 不同精度分别量化
    3. 同音高近距合并 -> 同格和弦合并 -> no_overlap 截断 -> 相邻同和弦合并
    4. 钢琴声部零连线(踏板延音按记谱惯例), 跨小节音符拆分到小节内
    5. 扁平 Part 列表 + layout.StaffGroup 合并大谱表(Part 嵌套 PartStaff 会 "no measures found")
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .analyze import MusicAnalysis

LogFn = Callable[[str], None]

# 每小节八分音符格数
_EPM8 = {"2/4": 4, "3/4": 6, "4/4": 8, "6/8": 6, "5/4": 10, "6/4": 12}

SPLIT_MIDI = 60          # C4 分手
MIN_DUR = 0.06           # 秒, 短于此的音符丢弃
MIN_VELOCITY = 25


class ScoreBuildError(RuntimeError):
    pass


@dataclass
class BuildResult:
    musicxml_path: Path
    n_measures: int
    n_notes: int
    title: str


def _log(log: Optional[LogFn], msg: str) -> None:
    line = f"[score] {msg}"
    if log:
        log(line)
    else:
        print(line, flush=True)


# ------------------------------------------------------------------ 拍位映射
class BarMapper:
    """时间(秒) -> 小节位置(浮点, 1 小节 = 1.0), 基于 downbeat 的分段线性映射。"""

    def __init__(self, downbeats: np.ndarray, med_bar: float, t_first: float):
        self.down = np.asarray(downbeats, dtype=float).copy()
        self.med_bar = float(med_bar)
        # 首拍前推: 第 1 小节从 ~0s 开始, 且不晚于首个音符
        while self.down[0] - self.med_bar > -0.2:
            self.down = np.concatenate([[self.down[0] - self.med_bar], self.down])
        while self.down[0] > t_first + 0.1:
            self.down = np.concatenate([[self.down[0] - self.med_bar], self.down])

    def t_to_bar(self, t: float) -> float:
        d = self.down
        if t <= d[0]:
            return (t - d[0]) / self.med_bar
        if t >= d[-1]:
            return (len(d) - 1) + (t - d[-1]) / self.med_bar
        i = int(np.searchsorted(d, t) - 1)
        i = max(0, min(i, len(d) - 2))
        return i + (t - d[i]) / max(1e-6, d[i + 1] - d[i])


# ------------------------------------------------------------------ 清理/量化
def _merge_same_pitch(ns, gap: float = 0.10):
    """同音高、间隙小的合并(断奏/重复击键/踏板残响合一)。"""
    import pretty_midi
    ns = sorted(ns, key=lambda n: (n.start, n.pitch))
    out = []
    for n in ns:
        hit = None
        for m in reversed(out[-8:]):
            if m.pitch == n.pitch and -0.02 <= n.start - m.end < gap:
                hit = m
                break
        if hit:
            hit.end = max(hit.end, n.end)
            hit.velocity = max(hit.velocity, n.velocity)
        else:
            out.append(pretty_midi.Note(n.velocity, n.pitch, n.start, n.end))
    return out


def _quantize(ns, mapper: BarMapper, slots_per_measure: int, per_measure: int):
    """同格同音合并成事件 (slot, dur_slots, vel, [pitches])。"""
    buckets = defaultdict(list)
    for n in ns:
        s = int(round(mapper.t_to_bar(n.start) * slots_per_measure))
        if s < 0:
            continue
        e = max(s + 1, int(round(mapper.t_to_bar(n.end) * slots_per_measure)))
        e = min(e, (s // per_measure + 1) * per_measure)   # 不跨小节(钢琴不写连线)
        buckets[s].append((n.pitch, e - s, n.velocity))
    ev = []
    for s in sorted(buckets):
        items = buckets[s]
        pitches = sorted({p for p, _, _ in items})
        d = max(x[1] for x in items)
        v = max(x[2] for x in items)
        ev.append((s, d, v, pitches))
    return ev


def _no_overlap(events):
    """长音时值截断到下一音符起始, 否则小节时值溢出。"""
    events = sorted(events, key=lambda x: x[0])
    out = []
    for i, (s, d, v, ps) in enumerate(events):
        if i + 1 < len(events):
            d = min(d, events[i + 1][0] - s)
        out.append((s, max(1, d), v, ps))
    return out


def _merge_adjacent_same(events, per_measure):
    out = []
    for ev in events:
        if out:
            ps, pd, pv, pp = out[-1]
            cs, cd, cv, cp = ev
            if sorted(pp) == sorted(cp) and (ps + pd) == cs and (ps // per_measure) == (cs // per_measure):
                out[-1] = (ps, pd + cd, max(pv, cv), pp)
                continue
        out.append(ev)
    return out


def _split_measures(events, per_measure):
    """跨小节音符拆分为小节内片段: (measure_idx, pos_in_measure, dur, pitches, vel)"""
    out = []
    for (s, d, vel, pitches) in events:
        cur_s, cur_d = s, d
        while cur_d > 0:
            m = cur_s // per_measure
            pos = cur_s % per_measure
            take = min(cur_d, per_measure - pos)
            out.append((m, pos, take, pitches, vel))
            cur_s += take
            cur_d -= take
    return out


# ------------------------------------------------------------------ 建谱
def build_musicxml(midi_path: Path, analysis: MusicAnalysis, out_xml: Path,
                   title: str = "", composer: str = "",
                   log: Optional[LogFn] = None) -> BuildResult:
    import pretty_midi
    from music21 import stream, note, chord, meter, tempo, clef, metadata, key, duration, bar, layout
    from music21.stream import Part

    sig = analysis.time_sig
    epm8 = _EPM8.get(sig, 8)
    compound = sig.endswith("/8")

    # RH: 1/8 网格; LH: 简单拍 1/4 网格, 复合拍(6/8) 附点四分网格
    rh_slots, rh_pm, rh_ql = epm8, epm8, 0.5
    if compound:
        lh_slots, lh_pm, lh_ql = epm8 // 3, epm8 // 3, 1.5
    else:
        lh_slots, lh_pm, lh_ql = epm8 // 2, epm8 // 2, 1.0
    bar_ql = epm8 * 0.5   # 一小节四分长度

    # ---------- 读 MIDI
    pm = pretty_midi.PrettyMIDI(str(midi_path))
    all_notes = sorted([n for i in pm.instruments for n in i.notes], key=lambda n: n.start)
    if not all_notes:
        raise ScoreBuildError("MIDI 中没有音符, 无法建谱")
    t_first = all_notes[0].start
    t_last = max(n.end for n in all_notes)

    down = analysis.downbeats
    if len(down) < 2:
        raise ScoreBuildError("强拍数量不足, 无法对齐拍位")
    med_bar = float(np.median(np.diff(down)))
    mapper = BarMapper(down, med_bar, t_first)
    n_bars = int(np.ceil(mapper.t_to_bar(t_last))) + 1
    _log(log, f"拍位映射: downbeats={len(down)}, 中位小节长={med_bar:.3f}s, 共 ~{n_bars} 小节")

    # ---------- 清理
    notes = [n for n in all_notes if n.end - n.start >= MIN_DUR and n.velocity >= MIN_VELOCITY]
    rh_raw = [n for n in notes if n.pitch >= SPLIT_MIDI]
    lh_raw = [n for n in notes if n.pitch < SPLIT_MIDI]
    rh = _merge_same_pitch(rh_raw)
    lh = _merge_same_pitch(lh_raw)
    _log(log, f"音符清理: raw={len(all_notes)} -> filtered={len(notes)} "
              f"-> merged RH={len(rh)} LH={len(lh)} (C4 分手)")

    # ---------- 量化
    rh_e = _merge_adjacent_same(_no_overlap(_quantize(rh, mapper, rh_slots, rh_pm)), rh_pm)
    lh_e = _merge_adjacent_same(_no_overlap(_quantize(lh, mapper, lh_slots, lh_pm)), lh_pm)
    _log(log, f"量化: RH(1/8 网格)={len(rh_e)} 事件, LH(四分长度 {lh_ql})={len(lh_e)} 事件")

    # ---------- 分部
    bpm = max(20, min(300, int(round(analysis.bpm))))

    def build_part(events, per_measure, unit_ql, cl, part_id):
        p = Part()
        p.id = part_id
        segs = _split_measures(events, per_measure)
        max_m = max((m for m, *_ in segs), default=0)
        slots_pm = per_measure
        for m in range(max_m + 1):
            meas = stream.Measure(number=m + 1)
            if m == 0:
                meas.append(clef.BassClef() if cl == "bass" else clef.TrebleClef())
                meas.append(key.KeySignature(analysis.key_sharps))
                meas.append(meter.TimeSignature(sig))
                meas.append(tempo.MetronomeMark(number=bpm, referent=duration.Duration(1.0)))
            pos = 0.0
            for (_, p_in, d, pitches, vel) in sorted(
                    [x for x in segs if x[0] == m], key=lambda x: x[1]):
                if p_in > pos + 1e-9:
                    meas.append(note.Rest(quarterLength=(p_in - pos) * unit_ql))
                    pos = p_in
                el = note.Note(pitches[0]) if len(pitches) == 1 else chord.Chord(pitches)
                el.quarterLength = d * unit_ql
                try:
                    el.volume.velocity = int(vel)
                except Exception:
                    pass
                meas.append(el)
                pos = p_in + d
            if pos < slots_pm - 1e-9:
                meas.append(note.Rest(quarterLength=(slots_pm - pos) * unit_ql))
            p.append(meas)
        p.rightBarline = bar.Barline("final")
        return p

    rh_p = build_part(rh_e, rh_pm, rh_ql, "treble", "RH")
    rh_p.partName = "Piano"
    lh_p = build_part(lh_e, lh_pm, lh_ql, "bass", "LH")

    for p in (rh_p, lh_p):
        p.makeBeams(inPlace=True)

    # ---------- 校验小节时值
    def check(p, label):
        bad = []
        for m in p.getElementsByClass("Measure"):
            tot = sum(float(e.quarterLength) for e in m.notesAndRests)
            if abs(tot - bar_ql) > 1e-6:
                bad.append((m.number, round(tot, 2)))
        _log(log, f"{label}: {len(p.getElementsByClass('Measure'))} 小节, "
                  f"{len(p.recurse().notes)} 音符, 时值异常小节 {len(bad)} {bad[:5]}")
        return bad

    bad_rh, bad_lh = check(rh_p, "RH"), check(lh_p, "LH")
    if bad_rh or bad_lh:
        _log(log, "⚠ 存在时值异常小节, 尝试重填充休止修复 ...")
        for p, bad in ((rh_p, bad_rh), (lh_p, bad_lh)):
            for mnum, _tot in bad:
                try:
                    m = p.getElementsByClass("Measure")[mnum - 1]
                    m.clear()
                    m.append(note.Rest(quarterLength=bar_ql))
                except Exception:
                    pass

    # ---------- 对齐小节数
    def pad_to(p, target):
        ms = list(p.getElementsByClass("Measure"))
        while len(ms) < target:
            m = stream.Measure(number=len(ms) + 1)
            m.append(note.Rest(quarterLength=bar_ql))
            p.append(m)
            ms.append(m)

    target = max(len(list(p.getElementsByClass("Measure"))) for p in (rh_p, lh_p))
    pad_to(rh_p, target)
    pad_to(lh_p, target)

    # ---------- Score (扁平 Part + StaffGroup, 勿嵌套 PartStaff)
    score = stream.Score()
    md = metadata.Metadata()
    md.title = title or Path(midi_path).stem
    md.composer = composer or "自动转谱 (Piano Sheet Generator)"
    score.insert(0, md)
    score.insert(0, rh_p)
    score.insert(0, lh_p)
    score.insert(0, layout.StaffGroup([rh_p, lh_p], symbol="brace",
                                      barTogether=True, name="Piano"))

    out_xml.parent.mkdir(parents=True, exist_ok=True)
    score.write("musicxml", fp=str(out_xml))
    n_notes = len(rh_p.recurse().notes) + len(lh_p.recurse().notes)
    _log(log, f"MusicXML 已写出: {out_xml} ({target} 小节, {n_notes} 音符, "
              f"{analysis.key_name}, {sig}, ♩={bpm})")
    return BuildResult(musicxml_path=out_xml, n_measures=target, n_notes=n_notes, title=md.title)
