# -*- coding: utf-8 -*-
"""一键编排: 音频 -> 分离钢琴轨 -> MIDI -> MusicXML -> PDF。

各阶段:
    prepare    转码为 ASCII 路径 WAV(立体声 44.1k / 单声道)
    separate   BS-RoFormer 多轨分离取钢琴轨(可跳过)
    analyze    调性/拍号/速度/拍位分析
    transcribe 钢琴音频 -> MIDI (CRNN)
    build      MIDI -> MusicXML (music21)
    render     MusicXML -> PDF (+PNG 预览)
"""
from __future__ import annotations

import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import analyze as analyze_mod
from . import audio as audio_mod
from . import renderer as renderer_mod
from . import score_builder as builder_mod
from . import transcribe as transcribe_mod
from .config import Config, ConfigError, ascii_safe_dir, validate_input

LogFn = Callable[[str], None]
ProgressFn = Callable[["StageProgress"], None]

STAGES = ["prepare", "separate", "analyze", "transcribe", "build", "render"]
STAGE_LABELS = {
    "prepare": "音频预处理",
    "separate": "钢琴音轨分离",
    "analyze": "调性/节拍分析",
    "transcribe": "钢琴转写 MIDI",
    "build": "生成五线谱数据",
    "render": "渲染 PDF",
}
# 大致耗时权重(用于总进度条估算)
_STAGE_WEIGHT = {"prepare": 0.05, "separate": 0.25, "analyze": 0.10,
                 "transcribe": 0.40, "build": 0.10, "render": 0.10}


class PipelineError(RuntimeError):
    """流水线失败, 带阶段名。"""

    def __init__(self, stage: str, message: str, cause: Optional[BaseException] = None,
                 recoverable_xml: Optional[Path] = None):
        super().__init__(f"[{stage}] {message}")
        self.stage = stage
        self.cause = cause
        # 若已有 MusicXML, 用户可用它 --rerender 补救, 不必重跑流水线
        self.recoverable_xml = recoverable_xml


@dataclass
class StageProgress:
    stage: str
    label: str
    index: int            # 1-based
    total: int
    fraction: float       # 0..1 全局进度(估算)
    message: str = ""


@dataclass
class PipelineResult:
    pdf_path: Path
    midi_path: Path
    musicxml_path: Path
    piano_wav: Optional[Path]
    analysis: Optional[analyze_mod.MusicAnalysis]
    separated: bool
    pages: int
    n_notes: int
    n_measures: int
    elapsed: float
    output_dir: Path
    png_previews: list = field(default_factory=list)
    logs: list = field(default_factory=list)


class Pipeline:
    """线程安全的一次性流水线对象(每次处理新建一个)。"""

    def __init__(self, config: Optional[Config] = None,
                 log: Optional[LogFn] = None,
                 progress: Optional[ProgressFn] = None,
                 should_stop: Optional[Callable[[], bool]] = None):
        self.config = config or Config.detect()
        self._log_fn = log
        self._progress_fn = progress
        self._should_stop = should_stop or (lambda: False)
        self.logs: list = []
        self._stopped = False

    # ------------------------------------------------------------------ 内部
    def _log(self, msg: str) -> None:
        """唯一日志出口: 有回调走回调(CLI 传 print / GUI 传队列), 无回调才自己 print。"""
        self.logs.append(msg)
        if self._log_fn:
            try:
                self._log_fn(msg)
                return
            except Exception:  # noqa: BLE001  (GUI 已销毁等)
                pass
        print(msg, flush=True)

    def _stage(self, stage: str, message: str = "") -> None:
        if self._should_stop():
            self._stopped = True
            raise PipelineError(stage, "用户已取消")
        idx = STAGES.index(stage) + 1
        frac = sum(_STAGE_WEIGHT[s] for s in STAGES[:idx - 1])
        p = StageProgress(stage=stage, label=STAGE_LABELS[stage], index=idx,
                          total=len(STAGES), fraction=frac, message=message)
        self._log(f"▶ 阶段 {idx}/{len(STAGES)}: {STAGE_LABELS[stage]} {message}".rstrip())
        if self._progress_fn:
            try:
                self._progress_fn(p)
            except Exception:  # noqa: BLE001
                pass

    def _done(self, frac: float = 1.0) -> None:
        if self._progress_fn:
            try:
                self._progress_fn(StageProgress(stage="done", label="完成", index=len(STAGES),
                                                total=len(STAGES), fraction=frac, message="完成"))
            except Exception:  # noqa: BLE001
                pass

    def _dump_failure(self, out_dir: Path, stage: str, err: BaseException) -> Optional[Path]:
        """把完整报错 + 环境信息写到输出目录, 便于事后诊断(下次失败能自动留现场)。"""
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            f = out_dir / f"{stage.upper()}_ERROR.txt"
            import platform
            body = [
                f"阶段: {stage}",
                f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
                f"Python: {sys.version}",
                f"平台: {platform.platform()}",
                f"异常: {type(err).__name__}: {err}",
                "",
                "Traceback:",
                traceback.format_exc(),
                "",
                "最近日志:",
                *self.logs[-40:],
            ]
            f.write_text("\n".join(body), encoding="utf-8")
            return f
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------ 主流程
    def run(self, input_path, title: str = "", composer: str = "",
            separate: Optional[bool] = None, device: str = "auto") -> PipelineResult:
        t_start = time.time()
        cfg = self.config
        src = validate_input(input_path)
        title = title or src.stem

        do_separate = cfg.separate if separate is None else separate
        if do_separate and not cfg.can_separate:
            self._log("⚠ 缺少 ffmpeg 或分离模型 CLI, 自动降级为「不分离, 直接转写」")
            do_separate = False
        if cfg.ffmpeg is None:
            raise PipelineError("prepare", "未找到 ffmpeg, 无法转码音频 (请设置 PSG_FFMPEG)")

        workdir = ascii_safe_dir(cfg.workdir, src.stem)
        # 输出目录保留原名(渲染走 loadData 字符串, 不受 verovio 中文路径限制)
        out_dir = cfg.output_dir / _slug(src.stem)
        out_dir.mkdir(parents=True, exist_ok=True)
        self._log(f"输入: {src}")
        self._log(f"工作目录: {workdir}")
        self._log(f"输出目录: {out_dir}")

        piano_wav: Optional[Path] = None
        analysis_src_wav: Path

        # ---- 1. 预处理
        self._stage("prepare", f"({src.name})")
        try:
            if do_separate:
                stereo = audio_mod.transcode(src, workdir / f"{_slug(src.stem)}_44k.wav",
                                             cfg.ffmpeg, log=self._log, sr=44100, mono=False)
            else:
                stereo = None
            mono = audio_mod.transcode(src, workdir / f"{_slug(src.stem)}_mono.wav",
                                       cfg.ffmpeg, log=self._log, sr=44100, mono=True)
        except Exception as e:  # noqa: BLE001
            raise PipelineError("prepare", f"音频预处理失败: {e}", e) from e

        # ---- 2. 分离
        if do_separate:
            self._stage("separate", "(BS-RoFormer 多轨模型)")
            try:
                sep = audio_mod.separate_piano(stereo, workdir, cfg.roformer_cli,
                                               device=device, log=self._log)
                piano_wav = sep.piano_wav
            except Exception as e:  # noqa: BLE001
                self._log(f"⚠ 分离失败, 降级为直接转写: {e}")
                piano_wav = None

        # 转写用的音频: 有钢琴轨用钢琴轨(单声道), 否则用整曲单声道
        transcribe_wav = piano_wav if piano_wav is not None else mono
        # 分析用: 优先钢琴轨(节奏规整), 否则整曲
        analysis_src_wav = transcribe_wav

        # ---- 3. 分析
        self._stage("analyze", "(调性 / 拍号 / 速度)")
        try:
            analysis = analyze_mod.analyze(analysis_src_wav, log=self._log)
        except Exception as e:  # noqa: BLE001
            self._log(f"⚠ 拍位分析失败, 回退整曲再试: {e}")
            if piano_wav is not None:
                try:
                    analysis = analyze_mod.analyze(mono, log=self._log)
                except Exception as e2:  # noqa: BLE001
                    raise PipelineError("analyze", f"节拍分析失败: {e2}", e2) from e2
            else:
                raise PipelineError("analyze", f"节拍分析失败: {e}", e) from e

        # ---- 4. 转写
        self._stage("transcribe", "(CRNN 钢琴转写, 这一步最耗时)")
        midi_path = workdir / f"{_slug(src.stem)}_piano.mid"
        try:
            tr = transcribe_mod.transcribe(transcribe_wav, midi_path,
                                           checkpoint=cfg.pti_checkpoint,
                                           device="cpu", log=self._log)
        except Exception as e:  # noqa: BLE001
            raise PipelineError("transcribe", f"钢琴转写失败: {e}", e) from e

        # ---- 5. 建谱
        self._stage("build", "(music21 量化 / 大小谱表)")
        xml_path = out_dir / f"{_slug(src.stem)}_piano.musicxml"
        try:
            bres = builder_mod.build_musicxml(tr.midi_path, analysis, xml_path,
                                              title=title, composer=composer, log=self._log)
        except Exception as e:  # noqa: BLE001
            self._dump_failure(out_dir, "build", e)
            raise PipelineError("build", f"建谱失败: {e}", e) from e

        # ---- 产物先落输出目录(渲染失败时用户仍可用 --rerender 补救)
        final_midi = out_dir / f"{_slug(src.stem)}_piano.mid"
        if final_midi.resolve() != tr.midi_path.resolve():
            try:
                final_midi.write_bytes(tr.midi_path.read_bytes())
            except Exception as e:  # noqa: BLE001
                self._log(f"⚠ MIDI 拷贝到输出目录失败: {e}")
                final_midi = tr.midi_path

        final_piano_wav = None
        if piano_wav is not None and cfg.keep_intermediate:
            final_piano_wav = out_dir / f"{_slug(src.stem)}_piano.wav"
            try:
                final_piano_wav.write_bytes(piano_wav.read_bytes())
            except Exception as e:  # noqa: BLE001
                self._log(f"⚠ 钢琴轨音频拷贝失败: {e}")
                final_piano_wav = piano_wav

        # ---- 6. 渲染
        self._stage("render", "(verovio -> PDF)")
        pdf_path = out_dir / f"{_slug(src.stem)}_钢琴谱.pdf"
        try:
            rres = renderer_mod.render_pdf(bres.musicxml_path, pdf_path, log=self._log,
                                           title=title, composer=composer)
        except Exception as e:  # noqa: BLE001
            self._dump_failure(out_dir, "render", e)
            raise PipelineError(
                "render",
                f"PDF 渲染失败: {e}\n"
                f"   ⚠ 好消息: 分离/转写/建谱的成果都已保存, 无需重跑几分钟的流水线。\n"
                f"   补救方法(几秒钟):  python -m piano_sheet --rerender \"{bres.musicxml_path}\"\n"
                f"   或在界面点击「♻ 重新渲染 PDF」按钮。\n"
                f"   完整报错已存到: {out_dir / 'RENDER_ERROR.txt'}",
                e,
                recoverable_xml=bres.musicxml_path,
            ) from e

        # ---- 完成
        elapsed = time.time() - t_start
        self._log("─" * 60)
        self._log(f"✅ 全部完成, 用时 {elapsed:.1f}s")
        self._log(f"   PDF      : {rres.pdf_path} ({rres.pages} 页)")
        self._log(f"   MIDI     : {final_midi}")
        self._log(f"   MusicXML : {bres.musicxml_path}")
        if final_piano_wav:
            self._log(f"   钢琴轨   : {final_piano_wav}")
        self._log(f"   调性/拍号: {analysis.key_name}, {analysis.time_sig}, ♩={analysis.bpm:.0f}")
        self._done(1.0)

        return PipelineResult(
            pdf_path=rres.pdf_path, midi_path=final_midi,
            musicxml_path=bres.musicxml_path, piano_wav=final_piano_wav,
            analysis=analysis, separated=piano_wav is not None,
            pages=rres.pages, n_notes=bres.n_notes, n_measures=bres.n_measures,
            elapsed=elapsed, output_dir=out_dir, png_previews=rres.png_previews,
            logs=list(self.logs),
        )


def _slug(name: str) -> str:
    """文件名安全化(输出目录允许中文, 但去掉路径非法字符)。"""
    bad = '\\/:*?"<>|'
    return "".join("_" if ch in bad else ch for ch in name).strip(" ._") or "output"


# ------------------------------------------------------------------ CLI 便捷入口
def run_file(input_path, output_dir=None, separate=True, device="auto",
             title="", composer=""):
    """命令行/脚本调用的简化入口。"""
    cfg = Config.detect(output_dir=output_dir)
    cfg.separate = separate
    p = Pipeline(config=cfg)
    return p.run(input_path, title=title, composer=composer, device=device)


__all__ = ["Pipeline", "PipelineError", "PipelineResult", "StageProgress",
           "STAGES", "STAGE_LABELS", "run_file", "ConfigError"]
