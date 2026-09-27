# -*- coding: utf-8 -*-
"""项目根启动脚本: 双击或 `python run.py` 即可打开 GUI。

自动把 src/ 加入 sys.path, 无需 pip install -e。
若当前解释器缺少依赖, 会按顺序尝试:
  1. 项目内 .venv-score (setup 脚本创建) —— 自动转交该解释器重新执行;
  2. 环境变量 PSG_PYTHON_SCORE 指向的解释器;
都不存在则提示先运行 setup.bat (Windows) 或 setup/setup.sh (Linux/macOS)。
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

_NEED = ("numpy", "librosa", "music21", "verovio", "svglib", "pypdf",
         "piano_transcription_inference", "pretty_midi", "soundfile")


def _deps_ok() -> bool:
    for m in _NEED:
        try:
            __import__(m)
        except ImportError:
            return False
    return True


def _fallback_pythons():
    """返回可以重试的解释器路径 (按优先级)。"""
    out = []
    for cand in (ROOT / ".venv-score" / "Scripts" / "python.exe",   # Windows
                 ROOT / ".venv-score" / "bin" / "python",           # POSIX
                 ROOT / "venv" / "Scripts" / "python.exe",
                 ROOT / "venv" / "bin" / "python"):
        if cand.exists():
            out.append(str(cand))
    env = os.environ.get("PSG_PYTHON_SCORE")
    if env and Path(env).exists():
        out.append(env)
    return out


def _reexec(argv):
    for py in _fallback_pythons():
        if Path(py).resolve() == Path(sys.executable).resolve():
            continue
        try:
            return subprocess.call([py, str(ROOT / "run.py")] + argv)
        except OSError:
            continue
    return None


def main():
    if "--cli" in sys.argv:
        sys.argv.remove("--cli")
        from piano_sheet.cli import main as cli_main
        raise SystemExit(cli_main(sys.argv[1:]))

    if not _deps_ok():
        # 先尝试用已配置好的环境重跑一次 (对用户透明)
        rc = _reexec(sys.argv[1:])
        if rc is not None:
            raise SystemExit(rc)

        msg = (
            "当前 Python 解释器缺少运行依赖。\n\n"
            "请先搭建环境 (二选一):\n"
            "  Windows      双击  setup.bat\n"
            "  Linux/macOS  执行  bash setup/setup.sh\n\n"
            "或使用已配置好的解释器运行:\n"
            '  "<env-python>" run.py\n'
            "  (也可设置环境变量 PSG_PYTHON_SCORE 指向它)"
        )
        print(msg)
        try:
            import tkinter as tk
            from tkinter import messagebox
            r = tk.Tk(); r.withdraw()
            messagebox.showerror("缺少依赖", msg)
            r.destroy()
        except Exception:
            pass
        raise SystemExit(1)

    from piano_sheet.gui.app import main as gui_main
    gui_main()


if __name__ == "__main__":
    main()
