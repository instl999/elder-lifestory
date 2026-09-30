"""End-to-end demo: two chapters of Mandarin audio to a Chinese book.

    python samples/zh/run_demo.py

Runs every real workbench command -- ingest, extract, review, confirm,
answers, direct, draft, check, export -- on the transcripts produced by
`evaluate_asr.py --save`. It needs the API key in `.env`.

Two things a real operator does by hand are scripted here, and printed so
nothing is hidden:

* **Checking the transcript against the audio.** Done here by comparing the
  checklist with the published text, since this script has no ears. The
  corrections it makes are listed; in a real case you make them yourself.
* **The story owner's decisions in `review` and the family's in `answers`.**
  Routine cards are released; anything marked private is kept private;
  every confirmation is accepted.

The case is created as `luxun-demo` and deleted first if it already exists.
Delete it afterwards so it does not count in `portfolio` or `bank`.
"""

from __future__ import annotations

import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from lifestory import archive as store  # noqa: E402
from lifestory import cli  # noqa: E402
from lifestory.models import Sensitivity  # noqa: E402

CASE = "luxun-demo"
SAMPLES = ROOT / "samples" / "zh"

# What an operator listening to the audio would fix on the checklist. Found by
# comparing the checklist against the published text; see the module docstring.
# Deliberately limited to what the checklist flagged plus LibriVox's own
# announcements: errors it did not flag (赠物 for 憎恶, 扩弃 for 阔气) are left
# in, so the resulting book shows what a checklist-only review produces. A
# real operator also reads along with the audio and catches more.
CORRECTIONS: dict[str, str] = {
    # LibriVox's spoken preamble and credits: not the story owner's words.
    "鲁迅散文集，召花稀时，阿长与山海经，此次 LibriVox 录音有公众所有，": "",
    "鲁迅散文集，昭花西时，父亲的病，此次Librevox录音有公众所有。": "",
    "3月10日，阿长与山海京结束。": "3月10日。",
    "10月7日，父亲的病结束。此次录音有礼金提供。": "10月7日。",
    "此次录音有礼金提供。": "",
    # Names on the checklist, checked against the published text.
    "马鹰花": "马缨花",
    "顾双": "孤孀",
    "冯瓢复英扬": "凭票付英洋",
    "陈连河": "陈莲河",
    "陈联合": "陈莲河",
    "宣元齐伯": "轩辕岐伯",
    "卢根": "芦根",
    "眼太太": "衍太太",
}


def step(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}", flush=True)


def run(*argv: str) -> None:
    started = time.perf_counter()
    code = 0
    try:
        code = cli.main(list(argv)) or 0
    except SystemExit as exc:
        code = int(exc.code or 0)
    print(f"  [{argv[0]} finished in {time.perf_counter() - started:.0f}s, exit {code}]", flush=True)
    if code:
        raise SystemExit(f"demo stopped: `{' '.join(argv)}` failed")


def operator_review(transcript: Path) -> Path:
    """Stand in for the operator's pass against the audio.

    Relabel every line as the story owner (this recording is a monologue) and
    apply the name corrections. Returns the reviewed copy.
    """
    text = transcript.read_text(encoding="utf-8")
    reviewed_lines = []
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            reviewed_lines.append(line)
            continue
        line = re.sub(r"^(\[[\d:]+\]\s*)[^:：]{1,40}(:\s)", r"\1鲁迅\2", line)
        for wrong, right in CORRECTIONS.items():
            line = line.replace(wrong, right)
        reviewed_lines.append(line)
    reviewed = transcript.with_name(transcript.stem + ".reviewed.txt")
    reviewed.write_text("\n".join(reviewed_lines), encoding="utf-8")
    return reviewed


def main() -> int:
    import os

    os.chdir(ROOT)
    for n in ("02", "07"):
        if not (SAMPLES / f"transcript_{n}.txt").exists():
            raise SystemExit(
                f"missing samples/zh/transcript_{n}.txt -- run evaluate_asr.py with --save first"
            )

    case_dir = ROOT / "cases" / CASE
    if case_dir.exists():
        shutil.rmtree(case_dir)

    # Scripted decisions, printed as they are made.
    current = {"card": None}
    original_show = cli._show_card

    def show_card(archive, card):
        current["card"] = card
        original_show(archive, card)

    def ask(prompt, choices=None, default=None):
        card = current["card"]
        if prompt.startswith("Who is deciding"):
            answer = "鲁迅（演示）"
        elif prompt.startswith("Whose answers"):
            answer = "周海婴（演示）"
        elif prompt == "Decision":
            answer = "k" if card is not None and card.sensitivity is Sensitivity.RESTRICTED else "r"
        elif "Type YES" in prompt:
            answer = "no"
        elif prompt.startswith("Keep those private too"):
            answer = "y"  # the owner's privacy covers every card built on those words
        elif prompt == "Answer":
            answer = "y"
        else:
            answer = default or ""
        print(f"  > scripted answer to {prompt[:40]!r}: {answer}", flush=True)
        return answer

    cli._show_card = show_card
    cli._ask = ask

    step("1. The case, in Chinese")
    run("new", CASE, "--owner", "鲁迅", "--preferred", "鲁迅", "--language", "zh",
        "--birth-year", "1881", "--sponsor", "周海婴", "--inheritor", "周海婴",
        "--operator", "Operator")
    for scope in ("recording", "ai_processing", "third_party_services", "family_sharing", "print"):
        run("consent", "grant", CASE, scope, "--by", "鲁迅", "--role", "story_owner")

    step("2. The operator's pass against the audio (scripted)")
    for original, correction in CORRECTIONS.items():
        print(f"  corrected {original} -> {correction}")
    reviewed = []
    for n in ("02", "07"):
        reviewed.append(operator_review(SAMPLES / f"transcript_{n}.txt"))

    step("3. Ingest both sessions")
    for number, path in enumerate(reviewed, start=1):
        run("ingest", CASE, str(path), "--session-number", str(number))

    step("4. Story cards")
    run("extract", CASE)
    run("cards", CASE)

    step("5. Review with the story owner (scripted)")
    run("review", CASE)

    step("6. The family's confirmation sheet, and their answers (scripted)")
    run("confirm", CASE)
    run("answers", CASE)

    step("7. The shape of the book")
    run("direct", CASE)

    step("8. Chapters")
    run("draft", CASE)

    step("9. Check and export")
    run("check", CASE, "--export")
    run("export", CASE)
    run("status", CASE)

    archive, paths = store.load(CASE)
    print(f"\nEverything is in {paths.exports}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
