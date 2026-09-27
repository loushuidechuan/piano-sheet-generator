"""piano_sheet — 音频钢琴轨分离与五线谱生成。

模块划分:
    config        环境/路径配置与自动探测
    audio         音频预处理(转码) 与钢琴轨分离(BS-RoFormer)
    analyze       调性/拍号/速度 自动分析
    transcribe    钢琴音频 -> MIDI (ByteDance piano_transcription_inference)
    score_builder MIDI -> MusicXML (music21, 真实拍位对齐 + 量化 + 简练化)
    renderer      MusicXML -> PDF (+PNG 预览)
    rerender      渲染失败补救(从已有 MusicXML 重出 PDF)
    pipeline      一键编排
    gui.app       Tkinter 图形界面
"""

__version__ = "1.0.0"

from .config import Config, ConfigError, SUPPORTED_INPUT  # noqa: F401
from .pipeline import (  # noqa: F401
    Pipeline,
    PipelineError,
    PipelineResult,
    StageProgress,
    STAGES,
    STAGE_LABELS,
    run_file,
)

__all__ = [
    "__version__",
    # 配置
    "Config", "ConfigError", "SUPPORTED_INPUT",
    # 流水线
    "Pipeline", "PipelineError", "PipelineResult", "StageProgress",
    "STAGES", "STAGE_LABELS", "run_file",
]
