"""Deterministic validators (spec v2 §10.4).

These are plain code, not models, because they must never be creative. They
are the checks that make it safe to let a language model write prose about a
real person's life.

Every validator returns `Finding`s. Severity `BLOCK` stops an export; `WARN`
goes on the operator's QA sheet.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from difflib import SequenceMatcher
from enum import Enum
from typing import Protocol

from pydantic import BaseModel, Field

from . import language as lang
from .models import (
    Access,
    Archive,
    ClaimStatus,
    ConsentScope,
    Sensitivity,
)

# Below this ratio, a "quote" has drifted far enough from the transcript that
# it is no longer the speaker's words.
QUOTE_FIDELITY_THRESHOLD = 0.92
# 300 DPI at 5 inches wide: the practical floor for a photograph in print.
MIN_PRINT_PIXELS = 1500


class Severity(str, Enum):
    BLOCK = "block"
    WARN = "warn"
    INFO = "info"


class Finding(BaseModel):
    check: str
    severity: Severity
    message: str
    ref_id: str | None = None
    detail: str | None = None


class ValidationReport(BaseModel):
    findings: list[Finding] = Field(default_factory=list)

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.BLOCK]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.WARN]

    def ok(self) -> bool:
        return not self.blocking

    def extend(self, more: list[Finding]) -> None:
        self.findings.extend(more)

    def summary(self) -> str:
        blocks = len(self.blocking)
        warns = len(self.warnings)
        warnings = f"{warns} warning" + ("" if warns == 1 else "s")
        if blocks:
            return f"{blocks} blocking, {warnings}"
        if warns:
            return f"clean, {warnings}"
        return "clean"


# ---------------------------------------------------------------------------
# 1. Chronology
# ---------------------------------------------------------------------------


def check_chronology(archive: Archive) -> list[Finding]:
    out: list[Finding] = []
    birth = archive.meta.owner_birth_year

    for card in archive.story_cards:
        if card.year and card.year_end and card.year_end < card.year:
            out.append(
                Finding(
                    check="chronology",
                    severity=Severity.BLOCK,
                    message=f"story card ends before it starts ({card.year}-{card.year_end})",
                    ref_id=card.id,
                    detail=card.title,
                )
            )
        if birth and card.year:
            age = card.year - birth
            if age < 0:
                out.append(
                    Finding(
                        check="chronology",
                        severity=Severity.BLOCK,
                        message=f"dated {card.year}, before the story owner was born ({birth})",
                        ref_id=card.id,
                        detail=card.title,
                    )
                )
            elif age > 110:
                out.append(
                    Finding(
                        check="chronology",
                        severity=Severity.WARN,
                        message=f"implies an age of {age}",
                        ref_id=card.id,
                        detail=card.title,
                    )
                )

    for person in archive.people:
        if person.birth_year and person.death_year and person.death_year < person.birth_year:
            out.append(
                Finding(
                    check="chronology",
                    severity=Severity.BLOCK,
                    message=f"{person.name} dies ({person.death_year}) before birth ({person.birth_year})",
                    ref_id=person.id,
                )
            )

    for event in archive.events:
        if event.year and event.year_end and event.year_end < event.year:
            out.append(
                Finding(
                    check="chronology",
                    severity=Severity.BLOCK,
                    message=f"event ends before it starts ({event.year}-{event.year_end})",
                    ref_id=event.id,
                    detail=event.summary,
                )
            )

    return out


# ---------------------------------------------------------------------------
# 2. Name consistency
# ---------------------------------------------------------------------------


def _normalise(name: str) -> str:
    # Unicode-aware. The first version kept only a-z, which reduced every
    # Chinese name to an empty string -- so the one check built to catch
    # misheard names skipped Chinese entirely, the language whose recognition
    # fails by homophone.
    return re.sub(r"[\W_]", "", lang.to_script(name, lang.ZH_HANS).lower())


def _tokens(name: str) -> list[str]:
    return [t for t in re.split(r"[^a-z]+", name.lower()) if t]


def _duplicate_signal(a: str, b: str) -> str | None:
    """Why `a` and `b` might be the same person, or None.

    Raw string similarity alone misses the commonest real case -- "Ron Hale"
    and "Ronald Hale" score only 0.82 -- so the shared-surname rule carries
    most of the weight here. For Chinese, the telling case is a homophone:
    王秀英 and 王秀瑛 share two characters in three and score 0.67, but they
    are pronounced identically, which is exactly how speech recognition
    produces the second from the first.
    """
    na, nb = _normalise(a), _normalise(b)
    if not na or not nb:
        return None

    if na == nb:
        return "identical names on separate records"

    if lang.CJK_PATTERN.search(na) and lang.CJK_PATTERN.search(nb):
        if lang.pronunciation_key(na) == lang.pronunciation_key(nb):
            return (
                f"same pronunciation ({lang.pronunciation_key(na)}), different "
                f"characters -- probably one name misheard"
            )
        if len(na) == len(nb) >= 2 and na[0] == nb[0]:
            differing = sum(1 for x, y in zip(na, nb) if x != y)
            if differing == 1 and len(na) >= 3:
                return "same surname, one character different"
        return None

    ta, tb = _tokens(a), _tokens(b)
    if len(ta) > 1 and len(tb) > 1 and ta[-1] == tb[-1]:
        first_a, first_b = ta[0], tb[0]
        if first_a != first_b and (
            first_a.startswith(first_b) or first_b.startswith(first_a)
        ):
            return "same surname, one forename is a short form of the other"

    ratio = SequenceMatcher(None, na, nb).ratio()
    return f"similarity {ratio:.2f}" if ratio >= 0.85 else None


def check_names(archive: Archive) -> list[Finding]:
    """Catch the same person recorded under variant spellings.

    Getting a relative's name wrong in a printed book is the single most
    reliable way to make a family wince (§7.4).
    """
    out: list[Finding] = []
    seen: list[tuple[str, str]] = []  # (display name, person_id)
    reported: set[tuple[str, str]] = set()

    for person in archive.people:
        for name in person.all_names():
            if not _normalise(name):
                continue
            for other_name, other_id in seen:
                if other_id == person.id:
                    continue
                pair = tuple(sorted((person.id, other_id)))
                if pair in reported:
                    continue
                signal = _duplicate_signal(name, other_name)
                if signal:
                    reported.add(pair)
                    out.append(
                        Finding(
                            check="names",
                            severity=Severity.WARN,
                            message=f"'{name}' and '{other_name}' may be the same person",
                            ref_id=person.id,
                            detail=f"{signal}; merge them or add an alias",
                        )
                    )
            seen.append((name, person.id))

    unnamed = [p for p in archive.people if not p.name.strip()]
    for person in unnamed:
        out.append(
            Finding(
                check="names",
                severity=Severity.BLOCK,
                message="person record with an empty name",
                ref_id=person.id,
            )
        )

    return out


# ---------------------------------------------------------------------------
# 3. Quote fidelity -- diffed against the source transcript
# ---------------------------------------------------------------------------


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", text.lower())).strip()


def quote_matches_source(quote_text: str, source_text: str) -> bool:
    """Whether `quote_text` is a faithful reproduction of `source_text`.

    Shared with the extraction merge so that both agree on what "verbatim"
    means. They disagreed in the first implementation -- merge did a raw
    substring test and flagged every quote whose punctuation differed from the
    transcript, which is most of them.
    """
    needle = _squash(quote_text)
    haystack = _squash(source_text)
    if not needle:
        return False
    return _partial_ratio(needle, haystack) >= QUOTE_FIDELITY_THRESHOLD


def _partial_ratio(needle: str, haystack: str) -> float:
    """How well `needle` matches the best same-length window of `haystack`.

    Comparing a quote against the *whole* utterance (as the first version did)
    punishes quoting part of a long line: a 15-character Chinese quote with one
    character changed scored about 0.2 against its 120-character source and
    would have blocked the export. Windowing also stops scattered common
    characters (的, 我, 了) from adding up to a false match, which a plain
    count of matching characters across a long line would allow.
    """
    if not needle or not haystack:
        return 0.0
    if needle in haystack:
        return 1.0
    if len(needle) >= len(haystack):
        return SequenceMatcher(None, needle, haystack, autojunk=False).ratio()

    best = 0.0
    matcher = SequenceMatcher(None, needle, haystack, autojunk=False)
    for block in matcher.get_matching_blocks():
        start = max(0, min(block.b - block.a, len(haystack) - len(needle)))
        window = haystack[start : start + len(needle)]
        best = max(best, SequenceMatcher(None, needle, window, autojunk=False).ratio())
        if best >= 0.999:
            break
    return best


def check_quote_fidelity(archive: Archive) -> list[Finding]:
    """Every quote must still match what the speaker actually said."""
    out: list[Finding] = []

    for quote in archive.quotes:
        source = archive.utterance(quote.source.ref_id)
        if source is None:
            out.append(
                Finding(
                    check="quote_fidelity",
                    severity=Severity.BLOCK,
                    message="quote cites an utterance that is not in the archive",
                    ref_id=quote.id,
                    detail=quote.text[:80],
                )
            )
            continue

        needle = _squash(quote.text)
        if not needle:
            continue

        # The same test extraction applied when it stored the quote.
        ratio = _partial_ratio(needle, _squash(source.text))
        if ratio < QUOTE_FIDELITY_THRESHOLD:
            out.append(
                Finding(
                    check="quote_fidelity",
                    severity=Severity.BLOCK,
                    message=f"quote does not match its source (similarity {ratio:.2f})",
                    ref_id=quote.id,
                    detail=f"quote: {quote.text[:70]!r} / source: {source.text[:70]!r}",
                )
            )

    return out


# Quotation marks in both scripts: “…” "…" 「…」 『…』.
_QUOTED = re.compile(
    '“([^”]{2,400})”|"([^"]{2,400})"|'
    "「([^」]{2,400})」|『([^』]{2,400})』"
)


def _squash(text: str) -> str:
    """`_clean` without spaces, so a quote spanning two transcript lines matches."""
    if lang.CJK_PATTERN.search(text):
        text = lang.to_script(text, lang.ZH_HANS)
    return re.sub(r"\s+", "", _clean(text))


def check_draft_quotes(archive: Archive, draft_text: str) -> list[Finding]:
    """Any quoted passage in a draft must trace to a real utterance.

    This is the check that catches a model inventing a plausible-sounding line
    for someone's grandmother.
    """
    language = archive.meta.language
    out: list[Finding] = []
    corpus = "".join(_squash(u.text) for u in archive.utterances)
    lines = [_squash(u.text) for u in archive.utterances]

    for match in _QUOTED.finditer(draft_text):
        span = next(g for g in match.groups() if g is not None)
        if lang.units(span) < lang.quote_min_units(language):
            continue
        quoted = _squash(span)
        if not quoted or quoted in corpus:
            continue
        # Fall back to a windowed comparison before crying wolf.
        best = max((_partial_ratio(quoted, line) for line in lines), default=0.0)
        if best < QUOTE_FIDELITY_THRESHOLD:
            out.append(
                Finding(
                    check="draft_quotes",
                    severity=Severity.BLOCK,
                    message=f"quoted passage not found in any transcript (best match {best:.2f})",
                    detail=span[:90],
                )
            )

    return out


# ---------------------------------------------------------------------------
# 4. Consent gate
# ---------------------------------------------------------------------------


def check_consent(archive: Archive, scopes: list[ConsentScope]) -> list[Finding]:
    return [
        Finding(
            check="consent",
            severity=Severity.BLOCK,
            message=f"no live consent record for '{scope.value}'",
        )
        for scope in archive.missing_consents(scopes)
    ]


# ---------------------------------------------------------------------------
# 5. Access leak -- the §12.2 enforcement
# ---------------------------------------------------------------------------


def check_access(archive: Archive, audience: Access = Access.FAMILY) -> list[Finding]:
    """Nothing the story owner has not released may reach the family.

    This is the check that protects the disclosure in §12.2. If it ever starts
    failing noisily, the answer is to release material deliberately -- not to
    relax the check.
    """
    out: list[Finding] = []

    for card in archive.story_cards:
        # Restricted material must never be released, whatever else is true.
        # Checked across every card, not just the usable ones -- filtering
        # first would make this check unreachable for exactly the material it
        # exists to protect.
        if card.sensitivity is Sensitivity.RESTRICTED and card.access.rank() >= Access.FAMILY.rank():
            out.append(
                Finding(
                    check="access",
                    severity=Severity.BLOCK,
                    message=f"restricted story card is released to {card.access.value}",
                    ref_id=card.id,
                    detail=card.title,
                )
            )

        # Only released cards can leak; an operator-level card is not yet out.
        if card.access.rank() < audience.rank():
            continue

        for uid in (s.ref_id for s in card.sources if s.kind == "utterance"):
            source = archive.utterance(uid)
            if source is None:
                continue
            if not source.access.visible_to(audience):
                out.append(
                    Finding(
                        check="access",
                        severity=Severity.BLOCK,
                        message=(
                            f"story card is released to {audience.value} but rests on an "
                            f"{source.access.value} utterance"
                        ),
                        ref_id=card.id,
                        detail=f"{card.title} <- {uid}",
                    )
                )

    for quote in archive.quotes:
        if quote.publication_ok and quote.access is Access.OWNER_ONLY:
            out.append(
                Finding(
                    check="access",
                    severity=Severity.BLOCK,
                    message="quote marked publishable but still owner-only",
                    ref_id=quote.id,
                    detail=quote.text[:70],
                )
            )

    return out


def check_confirmation_safety(
    archive: Archive, audience: Access = Access.FAMILY
) -> list[Finding]:
    """Nothing the family is asked to confirm may rest on unreleased material.

    The confirmation sheet is the one artifact that goes to the family *before*
    the book, and in the first implementation it was the leak: a claim
    extracted from a restricted disclosure went into the sheet quoting the
    recording verbatim. `confirm.build` now withholds those, and this is the
    independent check that it still does.
    """
    from . import confirm  # local import: confirm has no dependency on us

    out: list[Finding] = []

    # Restricted material must never have been released in the first place.
    for utterance in archive.utterances:
        if (
            utterance.sensitivity is Sensitivity.RESTRICTED
            and utterance.access.rank() >= Access.FAMILY.rank()
        ):
            out.append(
                Finding(
                    check="confirmation",
                    severity=Severity.BLOCK,
                    message=(
                        f"restricted material has been released to "
                        f"{utterance.access.value}"
                    ),
                    ref_id=utterance.id,
                    detail=utterance.text[:70],
                )
            )

    # Whatever the queue actually contains must be visible to its audience.
    for item in confirm.build(archive, audience=audience).items:
        claim = archive.claim(item.claim_id)
        if claim is None or not archive.visible_to(claim, audience):
            out.append(
                Finding(
                    check="confirmation",
                    severity=Severity.BLOCK,
                    message="confirmation item rests on material not released to the family",
                    ref_id=item.claim_id,
                    detail=item.current_value[:70],
                )
            )

    return out


def check_confirmation_wording(archive: Archive) -> list[Finding]:
    """Every item on the family's sheet should name the story owner.

    The family reads each claim exactly as written. "I was born in 1931" on a
    sheet sent to a daughter reads as though she wrote it. Extraction swaps
    unmistakable stand-ins for the owner's name; this catches what cannot be
    swapped safely, so the operator can reword it before the sheet goes out.
    """
    from . import confirm  # local import: confirm has no dependency on us

    code = archive.meta.language
    out: list[Finding] = []
    for item in confirm.build(archive).items:
        problem = lang.wording_problem(item.current_value, code)
        if problem:
            out.append(
                Finding(
                    check="wording",
                    severity=Severity.WARN,
                    message=f"the family will read this as written: it {problem}",
                    ref_id=item.claim_id,
                    detail=item.current_value[:70],
                )
            )
    return out


# ---------------------------------------------------------------------------
# 6. Sources and print readiness
# ---------------------------------------------------------------------------


def check_sources(archive: Archive) -> list[Finding]:
    out: list[Finding] = []

    for card in archive.story_cards:
        if not card.sources:
            out.append(
                Finding(
                    check="sources",
                    severity=Severity.BLOCK,
                    message="story card has no source references",
                    ref_id=card.id,
                    detail=card.title,
                )
            )
        for ref in card.sources:
            if ref.kind == "utterance" and archive.utterance(ref.ref_id) is None:
                out.append(
                    Finding(
                        check="sources",
                        severity=Severity.BLOCK,
                        message=f"story card cites unknown utterance {ref.ref_id}",
                        ref_id=card.id,
                    )
                )

    excluded = [c for c in archive.claims if c.status is ClaimStatus.EXCLUDED]
    for claim in excluded:
        for card in archive.story_cards:
            if claim.id in card.claim_ids:
                out.append(
                    Finding(
                        check="sources",
                        severity=Severity.BLOCK,
                        message="story card depends on an excluded claim",
                        ref_id=card.id,
                        detail=claim.text[:70],
                    )
                )

    return out


def check_print_readiness(archive: Archive) -> list[Finding]:
    out: list[Finding] = []

    for artifact in archive.artifacts:
        if artifact.kind != "photograph":
            continue
        if artifact.pixel_width and artifact.pixel_width < MIN_PRINT_PIXELS:
            out.append(
                Finding(
                    check="print",
                    severity=Severity.WARN,
                    message=f"photograph is {artifact.pixel_width}px wide; below print quality",
                    ref_id=artifact.id,
                    detail=artifact.caption or artifact.path,
                )
            )
        if artifact.transformation and not artifact.original_path:
            out.append(
                Finding(
                    check="print",
                    severity=Severity.BLOCK,
                    message=f"'{artifact.transformation}' applied but no original preserved",
                    ref_id=artifact.id,
                )
            )
        if artifact.transformation and not (artifact.caption or "").strip():
            out.append(
                Finding(
                    check="print",
                    severity=Severity.WARN,
                    message="transformed image has no caption; transformations must be labelled",
                    ref_id=artifact.id,
                )
            )

    return out


# ---------------------------------------------------------------------------
# 7. The drafted book, chapter by chapter
# ---------------------------------------------------------------------------


class _Chapter(Protocol):
    number: int
    prose: str
    provenance: list[str]


def _run_tokens(text: str, code: str | None) -> list[str]:
    return list(_squash(text)) if lang.is_chinese(code) else _clean(text).split()


def check_repeated_passages(chapters: Sequence[_Chapter], code: str | None) -> list[Finding]:
    """The same passage told in two chapters.

    Found on the Mandarin demo: two story cards both carried the doctor being
    carried past in his sedan chair, and chapters 5 and 6 each told it, a page
    apart. Every other check passed. A family reads it as a mistake.
    """
    chinese = lang.is_chinese(code)
    size, joiner = (15, "") if chinese else (12, " ")  # characters / words
    first_seen: dict[str, int] = {}
    reported: set[tuple[int, int]] = set()
    out: list[Finding] = []
    for chapter in sorted(chapters, key=lambda c: c.number):
        tokens = _run_tokens(chapter.prose, code)
        for i in range(len(tokens) - size + 1):
            run = joiner.join(tokens[i : i + size])
            earlier = first_seen.setdefault(run, chapter.number)
            if earlier != chapter.number and (earlier, chapter.number) not in reported:
                reported.add((earlier, chapter.number))
                out.append(
                    Finding(
                        check="repetition",
                        severity=Severity.WARN,
                        message=f"chapters {earlier} and {chapter.number} tell the same passage",
                        ref_id=f"chapter {chapter.number}",
                        detail=run,
                    )
                )
    return out


class Unsupported(BaseModel):
    chapter: int
    sentence: str
    support: float  # share of the sentence found anywhere in the recordings


_STOP_EN = set(
    "the and but for nor yet with from into onto than then that this these those there "
    "their they them she her hers his him was were are is been being have has had "
    "not all any some one what when where which who whom whose why how would could "
    "should will shall can may might must did does done very just also only even "
    "about after again before over under once more most much such own same so too "
    "out off our ours you your its".split()
)
_CJK_RUN = re.compile(f"[{lang.CJK_RANGE}]+")


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def _support_units(text: str, chinese: bool) -> list[str]:
    """What a sentence is compared on.

    Chinese: character pairs. Whole words were tried first and failed on the
    demo: one misheard character in the transcript (a homophone, the usual
    Chinese error) made a correctly written word look invented. A pair
    survives the error on either side of it. Everything else: content words,
    stemmed, because a paraphrase keeps its words and loses its order.
    """
    if chinese:
        text = lang.to_script(text, lang.ZH_HANS)
        return [run[i : i + 2] for run in _CJK_RUN.findall(text) for i in range(len(run) - 1)]
    return [_stem(w) for w in _clean(text).split() if len(w) > 2 and w not in _STOP_EN]


def least_supported(
    archive: Archive,
    chapters: Sequence[_Chapter],
    *,
    per_chapter: int = 5,
    below: float = 0.5,
) -> list[Unsupported]:
    """The sentences most likely to be invented, for the QA spot-check.

    Not a verdict: nothing can prove a sentence was never said, a faithful
    paraphrase can score low, and an invented link between two true things
    can score high. On the Mandarin demo it listed 11 of 134 sentences and 6
    of those were invented -- among them a family pawning their clothes to pay
    the doctor, which the recording never says. The other 5 were faithful
    paraphrases or words the transcript still had wrong. So the human
    spot-check (QA checklist, Fabrication) starts here, not at random.

    Compared against every recording in the case, not just the ones a chapter
    cites: a true sentence drawn from elsewhere is not the danger.
    """
    chinese = lang.is_chinese(archive.meta.language)
    minimum = 5 if chinese else 3  # shorter than this, too little to judge
    heard = set(_support_units(" ".join(u.text for u in archive.utterances), chinese))

    out: list[Unsupported] = []
    for chapter in sorted(chapters, key=lambda c: c.number):
        scored = []
        for paragraph in chapter.prose.splitlines():
            for sentence in lang.split_sentences(paragraph.strip()):
                units = _support_units(sentence, chinese)
                if len(units) < minimum:
                    continue
                support = sum(u in heard for u in units) / len(units)
                if support < below:
                    scored.append(
                        Unsupported(chapter=chapter.number, sentence=sentence, support=support)
                    )
        scored.sort(key=lambda u: u.support)
        out.extend(scored[:per_chapter])
    return out


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_all(
    archive: Archive,
    *,
    draft_text: str | None = None,
    chapters: Sequence[_Chapter] | None = None,
    for_export: bool = False,
) -> ValidationReport:
    report = ValidationReport()
    report.extend(check_chronology(archive))
    report.extend(check_names(archive))
    report.extend(check_quote_fidelity(archive))
    report.extend(check_sources(archive))
    report.extend(check_access(archive))
    report.extend(check_confirmation_safety(archive))
    report.extend(check_print_readiness(archive))

    if chapters:
        draft_text = draft_text or "\n\n".join(c.prose for c in chapters)
        report.extend(check_repeated_passages(chapters, archive.meta.language))
    if draft_text:
        report.extend(check_draft_quotes(archive, draft_text))

    if for_export:
        report.extend(
            check_consent(
                archive,
                [
                    ConsentScope.RECORDING,
                    ConsentScope.AI_PROCESSING,
                    ConsentScope.FAMILY_SHARING,
                    ConsentScope.PRINT,
                ],
            )
        )

    return report
