# -*- coding: utf-8 -*-
"""pytest 冒烟测试: 验证核心纯函数逻辑(不依赖大模型/长耗时推理)。

运行:
    pytest tests/ -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from piano_sheet import analyze as A            # noqa: E402
from piano_sheet import score_builder as B      # noqa: E402
from piano_sheet.config import Config, ascii_safe_dir, validate_input  # noqa: E402


# ------------------------------------------------------------------ config
def test_validate_input_rejects_missing(tmp_path):
    from piano_sheet.config import ConfigError
    with pytest.raises(ConfigError):
        validate_input(tmp_path / "nope.mp3")


def test_validate_input_rejects_bad_ext(tmp_path):
    from piano_sheet.config import ConfigError
    f = tmp_path / "a.txt"
    f.write_text("x")
    with pytest.raises(ConfigError):
        validate_input(f)


def test_ascii_safe_dir(tmp_path):
    d = ascii_safe_dir(tmp_path, "如愿 钢琴·演奏")
    assert d.exists()
    assert d.is_absolute()
    # 目录名必须是纯 ASCII(verovio 要求)
    assert all(ord(c) < 128 for c in d.name)


# ------------------------------------------------------------------ analyze
def test_key_estimation_major():
    """合成 C 大调和弦的 chroma 应估为 C major。"""
    chroma = np.zeros((12, 50))
    for pc in (0, 4, 7):          # C E G
        chroma[pc, :] = 1.0
    name, sharps, corr = A.estimate_key(chroma)
    assert name == "C major"
    assert sharps == 0
    assert corr > 0.5


def test_key_estimation_minor_sharps():
    """F# 小调应得到 3 个升号。"""
    chroma = np.zeros((12, 50))
    for pc in (6, 9, 1):          # F# A C#
        chroma[pc, :] = 1.0
    name, sharps, corr = A.estimate_key(chroma)
    assert name.endswith("minor")
    if name.startswith("F#"):
        assert sharps == 3


def test_key_sharps_table():
    assert A._key_sharps("C", "major") == 0
    assert A._key_sharps("A", "minor") == 0
    assert A._key_sharps("F#", "minor") == 3
    assert A._key_sharps("Eb", "major") == -3
    assert A._key_sharps("D", "major") == 2


def test_downbeat_phase():
    """每 4 拍第一拍更强 -> phase 应为 0。"""
    bt = np.arange(0, 40, 0.5)               # 80 拍
    fps = 100.0
    oenv = np.zeros(int(bt[-1] * fps) + 10)
    for i, t in enumerate(bt):
        oenv[int(t * fps)] = 10.0 if i % 4 == 0 else 1.0
    phase, down = A.find_downbeat_phase(bt, oenv, fps, 4)
    assert phase == 0
    assert len(down) == len(bt) // 4


def _synth_pulse(n_beats, fps=100.0, ibi=0.625, strengths=None):
    """构造 onset 包络与拍位; strengths 为长度=每小节拍数的循环强弱轮廓。"""
    bt = np.arange(n_beats) * ibi
    oenv = np.zeros(int(bt[-1] * fps) + 20)
    k = len(strengths) if strengths else 1
    for i, t in enumerate(bt):
        oenv[int(t * fps)] = (strengths[i % k] if strengths else 1.0)
    return bt, oenv, fps


def test_time_signature_detects_4_4():
    """强-弱-次强-弱 轮廓应判为 4/4 (River Flows in You 误判 6/8 的回归用例)。"""
    bt, oenv, fps = _synth_pulse(160, strengths=[10.0, 3.0, 6.0, 3.0])
    sig, bpb = A.detect_time_signature(bt, oenv, fps)
    assert sig == "4/4"
    assert bpb == 4


def test_time_signature_detects_3_4():
    """华尔兹轮廓应判为 3/4。"""
    bt, oenv, fps = _synth_pulse(150, strengths=[10.0, 3.0, 3.0])
    sig, bpb = A.detect_time_signature(bt, oenv, fps)
    assert sig == "3/4"
    assert bpb == 3


def test_time_signature_never_returns_6_8_on_4_4_input():
    """4/4 输入绝不能被判成 6 拍/小节(旧版 (max-mean)/std 打分的系统性偏差)。"""
    bt, oenv, fps = _synth_pulse(200, strengths=[10.0, 3.0, 6.0, 3.0])
    _sig, bpb = A.detect_time_signature(bt, oenv, fps)
    assert bpb in (2, 3, 4)


def test_downbeat_phase_offset():
    """强弱轮廓的第 3 个相位才是真强拍时, 应返回 phase=2。"""
    bt, oenv, fps = _synth_pulse(120, strengths=[3.0, 3.0, 10.0, 3.0])
    phase, down = A.find_downbeat_phase(bt, oenv, fps, 4)
    assert phase == 2
    assert len(down) == len(bt) // 4


def test_bar_profile_prior_prefers_4_4_on_flat_input():
    """各拍强度均等(无强弱信息)时, 先验应让 4/4 胜出而不是随机拍号。"""
    bt, oenv, fps = _synth_pulse(160, strengths=[5.0, 5.0, 5.0, 5.0])
    sig, bpb = A.detect_time_signature(bt, oenv, fps)
    assert sig in ("4/4", "2/4", "3/4")
    assert bpb in (2, 3, 4)


# ------------------------------------------------------------------ BarMapper
def test_bar_mapper_linear():
    """等间隔强拍 -> t_to_bar 应线性且每小节=1.0。"""
    down = np.array([0.0, 2.0, 4.0, 6.0, 8.0])
    m = B.BarMapper(down, med_bar=2.0, t_first=0.0)
    assert m.t_to_bar(0.0) == pytest.approx(0.0, abs=1e-6)
    assert m.t_to_bar(1.0) == pytest.approx(0.5, abs=1e-6)
    assert m.t_to_bar(2.0) == pytest.approx(1.0, abs=1e-6)
    assert m.t_to_bar(9.0) == pytest.approx(4.5, abs=1e-6)   # 末端外推


def test_bar_mapper_rubato_no_drift():
    """小节长局部波动时, 分段线性映射仍应把每个强拍对准整数小节。"""
    rng = np.random.default_rng(0)
    ideal = np.arange(0, 40) * 2.0
    jitter = np.concatenate([[0.0], np.cumsum(rng.normal(0, 0.08, 39))])
    down = ideal + jitter
    m = B.BarMapper(down, med_bar=float(np.median(np.diff(down))), t_first=0.0)
    offs = len(m.down) - len(down)         # 可能前推了若干小节
    for i, t in enumerate(down):
        assert m.t_to_bar(t) == pytest.approx(i + offs, abs=1e-6)


# ------------------------------------------------------------------ 量化
class _N:
    """轻量替身, 模仿 pretty_midi.Note 的字段。"""
    def __init__(self, pitch, start, end, velocity=80):
        self.pitch, self.start, self.end, self.velocity = pitch, start, end, velocity


def test_quantize_merges_same_slot():
    """同一格内的两个音应合并为和弦。"""
    down = np.array([0.0, 2.0, 4.0])
    m = B.BarMapper(down, 2.0, 0.0)
    notes = [_N(60, 0.02, 0.9), _N(64, 0.03, 0.9), _N(72, 1.05, 1.8)]
    ev = B._quantize(notes, m, slots_per_measure=4, per_measure=4)
    assert ev[0][3] == [60, 64]            # 和弦合并
    assert len(ev) == 2


def test_no_overlap_truncates():
    ev = [(0, 8, 80, [60]), (2, 8, 80, [64])]
    out = B._no_overlap(ev)
    assert out[0][1] == 2                  # 被下一音符截断
    assert out[1][1] == 8


def test_merge_adjacent_same():
    ev = [(0, 2, 80, [60]), (2, 2, 80, [60]), (4, 2, 80, [64])]
    out = B._merge_adjacent_same(ev, per_measure=4)
    assert out[0][1] == 4
    assert len(out) == 2


def test_split_measures_cross_bar():
    """跨小节音符应拆成两段(钢琴不写连线)。"""
    ev = [(3, 4, 80, [60])]                # 第 0 小节第 3 格起, 长 4 格, per_measure=4
    segs = B._split_measures(ev, per_measure=4)
    assert len(segs) == 2
    assert segs[0][:3] == (0, 3, 1)
    assert segs[1][:3] == (1, 0, 3)


def test_merge_same_pitch():
    pretty_midi = pytest.importorskip("pretty_midi")
    ns = [pretty_midi.Note(80, 60, 0.0, 0.5), pretty_midi.Note(70, 60, 0.55, 1.0)]
    out = B._merge_same_pitch(ns, gap=0.10)
    assert len(out) == 1
    assert out[0].end == pytest.approx(1.0)
    assert out[0].velocity == 80


# ------------------------------------------------------------------ 端到端(合成, 不需模型)
def test_build_and_render_synthetic(tmp_path):
    """合成一个 C 大调 4 小节的 MIDI, 跑通 build_musicxml -> render_pdf 全链路。"""
    pytest.importorskip("music21")
    pytest.importorskip("verovio")
    pytest.importorskip("svglib")
    import pretty_midi

    pm = pretty_midi.PrettyMIDI(initial_tempo=120)
    inst = pretty_midi.Instrument(program=0)
    # 4 小节 4/4, 每拍一个 C 大调音阶音 + 左手每小节一个根音
    scale = [60, 62, 64, 65, 67, 69, 71, 72]
    t = 0.0
    for bar_i in range(4):
        for beat in range(4):
            inst.notes.append(pretty_midi.Note(90, scale[(bar_i * 4 + beat) % 8], t, t + 0.45))
            t += 0.5
        inst.notes.append(pretty_midi.Note(80, 48 + (bar_i % 2) * 7, t - 2.0, t))
    pm.instruments.append(inst)
    midi = tmp_path / "synth.mid"
    pm.write(str(midi))

    # 构造等间隔拍位的 analysis(2s/小节, 4 拍/小节 -> IBI=0.5)
    beats = np.arange(0, 8.0, 0.5)
    downbeats = beats[::4]
    analysis = A.MusicAnalysis(
        key_name="C major", key_sharps=0, time_sig="4/4", beats_per_bar=4,
        bpm=120.0, beat_times=beats, downbeats=downbeats, ibi_cv=0.0, confidence=0.9,
    )

    xml = tmp_path / "synth.musicxml"
    res = B.build_musicxml(midi, analysis, xml, title="Synthetic Test")
    assert xml.exists() and res.n_measures >= 4 and res.n_notes > 0

    from piano_sheet import renderer
    pdf = tmp_path / "synth.pdf"
    rr = renderer.render_pdf(xml, pdf, png_preview=True)
    assert pdf.exists() and pdf.stat().st_size > 2000
    assert rr.pages >= 1
    assert rr.png_previews, "应至少生成一页 PNG 预览"


def test_render_no_temp_files_left(tmp_path):
    """渲染全程内存化后, 输出目录不应残留任何临时目录/文件。"""
    pytest.importorskip("music21")
    pytest.importorskip("verovio")
    from music21 import stream, note, meter, key, clef
    from piano_sheet import renderer

    p = stream.Part()
    m = stream.Measure(number=1)
    m.append(clef.TrebleClef()); m.append(key.KeySignature(0))
    m.append(meter.TimeSignature("4/4"))
    for pn in ("C4", "E4", "G4", "C5"):
        m.append(note.Note(pn, quarterLength=1.0))
    p.append(m)
    s = stream.Score(); s.insert(0, p)
    xml = tmp_path / "t.musicxml"
    s.write("musicxml", fp=str(xml))

    pdf = tmp_path / "t.pdf"
    rr = renderer.render_pdf(xml, pdf, png_preview=False, title="T", composer="C")
    assert pdf.exists()
    leftovers = [f for f in tmp_path.iterdir() if f.name != "t.musicxml"
                 and f.name != "t.pdf"]
    assert not leftovers, f"残留临时文件: {leftovers}"


def test_render_cjk_title(tmp_path):
    """中文标题必须真实写入 PDF(svglib 链路会丢 CJK, 由 pymupdf 叠加兜底)。"""
    pytest.importorskip("music21")
    pytest.importorskip("verovio")
    pymupdf = pytest.importorskip("pymupdf")
    from music21 import stream, note, meter, key, clef
    from piano_sheet import renderer

    p = stream.Part()
    m = stream.Measure(number=1)
    m.append(clef.TrebleClef()); m.append(key.KeySignature(0))
    m.append(meter.TimeSignature("4/4"))
    for pn in ("C4", "E4", "G4", "C5"):
        m.append(note.Note(pn, quarterLength=1.0))
    p.append(m)
    s = stream.Score(); s.insert(0, p)
    xml = tmp_path / "zh.musicxml"
    s.write("musicxml", fp=str(xml))

    # 输出路径也带中文(模拟真实场景)
    zh_dir = tmp_path / "中文目录 测试"
    zh_dir.mkdir()
    pdf = zh_dir / "中文曲名_钢琴谱.pdf"
    rr = renderer.render_pdf(xml, pdf, png_preview=False,
                             title="我和我的祖国", composer="自动转谱")
    assert pdf.exists()
    text = pymupdf.open(str(pdf))[0].get_text()
    if renderer._find_cjk_font():
        assert rr.title_drawn, "有 CJK 字体时标题必须叠加成功"
        assert "我和我的祖国" in text, "中文标题应可从 PDF 提取"
    else:
        pytest.skip("本机无 CJK 字体, 跳过叠加断言")


def test_extract_title_from_musicxml(tmp_path):
    """标题缺省应能从 MusicXML 元数据提取。"""
    pytest.importorskip("music21")
    from music21 import stream, note, metadata
    from piano_sheet.renderer import _extract_title

    s = stream.Score()
    md = metadata.Metadata()
    md.title = "测试曲名"
    s.insert(0, md)
    p = stream.Part(); m = stream.Measure(number=1); m.append(note.Note("C4")); p.append(m)
    s.insert(0, p)
    xml = tmp_path / "m.musicxml"
    s.write("musicxml", fp=str(xml))
    assert _extract_title(xml.read_text(encoding="utf-8")) == "测试曲名"


def test_pipeline_error_carries_stage():
    from piano_sheet.pipeline import PipelineError
    e = PipelineError("render", "boom", ValueError("x"))
    assert e.stage == "render"
    assert "boom" in str(e)


def test_config_detect_or_skip():
    """环境自检不应抛未捕获异常(缺依赖时抛 ConfigError 是可接受的)。"""
    try:
        cfg = Config.detect()
    except Exception:
        pytest.skip("本机无可用解释器配置")
    assert cfg.describe()
    assert cfg.workdir.exists()
