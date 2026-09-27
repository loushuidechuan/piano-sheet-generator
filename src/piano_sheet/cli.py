# -*- coding: utf-8 -*-
"""命令行入口。

用法:
    python -m piano_sheet <音频文件> [选项]
    python -m piano_sheet --gui
    python -m piano_sheet --check-env

示例:
    python -m piano_sheet "river flows in you.mp4" -o output --title "River Flows in You"
    python -m piano_sheet song.mp3 --no-separate        # 跳过分离(纯钢琴独奏)
    python -m piano_sheet song.mp3 --device cpu
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="piano-sheet",
        description="分离音乐中的钢琴音轨并生成五线谱 PDF",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("input", nargs="?", help="输入音频/视频文件 (mp3/mp4/wav/m4a/flac/ogg...)")
    p.add_argument("-o", "--output", default=None, help="输出目录 (默认 ./output)")
    p.add_argument("--title", default="", help="谱面标题 (默认用文件名)")
    p.add_argument("--composer", default="", help="作曲者署名")
    p.add_argument("--no-separate", action="store_true",
                   help="跳过音轨分离, 直接对整曲转写 (适合纯钢琴独奏)")
    p.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"],
                   help="分离阶段的计算设备 (默认 auto)")
    p.add_argument("--gui", action="store_true", help="启动图形界面")
    p.add_argument("--check-env", action="store_true", help="只做环境自检并打印结果")
    p.add_argument("--rerender", metavar="MUSICXML",
                   help="补救模式: 从已有 MusicXML 直接重新渲染 PDF, 不重跑分离/转写")
    p.add_argument("--rerender-dir", metavar="DIR",
                   help="补救模式(批量): 递归重渲目录下所有 .musicxml")
    p.add_argument("-q", "--quiet", action="store_true", help="安静模式(仅打印关键结果)")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.rerender or args.rerender_dir:
        from .rerender import main as rerender_main
        sub = []
        if args.rerender_dir:
            sub += ["--dir", args.rerender_dir]
        else:
            sub.append(args.rerender)
        if args.output:
            sub += ["-o", args.output]
        if args.title:
            sub += ["--title", args.title]
        if args.composer:
            sub += ["--composer", args.composer]
        if args.quiet:
            sub += ["--no-png"]
        return rerender_main(sub)

    if args.gui or (not args.input and not args.check_env):
        from .gui.app import main as gui_main
        gui_main()
        return 0

    from .config import Config, ConfigError

    try:
        cfg = Config.detect(output_dir=args.output)
    except ConfigError as e:
        print(f"❌ 环境检测失败: {e}", file=sys.stderr)
        return 2

    if args.check_env:
        print(cfg.describe())
        print("\n可分离钢琴音轨:" , "是" if cfg.can_separate else "否 (缺 ffmpeg 或 BS-RoFormer CLI)")
        return 0 if (cfg.ffmpeg is not None) else 1

    if not args.input:
        print("❌ 请提供输入文件, 或使用 --gui 启动界面", file=sys.stderr)
        return 2

    cfg.separate = not args.no_separate

    from .pipeline import Pipeline, PipelineError

    log = (lambda m: None) if args.quiet else (lambda m: print(m, flush=True))
    pipe = Pipeline(config=cfg, log=log)
    try:
        res = pipe.run(args.input, title=args.title, composer=args.composer, device=args.device)
    except ConfigError as e:
        # 输入文件不存在 / 类型不支持等用户输入错误: 给一行友好提示而非 traceback
        print(f"❌ 输入无效: {e}", file=sys.stderr)
        return 2
    except PipelineError as e:
        print(f"\n❌ 处理失败 [{e.stage}]: {e}", file=sys.stderr)
        if e.cause is not None and not args.quiet:
            print(f"   原因: {type(e.cause).__name__}: {e.cause}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n⏹ 已中断", file=sys.stderr)
        return 130

    print()
    print("=" * 64)
    print(f"✅ 完成  用时 {res.elapsed:.1f}s")
    print(f"   PDF      : {res.pdf_path}")
    print(f"   MIDI     : {res.midi_path}")
    print(f"   MusicXML : {res.musicxml_path}")
    if res.piano_wav:
        print(f"   钢琴轨   : {res.piano_wav}")
    a = res.analysis
    if a:
        print(f"   分析     : {a.key_name} · {a.time_sig} · ♩={a.bpm:.0f} · 拍位稳定度CV={a.ibi_cv:.3f}")
    print(f"   谱面     : {res.pages} 页 · {res.n_measures} 小节 · {res.n_notes} 音符")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
