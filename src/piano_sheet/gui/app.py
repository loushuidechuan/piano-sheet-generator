# -*- coding: utf-8 -*-
"""Tkinter 图形界面。

特性:
    - 拖拽/浏览选择音频文件(mp3/mp4/wav/m4a/flac...)
    - 可勾选「先分离钢琴音轨」(需 BS-RoFormer 环境) / 输出目录
    - 后台线程跑流水线, 实时日志 + 阶段进度条
    - 完成后可一键打开 PDF / 输出目录 / 播放钢琴轨
    - 环境自检面板, 缺什么一目了然

运行:  python -m piano_sheet.gui   (或项目根 run_gui.bat / run_gui.py)
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

# 允许直接 python src/piano_sheet/gui/app.py 运行
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from piano_sheet.config import Config, ConfigError, _SUPPORTED_INPUT
    from piano_sheet.pipeline import Pipeline, PipelineError, STAGE_LABELS
else:
    from ..config import Config, ConfigError, _SUPPORTED_INPUT
    from ..pipeline import Pipeline, PipelineError, STAGE_LABELS

APP_TITLE = "钢琴音轨分离 · 五线谱生成器"
SUPPORTED_PATTERN = [("音频/视频文件", " ".join(f"*{e}" for e in sorted(_SUPPORTED_INPUT))),
                     ("所有文件", "*.*")]

_ACCENT = "#2f6f4e"
_BG = "#f6f7f5"
_FG = "#22272a"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("900x680")
        self.minsize(760, 560)
        self.configure(bg=_BG)

        self._q: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._stop_flag = threading.Event()
        self._result = None
        self._config: Config | None = None

        self._build_ui()
        self.after(120, self._drain_queue)
        self.threading = threading
        self._detect_env_async()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TFrame", background=_BG)
        style.configure("TLabel", background=_BG, foreground=_FG, font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", background=_BG, foreground=_ACCENT,
                        font=("Microsoft YaHei UI", 15, "bold"))
        style.configure("Hint.TLabel", background=_BG, foreground="#6b7280",
                        font=("Microsoft YaHei UI", 9))
        style.configure("Disclaimer.TLabel", background=_BG, foreground="#8a6d3b",
                        font=("Microsoft YaHei UI", 9))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=6)
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 11, "bold"),
                        foreground="white", background=_ACCENT, padding=10)
        style.map("Accent.TButton", background=[("active", "#245a3e"), ("disabled", "#9db8a8")])
        style.configure("TCheckbutton", background=_BG, foreground=_FG, font=("Microsoft YaHei UI", 10))
        style.configure("TEntry", fieldbackground="white")
        style.configure("Horizontal.TProgressbar", troughcolor="#e4e7e2", background=_ACCENT, thickness=16)

        pad = dict(padx=14, pady=6)

        # 标题
        head = ttk.Frame(self)
        head.pack(fill="x", **pad)
        ttk.Label(head, text="🎹 钢琴音轨分离 → 五线谱 PDF", style="Title.TLabel").pack(anchor="w")
        ttk.Label(head, text="音频/视频 → 分离钢琴声部 → 转写 MIDI → 自动建谱 → 可打印 PDF",
                  style="Hint.TLabel").pack(anchor="w")
        ttk.Label(head, text="⚠ 本项目用于辅助音乐爱好者快速生成五线谱，不保证五线谱质量；产物需人工校对。",
                  style="Disclaimer.TLabel").pack(anchor="w", pady=(4, 0))

        # 文件选择
        box = ttk.LabelFrame(self, text=" 1. 选择音乐文件 ", padding=10)
        box.pack(fill="x", **pad)
        row = ttk.Frame(box); row.pack(fill="x")
        self.var_path = tk.StringVar()
        self.ent_path = ttk.Entry(row, textvariable=self.var_path)
        self.ent_path.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(row, text="浏览…", command=self._pick_file).pack(side="left")

        self.ent_path.bind("<Return>", lambda e: self._run())
        # 拖拽提示(无 tkinterdnd2 时退化为手动粘贴路径)
        self._setup_dnd(self.ent_path)

        # 选项
        opt = ttk.LabelFrame(self, text=" 2. 选项 ", padding=10)
        opt.pack(fill="x", **pad)
        self.var_separate = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, variable=self.var_separate,
                        text="先用 BS-RoFormer 分离钢琴音轨（推荐；多乐器/带人声的曲子必开）").grid(
                        row=0, column=0, columnspan=3, sticky="w")

        ttk.Label(opt, text="曲名:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.var_title = tk.StringVar()
        ttk.Entry(opt, textvariable=self.var_title, width=28).grid(row=1, column=1, sticky="w", pady=(8, 0))
        ttk.Label(opt, text="（留空则用文件名）", style="Hint.TLabel").grid(row=1, column=2, sticky="w", pady=(8, 0))

        ttk.Label(opt, text="作曲:").grid(row=2, column=0, sticky="w")
        self.var_composer = tk.StringVar()
        ttk.Entry(opt, textvariable=self.var_composer, width=28).grid(row=2, column=1, sticky="w")

        ttk.Label(opt, text="输出目录:").grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.var_outdir = tk.StringVar(value=str(Path.cwd() / "output"))
        orow = ttk.Frame(opt); orow.grid(row=3, column=1, columnspan=2, sticky="we", pady=(8, 0))
        ttk.Entry(orow, textvariable=self.var_outdir).pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(orow, text="选择…", command=self._pick_outdir).pack(side="left")

        ttk.Label(opt, text="计算设备:").grid(row=4, column=0, sticky="w", pady=(8, 0))
        self.var_device = tk.StringVar(value="auto")
        drow = ttk.Frame(opt); drow.grid(row=4, column=1, columnspan=2, sticky="w", pady=(8, 0))
        for txt, val in (("自动 (有GPU用GPU)", "auto"), ("CPU", "cpu"), ("CUDA", "cuda")):
            ttk.Radiobutton(drow, text=txt, value=val, variable=self.var_device).pack(side="left", padx=(0, 12))

        # 运行区
        run = ttk.Frame(self)
        run.pack(fill="x", **pad)
        self.btn_run = ttk.Button(run, text="▶  开始生成五线谱", style="Accent.TButton", command=self._run)
        self.btn_run.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.btn_stop = ttk.Button(run, text="■ 停止", command=self._stop, state="disabled")
        self.btn_stop.pack(side="left")

        # 阶段指示
        stage_frame = ttk.Frame(self)
        stage_frame.pack(fill="x", padx=14, pady=(2, 0))
        self.stage_labels = {}
        for i, (k, lab) in enumerate(STAGE_LABELS.items()):
            f = tk.Frame(stage_frame, bg="#e4e7e2", padx=6, pady=3)
            f.pack(side="left", padx=(0, 4))
            lb = tk.Label(f, text=f"{i+1}.{lab}", bg="#e4e7e2", fg="#6b7280",
                          font=("Microsoft YaHei UI", 8))
            lb.pack()
            self.stage_labels[k] = (f, lb)

        self.progress = ttk.Progressbar(self, mode="determinate", maximum=100)
        self.progress.pack(fill="x", padx=14, pady=(8, 2))
        self.var_status = tk.StringVar(value="就绪")
        ttk.Label(self, textvariable=self.var_status, style="Hint.TLabel").pack(anchor="w", padx=16)

        # 日志
        logbox = ttk.LabelFrame(self, text=" 运行日志 ", padding=6)
        logbox.pack(fill="both", expand=True, **pad)
        self.txt_log = tk.Text(logbox, height=12, wrap="word", bg="#ffffff", fg="#1f2421",
                               font=("Consolas", 9), relief="flat", state="disabled")
        self.txt_log.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(logbox, command=self.txt_log.yview)
        sb.pack(side="right", fill="y")
        self.txt_log.configure(yscrollcommand=sb.set)
        for t, c in (("ok", "#1a7f37"), ("warn", "#b45309"), ("err", "#b91c1c"),
                     ("stage", "#2f6f4e"), ("dim", "#6b7280")):
            self.txt_log.tag_configure(t, foreground=c)
        self.txt_log.tag_configure("stage", font=("Consolas", 9, "bold"))

        # 结果按钮
        res = ttk.Frame(self)
        res.pack(fill="x", padx=14, pady=(0, 12))
        self.btn_pdf = ttk.Button(res, text="📄 打开 PDF", command=self._open_pdf, state="disabled")
        self.btn_pdf.pack(side="left", padx=(0, 6))
        self.btn_dir = ttk.Button(res, text="📁 打开输出目录", command=self._open_outdir, state="disabled")
        self.btn_dir.pack(side="left", padx=(0, 6))
        self.btn_play = ttk.Button(res, text="🔊 试听钢琴轨", command=self._play_piano, state="disabled")
        self.btn_play.pack(side="left", padx=(0, 6))
        self.btn_rerender = ttk.Button(res, text="♻ 重新渲染 PDF", command=self._rerender, state="disabled")
        self.btn_rerender.pack(side="left", padx=(0, 6))
        self.btn_env = ttk.Button(res, text="🔧 环境自检", command=self._show_env)
        self.btn_env.pack(side="right")

    # ------------------------------------------------------------------ 拖拽(可选)
    def _setup_dnd(self, widget):
        try:
            from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore  # noqa: F401
        except Exception:
            return
        try:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:
            pass

    def _on_drop(self, event):
        data = event.data.strip()
        if data.startswith("{"):
            data = data[1:data.rfind("}")]
        self.var_path.set(data.split("} {")[0] if "} {" in data else data)

    # ------------------------------------------------------------------ 动作
    def _pick_file(self):
        p = filedialog.askopenfilename(title="选择音乐文件", filetypes=SUPPORTED_PATTERN)
        if p:
            self.var_path.set(p)
            if not self.var_title.get():
                self.var_title.set(Path(p).stem)

    def _pick_outdir(self):
        p = filedialog.askdirectory(title="选择输出目录")
        if p:
            self.var_outdir.set(p)

    def _log(self, msg, tag=None):
        self.txt_log.configure(state="normal")
        auto_tag = None
        if tag is None:
            if "✅" in msg or "完成" in msg:
                auto_tag = "ok"
            elif "⚠" in msg:
                auto_tag = "warn"
            elif "失败" in msg or "错误" in msg or "Error" in msg:
                auto_tag = "err"
            elif msg.startswith("▶"):
                auto_tag = "stage"
        self.txt_log.insert("end", msg + "\n", tag or auto_tag or ())
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

    def _run(self):
        if self._worker and self._worker.is_alive():
            return
        # ⚠ 所有 Tk 变量必须在主线程读取, 再传给工作线程
        path = self.var_path.get().strip().strip('"')
        if not path:
            messagebox.showwarning(APP_TITLE, "请先选择音乐文件")
            return
        if not Path(path).exists():
            messagebox.showerror(APP_TITLE, f"文件不存在:\n{path}")
            return

        job = {
            "path": path,
            "outdir": self.var_outdir.get().strip() or None,
            "title": self.var_title.get().strip(),
            "composer": self.var_composer.get().strip(),
            "separate": bool(self.var_separate.get()),
            "device": self.var_device.get(),
        }

        self._result = None
        self._last_pdf = None
        self._recover_xml = None
        self._stop_flag.clear()
        self.txt_log.configure(state="normal"); self.txt_log.delete("1.0", "end"); self.txt_log.configure(state="disabled")
        self.btn_run.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        for b in (self.btn_pdf, self.btn_dir, self.btn_play, self.btn_rerender):
            b.configure(state="disabled")
        self.progress.configure(value=0)
        self._reset_stage_chips()
        self.var_status.set("正在检测运行环境…")
        self._log("正在检测运行环境（解释器 / ffmpeg / 分离模型 / 转写权重）…", "dim")

        self._worker = threading.Thread(target=self._work, args=(job,), daemon=True)
        self._worker.start()

    def _reset_stage_chips(self):
        for f, lb in self.stage_labels.values():
            f.configure(bg="#e4e7e2")
            lb.configure(bg="#e4e7e2", fg="#6b7280")

    def _mark_stage(self, key, state):
        if key not in self.stage_labels:
            return
        f, lb = self.stage_labels[key]
        colors = {"run": ("#d9ead9", "#2f6f4e"), "done": ("#2f6f4e", "white"),
                  "fail": ("#f5c2c0", "#b91c1c"), "skip": ("#e4e7e2", "#9aa0a6")}
        bg, fg = colors.get(state, ("#e4e7e2", "#6b7280"))
        f.configure(bg=bg); lb.configure(bg=bg, fg=fg)

    def _work(self, job: dict):
        # 环境探测在工作线程做(含子进程调用), 避免卡住 UI
        try:
            cfg = Config.detect(output_dir=job["outdir"])
        except ConfigError as e:
            self._q.put(("fatal", f"环境检测失败: {e}"))
            return
        except Exception as e:  # noqa: BLE001
            self._q.put(("fatal", f"环境检测异常: {type(e).__name__}: {e}"))
            return

        self._q.put(("config", cfg))
        cfg.separate = job["separate"]

        pipe = Pipeline(config=cfg,
                        log=lambda m: self._q.put(("log", m)),
                        progress=lambda p: self._q.put(("progress", p)),
                        should_stop=lambda: self._stop_flag.is_set())
        try:
            res = pipe.run(job["path"], title=job["title"],
                           composer=job["composer"], device=job["device"])
            self._q.put(("result", res))
        except PipelineError as e:
            self._q.put(("error", (e.stage, str(e),
                                   str(e.recoverable_xml) if e.recoverable_xml else None,
                                   job.get("title", ""), job.get("composer", ""))))
        except Exception as e:  # noqa: BLE001
            import traceback
            self._q.put(("error", ("?", f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=4)}",
                                   None, "", "")))

    # ------------------------------------------------------------------ 补救: 仅重渲染
    def _rerender(self):
        """渲染阶段失败后的一键补救: 用已保存的 MusicXML 重出 PDF, 几秒完成。"""
        xml = getattr(self, "_recover_xml", None)
        if not xml or not Path(xml).exists():
            messagebox.showwarning(APP_TITLE, "没有可用的 MusicXML 产物, 请重新运行完整流程")
            return
        if self._worker and self._worker.is_alive():
            return
        title = self.var_title.get().strip()
        composer = self.var_composer.get().strip()
        self.btn_rerender.configure(state="disabled")
        self.btn_run.configure(state="disabled")
        self.var_status.set("正在重新渲染 PDF…")
        self._log(f"♻ 重新渲染: {xml}", "stage")
        self._worker = threading.Thread(target=self._rerender_work,
                                        args=(xml, title, composer), daemon=True)
        self._worker.start()

    def _rerender_work(self, xml: str, title: str, composer: str):
        try:
            from ..rerender import rerender_one
        except ImportError:
            from piano_sheet.rerender import rerender_one
        try:
            pdf = rerender_one(Path(xml), title=title, composer=composer, png_preview=True)
            self._q.put(("rerender_ok", pdf))
        except Exception as e:  # noqa: BLE001
            import traceback
            self._q.put(("rerender_fail",
                         f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=4)}"))

    def _stop(self):
        self._stop_flag.set()
        self._log("⏹ 正在请求停止（当前阶段结束后生效）…", "warn")

    def _drain_queue(self):
        try:
            while True:
                kind, payload = self._q.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "progress":
                    self.progress.configure(value=payload.fraction * 100)
                    self.var_status.set(f"阶段 {payload.index}/{payload.total}: {payload.label} {payload.message}".rstrip())
                    self._mark_stage(payload.stage, "run")
                    for i, k in enumerate(STAGE_LABELS):
                        if i < payload.index - 1 and self.stage_labels[k][1]["fg"] != "white":
                            self._mark_stage(k, "done")
                elif kind == "result":
                    self._on_result(payload)
                elif kind == "config":
                    self._config = payload
                    self._env_text = payload.describe()
                    self._log(payload.describe(), "dim")
                    if not payload.can_separate and self.var_separate.get():
                        self._log("⚠ 未检测到 ffmpeg 或分离模型，本次将跳过分离直接转写。", "warn")
                    self.var_status.set("环境就绪，开始处理…")
                elif kind == "fatal":
                    self._on_fatal(payload)
                elif kind == "error":
                    self._on_error(payload)
                elif kind == "rerender_ok":
                    self._on_rerender_ok(payload)
                elif kind == "rerender_fail":
                    self._on_rerender_fail(payload)
                elif kind == "env":
                    self._env_ready(payload)
        except queue.Empty:
            pass
        self.after(120, self._drain_queue)

    def _on_result(self, res):
        self._result = res
        self.progress.configure(value=100)
        for k in STAGE_LABELS:
            self._mark_stage(k, "done")
        if not res.separated:
            self._mark_stage("separate", "skip")
        self.var_status.set(f"完成 ✅  {res.pages} 页 · {res.n_notes} 音符 · {res.n_measures} 小节 · 用时 {res.elapsed:.0f}s")
        self.btn_run.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.btn_pdf.configure(state="normal")
        self.btn_dir.configure(state="normal")
        self.btn_play.configure(state="normal" if res.piano_wav else "disabled")
        self._log(f"🎉 已生成: {res.pdf_path}", "ok")
        if messagebox.askyesno(APP_TITLE, f"生成完成！\n\n{res.pdf_path.name}\n{res.pages} 页 · {res.n_notes} 音符 · 用时 {res.elapsed:.0f} 秒\n\n立即打开 PDF？"):
            self._open_pdf()

    def _on_error(self, payload):
        # 兼容 5 元组 (stage, msg, recover_xml, title, composer)
        if len(payload) >= 5:
            stage, msg, recover_xml, _title, _composer = payload[:5]
        else:
            stage, msg = payload[0], payload[1]
            recover_xml = None
        if stage in STAGE_LABELS:
            self._mark_stage(stage, "fail")
        self.btn_run.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        if "取消" in msg:
            self.var_status.set("已停止")
            self._log("⏹ 已停止", "warn")
            return
        self.var_status.set(f"失败: {stage}")
        self._log(f"❌ [{stage}] {msg}", "err")
        if recover_xml:
            self._recover_xml = recover_xml
            self.btn_rerender.configure(state="normal")
            self._log("♻ 已启用「重新渲染 PDF」按钮：几分钟的分离/转写成果已保存，"
                      "点击即可几秒内补救出 PDF。", "stage")
        messagebox.showerror(APP_TITLE, f"处理失败（阶段: {stage}）\n\n{msg}")

    def _on_fatal(self, msg: str):
        """流水线启动前的致命错误(环境不可用), 恢复界面并弹窗。"""
        self.btn_run.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.var_status.set("无法启动: 环境检测失败")
        self.progress.configure(value=0)
        self._log(f"❌ {msg}", "err")
        messagebox.showerror(APP_TITLE, f"{msg}\n\n点击「🔧 环境自检」查看详情，"
                                        f"或阅读 README.md 的「环境要求」章节。")

    def _on_rerender_ok(self, pdf_path):
        self.btn_run.configure(state="normal")
        self.btn_rerender.configure(state="disabled")
        self._mark_stage("render", "done")
        self._last_pdf = Path(pdf_path)
        self.btn_pdf.configure(state="normal")
        self.var_status.set("重新渲染完成 ✅")
        self._log(f"♻ 重新渲染成功: {pdf_path}", "ok")
        if messagebox.askyesno(APP_TITLE, f"重新渲染成功！\n\n{Path(pdf_path).name}\n\n立即打开 PDF？"):
            self._open_path(pdf_path)

    def _on_rerender_fail(self, msg):
        self.btn_run.configure(state="normal")
        self.btn_rerender.configure(state="normal")
        self.var_status.set("重新渲染失败")
        self._log(f"❌ 重新渲染失败: {msg}", "err")
        messagebox.showerror(APP_TITLE, f"重新渲染失败:\n\n{msg}")

    # ------------------------------------------------------------------ 结果动作
    def _open_path(self, p):
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(p))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(p)])
            else:
                subprocess.Popen(["xdg-open", str(p)])
        except Exception as e:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, f"打开失败: {e}")

    def _open_pdf(self):
        # 优先用补救渲染产出的 PDF, 其次是完整流水线的结果
        last = getattr(self, "_last_pdf", None)
        if last and Path(last).exists():
            self._open_path(last)
        elif self._result:
            self._open_path(self._result.pdf_path)

    def _open_outdir(self):
        p = self._result.output_dir if self._result else Path(self.var_outdir.get() or ".")
        self._open_path(p)

    def _play_piano(self):
        if self._result and self._result.piano_wav:
            self._open_path(self._result.piano_wav)

    # ------------------------------------------------------------------ 环境自检
    def _detect_env_async(self):
        # ⚠ Tkinter 变量只能在主线程读取: 先在主线程取值, 再交给工作线程
        outdir = self.var_outdir.get().strip() or None
        threading.Thread(target=self._detect_env, args=(outdir,), daemon=True).start()

    def _detect_env(self, outdir=None):
        try:
            cfg = Config.detect(output_dir=outdir)
            self._q.put(("env", cfg))
        except Exception as e:  # noqa: BLE001
            self._q.put(("env", f"环境检测失败: {type(e).__name__}: {e}"))

    def _env_ready(self, payload):
        if isinstance(payload, Config):
            self._config = payload
            self.var_status.set("环境就绪" if payload.can_separate else "环境部分就绪（无法分离，将直接转写）")
            self._env_text = payload.describe()
            if not payload.can_separate:
                self._log("⚠ 未检测到分离模型 CLI 或 ffmpeg，「分离钢琴音轨」将不可用；仍可勾选关闭分离直接转写。", "warn")
        else:
            self._env_text = str(payload)
            self._log("❌ " + str(payload), "err")

    def _show_env(self):
        win = tk.Toplevel(self)
        win.title("环境自检")
        win.geometry("720x420")
        win.configure(bg=_BG)
        txt = tk.Text(win, wrap="word", bg="white", font=("Consolas", 10), relief="flat")
        txt.pack(fill="both", expand=True, padx=12, pady=12)
        txt.insert("end", getattr(self, "_env_text", "尚未检测…"))
        txt.insert("end", "\n\n—— 环境变量覆盖项 ——\n")
        txt.insert("end", "PSG_PYTHON_SCORE / PSG_ROFORMER_CLI / PSG_FFMPEG / PSG_PTI_CKPT / PSG_WORKDIR\n")
        txt.configure(state="disabled")
        ttk.Button(win, text="关闭", command=win.destroy).pack(pady=(0, 12))


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
