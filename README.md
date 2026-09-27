# 🎹 Piano Sheet Generator — 钢琴音轨分离 · 五线谱自动生成器

从任意音乐文件（mp3 / mp4 / wav / m4a / flac…）中**分离钢琴音轨**，自动**转写为 MIDI**，再**生成可打印的五线谱 PDF**。带图形界面，也可命令行/脚本调用。

```
输入音频 ──► ① 预处理 ──► ② BS-RoFormer 分离钢琴轨 ──► ③ 调性/节拍分析
                                                              │
   PDF 五线谱 ◄── ⑥ verovio 渲染 ◄── ⑤ music21 建谱 ◄── ④ CRNN 钢琴转写 MIDI
```

> ### ⚠️ 免责声明
>
> **本项目用于辅助音乐爱好者快速生成五线谱，不保证五线谱质量。**
>
> 自动转写本质是「听写」而非「读取原谱」，节奏量化、拍号判定、声部分配与踏板处理均为算法推断，结果可能与原曲存在偏差，需人工校对后方可用于正式场合。产物同时输出 MusicXML，可导入 MuseScore / Sibelius 精修。本项目仅供学习交流，请勿用于侵犯他人音乐作品著作权的用途。

---

## ✨ 功能特性

| 功能               | 说明                                                                                        |
| ---------------- | ----------------------------------------------------------------------------------------- |
| 🎚️ **钢琴音轨分离**   | BS-RoFormer 多轨模型（bass/drums/other/vocals/guitar/piano），GPU 分离约 30 秒/首（2 分钟歌曲），CPU 约慢 10 倍 |
| 🎼 **SOTA 钢琴转写** | ByteDance `piano_transcription_inference`（CRNN，Note F1=0.97 / Pedal F1=0.92）              |
| 🧭 **自动音乐分析**    | Krumhansl-Schmuckler 估调、多候选 start_bpm 拍位对齐、强弱轮廓匹配判拍号（2/4、3/4、4/4）、速度估算                    |
| 📐 **专业建谱**      | 真实拍位分段线性映射（抗 rubato 漂移）、C4 自动分大小谱表、右手 1/8 + 左手 1/4 差异化量化、踏板延音零连线、和弦合并                     |
| 📄 **高质量输出**     | A4 竖版 PDF + 逐页 PNG 预览 + MIDI + MusicXML（可导入 MuseScore/Sibelius 二次编辑）                      |
| 🖥️ **图形界面**     | Tkinter：选文件 → 勾选选项 → 一键生成，实时日志 + 六阶段进度指示；完成后可一键打开 PDF / 输出目录 / 试听钢琴轨 / 重新渲染 / 环境自检        |
| ⌨️ **命令行**       | 全参数 CLI，可脚本化批量处理                                                                          |
| 🛡️ **健壮性**      | 中文路径自动规避（verovio 兼容）、假 mp3（AAC 封装）自动转码、分离失败自动降级为直接转写、渲染失败可补救、25 项单元测试                     |

---

## 📁 项目结构（标准 Python 布局）

```
piano-sheet-generator/
├── pyproject.toml              # PEP 621 项目元数据与依赖
├── setup.bat                   # ★ Windows 一键搭建环境(双击运行)
├── setup.sh                    # ★ Linux/macOS 一键搭建环境(转发到 setup/setup.sh)
├── run.py                      # 根启动脚本(自动加 src 到路径, 缺依赖自动转交 .venv-score)
├── run_gui.bat                 # Windows 双击启动 GUI
├── run_gui.sh                  # Linux/macOS 启动 GUI
├── cleanup_test_output.bat     # 清空 output/ 测试产物(需右键以管理员身份运行)
├── setup/                      # ★ 环境自动搭建脚本
│   ├── install.py              #   跨平台安装器核心(探测 Python/建 venv/装依赖/下模型)
│   ├── setup.sh                #   Linux/macOS 入口(检查 venv 模块与 libsndfile)
│   ├── download_weights.py     #   预下载钢琴转写权重(165MB, 规避库内 wget 依赖)
│   ├── requirements-core.txt   #   主环境依赖清单
│   └── requirements-sep.txt    #   分离环境依赖清单
├── README.md                   # 本文档
├── src/
│   └── piano_sheet/            # 主包(src-layout)
│       ├── __init__.py
│       ├── __main__.py         # python -m piano_sheet 入口
│       ├── cli.py              # 命令行参数解析
│       ├── config.py           # 环境自动探测与路径配置(读 .env.local)
│       ├── audio.py            # ①② ffmpeg 转码 + BS-RoFormer 钢琴分离
│       ├── analyze.py          # ③ 调性/拍号/速度/拍位分析
│       ├── transcribe.py       # ④ 音频 -> MIDI (CRNN)
│       ├── score_builder.py    # ⑤ MIDI -> MusicXML (music21)
│       ├── renderer.py         # ⑥ MusicXML -> PDF (+PNG, 全内存渲染)
│       ├── rerender.py         # 渲染失败补救(--rerender)
│       ├── pipeline.py         # 一键编排 + 进度回调 + 取消支持
│       └── gui/
│           ├── __init__.py
│           └── app.py          # Tkinter 图形界面
├── tests/
│   └── test_core.py            # 25 项单元测试(含合成 MIDI 端到端)
├── docs/                       # 扩展文档目录(ALGORITHM.md 等)
└── output/                     # 默认输出目录(按曲名建子文件夹)
```

> `setup` 脚本还会在项目根创建两个虚拟环境（已加入 `.gitignore`）：  
> `.venv-score/`（主环境）、`.venv-sep/`（分离环境）、`.env.local`（自动生成的路径配置）。

---

## 🏗️ 环境搭建与部署（新机器首次部署）

一键脚本自动完成：探测本机 Python → 建虚拟环境 → 装依赖（清华镜像）→ 下载模型权重 → 写 `.env.local`。

| 平台                | 操作                                         |
| ----------------- | ------------------------------------------ |
| **Windows**       | 双击 `setup.bat`（或 cmd 中 `setup.bat`）        |
| **Linux / macOS** | `bash setup.sh`（等价于 `bash setup/setup.sh`） |

搭建两个隔离环境（互不污染系统 Python）：

- `.venv-score` — 主环境（转写/建谱/渲染），**Python 3.10~3.12**，CPU 版 torch
- `.venv-sep` — 分离环境（BS-RoFormer），**Python 3.10~3.13**，默认带 CUDA torch

### 第 1 步：安装系统前置依赖

| 依赖                   | Windows                                      | Debian / Ubuntu                               | Fedora / RHEL                   | macOS (Homebrew)           |
| -------------------- | -------------------------------------------- | --------------------------------------------- | ------------------------------- | -------------------------- |
| Python 3.10~3.12     | `winget install Python.Python.3.11`          | `sudo apt install python3.11 python3.11-venv` | `sudo dnf install python3.12`   | `brew install python@3.11` |
| ffmpeg（可选*）          | `winget install ffmpeg`                      | `sudo apt install ffmpeg`                     | `sudo dnf install ffmpeg`       | `brew install ffmpeg`      |
| libsndfile（Linux 必需） | —                                            | `sudo apt install libsndfile1`                | `sudo dnf install libsndfile`   | 系统自带                       |
| NVIDIA 驱动（可选，GPU 分离） | 官网/`winget install Nvidia.GeForceExperience` | `sudo apt install nvidia-driver-550`          | `sudo dnf install akmod-nvidia` | 不适用                        |

\* 依赖清单里含 `imageio-ffmpeg`（自带静态 ffmpeg 二进制），**未装系统 ffmpeg 也能工作**；装了则优先用系统版。

### 第 2 步：运行 setup 脚本

```bash
setup.bat                 # Windows: 全量搭建(主环境+分离环境+模型权重)
bash setup.sh             # Linux/macOS: 同上
```

常用选项（Windows/Linux 通用）：

```bash
setup.bat --core-only     # 只搭主环境(不需要分离功能, 省 ~4GB)
setup.bat --cpu           # 无独显机器: 分离环境也用 CPU 版 torch(分离慢约 10 倍)
setup.bat --no-models     # 跳过模型下载(网络差时先装依赖, 权重稍后手动下)
setup.bat --no-mirror     # 不用清华镜像, 走官方 PyPI(海外机器)
setup.bat --sep-only      # 只补搭分离环境
```

脚本会自动枚举 conda envs / py launcher / PATH 上的解释器并**避开**无 torch wheel 的 Python 3.14；完成后在项目根写出 `.env.local`，程序启动自动读取，**无需手动配置任何环境变量**。

### 第 3 步：验证部署

```bash
# Windows
.venv-score\Scripts\python.exe run.py --cli --check-env
# Linux/macOS
.venv-score/bin/python run.py --cli --check-env
```

输出应显示：转写解释器、ffmpeg、分离 CLI、转写权重均已找到，末行「可分离钢琴音轨: 是」。然后双击 `run_gui.bat`（或 `./run_gui.sh`）跑一首歌即可。

### 部署故障排查

| 症状                                  | 原因与处置                                                                                                                                                                                                                                                                                   |
| ----------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `No Python found`                   | 未装 Python 3.10~3.12，按上表安装后重试                                                                                                                                                                                                                                                            |
| `Python venv module missing`（Linux） | Debian/Ubuntu 拆包所致：`sudo apt install python3.11-venv`                                                                                                                                                                                                                                   |
| torch 安装时尝试源码编译后失败                  | 被选中了 Python 3.13/3.14（无 wheel）；确认脚本输出的「选用 Python」行，必要时装 3.11 重试                                                                                                                                                                                                                         |
| 分离模型下载失败                            | 模型来自 GitHub Releases（`github.com/TRvlvr/model_repo`）+ `raw.githubusercontent.com`，被网络拦截时会失败。重跑 `setup.bat`；或从可联网机器手动下载 `BS-Rofo-SW-Fixed.ckpt` 与同名 config，放入 `~/.cache/bs-roformer-infer/roformer-model-bs-roformer-sw-by-jarredou/`（目录可用 `BS_ROFORMER_MODELS_PATH` 或 `--output-dir` 改） |
| 转写权重下载失败                            | 重跑 `python setup/download_weights.py`（断点续传）；或从 Zenodo 手动下载放入 `~/piano_transcription_inference_data/`                                                                                                                                                                                    |
| pip `WinError 5`                    | 偶发文件锁；脚本已带 `--no-cache-dir`，重跑即可                                                                                                                                                                                                                                                        |
| Linux 加载音频报 `libsndfile not found`  | `sudo apt install libsndfile1`                                                                                                                                                                                                                                                          |
| CUDA 显示不可用但有独显                      | 驱动过旧或装成 CPU torch；更新驱动后删 `.venv-sep` 重跑 setup（不带 `--cpu`）                                                                                                                                                                                                                               |

### 离线/内网部署

1. 在联网机器上跑完 setup，打包整个项目目录（含 `.venv-score/`、`.venv-sep/`）；
2. 模型权重一并拷贝：`~/piano_transcription_inference_data/`（165MB）与 `~/.cache/bs-roformer-infer/`（数百 MB）；
3. 目标机器需有同版本 Python 与系统库（ffmpeg/libsndfile），venv 路径不同的话删除 `.env.local` 重跑一次 `setup.bat --core-only`（复用已装包，秒级完成）。

> 已有可复用环境的机器（如本机 conda `score` 环境）可跳过 setup，直接看下文快速开始。

---

## 🚀 快速开始

### 方式一：图形界面（推荐）

**Windows 双击** `run_gui.bat`；**Linux/macOS** 执行 `./run_gui.sh`。  
探测顺序：项目 `.venv-score` → `PSG_PYTHON_SCORE` → 已知 conda 环境 → PATH。若用 `python run.py` 启动但当前解释器缺依赖，`run.py` 会自动转交 `.venv-score` 重跑。

界面操作三步：

1. **选择音乐文件**（浏览或直接把文件拖进输入框*）
2. 勾选/填写选项：是否先分离钢琴轨、曲名、作曲者、输出目录、计算设备
3. 点击 **▶ 开始生成五线谱**，观察六阶段进度与实时日志，完成后一键打开 PDF

\* 拖拽需可选安装 `tkinterdnd2`；未安装时手动浏览/粘贴路径即可。

### 方式二：命令行

先设定解释器变量（用 setup 脚本搭好的 `.venv-score`）：

```bat
:: Windows (cmd)
set PY=.venv-score\Scripts\python.exe
```

```bash
# Linux / macOS
PY=.venv-score/bin/python
```

然后运行（Windows 用 `%PY%`，Linux/macOS 用 `$PY`，参数完全一致）：

```bash
# 标准流程: 分离钢琴轨 -> 转写 -> 五线谱 PDF
$PY run.py --cli "river flows in you.mp4" --title "River Flows in You" --composer "Yiruma"

# 纯钢琴独奏可跳过分离(更快)
$PY run.py --cli solo.mp3 --no-separate

# 强制 CPU / 指定输出目录
$PY run.py --cli song.mp3 --device cpu -o /d/scores

# 补救模式: 渲染失败后, 用已保存的 MusicXML 几秒重出 PDF(无需重跑分离/转写)
$PY run.py --cli --rerender "output/曲名/曲名_piano.musicxml" --title "曲名"
$PY run.py --cli --rerender-dir output     # 批量重渲 output 下所有谱

# 环境自检
$PY run.py --cli --check-env

# 安静模式(只输出结果路径)
$PY run.py --cli song.mp3 -q
```

### 方式三：作为库调用

```python
from piano_sheet.pipeline import run_file

result = run_file("song.mp3", output_dir="output", separate=True)
print(result.pdf_path, result.pages, "页")
print(result.analysis.key_name, result.analysis.time_sig, result.analysis.bpm)
```

带进度回调（自定义 UI 集成）：

```python
from piano_sheet import Pipeline, Config

pipe = Pipeline(config=Config.detect(),
                log=print,
                progress=lambda p: print(f"{p.index}/{p.total} {p.label}"))
res = pipe.run("song.mp3", title="曲名")
```

---

## 📦 输出产物

以《River Flows in You》为例，全部落在 `output/<曲名>/`：

| 文件                 | 说明                                             |
| ------------------ | ---------------------------------------------- |
| `*_钢琴谱.pdf`        | **最终交付**：A4 竖版五线谱，大谱表（右手高音谱号 + 左手低音谱号）         |
| `*_钢琴谱_p01.png` …  | PDF 每页的 PNG 预览（页码两位补零），便于快速目检                  |
| `*_piano.wav`      | 分离出的钢琴音轨（可试听验证分离质量；跳过分离时无此文件）                  |
| `*_piano.mid`      | 转写 MIDI（可导入 DAW）                               |
| `*_piano.musicxml` | 乐谱源数据（可导入 MuseScore / Sibelius / Finale 精修）    |
| `RENDER_ERROR.txt` | 仅在渲染失败时生成：完整报错现场（traceback + 环境 + 日志），供补救与反馈问题 |

---

## 🧰 环境要求

### 推荐：setup 脚本自动搭建（见上文「环境搭建」）

`setup.bat` / `setup.sh` 会在项目内创建两个虚拟环境并自动写入 `.env.local`，程序启动时自动读取，无需手动配置任何环境变量。

### 手动方式（复用已有环境）

主环境需要 Python 3.10~3.12（music21 锁 `<10` 以兼容 3.10；verovio/numba 要求 ≥3.10）：

```bash
pip install -e .
# 或手动(与 setup/requirements-core.txt 一致):
# pip install numpy scipy librosa soundfile audioread numba pretty-midi \
#     piano_transcription_inference==0.0.6 "music21>=9.1,<10" verovio svglib \
#     reportlab pypdf pymupdf imageio-ffmpeg "setuptools<81"
```

> 本机已就绪的解释器（未跑 setup 时 `run_gui.bat` 回退使用）：  
> `C:/Users/teacher/AppData/Local/miniconda3/envs/score/python.exe`

### 可选（钢琴音轨分离）

- **BS-RoFormer CLI**：`bs-roformer-infer`（setup 脚本装入 `.venv-sep`，含 torch；`--cpu` 选项装 CPU 版）  
  本机备用路径：`C:/Users/teacher/.workbuddy/binaries/python/envs/roformer/Scripts/bs-roformer-infer.exe`
- **模型缓存**：`~/.cache/bs-roformer-infer/roformer-model-bs-roformer-sw-by-jarredou/`  
  （BS-Rofo-SW-Fixed.ckpt，六轨模型；缺失时用 `bs-roformer-download` 拉取）
- 无 GPU 也能跑（`--device cpu`），但速度约慢 10 倍
- ⚠ 分离环境**不要**用 Python 3.14：torch/torchaudio 尚无对应 wheel（setup 脚本已自动排除，上限 3.13）

### 自动降级策略

| 缺失组件        | 行为                                                                                                                                  |
| ----------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| ffmpeg      | 必需组件，但依赖里的 `imageio-ffmpeg` 自带静态二进制兜底，因此正常安装后即始终可用；仅当两者都缺失时报错退出                                                                     |
| 分离 CLI / 模型 | 跳过分离，直接对整曲转写（纯钢琴曲效果不受影响）                                                                                                            |
| 转写权重 .pth   | `piano_transcription_inference` 首次运行自动联网下载（165MB）；⚠ 库内下载走 `wget`，Windows 无 wget 会静默失败——setup 脚本已用 `setup/download_weights.py` 预下载规避 |


### 环境变量覆盖（可选）

setup 脚本生成的 `.env.local` 会自动加载（真实环境变量优先，不被覆盖）：

| 变量                 | 用途                      |
| ------------------ | ----------------------- |
| `PSG_PYTHON_SCORE` | 指定跑转写/建谱的解释器            |
| `PSG_ROFORMER_CLI` | 指定 bs-roformer-infer 路径 |
| `PSG_FFMPEG`       | 指定 ffmpeg 路径            |
| `PSG_PTI_CKPT`     | 指定钢琴转写权重 .pth           |
| `PSG_WORKDIR`      | 指定 ASCII 临时工作目录         |

---

## ⚙️ 技术要点与已踩过的坑

1. **verovio 不认中文路径**：`loadFile` 静默返回 False。本项目渲染统一用 `loadData(xml字符串)` 规避；中间产物一律落在 ASCII 临时目录。  
   ⚠ 注意 `str.isalnum()` 对中文返回 True，做 ASCII 化时必须同时校验 `isascii()`（单元测试覆盖）。
2. **假 mp3**：不少 .mp3 实为 AAC/MP4 容器（用 `head -c 16 file | xxd` 看到 `ftyp` 即是），音频库直读会炸。本项目所有输入先经 ffmpeg 统一转 44.1kHz WAV。
3. **piano_transcription_inference 的 load_audio 依赖 audioread 后端**，Windows 常 `NoBackendError`。本项目用 `soundfile + librosa.resample(16000)` 手动加载绕过。
4. **固定网格量化会随 rubato 累积漂移**（实测 91 小节漂移 >1s，越往后越错位）。本项目采用 **t_to_bar 分段线性映射**：多候选 `beat_track` 取拍位 → 强弱轮廓互相关定强拍相位 → downbeat 数组上 searchsorted + 线性插值。
5. **踏板延音 ≠ 连线**：钢琴转写 90%+ 音符带同音重叠（延音踏板效果）。处方：同音高近距合并（gap<0.1s）→ 量化到网格同位合并和弦 → 长音截断到下一音符起始 → **钢琴声部不写 tie**（按记谱惯例延音归踏板符号）。
6. **右手 1/8、左手 1/4 差异化量化**：伴奏粗粒度天然无符杠、谱面干净；旋律保持精度。
7. **music21 Part 嵌套 PartStaff 会 "no measures found"**：必须用扁平 Part 列表 + `layout.StaffGroup(brace)` 合并大谱表。
8. **PNG 预览不能用 svglib renderPM**（谱面字体 ZeroDivisionError）：先出 PDF，再用 pymupdf `get_pixmap` 栅格化。
9. **六轨模型的 `_instrumental` 轨是所有乐器相加**（漏人声），本项目直接取 `_piano` 轨，不受此坑影响。
10. **svglib/reportlab 无 CJK 字体**：中文标题在 verovio+svglib 链路里被静默丢弃。对策：verovio `header:none`，标题用 pymupdf `insert_font(msyh.ttc) + insert_text` 叠加（实测墨迹像素 65→1898）。⚠ `get_text_length` 是模块级函数不认页面注册的字体别名，测宽必须用 `pymupdf.Font(fontfile=...).text_length()`。
11. **逐页落盘临时 PDF 再重开有 Windows 文件锁风险**（写在中文输出目录里更甚）：改为 `renderPDF.drawToString(drawing)` 返回 bytes + `PdfReader(BytesIO)` 全程内存，渲染失败自动重试 2 次，PDF 被占用时给出明确中文提示。
12. **渲染失败不等于白跑**：流水线在渲染前就把 MIDI/MusicXML/钢琴轨落盘输出目录，失败时自动写 `RENDER_ERROR.txt` 诊断文件，GUI 亮起「♻ 重新渲染 PDF」按钮、CLI 提供 `--rerender`，几秒钟补救，无需重跑几分钟的分离/转写。

---

## ⏱️ 性能参考（RTX A4000 实测）

以一首约 2 分钟（126s）的钢琴曲《River Flows in You》为例：

| 阶段           | 耗时     | 说明 |
| ------------ | ------ | ---- |
| 预处理（转码）      | ~2s    | ffmpeg |
| 钢琴分离（GPU）    | ~30s   | CPU 分离约慢 10 倍 |
| 分析（调性/拍位）    | ~5s    | |
| CRNN 转写（CPU） | ~100s  | 约音频时长的 0.5~1 倍 |
| 建谱 + 渲染      | ~15s   | |
| **合计**       | **约 2.5 分钟** | 3~4 分钟歌曲约 3~4 分钟 |

---

## 🧪 测试

```bash
$PY -m pytest tests/ -q        # 25 项测试: 配置校验/估调/拍位映射/量化/中文标题PDF/合成MIDI端到端
```


覆盖：环境探测、中文路径 ASCII 化、Krumhansl 估调、强拍相位判定、BarMapper 抗 rubato 特性、和弦合并/截断/跨小节拆分、合成 MIDI 全链路建谱渲染。

---

## ❓ FAQ

**Q: 谱子和原曲有出入？**  
自动转写是「听写」而非「读原谱」，节奏量化和踏板处理会有取舍。产物含 MusicXML，导入 MuseScore 可快速手工精修。

**Q: 歌曲不是钢琴为主怎么办？**  
分离出的钢琴轨若 RMS 过低，日志会提示「接近静音」。纯人声清唱、纯电子乐等无钢琴声部的曲子不适合本工具。

**Q: 谱面太碎/连线太多？**  
默认处方已是简练化配置（左手 1/4 网格 + 零连线）。仍嫌碎可改 `score_builder.py` 中 RH 网格为 1/4（`rh_slots = epm8 // 2`）。

**Q: 支持视频文件吗？**  
支持。mp4/mov/mkv 自动抽取音轨（端到端验证即用 .mp4 完成）。

**Q: 没有 GPU？**  
`--device cpu` 强制 CPU 分离（约慢 10 倍）；或关闭「分离」选项直接转写。CRNN 转写本身默认 CPU。

**Q: 渲染 PDF 失败怎么办？**  
分离/转写/建谱的成果（MIDI + MusicXML + 钢琴轨）在渲染前就已保存到输出目录，不会白跑：

- 界面：点击「♻ 重新渲染 PDF」按钮，几秒完成
- 命令行：`python run.py --cli --rerender "output/曲名/曲名_piano.musicxml"`
- 诊断：输出目录下的 `RENDER_ERROR.txt` 记录了完整报错，反馈问题时请附上它
- 常见原因：目标 PDF 正被阅读器/预览窗格占用（关闭后重试即可）

---

## 📜 License 与免责声明

代码以 **MIT** 许可发布 — 自由使用、修改、分发。

**免责声明**：本项目用于辅助音乐爱好者快速生成五线谱，不保证五线谱质量。自动转写结果为算法推断，可能与原曲存在偏差，请人工校对后使用；请勿将本项目用于侵犯他人音乐作品著作权的用途。第三方模型（BS-RoFormer、piano_transcription_inference）与字体的使用请遵循其各自的许可条款。
