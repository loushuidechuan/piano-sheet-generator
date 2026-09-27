# -*- coding: utf-8 -*-
"""跨平台环境搭建器 (Windows / Linux 共用逻辑, 由 setup.bat / setup.sh 调用)。

搭建两个隔离环境:
    .venv-score  主环境: 转写 + 建谱 + 渲染 (Python 3.10~3.12, CPU torch)
    .venv-sep    分离环境: BS-RoFormer 钢琴音轨分离 (Python >=3.10, 可选 CUDA)
再下载钢琴转写权重(165MB)与分离模型(可选)。

用法:
    python install.py [--core-only] [--sep-only] [--cpu] [--no-models] [--mirror]
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

SETUP_DIR = Path(__file__).resolve().parent
ROOT = SETUP_DIR.parent
VENV_SCORE = ROOT / ".venv-score"
VENV_SEP = ROOT / ".venv-sep"

PIP_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"

# 主环境需要 Python 3.10~3.12 (music21<10 兼容 3.10; verovio/numba 要求 >=3.10)
CORE_MIN, CORE_MAX = (3, 10), (3, 12)
# 分离环境 3.10~3.13。⚠ 不要放开到 3.14+: torch/torchaudio 尚无对应 wheel,
#   pip 会回退源码编译并失败(本机实测 miniconda base 是 3.14)
SEP_MIN, SEP_MAX = (3, 10), (3, 13)

GREEN, YELLOW, RED, DIM, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"
if os.name == "nt" and not os.environ.get("WT_SESSION"):
    # 老 cmd 不支持 ANSI; Windows Terminal / PS7 支持
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)
        m = ctypes.c_uint()
        if k.GetConsoleMode(h, ctypes.byref(m)):
            k.SetConsoleMode(h, m.value | 0x0004)
    except Exception:
        GREEN = YELLOW = RED = DIM = RESET = ""


def say(msg: str = "", color: str = "") -> None:
    print(f"{color}{msg}{RESET}" if color else msg, flush=True)


def head(msg: str) -> None:
    say()
    say("─" * 64, DIM)
    say(f"  {msg}", GREEN)
    say("─" * 64, DIM)


def warn(msg: str) -> None:
    say(f"⚠ {msg}", YELLOW)


def fail(msg: str) -> None:
    say(f"✗ {msg}", RED)


def run(cmd: list, desc: str = "", check: bool = True, **kw) -> subprocess.CompletedProcess:
    """执行命令并流式输出。"""
    if desc:
        say(f"  $ {desc}", DIM)
    r = subprocess.run(cmd, text=True, errors="replace", **kw)
    if check and r.returncode != 0:
        raise RuntimeError(f"命令失败 (exit={r.returncode}): {' '.join(map(str, cmd[:6]))}")
    return r


# ---------------------------------------------------------------- Python 探测
def _ver(p: Path) -> tuple | None:
    try:
        r = subprocess.run([str(p), "-c", "import sys;print(sys.version_info[0],sys.version_info[1])"],
                           capture_output=True, text=True, timeout=60)
        a, b = r.stdout.split()
        return int(a), int(b)
    except Exception:
        return None


def find_pythons() -> list:
    """枚举本机所有候选 Python (去重, 带版本)。"""
    cands = []
    seen = set()

    def add(p):
        try:
            p = Path(p).resolve()
        except Exception:
            return
        if not p.exists() or str(p) in seen:
            return
        v = _ver(p)
        if v:
            seen.add(str(p))
            cands.append((p, v))

    for env in ("PSG_PYTHON_SCORE", "PSG_PYTHON_SEP"):
        if os.environ.get(env):
            add(os.environ[env])
    # conda envs
    for base in [Path.home() / "AppData/Local/miniconda3", Path.home() / "miniconda3",
                 Path.home() / "anaconda3", Path("/opt/conda"), Path("/opt/miniconda3")]:
        if not base.exists():
            continue
        add(base / "python.exe" if os.name == "nt" else base / "bin/python3")
        envs = base / "envs"
        if envs.is_dir():
            for d in sorted(envs.iterdir()):
                add(d / "python.exe" if os.name == "nt" else d / "bin/python3")
    # 托管运行时
    wb = Path.home() / ".workbuddy/binaries/python/versions"
    if wb.is_dir():
        for d in sorted(wb.iterdir()):
            for n in ("python.exe", "python3", "python"):
                add(d / n)
    # PATH 上的
    for name in (["py", "python", "python3"] if os.name == "nt" else ["python3", "python3.13",
                 "python3.12", "python3.11", "python3.10", "python"]):
        exe = shutil.which(name)
        if exe:
            if name == "py":
                for v in ("3.12", "3.11", "3.10", "3.13"):
                    r = subprocess.run(["py", f"-{v}", "-c", "import sys;print(sys.executable)"],
                                       capture_output=True, text=True)
                    if r.returncode == 0 and r.stdout.strip():
                        add(r.stdout.strip())
            else:
                add(exe)
    # 系统常见位置
    if os.name != "nt":
        for p in Path("/usr/bin").glob("python3.*"):
            add(p)
    cands.sort(key=lambda x: x[1], reverse=True)
    return cands


def pick_python(cands: list, vmin: tuple, vmax: tuple | None = None,
                prefer: tuple = ()) -> tuple | None:
    """在版本区间内选解释器。prefer 里的版本优先(如 3.11/3.12 生态最稳)。"""
    in_range = [(p, v) for p, v in cands if v >= vmin and (vmax is None or v <= vmax)]
    if not in_range:
        return None
    for want in prefer:
        for p, v in in_range:
            if v == want:
                return p, v
    return in_range[0]


# ---------------------------------------------------------------- venv 搭建
def make_venv(py: Path, target: Path) -> Path:
    if (target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")).exists():
        say(f"  已存在, 复用: {target}", DIM)
    else:
        run([str(py), "-m", "venv", str(target)], f"创建 venv: {target.name}")
    return venv_python(target)


def venv_python(target: Path) -> Path:
    return target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def pip(py: Path, args: list, desc: str = "", mirror: bool = True) -> None:
    cmd = [str(py), "-m", "pip", *args, "--no-cache-dir"]
    if mirror:
        cmd += ["-i", PIP_MIRROR]
    run(cmd, desc or f"pip {' '.join(args[:3])}...")


def setup_core(mirror: bool, cpu_torch: bool = True) -> Path:
    head("① 主环境 .venv-score (转写 / 建谱 / 渲染)")
    cands = find_pythons()
    pick = pick_python(cands, CORE_MIN, CORE_MAX, prefer=((3, 11), (3, 12), (3, 10)))
    if pick is None:
        fail(f"未找到 Python {CORE_MIN[0]}.{CORE_MIN[1]}~{CORE_MAX[0]}.{CORE_MAX[1]}。已发现:")
        for p, v in cands[:8]:
            say(f"      {v[0]}.{v[1]:<2}  {p}", DIM)
        raise SystemExit(
            "\n请安装 Python 3.10/3.11/3.12 后重试 (music21 9.x 尚不支持 3.13+, "
            "而 verovio/numba 要求 >=3.10)。\n"
            "  Windows: https://www.python.org/downloads/  或  winget install Python.Python.3.11\n"
            "  Linux  : sudo apt install python3.11 python3.11-venv"
        )
    py, ver = pick
    say(f"  选用 Python {ver[0]}.{ver[1]}: {py}", DIM)
    vpy = make_venv(py, VENV_SCORE)

    pip(vpy, ["install", "-U", "pip", "wheel", "setuptools<81"], "升级 pip/wheel/setuptools")
    if cpu_torch:
        # CPU 版 torch 单独装, 比默认 CUDA 版省 ~2GB
        run([str(vpy), "-m", "pip", "install", "torch", "--index-url", TORCH_CPU_INDEX,
             "--no-cache-dir"], "安装 torch (CPU 版, 转写用)")
    req = SETUP_DIR / "requirements-core.txt"
    pip(vpy, ["install", "-r", str(req)], "安装主依赖 (librosa/music21/verovio/…)", mirror=mirror)

    # 自检
    say("  自检导入...", DIM)
    r = subprocess.run(
        [str(vpy), "-c",
         "import numpy,librosa,soundfile,pretty_midi,music21,verovio,svglib,reportlab,"
         "pypdf,pymupdf,imageio_ffmpeg,piano_transcription_inference as p;"
         "print('core OK', music21.__version__ if hasattr(music21,'__version__') else '')"],
        capture_output=True, text=True)
    if r.returncode != 0:
        fail("主环境自检失败:\n" + (r.stderr or r.stdout)[-1500:])
        raise SystemExit(1)
    say(f"  ✓ {r.stdout.strip()}", GREEN)
    return vpy


def setup_sep(mirror: bool, use_cuda: bool) -> Path:
    head("② 分离环境 .venv-sep (BS-RoFormer 钢琴音轨分离)")
    cands = find_pythons()
    pick = pick_python(cands, SEP_MIN, SEP_MAX, prefer=((3, 12), (3, 11), (3, 13), (3, 10)))
    if pick is None:
        warn("未找到 Python >=3.10, 跳过分离环境 (仍可直接对整曲转写)")
        return None
    py, ver = pick
    say(f"  选用 Python {ver[0]}.{ver[1]}: {py}", DIM)
    vpy = make_venv(py, VENV_SEP)
    pip(vpy, ["install", "-U", "pip", "wheel"], "升级 pip/wheel")

    if not use_cuda:
        run([str(vpy), "-m", "pip", "install", "torch", "torchaudio", "--index-url",
             TORCH_CPU_INDEX, "--no-cache-dir"], "安装 torch (CPU 版, 分离会慢约 10 倍)")
    req = SETUP_DIR / "requirements-sep.txt"
    pip(vpy, ["install", "-r", str(req)], "安装 bs-roformer-infer", mirror=mirror)

    exe = VENV_SEP / ("Scripts/bs-roformer-infer.exe" if os.name == "nt" else "bin/bs-roformer-infer")
    if not exe.exists():
        warn(f"未找到 CLI: {exe} (分离功能不可用)")
    else:
        say(f"  ✓ 分离 CLI: {exe}", GREEN)
    if use_cuda:
        r = subprocess.run([str(vpy), "-c", "import torch;print(torch.cuda.is_available())"],
                           capture_output=True, text=True)
        say(f"  CUDA 可用: {r.stdout.strip()}", GREEN if r.stdout.strip() == "True" else YELLOW)
        if r.stdout.strip() != "True":
            warn("CUDA 不可用: 分离将回退 CPU (慢)。驱动正常的话无需处理; "
                 "无独显机器建议重跑 --cpu")
    return vpy


def setup_models(vpy_core: Path, venv_sep_py: Path | None) -> None:
    head("③ 模型权重")
    # 3.1 钢琴转写权重 (必需): 库自带下载走 wget, Windows 没有 → 主动下
    say("  钢琴转写权重 (~165MB, 必需)", DIM)
    r = subprocess.run([str(vpy_core), str(SETUP_DIR / "download_weights.py")],
                       text=True, errors="replace")
    if r.returncode != 0:
        warn("钢琴转写权重下载失败。转写时库会再尝试自动下载(Windows 上可能失败), "
             "可稍后手动重试: python setup/download_weights.py")

    # 3.2 分离模型 (可选)
    if venv_sep_py is None:
        say("  跳过分离模型 (未搭建分离环境)", DIM)
        return
    say("  BS-RoFormer 多轨模型 (~数百MB, 分离用)", DIM)
    exe = VENV_SEP / ("Scripts/bs-roformer-download.exe" if os.name == "nt" else "bin/bs-roformer-download")
    if not exe.exists():
        warn("未找到模型下载器, 跳过 (首次分离时会提示)")
        return
    r = run([str(exe), "--model", "roformer-model-bs-roformer-sw-by-jarredou"],
            "下载 SW 六轨模型", check=False)
    if r.returncode != 0:
        warn("分离模型下载失败(模型来自 GitHub Releases, 常被网络拦截)。可稍后重试, "
             "或参考 README「部署故障排查」手动下载放入 ~/.cache/bs-roformer-infer/。")


def write_env_file(vpy_core: Path, venv_sep_py: Path | None) -> None:
    """写出 .env.local, 供 run.py / 程序自动读取路径。"""
    sep_cli = None
    if venv_sep_py is not None:
        exe = VENV_SEP / ("Scripts/bs-roformer-infer.exe" if os.name == "nt" else "bin/bs-roformer-infer")
        if exe.exists():
            sep_cli = exe
    lines = [
        "# 由 setup 脚本生成: 环境路径覆盖项 (程序会自动读取)",
        f"PSG_PYTHON_SCORE={vpy_core}",
    ]
    if sep_cli:
        lines.append(f"PSG_ROFORMER_CLI={sep_cli}")
    f = ROOT / ".env.local"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    say(f"  ✓ 已写出 {f.name}", GREEN)


def main() -> int:
    ap = argparse.ArgumentParser(description="Piano Sheet Generator 环境搭建")
    ap.add_argument("--core-only", action="store_true", help="只搭主环境")
    ap.add_argument("--sep-only", action="store_true", help="只搭分离环境")
    ap.add_argument("--cpu", action="store_true", help="分离环境也用 CPU 版 torch (无独显)")
    ap.add_argument("--no-models", action="store_true", help="跳过模型下载")
    ap.add_argument("--mirror", action="store_true", default=True,
                    help="使用清华 PyPI 镜像 (默认开)")
    ap.add_argument("--no-mirror", dest="mirror", action="store_false",
                    help="使用官方 PyPI 源")
    args = ap.parse_args()

    t0 = time.time()
    say("Piano Sheet Generator — 环境自动搭建", GREEN)
    say(f"项目目录: {ROOT}", DIM)
    say(f"系统: {'Windows' if os.name == 'nt' else 'Linux'} | 镜像: "
        f"{'清华' if args.mirror else '官方 PyPI'}", DIM)

    do_core = not args.sep_only
    do_sep = not args.core_only

    vpy_core = None
    if do_core:
        vpy_core = setup_core(args.mirror, cpu_torch=True)
    else:
        cand = VENV_SCORE / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        vpy_core = cand if cand.exists() else None

    vpy_sep = setup_sep(args.mirror, use_cuda=not args.cpu) if do_sep else None

    if not args.no_models:
        if vpy_core is None:
            warn("主环境不存在, 跳过权重下载")
        else:
            setup_models(vpy_core, vpy_sep)

    head("④ 完成")
    if vpy_core:
        write_env_file(vpy_core, vpy_sep)
    say()
    if vpy_core:
        say("  启动图形界面:", GREEN)
        if os.name == "nt":
            say("    双击 run_gui.bat    或    .venv-score\\Scripts\\python.exe run.py", DIM)
        else:
            say("    ./run_gui.sh        或    .venv-score/bin/python run.py", DIM)
    if vpy_sep:
        say("  分离能力: 已就绪 (界面里勾选「先分离钢琴音轨」即可)", GREEN)
    else:
        say("  分离能力: 未搭建 (可用 --no-separate 直接对整曲转写)", YELLOW)
    say()
    say(f"  总用时 {time.time()-t0:.0f}s", DIM)
    say()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        say("\n已中断", YELLOW)
        raise SystemExit(130)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        fail(f"{type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        raise SystemExit(1)
