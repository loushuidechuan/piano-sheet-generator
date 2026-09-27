# -*- coding: utf-8 -*-
"""下载钢琴转写模型权重 (165MB, Zenodo)。

piano_transcription_inference 自带的自动下载走 `os.system('wget ...')`,
Windows 上没有 wget 会**静默失败**, 所以 setup 脚本必须主动预下载。

存放路径必须是库的默认约定位置:
    ~/piano_transcription_inference_data/note_F1=0.9677_pedal_F1=0.9186.pth

用法:
    python download_weights.py          # 下载(已存在且完整则跳过)
    python download_weights.py --check  # 只检查, 不下载
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

URL = ("https://zenodo.org/record/4034264/files/"
       "CRNN_note_F1%3D0.9677_pedal_F1%3D0.9186.pth?download=1")
DEST = Path.home() / "piano_transcription_inference_data" / "note_F1=0.9677_pedal_F1=0.9186.pth"
MIN_SIZE = int(1.6e8)      # 库的完整性判据: >= 160MB
EXPECT_SIZE = 171966578    # 本机实测字节数


def check() -> bool:
    return DEST.exists() and DEST.stat().st_size >= MIN_SIZE


def download(retries: int = 3) -> bool:
    DEST.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, retries + 1):
        tmp = Path(tempfile.gettempdir()) / f"pti_ckpt_part_{os.getpid()}"
        try:
            print(f"[{attempt}/{retries}] 下载 {URL}")
            print(f"      目标: {DEST}  (~165 MB)")
            t0 = time.time()
            req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0)
                got = 0
                last_print = 0.0
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    now = time.time()
                    if now - last_print > 5:
                        pct = f"{got/total*100:.0f}%" if total else f"{got>>20}MB"
                        print(f"      ... {pct}  ({got/1e6/(now-t0+1e-9):.1f} MB/s)")
                        last_print = now
            if tmp.stat().st_size < MIN_SIZE:
                raise RuntimeError(f"下载不完整: {tmp.stat().st_size} bytes < {MIN_SIZE}")
            tmp.replace(DEST)
            print(f"完成: {DEST.stat().st_size/1e6:.1f} MB, 用时 {time.time()-t0:.0f}s")
            return True
        except Exception as e:  # noqa: BLE001
            print(f"      失败: {type(e).__name__}: {e}", file=sys.stderr)
            tmp.unlink(missing_ok=True)
            if attempt < retries:
                wait = 5 * attempt
                print(f"      {wait}s 后重试...")
                time.sleep(wait)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="下载钢琴转写模型权重")
    ap.add_argument("--check", action="store_true", help="只检查是否已存在")
    args = ap.parse_args()

    if check():
        print(f"权重已就绪: {DEST} ({DEST.stat().st_size/1e6:.1f} MB)")
        return 0
    if args.check:
        print(f"权重缺失: {DEST}")
        return 1
    return 0 if download() else 2


if __name__ == "__main__":
    raise SystemExit(main())
