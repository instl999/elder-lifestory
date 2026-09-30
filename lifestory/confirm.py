"""The family confirmation queue (spec v2 §7.4).

v1 of the specification asked families to review claims at the archive level.
That is where these projects die: nobody proofreads 20,000 words about their
own mother, and the project stalls waiting for them to try.

So this module does the opposite of a thorough job on purpose. It surfaces only
what is embarrassing when wrong -- names, dates, relationships, places, who is
in which photograph -- ranks it, and hard-caps the result at 25 items.

Everything the system is unsure about that does not make this list is either
resolved by the editor, asked in the next interview, or preserved as
uncertainty in the prose. That is a deliberate editorial decision, not an
oversight.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from .i18n import t
from .models import Access, Archive, Claim, ClaimKind, ClaimStatus, Sensitivity

MAX_ITEMS = 25
TARGET_MINUTES = 15


class ConfirmationItem(BaseModel):
    claim_id: str
    kind: ClaimKind
    question: str
    current_value: str
    context: str | None = None
    importance: int
    conflicting: list[str] = Field(default_factory=list)


class ConfirmationQueue(BaseModel):
    case_id: str
    owner_name: str
    language: str = "en"
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    items: list[ConfirmationItem] = Field(default_factory=list)
    deferred_count: int = 0

    def estimated_minutes(self) -> int:
        # Measured against the design target: a tappable item every ~35 seconds.
        return max(1, round(len(self.items) * 0.6))


_QUESTION_TEMPLATES = {
    ClaimKind.NAME: "Have we spelled this name correctly?",
    ClaimKind.DATE: "Is this date right, or near enough?",
    ClaimKind.RELATIONSHIP: "Have we got this relationship right?",
    ClaimKind.PLACE: "Is this the right place name?",
    ClaimKind.PHOTO_SUBJECT: "Is this the right person in the photograph?",
}


def _score(claim: Claim, archive: Archive) -> float:
    """Rank by how much it would cost to get this wrong in print."""
    score = float(claim.importance)

    # Conflicting accounts are the whole reason to ask a human.
    if claim.status is ClaimStatus.CONFLICTING:
        score += 3.0
    elif claim.status is ClaimStatus.UNRESOLVED:
        score += 1.5

    # A claim that several story cards lean on matters more than a stray one.
    uses = sum(1 for card in archive.story_cards if claim.id in card.claim_ids)
    score += min(uses, 3) * 0.5

    # Names and relationships are the classic wince.
    if claim.kind in {ClaimKind.NAME, ClaimKind.RELATIONSHIP}:
        score += 1.0

    return score


def _context_for(claim: Claim, archive: Archive, audience: Access) -> str | None:
    """A short excerpt of the recording, to jog the reviewer's memory.

    Only ever drawn from material released to this audience. `build` already
    withholds unreleased claims entirely; this is the second lock on the same
    door, because the cost of getting it wrong is a family learning something
    the story owner chose not to tell them.
    """
    for ref in claim.sources:
        if ref.kind != "utterance":
            continue
        utterance = archive.utterance(ref.ref_id)
        if utterance is None:
            continue
        if utterance.sensitivity is Sensitivity.RESTRICTED:
            continue
        if not utterance.access.visible_to(audience):
            continue
        text = utterance.text.strip()
        return text if len(text) <= 180 else text[:177] + "..."
    return None


def build(
    archive: Archive,
    *,
    max_items: int = MAX_ITEMS,
    audience: Access = Access.FAMILY,
) -> ConfirmationQueue:
    """Build the queue the family actually sees.

    A claim only qualifies if the story owner has released everything it rests
    on. An unreleased claim is not "pending review" -- it is material the
    family is not entitled to see, and asking them to confirm it would disclose
    it just as surely as printing it (§12.2).
    """
    candidates = [
        c
        for c in archive.claims
        if c.kind.is_confirmable()
        and c.status
        in {ClaimStatus.STATED_BY_OWNER, ClaimStatus.UNRESOLVED, ClaimStatus.CONFLICTING}
        and archive.visible_to(c, audience)
    ]

    candidates.sort(key=lambda c: _score(c, archive), reverse=True)
    selected = candidates[:max_items]

    items = [
        ConfirmationItem(
            claim_id=c.id,
            kind=c.kind,
            question=_QUESTION_TEMPLATES.get(c.kind, "Is this right?"),
            current_value=c.text,
            context=_context_for(c, archive, audience),
            importance=c.importance,
            conflicting=[
                other.text
                for other in (archive.claim(cid) for cid in c.conflicting_with)
                if other is not None
            ],
        )
        for c in selected
    ]

    return ConfirmationQueue(
        case_id=archive.meta.id,
        owner_name=archive.meta.owner_preferred_name or archive.meta.owner_name,
        language=archive.meta.language,
        items=items,
        deferred_count=max(0, len(candidates) - len(selected)),
    )


def apply_answer(
    archive: Archive,
    claim_id: str,
    *,
    confirmed: bool,
    corrected_text: str | None = None,
    by: str,
) -> Claim:
    """Record a family answer against a claim."""
    claim = archive.claim(claim_id)
    if claim is None:
        raise KeyError(f"no claim {claim_id}")

    if corrected_text:
        claim.text = corrected_text
    claim.status = ClaimStatus.CONFIRMED_BY_FAMILY if confirmed else ClaimStatus.CONFLICTING
    claim.confirmed_by = by
    claim.confirmed_at = datetime.now(timezone.utc)
    return claim


def render_markdown(queue: ConfirmationQueue) -> str:
    """The queue as a sheet the family can work through on a phone.

    In the family's language: this is the one document they receive before
    the book, and it should read like a considerate person asking.
    """
    language = queue.language
    lines = [
        f"# {t('confirm.title', language, name=queue.owner_name)}",
        "",
        t("confirm.intro", language, n=len(queue.items), minutes=queue.estimated_minutes()),
        "",
        t("confirm.not_the_book", language),
        "",
    ]

    by_kind: dict[str, list[ConfirmationItem]] = {}
    for item in queue.items:
        by_kind.setdefault(item.kind.value, []).append(item)

    for kind, items in by_kind.items():
        lines.append(f"## {t(f'confirm.heading.{kind}', language)}")
        lines.append("")
        for item in items:
            lines.append(f"- [ ] **{item.current_value}**")
            if item.context:
                lines.append(f"  - {t('confirm.from_recording', language, text=item.context)}")
            if item.conflicting:
                lines.append(f"  - {t('confirm.also_told', language)}")
                lines.extend(f"    - {alt}" for alt in item.conflicting)
            lines.append(f"  - {t('confirm.correct_prompt', language)}")
            lines.append("")

    if queue.deferred_count:
        lines.extend(["---", "", f"*{t('confirm.deferred', language, n=queue.deferred_count)}*"])

    return "\n".join(lines)
