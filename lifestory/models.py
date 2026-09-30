"""The Life Archive data model (spec v2 §8).

Design rules carried over from the specification:

* The structured archive is authoritative; nothing is derived from raw chat
  history at generation time (§6.5, §11 "Data pattern").
* Every generated sentence must be traceable to source records, but that trail
  is *operator-facing only* (§6.2, §8 "Provenance").
* Raw utterances default to story-owner access. Sponsors and family see derived,
  approved content only (§6.7, §12.2). Access is a property of the data, not a
  UI setting.
* The StoryCard is the unit that matters (§8) and the most expensive artifact to
  produce (§14). Everything else in this schema exists to support it.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "2.0"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Access control (§6.7, §12.2)
# ---------------------------------------------------------------------------


class Access(str, Enum):
    """Who may see this record.

    The default for anything the story owner said is OWNER_ONLY. Material moves
    outward only by an affirmative, per-item release (§12.2) -- never by a bulk
    setting and never because the sponsor paid.
    """

    OWNER_ONLY = "owner_only"
    OPERATOR = "operator"       # editorial staff, under confidentiality
    FAMILY = "family"           # released by the story owner to the family
    PUBLIC = "public"           # released for wider use

    def rank(self) -> int:
        """How far this item has been released. Higher means wider."""
        return {"owner_only": 0, "operator": 1, "family": 2, "public": 3}[self.value]

    def visible_to(self, audience: "Access") -> bool:
        """True if an item at this release level may be shown to `audience`.

        An item must have been released *at least* as far as the audience
        sits: owner-only material is not visible to the family, family
        material is not visible to the public.

        The operator is the one exception. Editorial work requires seeing raw
        material, under confidentiality, and the operator never relays it to
        the sponsor (§12.2).
        """
        if audience is Access.OPERATOR:
            return True
        return self.rank() >= audience.rank()


class Sensitivity(str, Enum):
    """Editorial handling flag, independent of access."""

    ROUTINE = "routine"
    TENDER = "tender"           # grief, loss, hardship: handle with care
    CONTESTED = "contested"     # family disagreement likely
    RESTRICTED = "restricted"   # §12.2 disclosure: never surfaces without release


# ---------------------------------------------------------------------------
# Provenance (§8)
# ---------------------------------------------------------------------------


class SourceRef(BaseModel):
    """A pointer back into the archive. Operator-facing (§6.2)."""

    kind: Literal["utterance", "artifact", "family_note", "document"]
    ref_id: str
    locator: str | None = None   # timestamp, page number, photo region
    note: str | None = None

    def short(self) -> str:
        return f"{self.ref_id}@{self.locator}" if self.locator else self.ref_id


# ---------------------------------------------------------------------------
# Transcript atoms
# ---------------------------------------------------------------------------


class SpeakerRole(str, Enum):
    OWNER = "owner"
    INTERVIEWER = "interviewer"
    FAMILY = "family"
    UNKNOWN = "unknown"


class Utterance(BaseModel):
    """One turn of speech. The atom everything else is traced to.

    Defaults to OWNER_ONLY. This is the architectural constraint in §6.7 --
    if you find yourself relaxing this default, re-read §12.2 first.
    """

    id: str = Field(default_factory=lambda: _new_id("utt"))
    session_id: str
    seq: int
    speaker: str
    role: SpeakerRole = SpeakerRole.UNKNOWN
    t_start: str | None = None        # "00:12:31" as it appears in the transcript
    text: str
    access: Access = Access.OWNER_ONLY
    sensitivity: Sensitivity = Sensitivity.ROUTINE
    released_at: datetime | None = None
    released_note: str | None = None

    def release_to(self, level: Access, note: str | None = None) -> None:
        """Affirmative, per-item release (§12.2). Never bulk-applied."""
        self.access = level
        self.released_at = _utcnow()
        self.released_note = note


# ---------------------------------------------------------------------------
# Core entities (§8)
# ---------------------------------------------------------------------------


class Person(BaseModel):
    id: str = Field(default_factory=lambda: _new_id("per"))
    name: str
    aliases: list[str] = Field(default_factory=list)
    relationship: str | None = None          # "mother", "second wife", "foreman at the mill"
    birth_year: int | None = None
    death_year: int | None = None
    notes: str | None = None
    access: Access = Access.OPERATOR
    sensitivity: Sensitivity = Sensitivity.ROUTINE
    sources: list[SourceRef] = Field(default_factory=list)

    def all_names(self) -> list[str]:
        return [self.name, *self.aliases]


class Place(BaseModel):
    id: str = Field(default_factory=lambda: _new_id("plc"))
    name: str
    names_over_time: list[str] = Field(default_factory=list)
    region: str | None = None
    notes: str | None = None
    sources: list[SourceRef] = Field(default_factory=list)


class Event(BaseModel):
    id: str = Field(default_factory=lambda: _new_id("evt"))
    summary: str
    year: int | None = None
    year_end: int | None = None
    approximate: bool = False
    place_id: str | None = None
    participant_ids: list[str] = Field(default_factory=list)
    significance: str | None = None
    access: Access = Access.OPERATOR
    sensitivity: Sensitivity = Sensitivity.ROUTINE
    sources: list[SourceRef] = Field(default_factory=list)


class Quote(BaseModel):
    """Original wording, preserved verbatim.

    `text` must match the source utterance. The quote-fidelity validator
    (§10.4) diffs it against the transcript on every export.
    """

    id: str = Field(default_factory=lambda: _new_id("quo"))
    text: str
    speaker_person_id: str | None = None
    source: SourceRef
    access: Access = Access.OWNER_ONLY
    publication_ok: bool = False


class Artifact(BaseModel):
    id: str = Field(default_factory=lambda: _new_id("art"))
    kind: Literal["photograph", "letter", "document", "audio", "video", "certificate", "object"]
    path: str | None = None
    caption: str | None = None
    year: int | None = None
    approximate: bool = False
    depicts_person_ids: list[str] = Field(default_factory=list)
    place_id: str | None = None
    contributed_by: str | None = None
    access: Access = Access.OPERATOR
    # §11: originals are preserved separately from derivatives.
    original_path: str | None = None
    derived_from_id: str | None = None
    transformation: str | None = None      # "colourised", "scratch repair" -- always labelled
    pixel_width: int | None = None
    pixel_height: int | None = None


class ClaimStatus(str, Enum):
    """Verification states from §8."""

    STATED_BY_OWNER = "stated_by_owner"
    CONFIRMED_BY_FAMILY = "confirmed_by_family"
    DOCUMENTARY = "documentary"
    CONFLICTING = "conflicting"
    UNRESOLVED = "unresolved"
    EXCLUDED = "excluded"

    def is_usable(self) -> bool:
        """Whether a claim may appear in a draft at all."""
        return self is not ClaimStatus.EXCLUDED


class ClaimKind(str, Enum):
    """Why this matters: only the first five kinds reach the family
    confirmation queue (§7.4). Everything else is resolved by the editor or
    preserved as uncertainty in the prose.
    """

    NAME = "name"
    DATE = "date"
    RELATIONSHIP = "relationship"
    PLACE = "place"
    PHOTO_SUBJECT = "photo_subject"
    EVENT = "event"
    OTHER = "other"

    def is_confirmable(self) -> bool:
        return self in {
            ClaimKind.NAME,
            ClaimKind.DATE,
            ClaimKind.RELATIONSHIP,
            ClaimKind.PLACE,
            ClaimKind.PHOTO_SUBJECT,
        }


class Claim(BaseModel):
    id: str = Field(default_factory=lambda: _new_id("clm"))
    text: str
    kind: ClaimKind = ClaimKind.OTHER
    status: ClaimStatus = ClaimStatus.STATED_BY_OWNER
    subject_id: str | None = None            # person/place/event this is about
    sources: list[SourceRef] = Field(default_factory=list)
    conflicting_with: list[str] = Field(default_factory=list)
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    # How embarrassing is this if wrong? Drives confirmation-queue ranking.
    importance: int = Field(default=3, ge=1, le=5)
    access: Access = Access.OPERATOR
    notes: str | None = None


class StoryCard(BaseModel):
    """A narrative-ready episode (§8).

    Setup / conflict / choice / consequence / reflection is deliberate: a
    transcript passage that cannot fill these fields is material, not a scene,
    and will not produce a chapter worth reading.
    """

    id: str = Field(default_factory=lambda: _new_id("sc"))
    title: str
    period: str | None = None                # "early childhood", "the mill years"
    year: int | None = None
    year_end: int | None = None
    approximate: bool = False

    setup: str
    conflict: str | None = None
    choice: str | None = None
    consequence: str | None = None
    reflection: str | None = None

    person_ids: list[str] = Field(default_factory=list)
    place_ids: list[str] = Field(default_factory=list)
    quote_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    theme_ids: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)

    sources: list[SourceRef] = Field(default_factory=list)
    # 1-5. Below 3 means "material, not a scene" -- needs a follow-up question.
    strength: int = Field(default=3, ge=1, le=5)
    access: Access = Access.OPERATOR
    sensitivity: Sensitivity = Sensitivity.ROUTINE
    operator_note: str | None = None
    # §12.1: a visible record of who approved what, and when.
    released_at: datetime | None = None
    released_note: str | None = None

    def release_to(self, level: Access, note: str | None = None) -> None:
        self.access = level
        self.released_at = _utcnow()
        self.released_note = note

    def is_scene(self) -> bool:
        """A scene has stakes and an outcome. Summary does not."""
        return bool(self.conflict and self.consequence) and self.strength >= 3

    def missing_beats(self) -> list[str]:
        beats = {
            "conflict": self.conflict,
            "choice": self.choice,
            "consequence": self.consequence,
            "reflection": self.reflection,
        }
        return [name for name, value in beats.items() if not value]


class LifeTheme(BaseModel):
    id: str = Field(default_factory=lambda: _new_id("thm"))
    name: str
    description: str | None = None
    story_card_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Consent (§12) -- gates every export and every third-party call
# ---------------------------------------------------------------------------


class ConsentScope(str, Enum):
    RECORDING = "recording"
    AI_PROCESSING = "ai_processing"
    FAMILY_SHARING = "family_sharing"
    PRINT = "print"
    PUBLIC_RELEASE = "public_release"
    THIRD_PARTY_SERVICES = "third_party_services"
    MODEL_TRAINING = "model_training"


class ConsentRecord(BaseModel):
    id: str = Field(default_factory=lambda: _new_id("con"))
    scope: ConsentScope
    granted_by: str
    granted_by_role: Literal["story_owner", "sponsor", "family_reviewer", "guardian"]
    granted: bool
    granted_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime | None = None
    medium: str | None = None
    audience: str | None = None
    note: str | None = None
    # §12.3: capacity was assessed by a named human, not inferred by software.
    capacity_assessed_by: str | None = None

    def is_live(self, at: datetime | None = None) -> bool:
        at = at or _utcnow()
        if not self.granted:
            return False
        return self.expires_at is None or self.expires_at > at


# ---------------------------------------------------------------------------
# Question bank (§17) -- the asset that compounds
# ---------------------------------------------------------------------------


class QuestionOutcome(str, Enum):
    SCENE = "scene"              # produced a usable story card
    DETAIL = "detail"            # produced facts but not a scene
    SUMMARY = "summary"          # produced generalities
    DECLINED = "declined"
    NO_MEMORY = "no_memory"
    UNTAGGED = "untagged"

    def yielded(self) -> bool:
        return self in {QuestionOutcome.SCENE, QuestionOutcome.DETAIL}


class AskedQuestion(BaseModel):
    """Every question asked, tagged with what it produced (§17, §18).

    This log is the moat. Do not let a case close untagged.
    """

    id: str = Field(default_factory=lambda: _new_id("q"))
    case_id: str
    session_id: str
    text: str
    bank_id: str | None = None           # seed question it came from, if any
    topic: str | None = None
    asked_at: datetime = Field(default_factory=_utcnow)
    outcome: QuestionOutcome = QuestionOutcome.UNTAGGED
    produced_story_card_ids: list[str] = Field(default_factory=list)
    owner_birth_decade: int | None = None    # cohort: 1930, 1940, 1950...
    mode: Literal["standard", "assisted"] = "standard"
    operator_note: str | None = None


# ---------------------------------------------------------------------------
# Labour tracking (§18) -- decides what Phase 1 automates
# ---------------------------------------------------------------------------


class Stage(str, Enum):
    SETUP = "setup"
    INTERVIEW = "interview"
    TRANSCRIPT_CLEANUP = "transcript_cleanup"
    STORY_CARDS = "story_cards"
    PHOTO_HANDLING = "photo_handling"
    CONFIRMATION_CHASING = "confirmation_chasing"
    NARRATIVE_DIRECTION = "narrative_direction"
    DRAFTING = "drafting"
    EDITORIAL_REVIEW = "editorial_review"
    DESIGN_AND_PRINT = "design_and_print"
    DELIVERY = "delivery"


class LabourEntry(BaseModel):
    id: str = Field(default_factory=lambda: _new_id("lab"))
    case_id: str
    stage: Stage
    minutes: int
    at: datetime = Field(default_factory=_utcnow)
    who: str | None = None
    note: str | None = None


# ---------------------------------------------------------------------------
# Sessions and the case itself
# ---------------------------------------------------------------------------


class InterviewMode(str, Enum):
    STANDARD = "standard"
    ASSISTED = "assisted"        # §12.3 cognitive impairment


class Session(BaseModel):
    id: str = Field(default_factory=lambda: _new_id("ses"))
    case_id: str
    number: int
    held_on: date | None = None
    mode: InterviewMode = InterviewMode.STANDARD
    duration_minutes: int | None = None
    interviewer: str | None = None
    source_file: str | None = None
    note: str | None = None
    # Extraction progress. Without it, extracting session 2 paid for session 1
    # again and filled the owner's review with near-duplicates of cards they
    # had already decided on; an interrupted run started the session over.
    extracted_at: datetime | None = None
    extract_plan: str | None = None       # the windowing the progress refers to
    extract_windows_done: int = 0


class CaseMeta(BaseModel):
    """Project setup (§7.1)."""

    id: str
    owner_name: str
    owner_preferred_name: str | None = None
    owner_birth_year: int | None = None
    sponsor_name: str | None = None
    family_reviewer_name: str | None = None
    operator_name: str | None = None
    language: str = "en"
    dialect: str | None = None
    mode: InterviewMode = InterviewMode.STANDARD
    package: Literal["A", "B", "C"] = "B"
    # §3.1: which trigger brought them. Gift vs transition changes everything.
    trigger: Literal["gift", "transition", "unknown"] = "unknown"
    private_topics: list[str] = Field(default_factory=list)
    archive_inheritor: str | None = None      # §12.4, designated at project start
    created_at: datetime = Field(default_factory=_utcnow)
    notes: str | None = None


class Archive(BaseModel):
    """The whole case. One JSON file on disk, versioned on every write.

    Non-proprietary and fully exportable by construction (§12.1).
    """

    schema_version: str = SCHEMA_VERSION
    meta: CaseMeta
    revision: int = 0
    updated_at: datetime = Field(default_factory=_utcnow)

    sessions: list[Session] = Field(default_factory=list)
    utterances: list[Utterance] = Field(default_factory=list)
    people: list[Person] = Field(default_factory=list)
    places: list[Place] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    quotes: list[Quote] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    story_cards: list[StoryCard] = Field(default_factory=list)
    themes: list[LifeTheme] = Field(default_factory=list)
    consents: list[ConsentRecord] = Field(default_factory=list)
    questions: list[AskedQuestion] = Field(default_factory=list)
    labour: list[LabourEntry] = Field(default_factory=list)

    # ---- lookups -----------------------------------------------------------

    def person(self, pid: str) -> Person | None:
        return next((p for p in self.people if p.id == pid), None)

    def place(self, pid: str) -> Place | None:
        return next((p for p in self.places if p.id == pid), None)

    def quote(self, qid: str) -> Quote | None:
        return next((q for q in self.quotes if q.id == qid), None)

    def utterance(self, uid: str) -> Utterance | None:
        return next((u for u in self.utterances if u.id == uid), None)

    def story_card(self, sid: str) -> StoryCard | None:
        return next((s for s in self.story_cards if s.id == sid), None)

    def claim(self, cid: str) -> Claim | None:
        return next((c for c in self.claims if c.id == cid), None)

    def session(self, sid: str) -> Session | None:
        return next((s for s in self.sessions if s.id == sid), None)

    def unextracted_sessions(self) -> list[Session]:
        """Sessions not yet (or only partly) through extraction, in order."""
        cited = {ref.ref_id for record in [*self.story_cards, *self.claims]
                 for ref in record.sources}
        waiting = []
        for session in sorted(self.sessions, key=lambda s: s.number):
            if session.extracted_at is not None:
                continue
            legacy = session.extract_plan is None and any(
                u.id in cited for u in self.utterances if u.session_id == session.id
            )
            if legacy:
                continue  # extracted before progress was recorded
            waiting.append(session)
        return waiting

    # ---- consent -----------------------------------------------------------

    def has_consent(self, scope: ConsentScope) -> bool:
        return any(c.scope == scope and c.is_live() for c in self.consents)

    def missing_consents(self, scopes: list[ConsentScope]) -> list[ConsentScope]:
        return [s for s in scopes if not self.has_consent(s)]

    # ---- visibility --------------------------------------------------------

    def source_utterances(self, record: "Claim | StoryCard") -> list[Utterance | None]:
        """The utterances a record rests on. `None` marks a dangling citation."""
        return [
            self.utterance(ref.ref_id) for ref in record.sources if ref.kind == "utterance"
        ]

    def visible_to(self, record: "Claim | StoryCard", audience: Access) -> bool:
        """Whether a derived record may be shown to `audience`.

        A record is only as releasable as the material underneath it. This is
        computed from the sources every time rather than stored on the record,
        because a stored copy goes stale the moment someone re-restricts an
        utterance -- and going stale in the permissive direction is exactly the
        §12.2 failure we cannot afford.

        Three ways to be withheld:

        * a source has not been released as far as this audience;
        * a source is marked restricted (§12.2 disclosure);
        * there are no sources at all, which means nobody can check it.
        """
        if audience is Access.OPERATOR:
            return True

        refs = [ref for ref in record.sources if ref.kind == "utterance"]
        if not refs:
            return False

        for ref in refs:
            utterance = self.utterance(ref.ref_id)
            if utterance is None:
                return False
            if utterance.sensitivity is Sensitivity.RESTRICTED:
                return False
            if not utterance.access.visible_to(audience):
                return False
        return True

    # ---- release and restriction (§12.2) -------------------------------------

    def needs_owner_release(self, card: StoryCard) -> bool:
        """Whether releasing `card` needs the story owner's explicit say-so.

        True for a restricted card, and also for any card resting on words
        that were kept private through *another* card. Found on a real
        Mandarin chapter: once one card was kept private, a sibling card built
        on the same sentences could be released with no prompt at all --
        silently re-releasing the private words.
        """
        if card.sensitivity is Sensitivity.RESTRICTED:
            return True
        return any(
            u is not None and u.sensitivity is Sensitivity.RESTRICTED
            for u in self.source_utterances(card)
        )

    def release_card(
        self,
        card: StoryCard,
        level: Access,
        *,
        note: str | None = None,
        force: bool = False,
    ) -> None:
        """Release a card, and the words it rests on, to `level`.

        The card and its source utterances move together: a released card on
        unreleased words is a blocking error, and words released without
        their card would leak through any other record built on them.

        Restricted material needs `force`, which is the story owner's own
        decision, recorded in `note`. That clears the restriction from the
        card *and its words* -- leaving the words marked restricted while
        releasing them made the owner's decision impossible to export.
        """
        if self.needs_owner_release(card) and not force:
            raise PermissionError(
                f"'{card.title}' is restricted, or rests on words the story owner "
                f"kept private; releasing it is the owner's own decision (§12.2)"
            )
        card.release_to(level, note=note)
        if card.sensitivity is Sensitivity.RESTRICTED:
            card.sensitivity = Sensitivity.TENDER
        for utterance in self.source_utterances(card):
            if utterance is None:
                continue
            if force and utterance.sensitivity is Sensitivity.RESTRICTED:
                utterance.sensitivity = Sensitivity.TENDER
            utterance.release_to(level, note=note)

    def restrict_card(self, card: StoryCard, *, note: str | None = None) -> list[StoryCard]:
        """Pull a card, and the words it rests on, back to the story owner.

        Returns other released cards built on the same words: they now rest on
        private material, and the operator has to decide about them too. The
        first version changed only the card, so claims derived from words
        released earlier kept going out on the family's confirmation sheet
        after the owner had asked for the story to be kept private.
        """
        note = note or "kept private"
        card.sensitivity = Sensitivity.RESTRICTED
        card.access = Access.OWNER_ONLY
        card.released_at = None
        card.released_note = note  # marks it as decided, not merely unreviewed

        withdrawn = {ref.ref_id for ref in card.sources if ref.kind == "utterance"}
        for uid in withdrawn:
            utterance = self.utterance(uid)
            if utterance is not None:
                utterance.access = Access.OWNER_ONLY
                utterance.sensitivity = Sensitivity.RESTRICTED
                utterance.released_at = None
                utterance.released_note = note

        return [
            other
            for other in self.story_cards
            if other is not card
            and other.access.rank() >= Access.FAMILY.rank()
            and withdrawn & {ref.ref_id for ref in other.sources if ref.kind == "utterance"}
        ]

    # ---- views -------------------------------------------------------------

    def visible_story_cards(self, audience: Access = Access.OPERATOR) -> list[StoryCard]:
        return [s for s in self.story_cards if s.access.visible_to(audience)]

    def usable_story_cards(self) -> list[StoryCard]:
        """Cards a draft may draw on: released to family and not restricted."""
        return [
            s
            for s in self.story_cards
            if s.access.rank() >= Access.FAMILY.rank()
            and s.sensitivity is not Sensitivity.RESTRICTED
        ]

    def owner_utterances(self) -> list[Utterance]:
        return [u for u in self.utterances if u.role is SpeakerRole.OWNER]

    def total_labour_minutes(self) -> int:
        return sum(entry.minutes for entry in self.labour)

    def labour_by_stage(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for entry in self.labour:
            out[entry.stage.value] = out.get(entry.stage.value, 0) + entry.minutes
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))
