"""Local speech-to-text (spec v2 §11 "Core services", §12.1).

Runs entirely on the operator's machine. The audio never leaves it.

That is a deliberate choice, not an optimisation. A recording of an older
adult's voice is more exposing than the words alone -- it carries who they
are, how frail they sound, and every unguarded pause. Sending it to a
transcription vendor would be a second third-party disclosure on top of the
one extraction already makes, and a worse one. Whisper runs locally, so there
is nothing to consent to and nothing to withdraw.

§11 names transcription quality on older voices as a live risk. Nothing here
fixes that. What it does is make the failure *checkable*: the transcript says
plainly that it is machine-generated and unreviewed, and it opens with a
checklist of the names and dates it thinks it heard -- the §18 zero-error
categories, and the things that propagate furthest when one is wrong.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import language as lang

SAMPLE_RATE = 16000

# Whisper splits long speech across its own decode windows with a gap of
# literally zero. Only those artefacts are stitched back together. Anything
# longer stays its own line: a one-second pause is as likely to be a turn
# change as a breath, and guessing wrong merges an interviewer's question into
# the answer after it.
CONTINUATION_GAP_SECONDS = 0.3

# Even a genuine continuation stops being stitched past this length, so one
# speaker talking for ten minutes still reviews as readable lines rather than
# one wall of text.
MAX_TURN_UNITS = {"zh": 120, "other": 80}

MODELS = ("tiny", "base", "small", "medium", "large-v3")


def default_model(code: str | None) -> str:
    """`base` is fine for clear English and poor for Chinese.

    Measured on a real Mandarin recording: `base` rendered the book's own title
    朝花夕拾 as 招花西石 and LibriVox's 录音 ("recording") as 露營 ("camping").
    """
    return "small" if lang.is_chinese(code) else "base"


class TranscriptionError(RuntimeError):
    pass


@dataclass
class Turn:
    """One line of the transcript."""

    start: float
    end: float
    text: str
    speaker: str = "Unknown"

    def timestamp(self) -> str:
        return _clock(self.start)


@dataclass
class CheckItem:
    """Something worth verifying against the audio before extraction."""

    term: str
    kind: str          # person / place / organisation / name / date
    first_at: str      # timestamp of the first mention, to jump straight to it
    count: int = 1
    heard_also: list[str] = field(default_factory=list)  # homophone spellings
    # Primed with --hint. Measured on real Mandarin: a hint once appeared at
    # the start of a window where it was never said. A primed name on the
    # checklist is therefore *not* evidence it was heard correctly.
    hinted: bool = False


@dataclass
class TranscriptionResult:
    turns: list[Turn] = field(default_factory=list)
    language: str = lang.EN
    duration: float = 0.0
    model: str = ""
    checklist: list[CheckItem] = field(default_factory=list)

    @property
    def minutes(self) -> float:
        return round(self.duration / 60, 1)

    @property
    def heard_names(self) -> list[str]:
        return [item.term for item in self.checklist if item.kind != "date"]

    def speaker_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for turn in self.turns:
            out[turn.speaker] = out.get(turn.speaker, 0) + 1
        return out


def _clock(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


# ---------------------------------------------------------------------------
# Turns
# ---------------------------------------------------------------------------


def _assemble_turns(segments, gap: float, code: str) -> list[Turn]:
    """Rejoin Whisper's decode-window splits, and nothing else.

    The first version decided "is this a new sentence?" by asking whether the
    next segment began with a capital letter. Chinese has none, so every
    segment looked like a continuation and a 12.6-minute chapter came back as
    a single line.
    """
    cap = MAX_TURN_UNITS["zh" if lang.is_chinese(code) else "other"]
    turns: list[Turn] = []
    current: Turn | None = None

    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue

        continues = (
            current is not None
            and segment.start - current.end <= gap
            and not lang.ends_sentence(current.text)
            and lang.units(current.text) < cap
        )
        # Latin text only: a sentence that "ended" on an abbreviation ("Mr.")
        # and carries on in lower case is still one sentence.
        if (
            not continues
            and current is not None
            and not lang.is_chinese(code)
            and segment.start - current.end <= gap
            and text[:1].islower()
            and lang.units(current.text) < cap
        ):
            continues = True

        if continues and current is not None:
            joiner = "" if lang.is_chinese(code) else " "
            current.text = f"{current.text}{joiner}{text}".strip()
            current.end = segment.end
        else:
            if current is not None:
                turns.append(current)
            current = Turn(start=segment.start, end=segment.end, text=text)

    if current is not None:
        turns.append(current)
    return turns


def _guess_speakers(turns: list[Turn], owner: str, interviewer: str, code: str) -> None:
    """Label turns by shape. This is a guess and the transcript says so.

    Real diarisation needs voice modelling; this has only timing and wording.
    In an oral-history interview the asymmetry is usually stark -- short
    questions from the interviewer, long answers from the story owner -- and
    the operator reads the transcript against the audio anyway, so fixing the
    misses costs little on top.
    """
    if not turns:
        return

    typical = sorted(lang.units(t.text) for t in turns)[len(turns) // 2]
    threshold = max(12 if lang.is_chinese(code) else 10, typical)
    # Interviewers ask short questions. Measured on a Mandarin monologue, the
    # narrator's own rhetorical 我惧惮她什么呢？ was filed as the interviewer
    # because the median line was long and the limit scaled with it.
    ceiling = min(threshold * 2, 30 if lang.is_chinese(code) else 25)

    for index, turn in enumerate(turns):
        question = lang.looks_like_question(turn.text, code)
        asked = question and lang.units(turn.text) <= ceiling
        # A question that follows the speaker's last words without a pause is
        # the same breath -- the story owner asking themselves -- not a new
        # turn. Older speakers do this constantly; interviewers who are doing
        # their job wait before speaking.
        if asked and index > 0:
            previous = turns[index - 1]
            if previous.speaker == owner and turn.start - previous.end < 0.25:
                asked = False
        turn.speaker = interviewer if asked else owner

    # An interviewer rarely speaks twice running; a story owner often does.
    for index in range(1, len(turns)):
        previous, current = turns[index - 1], turns[index]
        if (
            previous.speaker == interviewer
            and current.speaker == interviewer
            and not current.text.strip().endswith(("?", "？"))
        ):
            current.speaker = owner


# ---------------------------------------------------------------------------
# The checklist: names and dates, with where to find them
# ---------------------------------------------------------------------------


def build_checklist(
    turns: list[Turn], code: str, hints: list[str] | None = None
) -> list[CheckItem]:
    """Names and dates the transcript claims to have heard, with timestamps.

    Why this exists: on the English test recording, Bewdley came back as
    "Beautly" and Kidderminster as "Kitter-Minster", and the segment containing
    the worst of them scored an average log-probability of -0.26 -- identical
    to the segments it got perfectly right. Whisper does not know when it has
    mangled a name, so confidence scoring cannot find these. Grammar can: names
    are listed, each with the first place to hear it, which turns "read the
    whole transcript carefully" into a short, concrete job.

    Chinese recognition fails by homophone rather than by spelling, so names
    that *sound* identical but are written differently are grouped: 王秀英 and
    王秀瑛 on the same list is one person heard two ways.
    """
    items: dict[str, CheckItem] = {}

    for turn in turns:
        for term, kind in lang.proper_nouns(turn.text, code):
            if term in items:
                items[term].count += 1
            else:
                items[term] = CheckItem(term=term, kind=kind, first_at=turn.timestamp())
        for when in lang.dates(turn.text, code):
            if when in items:
                items[when].count += 1
            else:
                items[when] = CheckItem(term=when, kind="date", first_at=turn.timestamp())

    # Fold homophone spellings of one name into a single entry.
    by_sound: dict[str, CheckItem] = {}
    merged: list[CheckItem] = []
    for item in items.values():
        if item.kind == "date":
            merged.append(item)
            continue
        key = lang.pronunciation_key(item.term)
        existing = by_sound.get(key)
        if existing is not None and existing.term != item.term:
            existing.heard_also.append(item.term)
            existing.count += item.count
            continue
        by_sound[key] = item
        merged.append(item)

    # A hinted name belongs on the list even when the name finder would not
    # have picked it out, because a hint can be written in where it was never
    # said -- and then it looks exactly like a name heard correctly.
    primed = {lang.to_script(h, code).strip() for h in (hints or []) if h and h.strip()}
    listed = {i.term for i in merged} | {t for i in merged for t in i.heard_also}
    for turn in turns:
        for hint in primed - listed:
            if hint in turn.text:
                merged.append(CheckItem(term=hint, kind="name", first_at=turn.timestamp()))
                listed.add(hint)
    for item in merged:
        if item.term in primed:
            item.hinted = True
            item.count = sum(t.text.count(item.term) for t in turns) or item.count

    order = {"person": 0, "place": 1, "organisation": 2, "name": 3, "date": 4}
    merged.sort(key=lambda i: (order.get(i.kind, 9), i.first_at, i.term))
    return merged


# ---------------------------------------------------------------------------
# Running Whisper
# ---------------------------------------------------------------------------


@dataclass
class _Segment:
    start: float
    end: float
    text: str


# ---------------------------------------------------------------------------
# Checkpointing: a long transcription survives a sleep, an update or a reboot
# ---------------------------------------------------------------------------
#
# A 40-minute session takes about 40 minutes to transcribe with `small` on a
# laptop. Twice while this was being built, the machine rebooted partway
# through and every minute of work was lost. Each segment is now appended to a
# checkpoint as it is produced; a restart resumes from the last one.
#
# The checkpoint is keyed to the audio file (path, size, modification time),
# the model, the language and the hints. Change any of them and it starts
# over, so an edited recording can never be silently stitched onto an old
# transcript.


def _checkpoint_key(audio: Path, model_size: str, code: str, hotwords: str | None) -> str:
    stat = audio.stat()
    raw = f"{audio.resolve()}|{stat.st_size}|{int(stat.st_mtime)}|{model_size}|{code}|{hotwords}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _load_checkpoint(path: Path | None, key: str) -> list[_Segment]:
    if path is None or not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        if not lines or json.loads(lines[0]).get("key") != key:
            return []
    except json.JSONDecodeError:
        return []
    done: list[_Segment] = []
    for line in lines[1:]:
        try:
            item = json.loads(line)
            done.append(_Segment(item["start"], item["end"], item["text"]))
        except (json.JSONDecodeError, KeyError):
            break  # a line torn by the interruption; everything before it is good
    return done


def _append_checkpoint(path: Path | None, segment: _Segment) -> None:
    if path is None:
        return
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(
            {"start": segment.start, "end": segment.end, "text": segment.text},
            ensure_ascii=False,
        ) + "\n")
        handle.flush()


def transcribe(
    audio: Path,
    *,
    owner_name: str,
    interviewer_name: str = "Operator",
    model_size: str | None = None,
    language: str | None = lang.EN,
    hints: list[str] | None = None,
    gap: float = CONTINUATION_GAP_SECONDS,
    progress=None,
    checkpoint: Path | None = None,
) -> TranscriptionResult:
    """Transcribe an audio file locally.

    With `checkpoint`, every segment is saved as it is produced and an
    interrupted run resumes where it stopped.

    `hints` are names the operator already knows -- the story owner, family
    members, home town. They are fed to Whisper at every window and to the
    Chinese name finder. Measured on real Mandarin they help modestly (1 of 7
    difficult names right without, 2 of 7 with) and can write a hinted word in
    where it was never said, so every primed name is marked on the checklist.

    `progress(message)` receives status lines, including a running position
    through the recording, which matters on a 40-minute session where
    otherwise nothing appears to happen for ten minutes.
    """
    code = lang.normalize(language)
    model_size = model_size or default_model(code)

    if not audio.exists():
        raise TranscriptionError(f"no such audio file: {audio}")
    if model_size not in MODELS:
        raise TranscriptionError(
            f"unknown model '{model_size}'; choose from {', '.join(MODELS)}"
        )

    # Happens when the operator is also the story owner, as in a rehearsal
    # run. Identical labels would make the whole guess useless.
    if owner_name.strip().lower() == interviewer_name.strip().lower():
        owner_name = f"{owner_name} (owner)"
        interviewer_name = f"{interviewer_name} (interviewer)"

    try:
        from faster_whisper import WhisperModel, decode_audio
    except ImportError as exc:  # pragma: no cover
        raise TranscriptionError(
            "local transcription needs faster-whisper: pip install faster-whisper"
        ) from exc

    hints = [h.strip() for h in (hints or []) if h and h.strip()]
    if hints and lang.is_chinese(code):
        lang.teach_names(hints)
    hotwords = lang.whisper_hotwords(code, hints)

    key = _checkpoint_key(audio, model_size, code, hotwords)
    collected = _load_checkpoint(checkpoint, key)
    resume_from = collected[-1].end if collected else 0.0
    if checkpoint is not None and not collected:
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        checkpoint.write_text(json.dumps({"key": key}) + "\n", encoding="utf-8")

    samples = decode_audio(str(audio), sampling_rate=SAMPLE_RATE)
    duration = len(samples) / SAMPLE_RATE

    if collected and progress:
        progress(f"resuming at {_clock(resume_from)} of {_clock(duration)} from the checkpoint")

    # Nothing meaningful left after the last saved segment: done already.
    if duration - resume_from > 1.0:
        if progress:
            progress(f"loading Whisper '{model_size}' (the first run downloads it)")

        # int8 on CPU: the accuracy cost is small beside the error an older,
        # quieter voice introduces anyway, and it keeps this usable on a laptop.
        whisper = WhisperModel(model_size, device="cpu", compute_type="int8")

        # Resuming feeds Whisper only the audio after the last saved segment
        # and shifts the timestamps back; it ends on a pause, so no word is cut.
        remaining = samples[int(resume_from * SAMPLE_RATE):] if resume_from else samples
        segments, _ = whisper.transcribe(
            remaining,
            language=lang.whisper_code(code),
            hotwords=hotwords,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 400},
            condition_on_previous_text=False,  # stops one bad guess cascading
        )

        last_reported = resume_from - 60.0
        for segment in segments:
            item = _Segment(
                start=segment.start + resume_from,
                end=segment.end + resume_from,
                text=lang.clean_transcript_text(segment.text, code),
            )
            collected.append(item)
            _append_checkpoint(checkpoint, item)
            if progress and item.end - last_reported >= 60:
                last_reported = item.end
                progress(f"{_clock(item.end)} of {_clock(duration)}")

    turns = _assemble_turns(collected, gap, code)
    _guess_speakers(turns, owner_name, interviewer_name, code)

    result = TranscriptionResult(
        turns=turns,
        language=code,
        duration=duration,
        model=model_size,
        checklist=build_checklist(turns, code, hints),
    )
    if checkpoint is not None:
        checkpoint.unlink(missing_ok=True)  # finished: nothing left to resume
    return result


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

_KIND_LABEL = {
    "person": "People",
    "place": "Places",
    "organisation": "Organisations",
    "name": "Other names",
    "date": "Dates and ages",
}


def render_checklist(checklist: list[CheckItem], *, prefix: str = "") -> list[str]:
    lines: list[str] = []
    current_kind = None
    for item in checklist:
        if item.kind != current_kind:
            current_kind = item.kind
            lines.append(f"{prefix}  {_KIND_LABEL.get(item.kind, item.kind)}:")
        extra = f"  (also heard as: {', '.join(item.heard_also)})" if item.heard_also else ""
        if item.hinted:
            extra += "  (primed: confirm it was really said here)"
        times = f" x{item.count}" if item.count > 1 else ""
        lines.append(f"{prefix}    [{item.first_at}] {item.term}{times}{extra}")
    return lines


def render_transcript(result: TranscriptionResult, audio_name: str) -> str:
    """Write the transcript in the format `ingest` reads.

    The header is not decoration. §11 makes transcription error on older
    voices a named risk, and an operator who forgets that will let a misheard
    name reach a printed book.
    """
    lines = [
        f"# Transcript of {audio_name}",
        f"# Whisper ({result.model}), run locally on {date.today().isoformat()}. "
        f"The audio never left this machine.",
        f"# {result.minutes} minutes, language: {lang.display_name(result.language)}",
        "#",
        "# NOT YET CHECKED BY A HUMAN. Before extracting:",
        "#",
        "#   1. CHECK EVERY NAME AND DATE BELOW against the audio. The timestamp",
        "#      is the first place each one is said. Whisper mishears names",
        "#      confidently and its own confidence score does not flag them.",
        "#   2. Fix the speaker labels. They are guessed from the shape of each",
        "#      turn, not from voice. Real diarisation is not running here.",
        "#   3. Play the audio and read along for anything else that is wrong.",
        "#",
    ]

    if result.checklist:
        lines.append(f"# To check ({len(result.checklist)}):")
        lines.extend(render_checklist(result.checklist, prefix="#"))
    else:
        lines.append("# No names or dates detected. Read it anyway.")

    lines.extend(["#", ""])

    for turn in result.turns:
        lines.append(f"[{turn.timestamp()}] {turn.speaker}: {turn.text}")
        lines.append("")

    return "\n".join(lines)
