"""Remaining model comparison runs, detached so they survive the app closing.

Appends to samples/zh/results.txt; writes a DONE marker at the end.
"""
import os, subprocess, sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
os.chdir(root)
env = dict(os.environ, PYTHONIOENCODING="utf-8", HF_HUB_DISABLE_SYMLINKS_WARNING="1")
runs = [
    ("02", "small", "鲁迅,长妈妈,阿长,绍兴", "samples/zh/transcript_02.txt"),
    ("07", "small", "鲁迅,绍兴,衍太太", "samples/zh/transcript_07.txt"),
]
log = root / "samples/zh/results.txt"
for chapter, model, hint, save in runs:
    cmd = [sys.executable, "samples/evaluate_asr.py",
           f"samples/zh/chaohuasishe_{chapter}_lu_64kb.mp3", f"samples/zh/ref_{chapter}.txt",
           "--language", "zh", "--model", model, "--hint", hint]
    if save:
        cmd += ["--save", save]
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env)
    lines = [l for l in (out.stdout + out.stderr).splitlines() if "unauthenticated" not in l]
    with log.open("a", encoding="utf-8") as f:
        f.write(f"\n# chapter {chapter}\n" + "\n".join(lines[-12:]) + "\n")
(root / "samples/zh/DONE").write_text("done", encoding="utf-8")
