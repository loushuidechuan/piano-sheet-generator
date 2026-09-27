# -*- coding: utf-8 -*-
"""补救工具: 从已有的 MusicXML 直接重新渲染 PDF, 不必重跑分离/转写。

场景: 渲染阶段失败(或想改标题/版式), 而 output 里已有 MusicXML 产物时,
      几秒钟即可重出 PDF, 无需再花几分钟跑完整流水线。

用法:
    python -m piano_sheet.rerender <musicxml文件> [-o 输出.pdf] [--title 曲名] [--composer 作者]
    python -m piano_sheet.rerender --dir output          # 批量重渲目录下所有 MusicXML
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional


def rerender_one(xml_path: Path, out_pdf: Optional[Path] = None,
                 title: str = "", composer: str = "",
                 png_preview: bool = True) -> Path:
    from .renderer import render_pdf

    xml_path = Path(xml_path)
    if not xml_path.exists():
        raise FileNotFoundError(f"MusicXML 不存在: {xml_path}")
    if out_pdf is None:
        stem = xml_path.stem
        if stem.endswith("_piano"):
            stem = stem[:-len("_piano")]
        out_pdf = xml_path.with_name(f"{stem}_钢琴谱.pdf")
    r = render_pdf(xml_path, out_pdf, title=title, composer=composer,
                   png_preview=png_preview)
    return r.pdf_path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="piano-sheet-rerender",
                                description="从已有 MusicXML 重新渲染 PDF（补救渲染失败）")
    p.add_argument("xml", nargs="?", help="MusicXML 文件路径")
    p.add_argument("--dir", help="批量模式: 递归渲染该目录下所有 .musicxml")
    p.add_argument("-o", "--output", help="输出 PDF 路径（单文件模式）")
    p.add_argument("--title", default="", help="谱面标题")
    p.add_argument("--composer", default="", help="作曲者署名")
    p.add_argument("--no-png", action="store_true", help="不生成 PNG 预览")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    preview = not args.no_png

    targets = []
    if args.dir:
        d = Path(args.dir)
        if not d.exists():
            print(f"❌ 目录不存在: {d}", file=sys.stderr)
            return 2
        targets = sorted(d.rglob("*.musicxml"))
        if not targets:
            print(f"❌ {d} 下没有找到 .musicxml 文件", file=sys.stderr)
            return 2
    elif args.xml:
        targets = [Path(args.xml)]
    else:
        print("❌ 请提供 MusicXML 文件路径, 或使用 --dir 批量渲染", file=sys.stderr)
        return 2

    ok, fail = 0, 0
    for xml in targets:
        try:
            out = None if len(targets) > 1 else (Path(args.output) if args.output else None)
            pdf = rerender_one(xml, out, title=args.title, composer=args.composer,
                               png_preview=preview)
            print(f"✅ {pdf}")
            ok += 1
        except Exception as e:  # noqa: BLE001
            print(f"❌ {xml.name}: {type(e).__name__}: {e}", file=sys.stderr)
            fail += 1

    print(f"\n完成: 成功 {ok}, 失败 {fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
