# -*- coding: utf-8 -*-
"""环境与路径配置。

设计原则: 全部依赖均自动探测, 探测不到才报错, 允许用户通过环境变量覆盖。
环境变量:
    PSG_PYTHON_SCORE   跑转写/建谱/渲染的解释器(需含 librosa/music21/verovio 等)
    PSG_ROFORMER_CLI   bs-roformer-infer 可执行文件路径
    PSG_FFMPEG         ffmpeg 可执行文件路径
    PSG_PTI_CKPT       钢琴转写模型权重 .pth
    PSG_WORKDIR        ASCII 临时工作目录(verovio 不支持中文路径)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------- 默认候选路径
_HOME = Path(os.path.expanduser("~"))


def _project_root() -> Path:
    """项目根目录 (src/piano_sheet/config.py 上两级)。"""
    return Path(__file__).resolve().parents[2]


def _venv_paths(root: Path, venv_name: str) -> list:
    """项目内 venv 的解释器/脚本路径 (兼容 Windows 与 POSIX 布局)。"""
    if os.name == "nt":
        return [root / venv_name / "Scripts" / "python.exe"]
    return [root / venv_name / "bin" / "python"]


def _venv_scripts(root: Path, venv_name: str, exe: str) -> list:
    """项目内 venv 的 Scripts/bin 可执行文件路径。"""
    if os.name == "nt":
        return [root / venv_name / "Scripts" / f"{exe}.exe"]
    return [root / venv_name / "bin" / exe]


_ROOT = _project_root()

# 主环境(转写/建谱/渲染): 优先项目内 setup 脚本搭建的 .venv-score, 其次本机已部署环境
_DEFAULT_SCORE_PYTHONS = [
    *_venv_paths(_ROOT, ".venv-score"),
    *_venv_paths(_ROOT, "venv"),
    _HOME / "AppData/Local/miniconda3/envs/score/python.exe",
    _HOME / ".workbuddy/binaries/python/envs/audio/Scripts/python.exe",
    Path(sys.executable),
]

# 分离环境(BS-RoFormer): 优先项目内 .venv-sep, 其次本机已部署 roformer venv
_DEFAULT_ROFORMER_CLIS = [
    *_venv_scripts(_ROOT, ".venv-sep", "bs-roformer-infer"),
    _HOME / ".workbuddy/binaries/python/envs/roformer/Scripts/bs-roformer-infer.exe",
    _HOME / ".workbuddy/binaries/python/envs/roformer/bin/bs-roformer-infer",
]

# 多轨分离模型注册表名称(BS-RoFormer SW by jarredou, 输出 bass/drums/other/vocals/guitar/piano)
MULTI_STEM_MODEL = "Roformer Model: BS Roformer SW by jarredou"

_DEFAULT_PTI_CKPT = (
    _HOME / "piano_transcription_inference_data"
    / "note_F1=0.9677_pedal_F1=0.9186.pth"
)

_SUPPORTED_INPUT = {".mp3", ".mp4", ".m4a", ".wav", ".flac", ".ogg", ".aac", ".wma", ".mov", ".mkv"}
SUPPORTED_INPUT = _SUPPORTED_INPUT   # public alias


class ConfigError(RuntimeError):
    """配置缺失或不满足运行条件。"""


@dataclass
class Config:
    """一次运行所需的全部环境配置。"""

    score_python: Path
    roformer_cli: Optional[Path]
    ffmpeg: Path
    pti_checkpoint: Optional[Path]
    workdir: Path
    output_dir: Path
    device: str = "auto"          # auto / cuda / cpu
    separate: bool = True         # 是否先做音轨分离
    keep_intermediate: bool = True

    report: dict = field(default_factory=dict)

    # ------------------------------------------------------------ 探测
    @classmethod
    def detect(cls, output_dir: Optional[os.PathLike] = None) -> "Config":
        rep: dict = {}

        score_python = _first_existing(_DEFAULT_SCORE_PYTHONS, "PSG_PYTHON_SCORE")
        if score_python is None:
            raise ConfigError(
                "未找到可用的 Python 解释器(需安装 librosa/music21/verovio)。"
                "请设置环境变量 PSG_PYTHON_SCORE 指向它。"
            )
        rep["score_python"] = str(score_python)
        rep["score_python_version"] = _py_version(score_python)

        roformer = _first_existing(_DEFAULT_ROFORMER_CLIS, "PSG_ROFORMER_CLI")
        if roformer is None:
            roformer = shutil.which("bs-roformer-infer")
            if roformer:
                roformer = Path(roformer)
        rep["roformer_cli"] = str(roformer) if roformer else None

        ffmpeg = _find_ffmpeg(score_python)
        rep["ffmpeg"] = str(ffmpeg) if ffmpeg else None

        ckpt = _env_path("PSG_PTI_CKPT") or (
            _DEFAULT_PTI_CKPT if _DEFAULT_PTI_CKPT.exists() else None
        )
        rep["pti_checkpoint"] = str(ckpt) if ckpt else None

        workdir = _env_path("PSG_WORKDIR") or Path(tempfile.gettempdir()) / "psg_work"
        workdir.mkdir(parents=True, exist_ok=True)

        out = Path(output_dir) if output_dir else Path.cwd() / "output"
        out.mkdir(parents=True, exist_ok=True)

        return cls(
            score_python=score_python,
            roformer_cli=roformer,
            ffmpeg=ffmpeg,
            pti_checkpoint=ckpt,
            workdir=workdir,
            output_dir=out,
            report=rep,
        )

    # ------------------------------------------------------------ 便捷判断
    @property
    def can_separate(self) -> bool:
        return self.roformer_cli is not None and self.ffmpeg is not None

    def describe(self) -> str:
        lines = ["环境自检结果:", f"  转写解释器 : {self.score_python} ({self.report.get('score_python_version')})"]
        lines.append(f"  ffmpeg     : {self.ffmpeg or '未找到 (无法转码/分离)'}")
        lines.append(f"  分离模型CLI: {self.roformer_cli or '未找到 (将跳过分离, 直接对原始音频转写)'}")
        lines.append(f"  转写权重   : {self.pti_checkpoint or '未找到 (将尝试联网自动下载)'}")
        lines.append(f"  临时目录   : {self.workdir}")
        lines.append(f"  输出目录   : {self.output_dir}")
        return "\n".join(lines)


# ---------------------------------------------------------------- helpers
_ENV_LOCAL_LOADED = False


def _load_env_local() -> None:
    """加载 setup 脚本生成的 .env.local (KEY=VALUE 每行一条)。

    真实环境变量优先, 不覆盖用户显式设置的值。只加载一次。
    """
    global _ENV_LOCAL_LOADED
    if _ENV_LOCAL_LOADED:
        return
    _ENV_LOCAL_LOADED = True
    f = _project_root() / ".env.local"
    if not f.exists():
        return
    try:
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and v and k not in os.environ:
                os.environ[k] = v
    except Exception:  # noqa: BLE001  损坏的 .env.local 不应让程序起不来
        pass


def _env_path(name: str) -> Optional[Path]:
    _load_env_local()
    v = os.environ.get(name)
    if v and Path(v).exists():
        return Path(v)
    return None


def _first_existing(candidates, env_name: str) -> Optional[Path]:
    p = _env_path(env_name)
    if p:
        return p
    for c in candidates:
        if c and Path(c).exists():
            return Path(c)
    return None


def _py_version(py: Path) -> str:
    try:
        out = subprocess.run([str(py), "-c", "import sys;print('.'.join(map(str,sys.version_info[:3])))"],
                             capture_output=True, text=True, timeout=60)
        return out.stdout.strip() or "?"
    except Exception:
        return "?"


def _find_ffmpeg(score_python: Path) -> Optional[Path]:
    """优先系统 PATH, 其次 imageio-ffmpeg 静态版, 再次已知部署路径。"""
    sysf = shutil.which("ffmpeg")
    if sysf:
        return Path(sysf)
    # imageio-ffmpeg 装在 score 环境或 audio 环境里
    for py in [score_python, *_DEFAULT_SCORE_PYTHONS]:
        if not py or not Path(py).exists():
            continue
        try:
            out = subprocess.run(
                [str(py), "-c", "import imageio_ffmpeg,shutil;p=imageio_ffmpeg.get_ffmpeg_exe();print(p)"],
                capture_output=True, text=True, timeout=120,
            )
            cand = out.stdout.strip()
            if cand and Path(cand).exists():
                return Path(cand)
        except Exception:
            continue
    known = [
        _HOME / ".workbuddy/binaries/python/envs/audio/Lib/site-packages/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe",
    ]
    for k in known:
        if k.exists():
            return k
    return None


def ascii_safe_dir(root: Path, name: str) -> Path:
    """verovio/部分原生库不认中文路径, 统一落到纯 ASCII 目录。

    注意: str.isalnum() 对中文也返回 True, 必须同时校验 isascii()。
    """
    safe = "".join(ch if (ch.isascii() and ch.isalnum()) or ch in "-_." else "_"
                   for ch in name).strip("._")[:40]
    if not safe:
        # 全中文名(如「如愿」)会被清空, 用稳定哈希兜底保证可复现
        import hashlib
        safe = "job_" + hashlib.md5(name.encode("utf-8")).hexdigest()[:8]
    d = root / safe
    d.mkdir(parents=True, exist_ok=True)
    return d


def validate_input(path: os.PathLike) -> Path:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"输入文件不存在: {p}")
    if p.suffix.lower() not in _SUPPORTED_INPUT:
        raise ConfigError(f"不支持的文件类型 {p.suffix} (支持: {', '.join(sorted(_SUPPORTED_INPUT))})")
    return p
