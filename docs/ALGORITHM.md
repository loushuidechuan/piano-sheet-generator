# 技术文档 — 算法流水线详解

本文档面向想理解或修改内部算法的开发者。使用指南请看根目录 [README.md](../README.md)。

> ⚠️ **免责声明**: 本项目用于辅助音乐爱好者快速生成五线谱，不保证五线谱质量。  
> 下文所有"验收标准"仅是本项目的实测参考基线，不构成对任意输入音频的输出质量承诺。

## 流水线总览

```
输入 (mp3/mp4/wav/…)
  │
  ├─① prepare    audio.transcode()          ffmpeg → 44.1kHz WAV (立体声 + 单声道)
  ├─② separate   audio.separate_piano()     BS-RoFormer 六轨模型 → *_piano.wav
  ├─③ analyze    analyze.analyze()          调性 / 拍位 / 拍号 / 速度
  ├─④ transcribe transcribe.transcribe()    CRNN → MIDI (音符 + 踏板事件)
  ├─⑤ build      score_builder.build_musicxml()  music21 → MusicXML 大谱表
  └─⑥ render     renderer.render_pdf()      verovio → SVG → PDF (+PNG)
```

编排入口: `pipeline.Pipeline.run()`。每阶段有独立的异常类型与降级路径,  
GUI 通过 `log` / `progress` 回调与 `should_stop` 闭包与流水线通信(线程安全, 队列中转)。

---

## ③ 音乐分析 (analyze.py)

### 调性: Krumhansl-Schmuckler

`chroma_cqt` 时间平均 → 与 24 个大小调轮廓模板逐一做归一化点积 → 取最大。  
实测《River Flows in You》判为 A major, 相关度 0.984 (其关系小调 F#m 亦 3 个升号, 谱面通用)。

### 拍位: 多候选 start_bpm 择优 ⚠ 关键设计

`librosa.beat.beat_track` 的 `start_bpm` 决定锁定哪个**脉冲层级**:

- 给 120 → 可能锁到附点四分 (143.6 bpm), 每小节只 3 拍 → 拍号被误判成 6/8
- 正确做法: 用 [60, 76, 92, 108, 126, 148, 176] 七个候选分别跑,  
  打分 `-CV - 0.004·|锁定bpm - 候选bpm|`, 只接受 55–200 bpm 区间, 取最优

实测: 92 候选胜出, 锁到 95.7 bpm 四分脉冲, CV=0.050。

### 拍号: 强弱轮廓模板匹配

小节内拍点的 onset 强度按 k∈{2,3,4} 分组取均值, 与理论强弱轮廓  
(4/4 = 强-弱-次强-弱 = [1.0, 0.35, 0.6, 0.35]) 做循环移位归一化互相关, 乘拍号先验 (4/4×1.06)。

**输出的拍号只有 2/4、3/4、4/4 三种**（拍脉冲已统一锁为四分音符）。6/8 类复合拍曲子会归入 3/4 记谱（每小节 3 个四分脉冲，听感等价）；证据不足时回退 4/4。`score_builder._EPM8` 中保留 6/8、5/4 等映射仅作防御（外部传入这些拍号时建谱不崩）。

> 旧版用 (max−mean)/std 打分, 分组数越多天然越大, 6/8 被系统性偏爱 — 已废弃并有回归测试锁定。

### 强拍相位

同样用轮廓互相关的最佳移位定相位, 而非简单 argmax (argmax 在轮廓平坦时会选错)。

---

## ④ 钢琴转写 (transcribe.py)

ByteDance `piano_transcription_inference` (CRNN, Note F1=0.9677 / Pedal F1=0.9186)。

- ⚠ 不用库自带 `load_audio` (audioread 后端在 Windows 常 NoBackendError),  
  改 `soundfile.read + librosa.resample(16000)` 手动喂 float32 单声道
- `segment_samples=160000` (10s 窗) 控制内存; CPU 推理约 0.8~1.5 倍音频时长
  (实测《River Flows in You》126s 音频约 100s)
- 权重 .pth (165MB) 优先取本机缓存路径, 缺失时库会自动下载(走 wget, Windows 常失败,
  setup 脚本用 download_weights.py 预下载规避)

---

## ⑤ 建谱 (score_builder.py) — 核心算法

### 1. t_to_bar 分段线性映射 (BarMapper)

真实演奏有 rubato, 固定时间网格量化会**累积漂移** (实测 91 小节漂 >1s)。  
做法: 在 downbeat 数组上 `searchsorted` + 线性插值, 时间 → 小节浮点位置;  
首拍向前外推覆盖音乐起点, 末端用中位小节长外推。

### 2. 音符清理链

以《River Flows in You》实测数据为例（数值随曲目而异）：

```
raw 1142 → 时长≥0.06s & velocity≥25 → 1063
        → 同音高近距合并 (gap<0.10s, 消踏板残响/重复击键) → RH 373 + LH 260
```

阈值常量: `score_builder.MIN_DUR` / `MIN_VELOCITY` / `SPLIT_MIDI`。

### 3. 差异化量化

- C4 (MIDI 60) 分大小谱表
- **RH 1/8 网格** (旋律保精度), **LH 1/4 网格** (伴奏粗粒度天然无符杠, 谱面干净)
- 同格事件合并为和弦; 时值截断到下一音符起始 (`no_overlap`), 且不跨小节
- 相邻同和弦合并 (`merge_adjacent_same`)

### 4. 记谱惯例

- **钢琴声部零连线**: 踏板延音不写 tie (写 tie 会让谱面碎成连线海洋)
- 跨小节长音按小节拆分 (`split_events`)
- 结构: **扁平 Part 列表 + layout.StaffGroup(brace)** —  
  music21 的 Part 嵌套 PartStaff 会让 verovio 报 "no measures found"
- 每小节时值校验, 异常小节整小节回填全休止兜底

---

## ⑥ 渲染 (renderer.py)

verovio (A4 竖版 2100×2970, scale 40) → 逐页 SVG → svglib/reportlab → pypdf 合并 → **全程内存, 单次原子写盘**。

必须绕开的坑:

1. **verovio loadFile 不认中文路径** (静默返回 False) → 一律 `loadData(xml字符串)`
2. **svglib renderPM 出 PNG 报 ZeroDivisionError** (谱面字体) → 先出 PDF, 再 pymupdf `get_pixmap` 栅格化
3. **svglib/reportlab 无 CJK 字体** → 中文标题被静默丢弃。对策: verovio `header:none`,  
   标题由 `_overlay_title()` 用 pymupdf + 微软雅黑叠加:  
   `insert_font(fontname, fontfile=msyh.ttc)` → `insert_text(..., fontname=...)`。  
   测宽必须用 `pymupdf.Font(fontfile).text_length()`(模块级 `get_text_length` 不认页面字体别名)。  
   实测: 不注册字体直接 `insert_text(fontfile=)` 墨迹仅 65 像素(丢字形), 注册后 1898 像素。
4. **临时文件锁竞争** → 逐页 PDF 用 `renderPDF.drawToString()` 返回 bytes + `PdfReader(BytesIO)`,  
   不再往(可能是中文的)输出目录写临时文件。注意 drawToString 第二参数是 msg 不是缓冲区。
5. **失败韧性**: 渲染自动重试 2 次; `PermissionError`(PDF 被阅读器占用)给出明确中文提示;  
   pipeline 在渲染失败时写 `RENDER_ERROR.txt`(traceback+环境+最近40条日志)留现场。

## 失败补救设计 (rerender.py)

昂贵阶段(分离~~30s + 转写~~90s)的产物在廉价阶段(渲染~10s)**之前**就落盘输出目录。  
渲染失败 → 用户可用 `--rerender xxx.musicxml` 或 GUI「♻ 重新渲染 PDF」按钮几秒钟补救,  
不必重跑几分钟的流水线。批量: `--rerender-dir output`（或 rerender 模块自身的 `--dir`）。

---

## 质量验收标准 (客观)

对《River Flows in You》实测:

| 指标   | 结果                                         |
| ---- | ------------------------------------------ |
| 拍号   | 4/4 ✓ (旧算法误判 6/8)                          |
| 速度   | ♩=96 ✓ (原曲约 96-100)                        |
| 调号   | 3 sharps (A major/F#m) ✓                   |
| 开头旋律 | RH: A4 C#5 A5 {C#/G#} {G#/A} E5 … = 原曲主题 ✓ |
| 和弦进行 | LH: F#3 → D3/A3 → E3/B3 = F#m-D-A-E ✓      |
| 小节时值 | 异常 0 个 ✓                                   |
| 谱面   | 86 小节 / 457 音符 / 3 页 PDF                   |

通用验收: 转写开头 8-12 小节旋律必须能"读"出原曲, 左手根音应像和弦进行。

---

## 修改指南

| 想改什么         | 改哪里                                                                       |
| ------------ | ------------------------------------------------------------------------- |
| 谱面更简练 (音符更少) | `score_builder.py`: RH 网格 1/8 → 1/4 (`rh_slots = epm8 // 2`)              |
| 分手音区         | `score_builder.py`: `SPLIT_MIDI = 60`                                     |
| 页边距/字号/页数    | `renderer.py`: `_VEROVIO_OPTIONS` (scale 调大字号变小)                          |
| 标题字体/字号      | `renderer.py`: `_CJK_FONT_CANDIDATES` / `_overlay_title()`                |
| 分离模型         | `config.py`: `MULTI_STEM_MODEL` (注册表见 bs-roformer-download --list-models) |
| 拍号候选         | `analyze.py`: `_BAR_PROFILE` / `_BAR_PRIOR`                               |
| 过滤更严 (少杂音)   | `score_builder.py`: `MIN_DUR` / `MIN_VELOCITY`                            |
