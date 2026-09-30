"""Transcript ingestion (spec v2 §14, §11 "Speech recognition is a named risk").

Turns a raw transcript file into `Utterance` records with speaker and timestamp
references. Deliberately forgiving about format, because real Phase 0 material
arrives as whatever the recorder produced.

Everything ingested defaults to OWNER_ONLY (§6.7). Nothing here decides what
the family may see.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from . import language as lang
from .models import Access, Archive, Session, SpeakerRole, Utterance

# "[00:12:31] Margaret: I was born in the back bedroom."
# "00:12:31 Margaret: ..."
# "Margaret (00:12:31): ..."
# "Margaret: ..."
_TIMESTAMP = r"(?:\[?(?P<ts>\d{1,2}:\d{2}(?::\d{2})?)\]?)"
# Parentheses are allowed: `transcribe` emits "Name (owner):" when the operator
# is also the story owner. Brackets stay out so a leading [timestamp] cannot be
# swallowed into the speaker name. Both colons are accepted, because Chinese
# transcripts use the full-width one.
_SPEAKER = r"(?P<speaker>[^:：\n\[\]]{1,40}?)"
_COLON = r"\s*[:：]\s*"

_PATTERNS = [
    re.compile(rf"^\s*{_TIMESTAMP}\s*{_SPEAKER}{_COLON}(?P<text>.*)$"),
    re.compile(rf"^\s*{_SPEAKER}\s*\(\s*{_TIMESTAMP}\s*\){_COLON}(?P<text>.*)$"),
    re.compile(rf"^\s*{_SPEAKER}{_COLON}(?P<text>.*)$"),
]

# Chinese narrative is full of 他说：“……” and 妈妈问我：…… -- a colon after a
# speech verb introduces reported speech, not a new speaker. Labels from
# common transcription tools are let through explicitly, since iFlytek writes
# 说话人1 and would otherwise trip the speech-verb test.
_TOOL_LABEL = re.compile(
    r"^(?:说话人|說話人|发言人|發言人|讲话人|講話人|Speaker\s*)\s*\d+$", re.IGNORECASE
)
_SPEECH_VERBS = set("说道问讲喊叫答說問講")
_LABEL_PUNCTUATION = re.compile("[，。！？、,.!?“”\"]")
_MAX_LABEL_UNITS = 12
_ENDS_CJK = re.compile(f"[{lang.CJK_RANGE}，。！？、]$")


def _plausible_speaker(label: str) -> bool:
    label = label.strip()
    if not label:
        return False
    if _TOOL_LABEL.match(label):
        return True
    if _LABEL_PUNCTUATION.search(label) or lang.units(label) > _MAX_LABEL_UNITS:
        return False
    # Speech verbs only disqualify Chinese labels; "Operator" and "Margaret"
    # contain none of these characters anyway.
    return not (set(label) & _SPEECH_VERBS)

# Lines that are not speech. `#` matters most: `transcribe` writes a comment
# header, and without this the line "# 0.7 minutes, language detected: en"
# parsed as a speaker turn and then swallowed the entire transcript after it
# as continuation text -- a silent, total loss that extraction then ran on.
_NOT_SPEECH = re.compile(r"^\s*(?:#|//|WEBVTT|\d+\s*$|\d{1,2}:\d{2}:\d{2}[.,]\d{3}\s*-->)")


def _role_for(speaker: str, owner_names: list[str], interviewer_names: list[str]) -> SpeakerRole:
    """Decide who is speaking.

    Exact matches are settled before any substring match is considered.
    Checking owner-by-substring first meant "Lee" matched inside
    "Lee (interviewer)" and every interviewer turn was filed as the story
    owner -- and the same collision would file "Annette" as "Ann".
    """
    low = speaker.strip().lower()
    owners = [n.strip().lower() for n in owner_names if n and n.strip()]
    interviewers = [n.strip().lower() for n in interviewer_names if n and n.strip()]

    if low in owners:
        return SpeakerRole.OWNER
    if low in interviewers:
        return SpeakerRole.INTERVIEWER

    # Conventional labels, before any fuzzy matching.
    if low in {"interviewer", "operator", "q", "me", "采访者", "访谈者", "采访人",
               "访问者", "记者", "主持人", "问", "採訪者", "訪談者", "記者", "問"}:
        return SpeakerRole.INTERVIEWER
    if low in {"a", "受访者", "被访者", "讲述者", "答", "受訪者", "被訪者", "講述者"}:
        return SpeakerRole.OWNER

    # Substring only where exactly one side claims it.
    owner_hit = any(name in low for name in owners)
    interviewer_hit = any(name in low for name in interviewers)
    if owner_hit and not interviewer_hit:
        return SpeakerRole.OWNER
    if interviewer_hit and not owner_hit:
        return SpeakerRole.INTERVIEWER

    # Both or neither: a parenthesised role is the last honest signal.
    if "(interviewer)" in low or "(operator)" in low:
        return SpeakerRole.INTERVIEWER
    if "(owner)" in low:
        return SpeakerRole.OWNER
    return SpeakerRole.UNKNOWN


def parse_transcript(
    text: str,
    session: Session,
    *,
    owner_names: list[str] | None = None,
    interviewer_names: list[str] | None = None,
) -> list[Utterance]:
    """Parse transcript text into utterances.

    Consecutive lines from the same speaker with no new speaker label are
    folded into the preceding utterance -- ASR output wraps mid-sentence
    constantly and splitting on newlines destroys quotes.
    """
    owner_names = owner_names or []
    interviewer_names = interviewer_names or []

    utterances: list[Utterance] = []
    seq = 0

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip() or _NOT_SPEECH.match(line):
            continue

        matched = None
        for pattern in _PATTERNS:
            m = pattern.match(line)
            if m and m.group("text") is not None and _plausible_speaker(m.group("speaker")):
                matched = m
                break

        if matched is None:
            # Continuation of the previous turn.
            if utterances:
                previous = utterances[-1].text
                # Chinese does not put a space where a line wrapped.
                joiner = "" if _ENDS_CJK.search(previous) else " "
                utterances[-1].text = f"{previous}{joiner}{line.strip()}".strip()
            continue

        speaker = matched.group("speaker").strip()
        body = matched.group("text").strip()
        ts = matched.groupdict().get("ts")

        if not body:
            continue

        seq += 1
        utterances.append(
            Utterance(
                session_id=session.id,
                seq=seq,
                speaker=speaker,
                role=_role_for(speaker, owner_names, interviewer_names),
                t_start=ts,
                text=body,
                access=Access.OWNER_ONLY,
            )
        )

    return utterances


def load_transcript_file(
    path: Path,
    session: Session,
    *,
    owner_names: list[str] | None = None,
    interviewer_names: list[str] | None = None,
) -> list[Utterance]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return parse_transcript(
        text,
        session,
        owner_names=owner_names,
        interviewer_names=interviewer_names,
    )


def _fingerprint(utterances: list[Utterance]) -> str:
    """A content hash of what was said, ignoring ids and session membership."""
    joined = "\n".join(f"{u.speaker}:{u.text}" for u in utterances)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def already_ingested(archive: Archive, utterances: list[Utterance]) -> Session | None:
    """The session these utterances were already loaded as, if any.

    Re-running `ingest` on the same file used to silently double every
    utterance in the archive, which then doubled the extraction bill and the
    story cards that came out of it.
    """
    incoming = _fingerprint(utterances)
    for session in archive.sessions:
        existing = [u for u in archive.utterances if u.session_id == session.id]
        if existing and _fingerprint(existing) == incoming:
            return session
    return None


def ingest_report(utterances: list[Utterance]) -> dict[str, object]:
    """Quick shape check on what was parsed.

    A high `unknown_role` count usually means the speaker labels are not what
    the operator expected -- worth catching before extraction spends tokens.
    """
    by_role: dict[str, int] = {}
    for u in utterances:
        by_role[u.role.value] = by_role.get(u.role.value, 0) + 1

    # Measured in reader units -- characters for Chinese, words otherwise.
    # Counting whitespace let a 3,000-character Chinese blob pass as "100
    # words" and the collapse guard waved it through.
    text = "".join(u.text for u in utterances)
    chinese = bool(re.search(f"[{lang.CJK_RANGE}]", text))
    owner_units = sum(lang.units(u.text) for u in utterances if u.role is SpeakerRole.OWNER)
    longest = max((lang.units(u.text) for u in utterances), default=0)
    return {
        "utterances": len(utterances),
        "by_role": by_role,
        "owner_units": owner_units,
        "unit": "characters" if chinese else "words",
        "has_timestamps": any(u.t_start for u in utterances),
        "unknown_role": by_role.get("unknown", 0),
        # One enormous utterance means the speaker pattern did not match and
        # everything collapsed into a continuation of the first line.
        "longest_utterance_units": longest,
        "suspect_collapse": len(utterances) <= 2 and longest > (300 if chinese else 120),
    }
