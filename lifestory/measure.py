"""What Phase 0 is actually for (spec v2 §17, §18).

Two instruments:

* **The question bank.** Every question asked, tagged with what it produced.
  After fifty projects this is the asset that makes our interviews better than
  a competitor's, and it is the one thing here that compounds (§17). v1 of the
  spec treated interview templates as an incidental deliverable; they are the
  point.
* **Labour tracking by stage.** §14 predicts story-card extraction will
  dominate operator hours, and Phase 1 automates whichever stage actually
  does. That decision is only as good as this log.

Neither instrument is clever. Both are useless if the operator does not fill
them in, which is why the CLI nags.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from .models import Archive, AskedQuestion, LabourEntry, QuestionOutcome, Stage

# §13: the labour budget for Package B, and what case one is expected to cost.
PACKAGE_B_TARGET_HOURS = 20
PACKAGE_B_FIRST_CASE_HOURS = 50


# ---------------------------------------------------------------------------
# Question bank
# ---------------------------------------------------------------------------


class SeedQuestion(BaseModel):
    id: str
    text: str
    # The same question adapted -- not translated -- for a Mandarin interview.
    # Declared here so `lifestory bank` round-trips it; an undeclared field
    # would be dropped silently on the next write-back.
    zh: str | None = None
    topic: str
    mode: str = "standard"           # "standard" | "assisted" | "both"
    note: str | None = None
    # Populated from real cases over time. This is the part that becomes a moat.
    asked: int = 0
    scenes: int = 0

    def yield_rate(self) -> float | None:
        return None if self.asked == 0 else self.scenes / self.asked


def load_bank(path: Path) -> list[SeedQuestion]:
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [SeedQuestion.model_validate(q) for q in raw.get("questions", [])]


def _leading_comment(path: Path) -> str:
    """The explanatory header at the top of a bank file.

    PyYAML drops comments on a round trip, and the header is how an operator
    knows what `mode` means and how yield gets written back. Preserved by hand
    rather than pulling in a comment-preserving YAML library for one file.
    """
    if not path.exists():
        return ""
    header: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            header.append(line)
        else:
            break
    return "\n".join(header).rstrip() + "\n\n" if header else ""


def save_bank(questions: list[SeedQuestion], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = _leading_comment(path)
    payload = {"questions": [q.model_dump(exclude_none=True) for q in questions]}
    body = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100)
    path.write_text(header + body, encoding="utf-8")


class YieldRow(BaseModel):
    text: str
    topic: str | None
    asked: int
    scenes: int
    details: int
    declined: int
    no_memory: int

    def scene_rate(self) -> float:
        return self.scenes / self.asked if self.asked else 0.0

    def any_yield_rate(self) -> float:
        return (self.scenes + self.details) / self.asked if self.asked else 0.0


def question_yield(archives: list[Archive]) -> list[YieldRow]:
    """Aggregate question performance across cases.

    Grouped by question text rather than by seed id, because operators
    improvise and the improvised questions are often the good ones.
    """
    buckets: dict[str, dict[str, object]] = {}

    for archive in archives:
        for question in archive.questions:
            key = question.text.strip().lower()
            bucket = buckets.setdefault(
                key,
                {
                    "text": question.text.strip(),
                    "topic": question.topic,
                    "asked": 0,
                    "scenes": 0,
                    "details": 0,
                    "declined": 0,
                    "no_memory": 0,
                },
            )
            bucket["asked"] = int(bucket["asked"]) + 1
            if question.outcome is QuestionOutcome.SCENE:
                bucket["scenes"] = int(bucket["scenes"]) + 1
            elif question.outcome is QuestionOutcome.DETAIL:
                bucket["details"] = int(bucket["details"]) + 1
            elif question.outcome is QuestionOutcome.DECLINED:
                bucket["declined"] = int(bucket["declined"]) + 1
            elif question.outcome is QuestionOutcome.NO_MEMORY:
                bucket["no_memory"] = int(bucket["no_memory"]) + 1

    rows = [YieldRow.model_validate(b) for b in buckets.values()]
    rows.sort(key=lambda r: (-r.scene_rate(), -r.asked))
    return rows


def untagged_questions(archive: Archive) -> list[AskedQuestion]:
    """Questions with no outcome recorded. A case should not close with any."""
    return [q for q in archive.questions if q.outcome is QuestionOutcome.UNTAGGED]


def refresh_bank_stats(bank: list[SeedQuestion], archives: list[Archive]) -> list[SeedQuestion]:
    """Fold real case outcomes back into the seed bank."""
    counts: dict[str, tuple[int, int]] = {}
    for archive in archives:
        for question in archive.questions:
            if not question.bank_id:
                continue
            asked, scenes = counts.get(question.bank_id, (0, 0))
            asked += 1
            if question.outcome is QuestionOutcome.SCENE:
                scenes += 1
            counts[question.bank_id] = (asked, scenes)

    for seed in bank:
        asked, scenes = counts.get(seed.id, (0, 0))
        seed.asked = asked
        seed.scenes = scenes
    return bank


# ---------------------------------------------------------------------------
# Labour
# ---------------------------------------------------------------------------


class LabourSummary(BaseModel):
    case_id: str
    total_minutes: int
    by_stage: dict[str, int] = Field(default_factory=dict)
    target_hours: int = PACKAGE_B_TARGET_HOURS

    @property
    def total_hours(self) -> float:
        return round(self.total_minutes / 60, 1)

    def dominant_stage(self) -> tuple[str, int] | None:
        if not self.by_stage:
            return None
        return max(self.by_stage.items(), key=lambda kv: kv[1])

    def over_target(self) -> bool:
        return self.total_hours > self.target_hours

    def verdict(self) -> str:
        """What this case says about the Phase 1 build decision (§14)."""
        dominant = self.dominant_stage()
        if dominant is None:
            return "No labour recorded. This case cannot inform the Phase 1 decision."

        stage, minutes = dominant
        share = minutes / self.total_minutes if self.total_minutes else 0
        line = (
            f"{self.total_hours}h total; '{stage}' dominates at "
            f"{round(minutes / 60, 1)}h ({share:.0%})."
        )
        if stage == Stage.STORY_CARDS.value:
            line += " Matches the §14 prediction — story-card extraction is the Phase 1 target."
        else:
            line += (
                f" Does NOT match the §14 prediction of story-card extraction. "
                f"If this holds across cases, Phase 1 should automate '{stage}' instead."
            )
        return line


def labour_summary(archive: Archive) -> LabourSummary:
    target = {"A": 8, "B": PACKAGE_B_TARGET_HOURS, "C": 100}[archive.meta.package]
    return LabourSummary(
        case_id=archive.meta.id,
        total_minutes=archive.total_labour_minutes(),
        by_stage=archive.labour_by_stage(),
        target_hours=target,
    )


def portfolio_labour(archives: list[Archive]) -> dict[str, object]:
    """Across all cases: the number that decides what Phase 1 builds."""
    totals: dict[str, int] = {}
    per_case: list[LabourSummary] = []

    for archive in archives:
        summary = labour_summary(archive)
        per_case.append(summary)
        for stage, minutes in summary.by_stage.items():
            totals[stage] = totals.get(stage, 0) + minutes

    ranked = sorted(totals.items(), key=lambda kv: -kv[1])
    grand = sum(totals.values())

    return {
        "cases": len(archives),
        "total_hours": round(grand / 60, 1),
        "mean_hours_per_case": round(grand / 60 / len(archives), 1) if archives else 0,
        "by_stage_hours": [(s, round(m / 60, 1), m / grand if grand else 0) for s, m in ranked],
        "per_case": per_case,
        "phase1_target": ranked[0][0] if ranked else None,
    }


def log_labour(
    archive: Archive, stage: Stage, minutes: int, *, who: str | None = None, note: str | None = None
) -> LabourEntry:
    entry = LabourEntry(
        case_id=archive.meta.id, stage=stage, minutes=minutes, who=who, note=note
    )
    archive.labour.append(entry)
    return entry
