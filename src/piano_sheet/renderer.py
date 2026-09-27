# -*- coding: utf-8 -*-
"""MusicXML -> PDF 渲染 (verovio SVG -> svglib/reportlab -> pypdf 合并), 可选 PNG 预览。

⚠ 本机实测踩坑与对策:
    1. verovio loadFile 不认中文路径(静默返回 False) -> 一律 loadData(xml 字符串)
    2. svglib renderPM 出 PNG 报 ZeroDivisionError -> PNG 预览改用 pymupdf 从 PDF 栅格化
    3. svglib/reportlab 无 CJK 字体 -> 中文标题在 PDF 里被静默丢弃(变 "t i t l e")
       对策: verovio 关掉 header, 标题改用 pymupdf + 微软雅黑叠加(实测墨迹像素 65 -> 1898)
    4. 逐页落盘临时 PDF 再 PdfReader 重开, 在 Windows 上有文件锁竞争风险,
       且临时目录建在中文输出目录里 -> 改为 renderPDF.drawToString + BytesIO 全程内存,
       不再产生任何临时文件
    5. loadData 在流水线里偶发返回 False(同一文件干净进程必成功; 2026-09-27 实测)
       -> 转写阶段(torch/librosa)后进程内存高位/状态污染所致。三层防御:
          a) 去 BOM(实测 BOM 前缀必致 loadData False) + 重试 3 次(每次新 toolkit + gc)
          b) enableLog/getLog 捕获 verovio 真实错误写入异常信息(不再只有干巴巴一句话)
          c) 进程内全失败 -> 干净子进程兜底渲染(sys.executable 重入本模块)
"""
from __future__ import annotations

import gc
import io
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

LogFn = Callable[[str], None]

# Windows / macOS / Linux 常见 CJK 字体候选(用于标题叠加)
_CJK_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",       # 微软雅黑
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simsun.ttc",     # 宋体
    "C:/Windows/Fonts/simhei.ttf",     # 黑体
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]


class RenderError(RuntimeError):
    pass


@dataclass
class RenderResult:
    pdf_path: Path
    pages: int
    png_previews: list
    title_drawn: bool = False     # 是否成功叠加了标题(含 CJK)


def _log(log: Optional[LogFn], msg: str) -> None:
    line = f"[render] {msg}"
    if log:
        log(line)
    else:
        print(line, flush=True)


def _has_cjk(s: str) -> bool:
    return any(ord(c) > 0x2E7F for c in s)


def _find_cjk_font() -> Optional[str]:
    for p in _CJK_FONT_CANDIDATES:
        if Path(p).exists():
            return p
    return None


# verovio 排版参数。header 固定关闭 —— 标题由 pymupdf 叠加, 以保证中文正确显示。
_VEROVIO_OPTIONS = {
    "pageWidth": 2100,          # A4 竖版(单位: 百分之一毫米量级的 verovio 单位)
    "pageHeight": 2970,
    "pageMarginTop": 80,
    "pageMarginBottom": 80,
    "pageMarginLeft": 80,
    "pageMarginRight": 80,
    "scale": 40,
    "adjustPageHeight": False,
    "footer": "none",
    "header": "none",           # ⚠ 不用 verovio 的 header(不支持 CJK)
    "breaks": "auto",
    "spacingSystem": 8,
    "spacingStaff": 6,
}
# 标题区预留高度(verovio 单位, 2100 宽 ≈ A4)。谱面从这之下开始排。
_TITLE_BAND = 200


def _strip_bom(text: str) -> str:
    """去除 UTF-8 BOM(实测 BOM 前缀会令 verovio loadData 直接返回 False)。"""
    return text[1:] if text.startswith("\ufeff") else text


def _load_with_retry(xml_text: str, opts: dict, log: Optional[LogFn],
                     attempts: int = 3):
    """反复尝试 loadData。

    流水线中(torch/librosa 跑完后内存高位)loadData 会偶发返回 False;
    同一文件在干净进程里必成功。每次用全新 toolkit + gc.collect() 提高成功率,
    并把 verovio 自身日志(enableLog/getLog)带出来, 失败时能看到真实原因。
    """
    import verovio

    try:
        verovio.enableLog(verovio.LOG_WARNING)
    except Exception:  # noqa: BLE001  # 老版本无此 API
        pass

    xml_text = _strip_bom(xml_text)
    last_log = ""
    for i in range(1, attempts + 1):
        tk = verovio.toolkit()
        try:
            tk.setOptions(opts)
            if tk.loadData(xml_text):
                n_pages = tk.getPageCount()
                if n_pages >= 1:
                    return tk, n_pages
                last_log = f"loadData 成功但排版 0 页(getPageCount={n_pages})"
            else:
                try:
                    last_log = (tk.getLog() or "").strip() or "loadData 返回 False(无日志)"
                except Exception:  # noqa: BLE001
                    last_log = "loadData 返回 False"
        except Exception as e:  # noqa: BLE001
            last_log = f"{type(e).__name__}: {e}"
        finally:
            if i < attempts:
                del tk
                gc.collect()
        if i < attempts:
            _log(log, f"⚠ verovio 第 {i} 次加载失败({last_log[:160]}), 重试…")
            time.sleep(0.3)
    raise RenderError(
        f"verovio 无法解析 MusicXML(已重试 {attempts} 次): {last_log[:400]}"
    )


def _render_in_subprocess(musicxml_path: Path, out_pdf: Path,
                          title: str, composer: str,
                          log: Optional[LogFn]) -> None:
    """终极兜底: 在干净子进程里完成 verovio 渲染(绕开本进程被污染的状态)。

    仅渲染 PDF 本体(不含 PNG/标题叠加, 那些由父进程在拿到 PDF 后继续做),
    用 PYTHONPATH 指向 src, 直接调用本模块的 _render_core。
    """
    src_dir = str(Path(__file__).resolve().parent.parent)
    env = dict(os.environ)
    env["PYTHONPATH"] = src_dir + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    code = (
        "import sys; from piano_sheet import renderer as r;"
        f" r._render_core(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])"
    )
    _log(log, "进程内渲染连续失败, 改用干净子进程兜底渲染…")
    proc = subprocess.run(
        [sys.executable, "-c", code,
         str(musicxml_path), str(out_pdf), title or "", composer or ""],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=600,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
        raise RenderError(
            "子进程兜底渲染也失败:\n   " + "\n   ".join(tail)
        )
    if not out_pdf.exists() or out_pdf.stat().st_size == 0:
        raise RenderError("子进程渲染后 PDF 为空或不存在")


def _render_core(musicxml: str, out_pdf: str, title: str, composer: str) -> None:
    """子进程入口: 读 MusicXML -> verovio -> PDF(不叠标题/不出 PNG)。"""
    from pathlib import Path as _P
    xml_text = _P(musicxml).read_text(encoding="utf-8")
    opts = dict(_VEROVIO_OPTIONS)
    if title or composer:
        opts["pageMarginTop"] = _VEROVIO_OPTIONS["pageMarginTop"] + _TITLE_BAND
    tk, n_pages = _load_with_retry(xml_text, opts, None)
    _compose_pdf(tk, n_pages, _P(out_pdf))


def render_pdf(musicxml_path: Path, out_pdf: Path, log: Optional[LogFn] = None,
               png_preview: bool = True, preview_dpi: int = 100,
               title: str = "", composer: str = "",
               retries: int = 2) -> RenderResult:
    """渲染 MusicXML 为 PDF。带重试(应对 Windows 偶发的库级失败)。"""
    musicxml_path = Path(musicxml_path)
    out_pdf = Path(out_pdf)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)

    if not musicxml_path.exists():
        raise RenderError(f"MusicXML 不存在: {musicxml_path}")

    xml_text = musicxml_path.read_text(encoding="utf-8")

    # 标题缺省从 MusicXML 元数据里取
    if not title:
        title = _extract_title(xml_text)

    opts = dict(_VEROVIO_OPTIONS)
    if title or composer:
        # 给标题腾出顶部空间
        opts["pageMarginTop"] = _VEROVIO_OPTIONS["pageMarginTop"] + _TITLE_BAND

    # ---- verovio 加载: 进程内重试 3 次; 全失败则干净子进程兜底(实测间歇性 False 的终极解)
    try:
        tk, n_pages = _load_with_retry(xml_text, opts, log)
    except RenderError as e:
        _log(log, f"⚠ {e}")
        _render_in_subprocess(musicxml_path, out_pdf, title, composer, log)
        n_pages = _count_pdf_pages(out_pdf)
    else:
        _log(log, f"verovio 排版完成: {n_pages} 页")
        # ---- SVG -> PDF: 全程内存, 不落任何临时文件(规避 Windows 文件锁 + 中文临时目录)
        last_err: Optional[BaseException] = None
        for attempt in range(1, retries + 2):
            try:
                _compose_pdf(tk, n_pages, out_pdf)
                last_err = None
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt <= retries:
                    _log(log, f"⚠ 第 {attempt} 次渲染失败({type(e).__name__}: {str(e)[:160]}), 重试…")
        if last_err is not None:
            raise RenderError(
                f"PDF 渲染失败(已重试 {retries} 次): {type(last_err).__name__}: {last_err}"
            ) from last_err

    if not out_pdf.exists() or out_pdf.stat().st_size == 0:
        raise RenderError(f"PDF 写出后为空或不存在: {out_pdf}")

    _log(log, f"PDF 已生成: {out_pdf} ({out_pdf.stat().st_size/1024:.0f} KB)")

    # ---- 叠加标题(verovio header 不支持中文, 必须自己画)
    title_drawn = False
    if title or composer:
        try:
            title_drawn = _overlay_title(out_pdf, title, composer, log)
        except Exception as e:  # noqa: BLE001
            _log(log, f"⚠ 标题叠加失败(谱面本身不受影响): {type(e).__name__}: {e}")

    previews = []
    if png_preview:
        try:
            previews = _make_png_previews(out_pdf, preview_dpi)
            _log(log, f"PNG 预览: {len(previews)} 页")
        except Exception as e:  # noqa: BLE001
            _log(log, f"⚠ PNG 预览生成失败(不影响 PDF): {type(e).__name__}: {e}")

    return RenderResult(pdf_path=out_pdf, pages=n_pages,
                        png_previews=previews, title_drawn=title_drawn)


def _count_pdf_pages(pdf_path: Path) -> int:
    """子进程兜底后, 从成品 PDF 反查页数。"""
    import pymupdf
    doc = pymupdf.open(str(pdf_path))
    try:
        return doc.page_count
    finally:
        doc.close()


def _compose_pdf(tk, n_pages: int, out_pdf: Path) -> None:
    """verovio 逐页 SVG -> reportlab 内存 PDF -> pypdf 合并 -> 一次写盘。"""
    from svglib.svglib import svg2rlg
    from reportlab.graphics import renderPDF
    from pypdf import PdfWriter, PdfReader

    writer = PdfWriter()
    for i in range(1, n_pages + 1):
        svg_text = tk.renderToSVG(i)
        if not svg_text or "<svg" not in svg_text:
            raise RenderError(f"第 {i} 页 SVG 渲染为空")
        # svg2rlg 支持从字符串加载, 避免写中文路径临时文件
        drawing = svg2rlg(io.StringIO(svg_text))
        if drawing is None:
            raise RenderError(f"第 {i} 页 SVG 转换失败(svg2rlg 返回 None)")
        buf = io.BytesIO(renderPDF.drawToString(drawing))   # ⚠ 内存输出, 不落盘
        buf.seek(0)
        reader = PdfReader(buf)
        if not reader.pages:
            raise RenderError(f"第 {i} 页 PDF 无页面")
        for pg in reader.pages:
            writer.add_page(pg)

    data = io.BytesIO()
    writer.write(data)
    try:
        out_pdf.write_bytes(data.getvalue())      # 单次原子写
    except PermissionError as e:
        # Windows 上最常见: PDF 正被阅读器/预览窗格占用
        raise RenderError(
            f"无法写入 PDF（文件正被占用）: {out_pdf.name}\n"
            f"   请关闭已打开该 PDF 的阅读器/预览窗口后重试。\n"
            f"   原始错误: {e}"
        ) from e


def _overlay_title(pdf_path: Path, title: str, composer: str,
                   log: Optional[LogFn] = None) -> bool:
    """在首页顶部叠加标题/作曲者, 支持 CJK。

    实测: insert_text + ttc 会丢字形(墨迹 65), 必须先 insert_font 显式注册字体(墨迹 1898)。
    """
    import pymupdf

    needs_cjk = _has_cjk(title) or _has_cjk(composer)
    font_path = _find_cjk_font() if needs_cjk else None
    if needs_cjk and font_path is None:
        _log(log, "⚠ 未找到 CJK 字体, 中文标题可能无法显示(谱面不受影响)")
        return False

    doc = pymupdf.open(str(pdf_path))
    try:
        page = doc[0]
        w = page.rect.width
        top = _TITLE_BAND * (w / 2100.0)        # verovio 单位 -> PDF pt

        # ⚠ get_text_length 是模块级函数, 不认页面注册的字体别名;
        #   必须用 Font 对象自己量宽, 否则报 "Font 'xxx' is unsupported"
        font_obj = pymupdf.Font(fontfile=font_path) if font_path else pymupdf.Font("helv")
        text_kwargs = {}
        if font_path:
            page.insert_font(fontname="psgcjk", fontfile=font_path)
            text_kwargs["fontname"] = "psgcjk"

        def center_draw(text: str, y: float, size: float) -> None:
            tw = font_obj.text_length(text, fontsize=size)
            page.insert_text(((w - tw) / 2, y), text, fontsize=size, **text_kwargs)

        # 标题: 居中, 字号按长度自适应
        if title:
            size = 22 if len(title) <= 18 else (18 if len(title) <= 30 else 15)
            center_draw(title, top * 0.45, size)
        # 作曲者: 居中, 小字
        if composer:
            center_draw(composer, top * 0.78, 11)

        doc.save(str(pdf_path) + ".tmp", garbage=3, deflate=True)
    finally:
        doc.close()

    tmp = Path(str(pdf_path) + ".tmp")
    if tmp.exists() and tmp.stat().st_size > 0:
        tmp.replace(pdf_path)
    else:
        tmp.unlink(missing_ok=True)
        return False
    _log(log, f"标题已叠加: 「{title}」" + (f" / {composer}" if composer else "")
              + ("（CJK 字体: " + Path(font_path).name + "）" if font_path else ""))
    return True


def _extract_title(xml_text: str) -> str:
    """从 MusicXML 头部取 work-title / movement-title。"""
    import re
    for tag in ("work-title", "movement-title"):
        m = re.search(rf"<{tag}>(.*?)</{tag}>", xml_text, re.S)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return ""


def _make_png_previews(pdf_path: Path, dpi: int) -> list:
    import pymupdf
    previews = []
    doc = pymupdf.open(str(pdf_path))
    try:
        for i in range(len(doc)):
            pix = doc[i].get_pixmap(dpi=dpi)
            p = pdf_path.with_name(f"{pdf_path.stem}_p{i+1:02d}.png")
            pix.save(str(p))
            previews.append(p)
    finally:
        doc.close()
    return previews
