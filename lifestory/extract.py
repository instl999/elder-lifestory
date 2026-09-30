"""Transcript -> story cards (spec v2 §8, §10.2, §14).

This is the step §14 predicts will dominate operator cost, which is why it is
the first thing in the workbench.

Three rules hold the whole thing together:

1. **Nothing is invented.** The model may only restate what the transcript
   says. Every story card cites the utterance ids it came from, and those
   citations are verified against the archive afterwards -- a fabricated
   citation is caught deterministically, not trusted (§6.2).
2. **Consent gates the call.** No transcript text leaves the machine without a
   live AI_PROCESSING consent record (§12.1).
3. **Extraction never decides visibility.** Everything comes out at OPERATOR
   access; releasing material to the family is a human act (§12.2).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Callable, Literal

from pydantic import BaseModel, Field

from .models import (
    Access,
    Archive,
    Claim,
    ClaimKind,
    ClaimStatus,
    ConsentScope,
    Person,
    Place,
    Quote,
    Sensitivity,
    SourceRef,
    SpeakerRole,
    StoryCard,
    Utterance,
)
from . import language as lang
from . import llm
from .validators import _clean as _clean_text
from .validators import quote_matches_source

DEFAULT_WINDOW = 60          # utterances per extraction call
DEFAULT_OVERLAP = 8          # carried between windows so scenes aren't cut in half


class ConsentError(RuntimeError):
    """Raised when a call would send material without a live consent record."""


# ---------------------------------------------------------------------------
# What we ask the model for. Flat and boring on purpose -- schema-validated.
# ---------------------------------------------------------------------------


class ExtractedQuote(BaseModel):
    text: str = Field(description="Verbatim words from the transcript. Never paraphrased.")
    source_utterance_id: str


class ExtractedPerson(BaseModel):
    name: str
    relationship: str | None = Field(
        default=None, description="Relationship to the story owner, if the transcript states it."
    )
    source_utterance_ids: list[str]


class ExtractedPlace(BaseModel):
    name: str
    region: str | None = None
    source_utterance_ids: list[str]


class ExtractedClaim(BaseModel):
    text: str = Field(
        description=(
            "One checkable factual statement, stated plainly, in the third person, "
            "calling the story owner by name. The family reads it as written."
        )
    )
    kind: Literal["name", "date", "relationship", "place", "photo_subject", "event", "other"]
    importance: int = Field(
        ge=1, le=5, description="5 = deeply embarrassing if wrong in a printed book."
    )
    source_utterance_ids: list[str]


class ExtractedStoryCard(BaseModel):
    title: str = Field(description="Short, concrete, drawn from the material.")
    period: str | None = Field(default=None, description="e.g. 'childhood', 'the mill years'")
    year: int | None = None
    year_end: int | None = None
    approximate: bool = Field(
        default=False, description="True if the dating is inferred rather than stated."
    )
    setup: str
    conflict: str | None = None
    choice: str | None = None
    consequence: str | None = None
    reflection: str | None = None
    people: list[str] = Field(default_factory=list, description="Names as the transcript gives them.")
    places: list[str] = Field(default_factory=list)
    quotes: list[ExtractedQuote] = Field(
        default_factory=list,
        description=(
            "At most three: the lines most characteristic of how this person "
            "speaks. The transcript itself keeps everything else."
        ),
    )
    themes: list[str] = Field(default_factory=list)
    strength: int = Field(
        ge=1,
        le=5,
        description=(
            "5 = a full scene with stakes and an outcome. "
            "2 or below = general reminiscence, not yet a scene."
        ),
    )
    sensitivity: Literal["routine", "tender", "contested", "restricted"] = "routine"
    source_utterance_ids: list[str]
    followup_question: str | None = Field(
        default=None,
        description="One short, concrete question that would turn this into a stronger scene.",
    )


class ExtractionResult(BaseModel):
    story_cards: list[ExtractedStoryCard] = Field(default_factory=list)
    people: list[ExtractedPerson] = Field(default_factory=list)
    places: list[ExtractedPlace] = Field(default_factory=list)
    claims: list[ExtractedClaim] = Field(default_factory=list)
    notes: str | None = Field(
        default=None,
        description=(
            "Brief -- a few short points the editor must know about this passage "
            "(apparent transcription errors, gaps, anything doubtful), not a full audit."
        ),
    )


SYSTEM = """\
You are the Archivist in an oral-history production workflow. An older adult has \
been interviewed about their life. Your job is to turn a passage of that \
transcript into structured records an editor can build a memoir from.

You are not writing prose. You are not making the material more interesting. \
You are reading carefully and recording what is actually there.

Hard rules:

1. Invent nothing. Every field must be supported by the transcript passage you \
were given. If the transcript does not say when something happened, leave the \
year empty rather than estimating. If a beat of a story is missing, leave that \
field empty and say what you would ask about it in `followup_question`.
2. Cite sources. Every record carries the utterance ids it came from. Use only \
ids that appear in the passage, exactly as written.
3. Quotes are verbatim. Copy the speaker's words exactly, including their \
characteristic phrasing. Do not tidy grammar, do not modernise vocabulary, do \
not merge two sentences that were separated. If you cannot reproduce it \
exactly, do not record it as a quote.
4. Do not correct the speaker. If they misremember a date or contradict \
themselves, record what they said and raise it as a claim. Their version is the \
material; resolving it is a human decision, not yours.
5. Be honest about strength. A story card is a scene: something happens, \
someone chooses, there is a consequence. General reminiscence ("we were poor \
but happy") is strength 1-2 and should be marked as such. Do not inflate a \
summary into a scene by inventing detail.
6. Mark sensitivity honestly. Use `restricted` for anything the speaker appears \
to be disclosing privately -- an affair, a hidden child, an estrangement, a \
wartime action, abuse. That flag keeps the material out of anything the family \
sees until the story owner personally releases it. When in doubt, mark it \
restricted.

Prefer a small number of well-sourced records over broad coverage.\
"""


def _format_passage(utterances: list[Utterance]) -> str:
    lines = []
    for u in utterances:
        stamp = f" [{u.t_start}]" if u.t_start else ""
        lines.append(f"({u.id}){stamp} {u.speaker} [{u.role.value}]: {u.text}")
    return "\n".join(lines)


def _windows(
    utterances: list[Utterance], size: int, overlap: int
) -> list[list[Utterance]]:
    if size <= 0:
        return [utterances]
    step = max(1, size - overlap)
    out: list[list[Utterance]] = []
    for start in range(0, len(utterances), step):
        window = utterances[start : start + size]
        if window:
            out.append(window)
        if start + size >= len(utterances):
            break
    return out


def check_consent(archive: Archive) -> None:
    """Refuse to send transcript text without live consent (§12.1).

    THIRD_PARTY_SERVICES is required because the model provider is a third
    party: the transcript leaves this machine and goes to an outside company
    under their retention and jurisdiction, not ours. §12.1 lists that as its
    own consent scope precisely so it cannot be folded into a general "we use
    software to help" and forgotten.
    """
    missing = archive.missing_consents(
        [
            ConsentScope.RECORDING,
            ConsentScope.AI_PROCESSING,
            ConsentScope.THIRD_PARTY_SERVICES,
        ]
    )
    if missing:
        names = ", ".join(s.value for s in missing)
        raise ConsentError(
            f"missing live consent for: {names}. "
            f"Transcript text would be sent to {llm.describe()}. "
            f"Record consent with `lifestory consent grant` before extracting."
        )


def extract_window(
    utterances: list[Utterance],
    *,
    owner_name: str,
    preferred_name: str | None = None,
    language: str | None = None,
    context_note: str | None = None,
) -> ExtractionResult:
    """One extraction call over one window of transcript."""
    passage = _format_passage(utterances)
    valid_ids = ", ".join(u.id for u in utterances)
    code = lang.normalize(language)
    called = preferred_name or owner_name
    known_as = f", known to the family as {called}" if called != owner_name else ""

    context = f"\n\nContext from the operator: {context_note}" if context_note else ""
    prompt = (
        f"The story owner is {owner_name}{known_as}.{context}\n\n"
        f"Here is a passage of the interview transcript. Each line begins with its "
        f"utterance id in parentheses.\n\n"
        f"---\n{passage}\n---\n\n"
        f"The only valid utterance ids are: {valid_ids}\n\n"
        f"Extract the story cards, people, places and claims this passage supports. "
        f"If the passage contains no usable scene, return empty lists and say why in `notes`.\n\n"
        # Claims go onto the family's confirmation sheet word for word, and
        # story cards feed the book, so both must be in the family's language.
        f"Write titles, beats, claims, notes and follow-up questions in "
        f"{lang.display_name(code)}. Quotes stay exactly as spoken.\n\n"
        # Claims are printed on the family's confirmation sheet verbatim. On a
        # Mandarin run one read "长妈妈是一向带领着故事主人的女工" -- the
        # internal term "story owner", translated, in a sentence for the family.
        f"Claims are printed word for word on a sheet the family reads. Write "
        f"each one in the third person and call the story owner {called}: never "
        f"\"I\", \"the story owner\", \"the speaker\", or a translation of those."
    )

    return llm.complete_json(
        system=SYSTEM,
        prompt=prompt,
        output_model=ExtractionResult,
        max_tokens=llm.EXTRACTION_TOKENS,
    )


# ---------------------------------------------------------------------------
# Merging results into the archive
# ---------------------------------------------------------------------------


def _resolve_sources(
    ids: list[str], valid: dict[str, Utterance]
) -> tuple[list[SourceRef], list[str]]:
    """Turn cited ids into SourceRefs, reporting any that do not exist.

    A citation to an utterance that is not in the archive is a fabricated
    citation. We drop it and tell the operator rather than storing it.
    """
    refs: list[SourceRef] = []
    bad: list[str] = []
    for uid in ids:
        u = valid.get(uid)
        if u is None:
            bad.append(uid)
            continue
        refs.append(SourceRef(kind="utterance", ref_id=uid, locator=u.t_start))
    return refs, bad


def _source_ids(refs: list[SourceRef]) -> set[str]:
    return {ref.ref_id for ref in refs if ref.kind == "utterance"}


def _title_key(title: str) -> str:
    # Unicode-aware: keeping only a-z0-9 reduced every Chinese title to an
    # empty key and silently switched title-based de-duplication off.
    return re.sub(r"[\W_]", "", lang.to_script(title, lang.ZH_HANS).lower())


def _find_duplicate_card(
    archive: Archive, title: str, sources: list[SourceRef]
) -> StoryCard | None:
    """Find an existing card describing the same moment.

    Extraction windows overlap on purpose, so a scene sitting in the overlap is
    sent to the model twice and comes back twice. Without this, roughly a third
    of a case's story cards were duplicates -- which wasted the operator's
    review time and inflated the very counts §18 measures.
    """
    incoming = _source_ids(sources)
    if not incoming:
        return None
    key = _title_key(title)

    for card in archive.story_cards:
        existing = _source_ids(card.sources)
        if not existing:
            continue
        overlap = len(incoming & existing) / len(incoming | existing)
        if overlap >= 0.5:
            return card
        # Same title over any shared source is the same scene retold.
        if key and key == _title_key(card.title) and (incoming & existing):
            return card
    return None


def _merge_into_card(card: StoryCard, incoming: ExtractedStoryCard, refs: list[SourceRef]) -> None:
    """Fold a second extraction of the same scene into the one we already have.

    Keeps the richer version field by field rather than picking a winner, since
    the two passes often saw different halves of the moment.
    """
    if incoming.strength > card.strength:
        card.strength = incoming.strength
        card.title = incoming.title
    for beat in ("conflict", "choice", "consequence", "reflection"):
        if not getattr(card, beat) and getattr(incoming, beat):
            setattr(card, beat, getattr(incoming, beat))
    if len(incoming.setup) > len(card.setup):
        card.setup = incoming.setup
    if card.year is None and incoming.year is not None:
        card.year = incoming.year
        card.year_end = incoming.year_end
        card.approximate = incoming.approximate
    # The stricter sensitivity always wins.
    if incoming.sensitivity == "restricted":
        card.sensitivity = Sensitivity.RESTRICTED

    known = _source_ids(card.sources)
    card.sources.extend(ref for ref in refs if ref.ref_id not in known)


def _find_duplicate_claim(archive: Archive, text: str, kind: ClaimKind) -> Claim | None:
    key = _clean_text(text)
    for claim in archive.claims:
        if claim.kind is kind and _clean_text(claim.text) == key:
            return claim
    return None


def _find_duplicate_quote(archive: Archive, text: str, source_id: str) -> Quote | None:
    key = _clean_text(text)
    for quote in archive.quotes:
        if quote.source.ref_id == source_id and _clean_text(quote.text) == key:
            return quote
    return None


def _find_or_add_person(archive: Archive, name: str, relationship: str | None) -> Person:
    lowered = name.strip().lower()
    for p in archive.people:
        if any(n.strip().lower() == lowered for n in p.all_names()):
            if relationship and not p.relationship:
                p.relationship = relationship
            return p
    person = Person(name=name.strip(), relationship=relationship)
    archive.people.append(person)
    return person


def _find_or_add_place(archive: Archive, name: str, region: str | None) -> Place:
    lowered = name.strip().lower()
    for p in archive.places:
        if p.name.strip().lower() == lowered:
            return p
    place = Place(name=name.strip(), region=region)
    archive.places.append(place)
    return place


def _name_the_owner(archive: Archive, result: ExtractionResult) -> None:
    """Put the owner's name wherever the model wrote "the story owner".

    The prompt asks for the name; this makes sure of it, before duplicates
    are compared, so "故事主人的保姆" and "鲁迅的保姆" are recognised as one
    claim. Quotes are never touched: they are the owner's own words.
    """
    name = archive.meta.owner_preferred_name or archive.meta.owner_name
    code = archive.meta.language
    for claim in result.claims:
        claim.text = lang.name_the_owner(claim.text, name, code)
    for card in result.story_cards:
        for field in ("title", "setup", "conflict", "choice", "consequence", "reflection"):
            value = getattr(card, field)
            if value:
                setattr(card, field, lang.name_the_owner(value, name, code))


class MergeReport(BaseModel):
    story_cards_added: int = 0
    people_added: int = 0
    places_added: int = 0
    claims_added: int = 0
    quotes_added: int = 0
    # Records folded into an existing one because a window overlap produced
    # them twice. Expected to be non-zero on any multi-window session.
    duplicates_merged: int = 0
    dropped_citations: list[str] = Field(default_factory=list)
    unquotable: list[str] = Field(default_factory=list)
    followups: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def merge(
    archive: Archive,
    result: ExtractionResult,
    window: list[Utterance],
) -> MergeReport:
    """Fold an extraction result into the archive, verifying every citation."""
    report = MergeReport()
    valid = {u.id: u for u in window}
    window_text = {u.id: u.text for u in window}
    _name_the_owner(archive, result)

    people_before = len(archive.people)
    places_before = len(archive.places)

    for ep in result.people:
        refs, bad = _resolve_sources(ep.source_utterance_ids, valid)
        report.dropped_citations.extend(bad)
        person = _find_or_add_person(archive, ep.name, ep.relationship)
        person.sources.extend(refs)

    for epl in result.places:
        refs, bad = _resolve_sources(epl.source_utterance_ids, valid)
        report.dropped_citations.extend(bad)
        place = _find_or_add_place(archive, epl.name, epl.region)
        place.sources.extend(refs)

    window_claims: list[Claim] = []
    for ec in result.claims:
        refs, bad = _resolve_sources(ec.source_utterance_ids, valid)
        report.dropped_citations.extend(bad)
        if not refs:
            continue

        kind = ClaimKind(ec.kind)
        existing = _find_duplicate_claim(archive, ec.text, kind)
        if existing is not None:
            known = _source_ids(existing.sources)
            existing.sources.extend(ref for ref in refs if ref.ref_id not in known)
            existing.importance = max(existing.importance, ec.importance)
            window_claims.append(existing)
            report.duplicates_merged += 1
            continue

        claim = Claim(
            text=ec.text,
            kind=kind,
            status=ClaimStatus.STATED_BY_OWNER,
            importance=ec.importance,
            sources=refs,
        )
        archive.claims.append(claim)
        window_claims.append(claim)
        report.claims_added += 1

    for esc in result.story_cards:
        refs, bad = _resolve_sources(esc.source_utterance_ids, valid)
        report.dropped_citations.extend(bad)
        if not refs:
            # A story card with no verifiable source is not admissible.
            report.notes.append(f"dropped story card '{esc.title}': no valid citations")
            continue

        quote_ids: list[str] = []
        for eq in esc.quotes:
            source_text = window_text.get(eq.source_utterance_id)
            if source_text is None:
                report.dropped_citations.append(eq.source_utterance_id)
                continue
            # The same verbatim test the export validator will apply, so the
            # two cannot disagree about what counts as a quote.
            if not quote_matches_source(eq.text, source_text):
                report.unquotable.append(eq.text[:60])
                continue

            duplicate = _find_duplicate_quote(archive, eq.text, eq.source_utterance_id)
            if duplicate is not None:
                quote_ids.append(duplicate.id)
                continue

            quote = Quote(
                text=eq.text.strip(),
                source=SourceRef(
                    kind="utterance",
                    ref_id=eq.source_utterance_id,
                    locator=valid[eq.source_utterance_id].t_start,
                ),
            )
            archive.quotes.append(quote)
            quote_ids.append(quote.id)
            report.quotes_added += 1

        # A card the model flagged as a private disclosure protects everything
        # derived from the same utterances, not just itself (§12.2).
        if esc.sensitivity == "restricted":
            for ref in refs:
                utterance = valid.get(ref.ref_id)
                if utterance is not None:
                    utterance.sensitivity = Sensitivity.RESTRICTED

        # Claims resting on the same utterances belong to this scene. Without
        # this link the confirmation ranking and the excluded-claim validator
        # were both unreachable.
        claim_ids = [
            claim.id
            for claim in window_claims
            if _source_ids(claim.sources) & _source_ids(refs)
        ]

        existing_card = _find_duplicate_card(archive, esc.title, refs)
        if existing_card is not None:
            _merge_into_card(existing_card, esc, refs)
            existing_card.quote_ids.extend(
                qid for qid in quote_ids if qid not in existing_card.quote_ids
            )
            existing_card.claim_ids.extend(
                cid for cid in claim_ids if cid not in existing_card.claim_ids
            )
            report.duplicates_merged += 1
            continue

        person_ids = [_find_or_add_person(archive, n, None).id for n in esc.people]
        place_ids = [_find_or_add_place(archive, n, None).id for n in esc.places]

        card = StoryCard(
            title=esc.title,
            period=esc.period,
            year=esc.year,
            year_end=esc.year_end,
            approximate=esc.approximate,
            setup=esc.setup,
            conflict=esc.conflict,
            choice=esc.choice,
            consequence=esc.consequence,
            reflection=esc.reflection,
            person_ids=person_ids,
            place_ids=place_ids,
            quote_ids=quote_ids,
            claim_ids=claim_ids,
            sources=refs,
            strength=esc.strength,
            access=Access.OPERATOR,
            sensitivity=Sensitivity(esc.sensitivity),
            operator_note=esc.followup_question,
        )
        archive.story_cards.append(card)
        report.story_cards_added += 1

        if esc.followup_question:
            report.followups.append(esc.followup_question)

    report.people_added = len(archive.people) - people_before
    report.places_added = len(archive.places) - places_before
    if result.notes:
        report.notes.append(result.notes)
    return report


MIN_SPLIT_WINDOW = 8
SPLIT_OVERLAP = 2


def _extract_adaptively(
    archive: Archive, window: list[Utterance], report: MergeReport, depth: int = 0
) -> list[tuple[list[Utterance], ExtractionResult]]:
    """Extract a window; if the answer will not fit in one response, halve it.

    A 12-minute Mandarin session overflowed a single response: Chinese output
    and reasoning both spend tokens freely, and the model is thorough. Rather
    than fail the case, the window is split in two (overlapping slightly, so a
    scene on the boundary is seen whole by at least one half -- duplicates are
    merged afterwards) and each half is extracted on its own.
    """
    try:
        result = extract_window(
            window,
            owner_name=archive.meta.owner_name,
            preferred_name=archive.meta.owner_preferred_name,
            language=archive.meta.language,
        )
        return [(window, result)]
    except llm.TruncatedError:
        if len(window) < MIN_SPLIT_WINDOW or depth >= 3:
            raise
        middle = len(window) // 2
        report.notes.append(
            f"a {len(window)}-line window was too long for one response and was "
            f"split in two"
        )
        first = window[: middle + SPLIT_OVERLAP]
        second = window[max(0, middle - SPLIT_OVERLAP):]
        return _extract_adaptively(archive, first, report, depth + 1) + _extract_adaptively(
            archive, second, report, depth + 1
        )


def extract_session(
    archive: Archive,
    session_id: str,
    *,
    window_size: int = DEFAULT_WINDOW,
    overlap: int = DEFAULT_OVERLAP,
    owner_only: bool = True,
    progress: Callable[[int, int], None] | None = None,
    checkpoint: Callable[[], None] | None = None,
) -> MergeReport:
    """Extract every window of one session and merge the results.

    `checkpoint` is called after each window is merged. Extraction is the most
    expensive thing the operator does, and before this existed a single API
    failure on the last window threw away every window before it. The session
    records how far it got, so running again resumes after the last complete
    window instead of paying for the finished ones twice.
    """
    check_consent(archive)

    utterances = [u for u in archive.utterances if u.session_id == session_id]
    if owner_only:
        # Interviewer turns are kept in the window for context but the model is
        # told who is who; dropping them entirely loses the question that
        # prompted each answer.
        utterances = [
            u for u in utterances if u.role in {SpeakerRole.OWNER, SpeakerRole.INTERVIEWER}
        ]
    if not utterances:
        raise ValueError(f"no utterances for session {session_id}")

    combined = MergeReport()
    windows = _windows(utterances, window_size, overlap)

    session = archive.session(session_id)
    plan = f"{window_size}/{overlap}/{len(utterances)}"
    resume_after = 0
    if session is not None:
        if session.extracted_at is None and session.extract_plan == plan:
            resume_after = min(session.extract_windows_done, len(windows))
        session.extracted_at = None
        session.extract_plan = plan
        session.extract_windows_done = resume_after
    if resume_after:
        combined.notes.append(
            f"resumed after window {resume_after} of {len(windows)}: "
            f"the earlier windows were saved last time"
        )

    for index, window in enumerate(windows, start=1):
        if index <= resume_after:
            continue
        if progress:
            progress(index, len(windows))
        for part, result in _extract_adaptively(archive, window, combined):
            report = merge(archive, result, part)
            combined.story_cards_added += report.story_cards_added
            combined.people_added += report.people_added
            combined.places_added += report.places_added
            combined.claims_added += report.claims_added
            combined.quotes_added += report.quotes_added
            combined.duplicates_merged += report.duplicates_merged
            combined.dropped_citations.extend(report.dropped_citations)
            combined.unquotable.extend(report.unquotable)
            combined.followups.extend(report.followups)
            combined.notes.extend(report.notes)

            if checkpoint:
                checkpoint()

        if session is not None:
            session.extract_windows_done = index
            if checkpoint:
                checkpoint()

    if session is not None:
        session.extracted_at = datetime.now(timezone.utc)
    return combined


def api_key_present() -> bool:
    return llm.credentials_present()
