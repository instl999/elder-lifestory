"""Measure local transcription against a reference text.

    python samples/evaluate_asr.py samples/zh/chaohuasishe_02_lu_64kb.mp3 \
        samples/zh/ref_02.txt --language zh --model small --hint 长妈妈,阿长

Reports two numbers:

* **CER** (character error rate) over Chinese characters, or word error rate
  for other languages. Alignment is free at both ends of the transcript, so a
  spoken preamble or outro the reference does not contain is not counted.
* **Name recall** -- of the proper nouns found in the reference, the share
  that appear spelled exactly right in the transcript. This is the number that
  matters for a memoir: §18 makes names a zero-error category, and a 5% CER
  that falls entirely on names is worse than a 10% CER that misses none.

Use it whenever you are deciding whether a model is good enough, or whenever
you have a real recording with a human-corrected transcript to compare with.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lifestory import language as lang  # noqa: E402
from lifestory import transcribe  # noqa: E402


def _tokens(text: str, code: str) -> list[str]:
    if lang.is_chinese(code):
        return re.findall(f"[{lang.CJK_RANGE}]", lang.to_script(text, lang.ZH_HANS))
    return re.findall(r"[a-z0-9']+", text.lower())


def error_rate(reference: list[str], hypothesis: list[str]) -> tuple[float, int]:
    """Edit distance / reference length, with free gaps at the ends of `hypothesis`.

    Vectorised row by row: insertions within a row become a running minimum,
    so a 3,000 x 3,000 alignment takes a fraction of a second.
    """
    if not reference:
        return 0.0, 0
    vocab = {t: i for i, t in enumerate(set(reference) | set(hypothesis))}
    hyp = np.array([vocab[t] for t in hypothesis], dtype=np.int64)
    m = len(hyp)
    ar = np.arange(m + 1, dtype=np.int64)
    prev = np.zeros(m + 1, dtype=np.int64)  # free start in the hypothesis

    for i, token in enumerate(reference, start=1):
        cost = (hyp != vocab[token]).astype(np.int64)
        tmp = np.empty(m + 1, dtype=np.int64)
        tmp[0] = i
        tmp[1:] = np.minimum(prev[:-1] + cost, prev[1:] + 1)
        prev = np.minimum.accumulate(tmp - ar) + ar

    distance = int(prev.min())  # free end in the hypothesis
    return distance / len(reference), distance


def name_recall(
    reference: str, hypothesis: str, code: str, gold: list[str] | None = None
) -> tuple[list[str], list[str]]:
    """Which names came back spelled exactly right.

    Pass a fixed `gold` list when comparing runs: the automatic list is drawn
    from the name detector, which changes as the detector is tuned, and the
    numbers stop being comparable from one run to the next.
    """
    if gold is None:
        gold = sorted({term for term, _ in lang.proper_nouns(reference, code)})
    hyp = lang.to_script(hypothesis, lang.ZH_HANS) if lang.is_chinese(code) else hypothesis
    hit = [g for g in gold if g in hyp]
    miss = [g for g in gold if g not in hyp]
    return hit, miss


def _read_names(path: str | None) -> list[str] | None:
    if not path:
        return None
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def _transcript_text(path: str) -> str:
    """The spoken text of a saved transcript: no header, labels or timestamps."""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        out.append(re.sub(r"^\[[\d:]+\]\s*[^:：]{1,40}[:：]\s*", "", line))
    return "".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("audio", help="the recording (ignored with --transcript)")
    parser.add_argument("reference")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--model", default=None)
    parser.add_argument("--hint", default="", help="comma-separated names to prime")
    parser.add_argument("--save", help="also write the rendered transcript here")
    parser.add_argument("--names", help="fixed list of names to score, one per line")
    parser.add_argument("--transcript", help="score this saved transcript instead of transcribing")
    args = parser.parse_args()

    code = lang.normalize(args.language)
    hints = [h for h in re.split("[,，、]", args.hint) if h.strip()]
    reference = Path(args.reference).read_text(encoding="utf-8")
    gold = _read_names(args.names)

    if args.transcript:
        hypothesis = _transcript_text(args.transcript)
        header = f"transcript {args.transcript}"
        timing, extra = "", ""
    else:
        started = time.perf_counter()
        result = transcribe.transcribe(
            Path(args.audio), owner_name="Owner", language=code,
            model_size=args.model, hints=hints,
            checkpoint=Path(args.save).with_suffix(".partial.jsonl") if args.save else None,
            progress=lambda m: print(f"  ... {m}", flush=True),
        )
        elapsed = time.perf_counter() - started
        if args.save:
            Path(args.save).write_text(
                transcribe.render_transcript(result, Path(args.audio).name), encoding="utf-8"
            )
        hypothesis = "".join(t.text for t in result.turns)
        header = f"model {result.model} | hints: {', '.join(hints) or 'none'}"
        timing = (f"  {result.minutes} min audio in {elapsed:.0f}s "
                  f"({elapsed / max(result.duration, 1):.2f}x real time)")
        extra = f"  checklist entries: {len(result.checklist)} | lines: {len(result.turns)}"

    rate, distance = error_rate(_tokens(reference, code), _tokens(hypothesis, code))
    hit, miss = name_recall(reference, hypothesis, code, gold)

    unit = "CER" if lang.is_chinese(code) else "WER"
    print(header)
    if timing:
        print(timing)
    print(f"  {unit} {rate:.1%}  ({distance} edits over {len(_tokens(reference, code))})")
    print(f"  name recall {len(hit)}/{len(hit) + len(miss)}"
          + ("  (fixed list)" if gold is not None else "  (automatic list)"))
    if miss:
        print(f"    missed: {', '.join(miss)}")
    if extra:
        print(extra)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
