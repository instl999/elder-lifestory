"""Tests for the logic that must not break.

These cover the checks that make it safe to let a model write about a real
person: citation verification, quote fidelity, the access boundary from
spec v2 §12.2, consent gating, and the §7.4 confirmation cap.

Prose quality is not tested here -- that is what the recognition test in §18
is for, and it needs human readers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lifestory import confirm, extract, ingest, validators
from lifestory.models import (
    Access,
    Archive,
    CaseMeta,
    Claim,
    ClaimKind,
    ClaimStatus,
    ConsentRecord,
    ConsentScope,
    Quote,
    Sensitivity,
    Session,
    SourceRef,
    SpeakerRole,
    StoryCard,
    Utterance,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample-session-1.txt"


def make_archive() -> Archive:
    return Archive(
        meta=CaseMeta(
            id="test-case",
            owner_name="Margaret Hale",
            owner_preferred_name="Margaret",
            owner_birth_year=1938,
        )
    )


def add_utterance(archive: Archive, text: str, **kwargs) -> Utterance:
    utterance = Utterance(
        session_id="ses_test",
        seq=len(archive.utterances) + 1,
        speaker="Margaret",
        role=SpeakerRole.OWNER,
        text=text,
        **kwargs,
    )
    archive.utterances.append(utterance)
    return utterance


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------


class TestIngest:
    def test_parses_timestamped_transcript(self):
        session = Session(case_id="test-case", number=1)
        utterances = ingest.load_transcript_file(
            FIXTURE, session, owner_names=["Margaret"], interviewer_names=["Operator"]
        )
        assert len(utterances) > 20
        assert all(u.session_id == session.id for u in utterances)

    def test_assigns_speaker_roles(self):
        session = Session(case_id="test-case", number=1)
        utterances = ingest.load_transcript_file(
            FIXTURE, session, owner_names=["Margaret"], interviewer_names=["Operator"]
        )
        roles = {u.role for u in utterances}
        assert SpeakerRole.OWNER in roles
        assert SpeakerRole.INTERVIEWER in roles
        assert SpeakerRole.UNKNOWN not in roles

    def test_captures_timestamps(self):
        session = Session(case_id="test-case", number=1)
        utterances = ingest.load_transcript_file(FIXTURE, session, owner_names=["Margaret"])
        assert all(u.t_start for u in utterances)

    def test_everything_defaults_to_owner_only(self):
        """§6.7 -- the architectural constraint, not a setting."""
        session = Session(case_id="test-case", number=1)
        utterances = ingest.load_transcript_file(FIXTURE, session, owner_names=["Margaret"])
        assert all(u.access is Access.OWNER_ONLY for u in utterances)

    def test_folds_wrapped_continuation_lines(self):
        session = Session(case_id="c", number=1)
        text = "Margaret: I was born in the back\nbedroom of the house on Lax Lane.\nOperator: When?"
        utterances = ingest.parse_transcript(
            text, session, owner_names=["Margaret"], interviewer_names=["Operator"]
        )
        assert len(utterances) == 2
        assert "back bedroom" in utterances[0].text


# ---------------------------------------------------------------------------
# Citation verification -- a model cannot cite a source that does not exist
# ---------------------------------------------------------------------------


class TestCitationVerification:
    def test_drops_story_card_with_fabricated_citations(self):
        archive = make_archive()
        real = add_utterance(archive, "My father worked the carpet mills.")

        result = extract.ExtractionResult(
            story_cards=[
                extract.ExtractedStoryCard(
                    title="Invented",
                    setup="Something that was never said.",
                    strength=4,
                    source_utterance_ids=["utt_doesnotexist"],
                )
            ]
        )
        report = extract.merge(archive, result, [real])

        assert report.story_cards_added == 0
        assert "utt_doesnotexist" in report.dropped_citations
        assert archive.story_cards == []

    def test_keeps_story_card_with_valid_citation(self):
        archive = make_archive()
        real = add_utterance(archive, "My father cycled six miles to the mill.")

        result = extract.ExtractionResult(
            story_cards=[
                extract.ExtractedStoryCard(
                    title="The cycle to Kidderminster",
                    setup="Her father cycled six miles each way to the carpet mills.",
                    strength=3,
                    source_utterance_ids=[real.id],
                )
            ]
        )
        report = extract.merge(archive, result, [real])

        assert report.story_cards_added == 1
        assert archive.story_cards[0].sources[0].ref_id == real.id

    def test_flags_quote_that_is_not_in_the_source(self):
        archive = make_archive()
        real = add_utterance(archive, "You'll not last a winter on those wards.")

        result = extract.ExtractionResult(
            story_cards=[
                extract.ExtractedStoryCard(
                    title="The warning",
                    setup="Her father doubted she would stay.",
                    strength=4,
                    source_utterance_ids=[real.id],
                    quotes=[
                        extract.ExtractedQuote(
                            text="You will never survive a single winter on those wards.",
                            source_utterance_id=real.id,
                        )
                    ],
                )
            ]
        )
        report = extract.merge(archive, result, [real])
        assert report.unquotable


# ---------------------------------------------------------------------------
# Quote fidelity
# ---------------------------------------------------------------------------


class TestQuoteFidelity:
    def test_exact_quote_passes(self):
        archive = make_archive()
        source = add_utterance(archive, "He said, you'll not last a winter on those wards.")
        archive.quotes.append(
            Quote(
                text="you'll not last a winter on those wards",
                source=SourceRef(kind="utterance", ref_id=source.id),
            )
        )
        assert validators.check_quote_fidelity(archive) == []

    def test_drifted_quote_blocks(self):
        archive = make_archive()
        source = add_utterance(archive, "He said, you'll not last a winter on those wards.")
        archive.quotes.append(
            Quote(
                text="You will never manage a single winter working on those hospital wards",
                source=SourceRef(kind="utterance", ref_id=source.id),
            )
        )
        findings = validators.check_quote_fidelity(archive)
        assert findings
        assert findings[0].severity is validators.Severity.BLOCK

    def test_quote_citing_missing_utterance_blocks(self):
        archive = make_archive()
        archive.quotes.append(
            Quote(text="Anything", source=SourceRef(kind="utterance", ref_id="utt_gone"))
        )
        findings = validators.check_quote_fidelity(archive)
        assert findings[0].severity is validators.Severity.BLOCK

    def test_fabricated_quote_in_a_draft_is_caught(self):
        """The check that catches a model inventing a line for someone's grandmother."""
        archive = make_archive()
        add_utterance(archive, "He said, you'll not last a winter on those wards.")

        draft = (
            'She remembered the day clearly. "You were always the brave one in this '
            'family, and I have never once doubted that," her father told her.'
        )
        findings = validators.check_draft_quotes(archive, draft)
        assert findings
        assert findings[0].severity is validators.Severity.BLOCK

    def test_real_quote_in_a_draft_passes(self):
        archive = make_archive()
        add_utterance(archive, "He said, you'll not last a winter on those wards.")
        draft = 'Her father put it plainly: "you\'ll not last a winter on those wards."'
        assert validators.check_draft_quotes(archive, draft) == []


# ---------------------------------------------------------------------------
# The §12.2 access boundary
# ---------------------------------------------------------------------------


class TestAccessBoundary:
    def test_restricted_card_cannot_be_usable(self):
        archive = make_archive()
        source = add_utterance(archive, "I never told the children about the first marriage.")
        source.access = Access.FAMILY

        archive.story_cards.append(
            StoryCard(
                title="The first marriage",
                setup="A marriage the family does not know about.",
                sources=[SourceRef(kind="utterance", ref_id=source.id)],
                access=Access.FAMILY,
                sensitivity=Sensitivity.RESTRICTED,
            )
        )
        findings = validators.check_access(archive)
        assert any(f.severity is validators.Severity.BLOCK for f in findings)

    def test_card_cannot_outrank_its_source(self):
        """A card released to the family may not rest on owner-only material."""
        archive = make_archive()
        source = add_utterance(archive, "Something she has not released.")
        assert source.access is Access.OWNER_ONLY

        archive.story_cards.append(
            StoryCard(
                title="Released too early",
                setup="Derived from material the owner has not released.",
                sources=[SourceRef(kind="utterance", ref_id=source.id)],
                access=Access.FAMILY,
            )
        )
        findings = validators.check_access(archive)
        assert any("rests on an owner_only utterance" in f.message for f in findings)

    def test_properly_released_card_passes(self):
        archive = make_archive()
        source = add_utterance(archive, "The fish supper afterwards.")
        source.release_to(Access.FAMILY, note="released by Margaret, 12 March")

        archive.story_cards.append(
            StoryCard(
                title="The fish supper",
                setup="Her father drove over for her certificate.",
                sources=[SourceRef(kind="utterance", ref_id=source.id)],
                access=Access.FAMILY,
            )
        )
        assert validators.check_access(archive) == []

    def test_restricted_material_is_excluded_from_usable_cards(self):
        archive = make_archive()
        archive.story_cards.append(
            StoryCard(
                title="Restricted",
                setup="x",
                access=Access.FAMILY,
                sensitivity=Sensitivity.RESTRICTED,
            )
        )
        assert archive.usable_story_cards() == []


# ---------------------------------------------------------------------------
# Consent gating
# ---------------------------------------------------------------------------


class TestConsent:
    def test_extraction_refuses_without_consent(self):
        archive = make_archive()
        with pytest.raises(extract.ConsentError):
            extract.check_consent(archive)

    def test_extraction_proceeds_with_consent(self):
        archive = make_archive()
        for scope in (
            ConsentScope.RECORDING,
            ConsentScope.AI_PROCESSING,
            ConsentScope.THIRD_PARTY_SERVICES,
        ):
            archive.consents.append(
                ConsentRecord(
                    scope=scope,
                    granted_by="Margaret Hale",
                    granted_by_role="story_owner",
                    granted=True,
                )
            )
        extract.check_consent(archive)  # does not raise

    def test_third_party_consent_is_required(self):
        """The provider is an outside company; §12.1 makes that its own scope."""
        archive = make_archive()
        for scope in (ConsentScope.RECORDING, ConsentScope.AI_PROCESSING):
            archive.consents.append(
                ConsentRecord(
                    scope=scope,
                    granted_by="Margaret Hale",
                    granted_by_role="story_owner",
                    granted=True,
                )
            )
        with pytest.raises(extract.ConsentError, match="third_party_services"):
            extract.check_consent(archive)

    def test_withdrawn_consent_is_not_live(self):
        archive = make_archive()
        archive.consents.append(
            ConsentRecord(
                scope=ConsentScope.RECORDING,
                granted_by="Margaret Hale",
                granted_by_role="story_owner",
                granted=False,
            )
        )
        assert not archive.has_consent(ConsentScope.RECORDING)

    def test_export_blocks_on_missing_consent(self):
        archive = make_archive()
        findings = validators.check_consent(archive, [ConsentScope.PRINT])
        assert findings[0].severity is validators.Severity.BLOCK


# ---------------------------------------------------------------------------
# Chronology and names
# ---------------------------------------------------------------------------


class TestChronology:
    def test_event_before_birth_blocks(self):
        archive = make_archive()  # born 1938
        archive.story_cards.append(
            StoryCard(title="Impossible", setup="x", year=1931, sources=[])
        )
        findings = validators.check_chronology(archive)
        assert any(f.severity is validators.Severity.BLOCK for f in findings)

    def test_reversed_range_blocks(self):
        archive = make_archive()
        archive.story_cards.append(
            StoryCard(title="Backwards", setup="x", year=1962, year_end=1958)
        )
        findings = validators.check_chronology(archive)
        assert any("ends before it starts" in f.message for f in findings)

    def test_plausible_dates_pass(self):
        archive = make_archive()
        archive.story_cards.append(
            StoryCard(title="Fine", setup="x", year=1957, year_end=1968)
        )
        assert validators.check_chronology(archive) == []


class TestNames:
    def test_near_duplicate_names_warn(self):
        from lifestory.models import Person

        archive = make_archive()
        archive.people.append(Person(name="Edith Pargeter"))
        archive.people.append(Person(name="Edith Pargetter"))
        findings = validators.check_names(archive)
        assert any(f.check == "names" for f in findings)

    def test_distinct_names_do_not_warn(self):
        from lifestory.models import Person

        archive = make_archive()
        archive.people.append(Person(name="Edith Pargeter"))
        archive.people.append(Person(name="Ronald Hale"))
        assert validators.check_names(archive) == []


# ---------------------------------------------------------------------------
# §7.4 -- the confirmation queue must stay small
# ---------------------------------------------------------------------------


class TestConfirmationQueue:
    def _archive_with_claims(self, count: int) -> Archive:
        archive = make_archive()
        source = add_utterance(archive, "Her name was Edith Mary Pargeter.")
        # The queue only draws on released material now (§12.2), which is the
        # order the runbook prescribes: release, then confirm.
        source.release_to(Access.FAMILY, note="Margaret, session 2")
        for index in range(count):
            archive.claims.append(
                Claim(
                    text=f"Claim number {index}",
                    kind=ClaimKind.NAME,
                    importance=(index % 5) + 1,
                    sources=[SourceRef(kind="utterance", ref_id=source.id)],
                )
            )
        return archive

    def test_caps_at_twenty_five(self):
        archive = self._archive_with_claims(200)
        queue = confirm.build(archive)
        assert len(queue.items) == confirm.MAX_ITEMS
        assert queue.deferred_count == 175

    def test_stays_under_fifteen_minutes(self):
        archive = self._archive_with_claims(200)
        queue = confirm.build(archive)
        assert queue.estimated_minutes() <= confirm.TARGET_MINUTES

    def test_excludes_unconfirmable_kinds(self):
        """Only the things that are embarrassing when wrong reach the family."""
        archive = make_archive()
        source = add_utterance(archive, "x")
        source.release_to(Access.FAMILY)
        archive.claims.append(
            Claim(
                text="An interpretive claim about the meaning of her life",
                kind=ClaimKind.OTHER,
                sources=[SourceRef(kind="utterance", ref_id=source.id)],
            )
        )
        queue = confirm.build(archive)
        assert queue.items == []

    def test_ranks_conflicting_claims_first(self):
        archive = make_archive()
        source = add_utterance(archive, "x")
        source.release_to(Access.FAMILY)
        archive.claims.append(
            Claim(text="Routine", kind=ClaimKind.DATE, importance=5,
                  sources=[SourceRef(kind="utterance", ref_id=source.id)])
        )
        archive.claims.append(
            Claim(text="Disputed", kind=ClaimKind.DATE, importance=3,
                  status=ClaimStatus.CONFLICTING,
                  sources=[SourceRef(kind="utterance", ref_id=source.id)])
        )
        queue = confirm.build(archive)
        assert queue.items[0].current_value == "Disputed"

    def test_excluded_claims_never_appear(self):
        archive = make_archive()
        source = add_utterance(archive, "x")
        source.release_to(Access.FAMILY)
        archive.claims.append(
            Claim(text="Excluded", kind=ClaimKind.NAME, status=ClaimStatus.EXCLUDED,
                  sources=[SourceRef(kind="utterance", ref_id=source.id)])
        )
        assert confirm.build(archive).items == []

    def test_answer_updates_status(self):
        archive = self._archive_with_claims(1)
        claim_id = archive.claims[0].id
        confirm.apply_answer(archive, claim_id, confirmed=True, by="Susan")
        assert archive.claims[0].status is ClaimStatus.CONFIRMED_BY_FAMILY
        assert archive.claims[0].confirmed_by == "Susan"

    def test_correction_rewrites_the_claim(self):
        archive = self._archive_with_claims(1)
        claim_id = archive.claims[0].id
        confirm.apply_answer(
            archive, claim_id, confirmed=True, corrected_text="Edith Mary Pargeter", by="Susan"
        )
        assert archive.claims[0].text == "Edith Mary Pargeter"


# ---------------------------------------------------------------------------
# Story card quality
# ---------------------------------------------------------------------------


class TestStoryCard:
    def test_scene_needs_conflict_and_consequence(self):
        summary = StoryCard(title="General", setup="They were poor but happy.", strength=4)
        assert not summary.is_scene()
        assert "conflict" in summary.missing_beats()

    def test_full_scene_qualifies(self):
        scene = StoryCard(
            title="The job at Worcester",
            setup="She was offered auxiliary nursing at the Royal Infirmary.",
            conflict="Her father did not want her to take it.",
            choice="She went without answering him.",
            consequence="She stayed eleven years.",
            strength=5,
        )
        assert scene.is_scene()
        assert scene.missing_beats() == ["reflection"]

    def test_weak_card_is_not_a_scene_even_when_complete(self):
        card = StoryCard(
            title="Thin", setup="x", conflict="y", consequence="z", strength=2
        )
        assert not card.is_scene()


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


class TestSources:
    def test_card_without_sources_blocks(self):
        archive = make_archive()
        archive.story_cards.append(StoryCard(title="Unsourced", setup="x", sources=[]))
        findings = validators.check_sources(archive)
        assert any(f.severity is validators.Severity.BLOCK for f in findings)

    def test_card_citing_missing_utterance_blocks(self):
        archive = make_archive()
        archive.story_cards.append(
            StoryCard(
                title="Bad citation",
                setup="x",
                sources=[SourceRef(kind="utterance", ref_id="utt_nope")],
            )
        )
        findings = validators.check_sources(archive)
        assert any("unknown utterance" in f.message for f in findings)
