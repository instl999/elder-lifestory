"""Regressions found in the first implementation review.

Each test here corresponds to a defect that was present in the initial build.
They are kept separate from `test_safety.py` so the history of what went wrong
stays legible -- particularly the first class, which is the failure this whole
system exists to prevent.
"""

from __future__ import annotations

import pytest

from lifestory import archive as store, confirm, extract, ingest, validators
from lifestory.models import (
    Access,
    Archive,
    CaseMeta,
    Claim,
    ClaimKind,
    ClaimStatus,
    Person,
    Sensitivity,
    Session,
    SourceRef,
    SpeakerRole,
    Utterance,
)


def make_archive() -> Archive:
    return Archive(meta=CaseMeta(id="reg", owner_name="Margaret Hale", owner_birth_year=1938))


def add_utterance(archive: Archive, text: str, **kwargs) -> Utterance:
    utterance = Utterance(
        session_id="ses_reg",
        seq=len(archive.utterances) + 1,
        speaker="Margaret",
        role=SpeakerRole.OWNER,
        text=text,
        **kwargs,
    )
    archive.utterances.append(utterance)
    return utterance


# ---------------------------------------------------------------------------
# 1. The confirmation queue leaked owner-only material to the family.
#
# The restricted story card was correctly blocked, but a *claim* extracted from
# the same utterance went into the family confirmation sheet quoting the
# disclosure verbatim. This is precisely the §12.2 failure the access system
# exists to prevent, and it was wide open.
# ---------------------------------------------------------------------------


class TestConfirmationQueueLeak:
    def _archive_with_secret(self) -> tuple[Archive, Utterance]:
        archive = make_archive()
        secret = add_utterance(
            archive,
            "I had a son before Ron, in 1959. He was adopted. Susan has never known.",
        )
        archive.claims.append(
            Claim(
                text="She had a son in 1959 who was adopted",
                kind=ClaimKind.NAME,
                importance=5,
                sources=[SourceRef(kind="utterance", ref_id=secret.id)],
            )
        )
        return archive, secret

    def test_unreleased_claim_never_reaches_the_family(self):
        archive, _ = self._archive_with_secret()
        queue = confirm.build(archive)
        assert queue.items == []

    def test_unreleased_text_never_appears_in_the_sheet(self):
        archive, _ = self._archive_with_secret()
        sheet = confirm.render_markdown(confirm.build(archive))
        assert "adopted" not in sheet
        assert "Susan has never known" not in sheet

    def test_restricted_source_stays_out_even_after_release(self):
        """Releasing the utterance is not enough if it is marked restricted."""
        archive, secret = self._archive_with_secret()
        secret.sensitivity = Sensitivity.RESTRICTED
        secret.release_to(Access.FAMILY, note="released in error")
        assert confirm.build(archive).items == []

    def test_released_claim_does_reach_the_family(self):
        archive = make_archive()
        source = add_utterance(archive, "Edith Mary Pargeter, before she married.")
        source.release_to(Access.FAMILY, note="Margaret, session 2")
        archive.claims.append(
            Claim(
                text="Her mother was Edith Mary Pargeter",
                kind=ClaimKind.NAME,
                importance=5,
                sources=[SourceRef(kind="utterance", ref_id=source.id)],
            )
        )
        queue = confirm.build(archive)
        assert len(queue.items) == 1
        assert "Pargeter" in confirm.render_markdown(queue)

    def test_claim_with_any_unreleased_source_is_withheld(self):
        """All sources must be released, not just one."""
        archive = make_archive()
        released = add_utterance(archive, "Her name was Edith.")
        released.release_to(Access.FAMILY)
        withheld = add_utterance(archive, "Something she has not released.")
        archive.claims.append(
            Claim(
                text="A claim resting on both",
                kind=ClaimKind.NAME,
                sources=[
                    SourceRef(kind="utterance", ref_id=released.id),
                    SourceRef(kind="utterance", ref_id=withheld.id),
                ],
            )
        )
        assert confirm.build(archive).items == []

    def test_unsourced_claim_is_withheld(self):
        archive = make_archive()
        archive.claims.append(Claim(text="From nowhere", kind=ClaimKind.NAME, sources=[]))
        assert confirm.build(archive).items == []

    def test_validator_catches_a_leaking_queue(self):
        """Defence in depth: the validator flags it even if build() regressed."""
        archive, _ = self._archive_with_secret()
        findings = validators.check_confirmation_safety(archive)
        assert not findings  # build() withholds it, so nothing to flag

        # Now force the claim visible the way a regression would.
        archive.utterances[0].release_to(Access.FAMILY)
        assert confirm.build(archive).items  # it is now visible
        archive.utterances[0].sensitivity = Sensitivity.RESTRICTED
        findings = validators.check_confirmation_safety(archive)
        assert any(f.severity is validators.Severity.BLOCK for f in findings)


# ---------------------------------------------------------------------------
# 2. Window overlap produced duplicate story cards, claims and quotes.
# ---------------------------------------------------------------------------


class TestOverlapDeduplication:
    def test_same_scene_extracted_twice_is_stored_once(self):
        archive = make_archive()
        source = add_utterance(archive, "He cycled six miles to the mill each way.")
        window = [source]

        for _ in range(2):
            extract.merge(
                archive,
                extract.ExtractionResult(
                    story_cards=[
                        extract.ExtractedStoryCard(
                            title="The cycle to the mill",
                            setup="He cycled six miles each way.",
                            strength=4,
                            source_utterance_ids=[source.id],
                        )
                    ]
                ),
                window,
            )
        assert len(archive.story_cards) == 1

    def test_duplicate_keeps_the_stronger_version(self):
        archive = make_archive()
        source = add_utterance(archive, "He cycled six miles to the mill each way.")
        window = [source]

        for strength, consequence in ((3, None), (5, "He did it for thirty years.")):
            extract.merge(
                archive,
                extract.ExtractionResult(
                    story_cards=[
                        extract.ExtractedStoryCard(
                            title="The cycle to the mill",
                            setup="He cycled six miles each way.",
                            conflict="In all weathers.",
                            consequence=consequence,
                            strength=strength,
                            source_utterance_ids=[source.id],
                        )
                    ]
                ),
                window,
            )
        assert len(archive.story_cards) == 1
        assert archive.story_cards[0].strength == 5
        assert archive.story_cards[0].consequence == "He did it for thirty years."

    def test_duplicate_claims_collapse(self):
        archive = make_archive()
        source = add_utterance(archive, "Her mother was Edith Mary Pargeter.")
        window = [source]
        for _ in range(3):
            extract.merge(
                archive,
                extract.ExtractionResult(
                    claims=[
                        extract.ExtractedClaim(
                            text="Her mother was Edith Mary Pargeter",
                            kind="name",
                            importance=5,
                            source_utterance_ids=[source.id],
                        )
                    ]
                ),
                window,
            )
        assert len(archive.claims) == 1

    def test_duplicate_quotes_collapse(self):
        archive = make_archive()
        source = add_utterance(archive, "You'll not last a winter on those wards.")
        window = [source]
        for _ in range(2):
            extract.merge(
                archive,
                extract.ExtractionResult(
                    story_cards=[
                        extract.ExtractedStoryCard(
                            title="The warning",
                            setup="Her father doubted her.",
                            strength=4,
                            source_utterance_ids=[source.id],
                            quotes=[
                                extract.ExtractedQuote(
                                    text="You'll not last a winter on those wards",
                                    source_utterance_id=source.id,
                                )
                            ],
                        )
                    ]
                ),
                window,
            )
        assert len(archive.quotes) == 1

    def test_distinct_scenes_are_not_collapsed(self):
        archive = make_archive()
        a = add_utterance(archive, "He cycled to the mill.")
        b = add_utterance(archive, "She went to Worcester at nineteen.")
        for title, setup, uid in (
            ("The cycle to the mill", "He cycled six miles.", a.id),
            ("The job at Worcester", "She left at nineteen.", b.id),
        ):
            extract.merge(
                archive,
                extract.ExtractionResult(
                    story_cards=[
                        extract.ExtractedStoryCard(
                            title=title, setup=setup, strength=4, source_utterance_ids=[uid]
                        )
                    ]
                ),
                [a, b],
            )
        assert len(archive.story_cards) == 2


# ---------------------------------------------------------------------------
# 3. card.claim_ids was never populated, so two code paths were dead.
# ---------------------------------------------------------------------------


class TestClaimLinkage:
    def _extract_one(self) -> Archive:
        archive = make_archive()
        source = add_utterance(archive, "Her mother was Edith, born 1908.")
        extract.merge(
            archive,
            extract.ExtractionResult(
                story_cards=[
                    extract.ExtractedStoryCard(
                        title="Edith by the range",
                        setup="Her mother had a chair nobody else sat in.",
                        strength=4,
                        source_utterance_ids=[source.id],
                    )
                ],
                claims=[
                    extract.ExtractedClaim(
                        text="Her mother was Edith, born 1908",
                        kind="name",
                        importance=5,
                        source_utterance_ids=[source.id],
                    )
                ],
            ),
            [source],
        )
        return archive

    def test_cards_link_to_claims_sharing_their_sources(self):
        archive = self._extract_one()
        card = archive.story_cards[0]
        assert card.claim_ids
        assert archive.claims[0].id in card.claim_ids

    def test_excluded_claim_blocks_its_card(self):
        """This validator was unreachable before claim linkage existed."""
        archive = self._extract_one()
        archive.claims[0].status = ClaimStatus.EXCLUDED
        findings = validators.check_sources(archive)
        assert any("excluded claim" in f.message for f in findings)


# ---------------------------------------------------------------------------
# 4. merge() and the validator disagreed about what counts as a verbatim quote.
# ---------------------------------------------------------------------------


class TestQuoteComparisonConsistency:
    def test_punctuation_difference_is_not_flagged_as_unquotable(self):
        archive = make_archive()
        source = add_utterance(archive, 'He said, "You\'ll not last a winter on those wards."')
        report = extract.merge(
            archive,
            extract.ExtractionResult(
                story_cards=[
                    extract.ExtractedStoryCard(
                        title="The warning",
                        setup="x",
                        strength=4,
                        source_utterance_ids=[source.id],
                        quotes=[
                            extract.ExtractedQuote(
                                text="You'll not last a winter on those wards",
                                source_utterance_id=source.id,
                            )
                        ],
                    )
                ]
            ),
            [source],
        )
        assert not report.unquotable
        # And the validator agrees.
        assert validators.check_quote_fidelity(archive) == []

    def test_genuine_paraphrase_is_still_rejected(self):
        archive = make_archive()
        source = add_utterance(archive, "You'll not last a winter on those wards.")
        report = extract.merge(
            archive,
            extract.ExtractionResult(
                story_cards=[
                    extract.ExtractedStoryCard(
                        title="The warning",
                        setup="x",
                        strength=4,
                        source_utterance_ids=[source.id],
                        quotes=[
                            extract.ExtractedQuote(
                                text="You will never survive a single winter working there",
                                source_utterance_id=source.id,
                            )
                        ],
                    )
                ]
            ),
            [source],
        )
        assert report.unquotable
        # A paraphrase is not stored as a quote at all.
        assert archive.quotes == []


# ---------------------------------------------------------------------------
# 5. check_names skipped exact duplicates, the strongest duplicate signal.
# ---------------------------------------------------------------------------


class TestNameDuplicates:
    def test_identical_names_on_separate_records_are_flagged(self):
        archive = make_archive()
        archive.people.append(Person(name="Edith Pargeter"))
        archive.people.append(Person(name="Edith Pargeter"))
        findings = validators.check_names(archive)
        assert any("same person" in f.message for f in findings)

    def test_alias_collision_is_flagged(self):
        archive = make_archive()
        archive.people.append(Person(name="Ronald Hale", aliases=["Ron"]))
        archive.people.append(Person(name="Ron Hale"))
        assert validators.check_names(archive)

    def test_a_person_with_aliases_is_not_flagged_against_itself(self):
        archive = make_archive()
        archive.people.append(Person(name="Ronald Hale", aliases=["Ron", "Ronnie"]))
        assert validators.check_names(archive) == []


# ---------------------------------------------------------------------------
# 6. Extraction lost every window's work if one call failed partway.
# ---------------------------------------------------------------------------


class TestExtractionResilience:
    def test_work_is_checkpointed_between_windows(self, tmp_path, monkeypatch):
        (tmp_path / "cases").mkdir()
        archive, paths = store.create_case(
            CaseMeta(id="resume", owner_name="Margaret"), root=tmp_path
        )
        from lifestory.models import ConsentRecord, ConsentScope

        for scope in (
            ConsentScope.RECORDING,
            ConsentScope.AI_PROCESSING,
            ConsentScope.THIRD_PARTY_SERVICES,
        ):
            archive.consents.append(
                ConsentRecord(
                    scope=scope, granted_by="M", granted_by_role="story_owner", granted=True
                )
            )
        session = Session(case_id="resume", number=1)
        archive.sessions.append(session)
        for i in range(40):
            archive.utterances.append(
                Utterance(
                    session_id=session.id,
                    seq=i,
                    speaker="Margaret",
                    role=SpeakerRole.OWNER,
                    text=f"A turn about the mill, number {i}.",
                )
            )
        store.save(archive, paths)

        calls = {"n": 0}

        def flaky(window, **kwargs):
            calls["n"] += 1
            if calls["n"] == 3:
                raise RuntimeError("API fell over")
            return extract.ExtractionResult(
                story_cards=[
                    extract.ExtractedStoryCard(
                        title=f"Scene {calls['n']}",
                        setup=f"Something happened, {calls['n']}.",
                        strength=4,
                        source_utterance_ids=[window[0].id],
                    )
                ]
            )

        monkeypatch.setattr(extract, "extract_window", flaky)

        saves = []
        with pytest.raises(RuntimeError):
            extract.extract_session(
                archive,
                session.id,
                window_size=10,
                overlap=2,
                checkpoint=lambda: saves.append(len(archive.story_cards)),
            )

        # The two windows that succeeded were saved before the third blew up.
        assert saves, "no checkpoint was taken before the failure"
        assert archive.story_cards, "completed windows were lost"


# ---------------------------------------------------------------------------
# 7. Every save wrote a full archive copy, even when nothing changed.
# ---------------------------------------------------------------------------


class TestVersionHistory:
    def test_no_op_save_does_not_bump_or_version(self, tmp_path):
        (tmp_path / "cases").mkdir()
        archive, paths = store.create_case(CaseMeta(id="v", owner_name="M"), root=tmp_path)
        store.save(archive, paths)
        revision = archive.revision
        before = len(list(paths.versions.glob("r*.json")))

        for _ in range(10):
            store.save(archive, paths)

        assert archive.revision == revision
        assert len(list(paths.versions.glob("r*.json"))) == before

    def test_real_change_does_bump(self, tmp_path):
        (tmp_path / "cases").mkdir()
        archive, paths = store.create_case(CaseMeta(id="v2", owner_name="M"), root=tmp_path)
        revision = archive.revision
        archive.people.append(Person(name="Edith"))
        store.save(archive, paths)
        assert archive.revision == revision + 1

    def test_history_is_pruned(self, tmp_path):
        (tmp_path / "cases").mkdir()
        archive, paths = store.create_case(CaseMeta(id="v3", owner_name="M"), root=tmp_path)
        for i in range(store.KEEP_VERSIONS + 15):
            archive.people.append(Person(name=f"Person {i}"))
            store.save(archive, paths)
        assert len(list(paths.versions.glob("r*.json"))) <= store.KEEP_VERSIONS


# ---------------------------------------------------------------------------
# 8. Ingesting the same transcript twice silently duplicated everything.
# ---------------------------------------------------------------------------


class TestDuplicateIngestion:
    def test_reingesting_the_same_file_is_detected(self):
        from pathlib import Path

        archive = make_archive()
        fixture = Path(__file__).parent / "fixtures" / "sample-session-1.txt"

        session = Session(case_id=archive.meta.id, number=1, source_file=fixture.name)
        utterances = ingest.load_transcript_file(fixture, session, owner_names=["Margaret"])
        archive.sessions.append(session)
        archive.utterances.extend(utterances)

        again = ingest.load_transcript_file(
            fixture, Session(case_id=archive.meta.id, number=2), owner_names=["Margaret"]
        )
        assert ingest.already_ingested(archive, again) is not None

    def test_a_different_transcript_is_not_flagged(self):
        from pathlib import Path

        archive = make_archive()
        fixture = Path(__file__).parent / "fixtures" / "sample-session-1.txt"
        session = Session(case_id=archive.meta.id, number=1)
        archive.sessions.append(session)
        archive.utterances.extend(
            ingest.load_transcript_file(fixture, session, owner_names=["Margaret"])
        )

        other = ingest.parse_transcript(
            "Margaret: An entirely different conversation about something else.",
            Session(case_id=archive.meta.id, number=2),
            owner_names=["Margaret"],
        )
        assert ingest.already_ingested(archive, other) is None


# ---------------------------------------------------------------------------
# 9b. Writing yield back to the question bank stripped its explanatory header.
# ---------------------------------------------------------------------------


class TestQuestionBankHeader:
    def test_header_survives_a_write_back(self, tmp_path):
        from lifestory import measure

        path = tmp_path / "seed.yaml"
        path.write_text(
            "# Seed question bank.\n"
            "# mode: standard | assisted | both\n"
            "\n"
            "questions:\n"
            "- id: q-one\n"
            "  text: What did your kitchen smell like?\n"
            "  topic: childhood\n"
            "  mode: both\n",
            encoding="utf-8",
        )
        bank = measure.load_bank(path)
        assert len(bank) == 1

        bank[0].asked = 3
        bank[0].scenes = 2
        measure.save_bank(bank, path)

        text = path.read_text(encoding="utf-8")
        assert text.startswith("# Seed question bank.")
        assert "mode: standard | assisted | both" in text
        assert measure.load_bank(path)[0].yield_rate() == pytest.approx(2 / 3)


# ---------------------------------------------------------------------------
# 11. Transcription output could not be read back by ingestion.
#
# `transcribe` writes a comment header and, when the operator is also the
# story owner, speaker labels like "Lee (owner):". Ingestion skipped neither
# `#` lines nor allowed parentheses in a speaker name, so the header line
# "# 0.7 minutes, language detected: en" parsed as a speaker turn and every
# real line after it was appended to it as continuation text. A nine-line
# transcript became one garbage utterance, silently, and extraction then ran
# on it.
# ---------------------------------------------------------------------------


class TestTranscriptRoundTrip:
    HEADER = (
        "# Transcript of session-1.wav\n"
        "# Whisper (base), run locally on 2026-09-21.\n"
        "# 0.7 minutes, language detected: en\n"
        "#\n"
        "# Names heard (2) -- verify every one:\n"
        "#   Beautly, Margarit\n"
        "#\n"
        "\n"
    )
    BODY = (
        "[00:00:00] Lee (interviewer): What is the first place you can picture?\n"
        "\n"
        "[00:00:08] Lee (owner): The kitchen. Always the kitchen.\n"
        "\n"
        "[00:00:14] Lee (owner): There was a range that never went out.\n"
    )

    def _parse(self, text: str):
        return ingest.parse_transcript(
            text,
            Session(case_id="rt", number=1),
            owner_names=["Lee (owner)"],
            interviewer_names=["Lee (interviewer)"],
        )

    def test_comment_header_is_not_parsed_as_speech(self):
        turns = self._parse(self.HEADER + self.BODY)
        assert len(turns) == 3
        assert not any(t.speaker.startswith("#") for t in turns)

    def test_parenthesised_speaker_labels_survive(self):
        turns = self._parse(self.HEADER + self.BODY)
        assert turns[0].role is SpeakerRole.INTERVIEWER
        assert turns[1].role is SpeakerRole.OWNER

    def test_transcript_does_not_collapse_into_one_blob(self):
        turns = self._parse(self.HEADER + self.BODY)
        report = ingest.ingest_report(turns)
        assert report["suspect_collapse"] is False
        assert report["longest_utterance_units"] < 30

    def test_collapse_is_detected_when_it_does_happen(self):
        """The guard that would have caught this at ingest time."""
        broken = "Everything on one line " * 60
        turns = ingest.parse_transcript(
            f"Speaker: {broken}", Session(case_id="rt", number=1)
        )
        assert ingest.ingest_report(turns)["suspect_collapse"] is True

    def test_timestamps_survive_the_round_trip(self):
        turns = self._parse(self.HEADER + self.BODY)
        assert [t.t_start for t in turns] == ["00:00:00", "00:00:08", "00:00:14"]


# ---------------------------------------------------------------------------
# 12. Whisper confidence does not correlate with the errors that matter.
# ---------------------------------------------------------------------------


def _heard_names(turns, code="en"):
    from lifestory.transcribe import build_checklist

    return [item.term for item in build_checklist(turns, code) if item.kind != "date"]


class TestHeardNames:
    def test_catches_the_errors_a_confidence_score_missed(self):
        """Every one of these scored avg_logprob -0.26, same as clean text."""
        from lifestory.transcribe import Turn
        heard_names = _heard_names

        turns = [
            Turn(0, 7, "Thank you for sitting down with me. Margarit, you mentioned "
                       "last week that you grew up near the river."),
            Turn(10, 11, "Beautly."),
            Turn(11, 16, "We were on the severed side, in a little terrace on Lax Lane."),
            Turn(17, 21, "My father worked the carpet mills at Kitter-Minster and "
                         "Heath cycle it."),
        ]
        names = heard_names(turns)
        for mangled in ("Margarit", "Beautly", "Kitter-Minster", "Heath"):
            assert any(mangled in name for name in names), f"missed {mangled}"

    def test_groups_consecutive_capitals_into_one_name(self):
        from lifestory.transcribe import Turn
        heard_names = _heard_names

        assert "Lax Lane" in heard_names([Turn(0, 1, "It was on Lax Lane.")])

    def test_ordinary_capitalised_words_are_not_listed(self):
        from lifestory.transcribe import Turn
        heard_names = _heard_names

        turns = [Turn(0, 1, "The kitchen. Always the kitchen. There was a range. "
                            "Six miles each way. What did you do?")]
        assert heard_names(turns) == []

    def test_lowercase_mishearings_are_not_claimed_to_be_caught(self):
        """"severed" for "Severn" is invisible to a capitalisation heuristic.

        Recorded so nobody mistakes the name list for a complete check.
        """
        from lifestory.transcribe import Turn
        heard_names = _heard_names

        names = heard_names([Turn(0, 1, "We were on the severed side.")])
        assert "severed" not in " ".join(names)


# ---------------------------------------------------------------------------
# 13. Loose substring matching filed the interviewer as the story owner.
# ---------------------------------------------------------------------------


class TestSpeakerRoleMatching:
    def _role(self, speaker, owners, interviewers):
        turns = ingest.parse_transcript(
            f"{speaker}: Some words here.",
            Session(case_id="r", number=1),
            owner_names=owners,
            interviewer_names=interviewers,
        )
        return turns[0].role

    def test_parenthesised_roles_do_not_collide(self):
        """Both labels start with the same name when operator == story owner."""
        assert self._role("Lee (owner)", ["Lee"], ["Lee"]) is SpeakerRole.OWNER
        assert (
            self._role("Lee (interviewer)", ["Lee"], ["Lee"])
            is SpeakerRole.INTERVIEWER
        )

    def test_one_name_inside_another_is_not_a_match(self):
        """"Ann" must not swallow "Annette"."""
        assert self._role("Annette", ["Ann"], ["Annette"]) is SpeakerRole.INTERVIEWER
        assert self._role("Ann", ["Ann"], ["Annette"]) is SpeakerRole.OWNER

    def test_exact_match_beats_substring(self):
        assert self._role("Margaret", ["Margaret"], ["Margaret Jones"]) is SpeakerRole.OWNER

    def test_conventional_labels_still_work(self):
        assert self._role("Operator", ["Margaret"], []) is SpeakerRole.INTERVIEWER
        assert self._role("Interviewer", ["Margaret"], []) is SpeakerRole.INTERVIEWER

    def test_unknown_speaker_stays_unknown(self):
        assert self._role("Susan", ["Margaret"], ["Operator"]) is SpeakerRole.UNKNOWN


# ---------------------------------------------------------------------------
# 14. The key in .env was never read.
#
# The README said to put the key in `.env`, and the "no API key" error said
# the same -- and nothing loaded the file. Anyone following the instructions
# exactly hit a dead end on their first extraction.
# ---------------------------------------------------------------------------


class TestDotenv:
    VARS = ("LIFESTORY_API_KEY", "DEEPSEEK_API_KEY", "LIFESTORY_MODEL", "LIFESTORY_API_BASE")

    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        import os

        from lifestory import llm

        (tmp_path / "cases").mkdir()
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(llm, "_DOTENV_LOADED", False)

        # Snapshot and restore by hand: the loader writes os.environ directly,
        # and monkeypatch.delenv on an absent key records nothing to undo.
        saved = {v: os.environ.pop(v, None) for v in self.VARS}
        self.root = tmp_path
        self.llm = llm
        yield
        for var, value in saved.items():
            os.environ.pop(var, None)
            if value is not None:
                os.environ[var] = value

    def test_key_in_dotenv_is_found(self):
        (self.root / ".env").write_text("LIFESTORY_API_KEY=sk-test\n", encoding="utf-8")
        assert self.llm.credentials_present()

    def test_nothing_anywhere_means_no_credentials(self):
        assert not self.llm.credentials_present()

    def test_the_environment_beats_the_file(self):
        import os

        (self.root / ".env").write_text("LIFESTORY_MODEL=from-file\n", encoding="utf-8")
        os.environ["LIFESTORY_MODEL"] = "from-shell"
        assert self.llm.describe().startswith("from-shell")

    @pytest.mark.parametrize(
        "encoding, label",
        [("utf-8-sig", "Notepad, UTF-8 with BOM"), ("utf-16", "Windows PowerShell 5.1 `>`")],
    )
    def test_windows_encodings(self, encoding, label):
        (self.root / ".env").write_bytes(
            "# my key\nexport LIFESTORY_MODEL=\"quoted-model\"\n".encode(encoding)
        )
        assert self.llm.describe().startswith("quoted-model"), label


# ---------------------------------------------------------------------------
# 15. Restricting a card after release left its claims going to the family.
#     Force-releasing a restricted card left the export blocked for good.
#
# Found when re-reading `restrict` against the claim-visibility model: it
# changed only the card, so utterances released earlier stayed released and
# every claim derived from them still reached the confirmation sheet. In the
# other direction, an owner's explicit release of restricted material left the
# utterances marked restricted but family-visible, which the confirmation
# validator blocks -- so the owner's own decision could never pass.
# ---------------------------------------------------------------------------


class TestReleaseAndRestrictMoveTogether:
    def _released_card_with_claim(self):
        from lifestory.models import StoryCard

        archive = make_archive()
        source = add_utterance(archive, "My first husband was called Thomas.")
        card = StoryCard(
            title="Thomas", setup="A first marriage.",
            sources=[SourceRef(kind="utterance", ref_id=source.id)],
        )
        archive.story_cards.append(card)
        archive.claims.append(
            Claim(text="Her first husband was Thomas", kind=ClaimKind.NAME,
                  sources=[SourceRef(kind="utterance", ref_id=source.id)])
        )
        archive.release_card(card, Access.FAMILY, note="Margaret, session 2")
        return archive, card

    def test_restricting_after_release_withdraws_derived_claims(self):
        archive, card = self._released_card_with_claim()
        assert confirm.build(archive).items, "precondition: the claim was visible"

        archive.restrict_card(card, note="Margaret changed her mind, session 3")

        assert confirm.build(archive).items == []
        assert "Thomas" not in confirm.render_markdown(confirm.build(archive))

    def test_restricting_reports_other_cards_on_the_same_words(self):
        from lifestory.models import StoryCard

        archive, card = self._released_card_with_claim()
        sibling = StoryCard(title="Sibling", setup="x", sources=list(card.sources))
        archive.story_cards.append(sibling)
        archive.release_card(sibling, Access.FAMILY, note="n")

        conflicts = archive.restrict_card(card)
        assert sibling in conflicts
        # And the validator agrees the sibling is now resting on private words.
        assert validators.check_access(archive)

    def test_owners_explicit_release_of_restricted_material_can_pass(self):
        from lifestory.models import StoryCard

        archive = make_archive()
        source = add_utterance(archive, "I never told the children about Thomas.")
        source.sensitivity = Sensitivity.RESTRICTED
        card = StoryCard(
            title="Thomas", setup="x", sensitivity=Sensitivity.RESTRICTED,
            sources=[SourceRef(kind="utterance", ref_id=source.id)],
        )
        archive.story_cards.append(card)

        with pytest.raises(PermissionError):
            archive.release_card(card, Access.FAMILY, note="n")

        archive.release_card(card, Access.FAMILY, note="Margaret's own decision", force=True)
        assert validators.check_confirmation_safety(archive) == []
        assert validators.check_access(archive) == []


# ---------------------------------------------------------------------------
# 16. An interrupted transcription lost every minute of work.
#
# A 40-minute session takes about 40 minutes with `small` on a laptop. Twice
# while Chinese support was being built, the machine rebooted partway through
# and all of it was lost. Segments are now checkpointed as they are produced.
# ---------------------------------------------------------------------------


class _FakeSegment:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text


class TestResumableTranscription:
    DURATION = 60.0

    @pytest.fixture
    def fake_whisper(self, monkeypatch, tmp_path):
        import numpy as np
        import faster_whisper

        calls = []
        state = {"interrupt_after": None}

        def decode_audio(path, sampling_rate=16000):
            return np.zeros(int(self.DURATION * sampling_rate), dtype=np.float32)

        class FakeModel:
            def __init__(self, *args, **kwargs):
                pass

            def transcribe(self, audio, **kwargs):
                seconds = len(audio) / 16000
                calls.append(seconds)

                def generate():
                    t, n = 0.0, 0
                    while t + 5 <= seconds + 1e-6:
                        if state["interrupt_after"] is not None and n >= state["interrupt_after"]:
                            state["interrupt_after"] = None
                            raise KeyboardInterrupt
                        yield _FakeSegment(t, t + 5, f"Line starting at {t:.0f}.")
                        t, n = t + 5, n + 1

                return generate(), None

        monkeypatch.setattr(faster_whisper, "decode_audio", decode_audio)
        monkeypatch.setattr(faster_whisper, "WhisperModel", FakeModel)
        audio = tmp_path / "session.m4a"
        audio.write_bytes(b"not really audio")
        return audio, calls, state

    def _run(self, audio, checkpoint, hints=None):
        from lifestory import transcribe

        return transcribe.transcribe(audio, owner_name="Margaret", model_size="base",
                                     language="en", hints=hints, checkpoint=checkpoint)

    def test_resumes_from_the_last_saved_segment(self, fake_whisper, tmp_path):
        audio, calls, state = fake_whisper
        checkpoint = tmp_path / "session.partial.jsonl"

        state["interrupt_after"] = 5  # "reboot" after 25 seconds of audio
        with pytest.raises(KeyboardInterrupt):
            self._run(audio, checkpoint)
        assert len(checkpoint.read_text(encoding="utf-8").splitlines()) == 1 + 5

        result = self._run(audio, checkpoint)

        # Only the remaining 35 seconds were sent the second time.
        assert calls == [60.0, pytest.approx(35.0)]
        starts = [t.start for t in result.turns]
        assert starts == sorted(starts)
        assert len(result.turns) == 12  # every 5-second line exactly once
        assert result.turns[-1].end == pytest.approx(60.0)
        assert not checkpoint.exists()  # finished: nothing to resume

    def test_changed_settings_start_over_rather_than_stitch(self, fake_whisper, tmp_path):
        audio, calls, state = fake_whisper
        checkpoint = tmp_path / "session.partial.jsonl"

        state["interrupt_after"] = 5
        with pytest.raises(KeyboardInterrupt):
            self._run(audio, checkpoint)

        # Different hints change what Whisper hears; the old half must not be reused.
        result = self._run(audio, checkpoint, hints=["Bewdley"])
        assert calls[-1] == pytest.approx(60.0)
        assert len(result.turns) == 12

    def test_torn_final_line_is_ignored(self, fake_whisper, tmp_path):
        audio, calls, state = fake_whisper
        checkpoint = tmp_path / "session.partial.jsonl"

        state["interrupt_after"] = 4
        with pytest.raises(KeyboardInterrupt):
            self._run(audio, checkpoint)
        with checkpoint.open("a", encoding="utf-8") as handle:
            handle.write('{"start": 20.0, "end": 25')  # cut off mid-write

        result = self._run(audio, checkpoint)
        assert len(result.turns) == 12


# ---------------------------------------------------------------------------
# 17. A narrator's rhetorical question was filed as the interviewer's.
#     A hinted name could appear where it was never said, looking verified.
# ---------------------------------------------------------------------------


class TestSpeakerGuessOnRealMonologue:
    def _speakers(self, turns, code="zh"):
        from lifestory.transcribe import _guess_speakers

        _guess_speakers(turns, "O", "I", code)
        return [t.speaker for t in turns]

    def test_rhetorical_question_in_the_same_breath_stays_with_the_owner(self):
        from lifestory.transcribe import Turn

        turns = [Turn(0, 5, "我想，我又不真做小长毛，不去攻城，也不放炮，更不怕炮炸"),
                 Turn(5.1, 7, "我惧惮她什么呢？")]
        assert self._speakers(turns) == ["O", "O"]

    def test_a_real_question_after_a_pause_is_the_interviewer(self):
        from lifestory.transcribe import Turn

        turns = [Turn(0, 5, "那时候我们家就住在河边，屋子很小。"),
                 Turn(6.2, 8, "那您母亲是做什么的？"),
                 Turn(9, 20, "她在纱厂做工，每天天不亮就出门。")]
        assert self._speakers(turns) == ["O", "I", "O"]

    def test_a_long_question_is_the_owner_musing(self):
        from lifestory.transcribe import Turn

        turns = [Turn(0, 5, "我们家就住在河边。"),
                 Turn(6.5, 14, "你说一个十几岁的姑娘，一个人跑到那么远的地方去做工，家里人怎么能放心呢？")]
        assert self._speakers(turns) == ["O", "O"]


class TestPrimedNamesAreFlagged:
    def test_hinted_name_is_listed_and_marked(self):
        from lifestory.transcribe import Turn, build_checklist, render_checklist

        turns = [Turn(0, 5, "茉莉的银鼠给她复仇的时候，一面又在渴慕着绘图的山海经了。")]
        checklist = build_checklist(turns, "zh", hints=["茉莉"])
        item = next(i for i in checklist if i.term == "茉莉")
        assert item.hinted
        assert "confirm it was really said here" in "\n".join(render_checklist(checklist))

    def test_unhinted_names_are_not_marked(self):
        from lifestory.transcribe import Turn, build_checklist

        turns = [Turn(0, 5, "长妈妈给我买来了三哼经。")]
        checklist = build_checklist(turns, "zh", hints=[])
        assert all(not i.hinted for i in checklist)


# ---------------------------------------------------------------------------
# 18. One Mandarin session overflowed a single model response and stopped
#     the case. Windows that are too long are now split and retried.
# ---------------------------------------------------------------------------


class TestAdaptiveExtraction:
    def _archive(self, n):
        from lifestory.models import ConsentRecord, ConsentScope

        archive = make_archive()
        archive.meta.language = "zh-Hans"
        for scope in (ConsentScope.RECORDING, ConsentScope.AI_PROCESSING,
                      ConsentScope.THIRD_PARTY_SERVICES):
            archive.consents.append(ConsentRecord(scope=scope, granted_by="M",
                                                  granted_by_role="story_owner", granted=True))
        session = Session(case_id="reg", number=1)
        archive.sessions.append(session)
        for i in range(n):
            archive.utterances.append(Utterance(session_id=session.id, seq=i, speaker="M",
                                                role=SpeakerRole.OWNER, text=f"第{i}句话。"))
        return archive, session

    def test_an_overflowing_window_is_split_not_fatal(self, monkeypatch):
        from lifestory import llm

        archive, session = self._archive(40)
        sizes, fitted = [], []

        def fake(window, **kwargs):
            sizes.append(len(window))
            if len(window) > 15:
                raise llm.TruncatedError("response truncated")
            fitted.append(len(window))
            return extract.ExtractionResult(story_cards=[extract.ExtractedStoryCard(
                title=f"片段{window[0].seq}", setup="……", strength=3,
                source_utterance_ids=[window[0].id])])

        monkeypatch.setattr(extract, "extract_window", fake)
        report = extract.extract_session(archive, session.id, window_size=40, overlap=0)

        assert sizes[0] == 40                     # tried whole first
        assert fitted and max(fitted) <= 15       # then split until each part fit
        # Every line was seen by some successful extraction.
        assert sum(fitted) >= 40
        assert archive.story_cards               # and the case carried on
        assert any("split in two" in note for note in report.notes)

    def test_a_tiny_window_that_still_overflows_is_reported(self, monkeypatch):
        from lifestory import llm

        archive, session = self._archive(5)
        monkeypatch.setattr(extract, "extract_window",
                            lambda window, **k: (_ for _ in ()).throw(llm.TruncatedError("x")))
        with pytest.raises(llm.TruncatedError):
            extract.extract_session(archive, session.id, window_size=5, overlap=0)


# ---------------------------------------------------------------------------
# 19. Keeping one card private let a sibling on the same words be released
#     with no prompt -- silently re-releasing the private words.
#
# Found on the Mandarin demo: the deathbed scene appeared on two cards built
# from the same sentences; one was kept private, and the other could still be
# released as if nothing had happened.
# ---------------------------------------------------------------------------


class TestSiblingsOfPrivateCards:
    def _siblings(self):
        from lifestory.models import StoryCard

        archive = make_archive()
        u = add_utterance(archive, "父亲的喘气颇长久，连我也听得很吃力。")
        ref = [SourceRef(kind="utterance", ref_id=u.id)]
        private = StoryCard(title="父亲躺在床上喘气", setup="x", sources=list(ref))
        sibling = StoryCard(title="父亲临终时，衍太太", setup="y", sources=list(ref))
        archive.story_cards += [private, sibling]
        return archive, private, sibling

    def test_sibling_cannot_be_released_without_the_owners_say_so(self):
        archive, private, sibling = self._siblings()
        archive.restrict_card(private, note="kept private")

        assert archive.needs_owner_release(sibling)
        with pytest.raises(PermissionError):
            archive.release_card(sibling, Access.FAMILY, note="n")

    def test_owners_explicit_decision_releases_it_cleanly(self):
        archive, private, sibling = self._siblings()
        archive.restrict_card(private, note="kept private")
        archive.release_card(sibling, Access.FAMILY, note="owner said yes", force=True)
        assert validators.check_confirmation_safety(archive) == []
        assert validators.check_access(archive) == []

    def test_review_offers_to_keep_the_sibling_private_too(self, tmp_path, monkeypatch):
        from lifestory import cli
        from lifestory.models import StoryCard

        (tmp_path / "cases").mkdir()
        monkeypatch.chdir(tmp_path)
        cli.main(["new", "lx", "--owner", "鲁迅", "--language", "zh"])
        archive, paths = store.load("lx")
        session = Session(case_id="lx", number=1)
        archive.sessions.append(session)
        u = Utterance(session_id=session.id, seq=1, speaker="鲁迅", role=SpeakerRole.OWNER,
                      text="父亲的喘气颇长久，连我也听得很吃力。")
        archive.utterances.append(u)
        ref = [SourceRef(kind="utterance", ref_id=u.id)]
        archive.story_cards += [
            StoryCard(title="临终", setup="a", year=1896, strength=5, sources=list(ref)),
            StoryCard(title="喘气", setup="b", year=1896, strength=4, sources=list(ref),
                      sensitivity=Sensitivity.RESTRICTED),
        ]
        store.save(archive, paths)

        replies = iter(["r", "k", "y"])  # release the first, keep the second, and its sibling
        monkeypatch.setattr(cli, "_ask", lambda *a, **k: next(replies))
        cli.main(["review", "lx", "--by", "鲁迅"])

        archive, _ = store.load("lx")
        assert all(c.access is not Access.FAMILY for c in archive.story_cards)
        assert validators.check_access(archive) == []


# ---------------------------------------------------------------------------
# 20. Claims on the family's sheet called the owner "故事主人" or “我”.
#
# The Mandarin demo's confirmation sheet asked the family to check
# "长妈妈是一向带领着故事主人的女工" and "阿长是“我”的保姆" -- the workbench's
# own jargon, translated, and the owner's first person, in sentences written
# for the family. The prompt now asks for the name; merge swaps stand-ins that
# can mean nobody else; what cannot be swapped safely is flagged for rewording.
# ---------------------------------------------------------------------------


class TestClaimsNameTheOwner:
    def _chinese(self):
        archive = Archive(meta=CaseMeta(id="lx", owner_name="周树人",
                                        owner_preferred_name="鲁迅", language="zh"))
        u = add_utterance(archive, "长妈妈，已经说过，是一个一向带领着我的女工。")
        return archive, u

    def test_the_prompt_asks_for_the_familys_name_for_them(self, monkeypatch):
        from lifestory import llm

        seen = {}

        def fake(*, system, prompt, output_model, **kw):
            seen["prompt"] = prompt
            return output_model()

        monkeypatch.setattr(llm, "complete_json", fake)
        archive, u = self._chinese()
        extract.extract_window([u], owner_name="周树人", preferred_name="鲁迅", language="zh")
        assert "known to the family as 鲁迅" in seen["prompt"]
        assert "call the story owner 鲁迅" in seen["prompt"]
        assert "third person" in seen["prompt"]

    def test_stand_ins_become_the_name_and_quotes_stay_verbatim(self):
        archive, u = self._chinese()
        extract.merge(
            archive,
            extract.ExtractionResult(
                claims=[
                    extract.ExtractedClaim(text="长妈妈是一向带领着故事主人的女工", kind="relationship",
                                           importance=4, source_utterance_ids=[u.id]),
                    extract.ExtractedClaim(text="阿长是“我”的保姆。", kind="relationship",
                                           importance=4, source_utterance_ids=[u.id]),
                ],
                story_cards=[
                    extract.ExtractedStoryCard(
                        title="带领着故事主人的女工", setup="长妈妈带领着讲述人。", strength=3,
                        source_utterance_ids=[u.id],
                        quotes=[extract.ExtractedQuote(text="是一个一向带领着我的女工",
                                                       source_utterance_id=u.id)],
                    )
                ],
            ),
            [u],
        )
        texts = [c.text for c in archive.claims]
        assert texts == ["长妈妈是一向带领着鲁迅的女工", "阿长是鲁迅的保姆。"]
        card = archive.story_cards[0]
        assert card.title == "带领着鲁迅的女工" and card.setup == "长妈妈带领着鲁迅。"
        assert archive.quotes[0].text == "是一个一向带领着我的女工"

    def test_a_named_and_an_unnamed_version_are_one_claim(self):
        archive, u = self._chinese()
        for text in ("阿长是故事主人的保姆", "阿长是鲁迅的保姆"):
            extract.merge(
                archive,
                extract.ExtractionResult(claims=[extract.ExtractedClaim(
                    text=text, kind="relationship", importance=4, source_utterance_ids=[u.id])]),
                [u],
            )
        assert len(archive.claims) == 1

    def test_english_jargon_is_swapped_but_a_real_speaker_is_not(self):
        archive = make_archive()
        archive.meta.owner_preferred_name = "Peggy"
        u = add_utterance(archive, "My mother was from Cork. Uncle Joe spoke at the wedding.")
        extract.merge(
            archive,
            extract.ExtractionResult(claims=[
                extract.ExtractedClaim(text="The story owner's mother was from Cork.",
                                       kind="place", importance=4, source_utterance_ids=[u.id]),
                extract.ExtractedClaim(text="The speaker at the wedding was Uncle Joe.",
                                       kind="name", importance=3, source_utterance_ids=[u.id]),
            ]),
            [u],
        )
        assert [c.text for c in archive.claims] == [
            "Peggy's mother was from Cork.",
            "The speaker at the wedding was Uncle Joe.",
        ]

    def test_first_person_on_the_sheet_is_flagged_not_rewritten(self):
        archive = make_archive()
        u = add_utterance(archive, "I was born in 1931 in Cork.", access=Access.FAMILY)
        ref = [SourceRef(kind="utterance", ref_id=u.id)]
        archive.claims += [
            Claim(text="I was born in 1931.", kind=ClaimKind.DATE, sources=list(ref)),
            Claim(text="Margaret was born in Cork.", kind=ClaimKind.PLACE, sources=list(ref)),
            Claim(text='Her father said "we will manage".', kind=ClaimKind.RELATIONSHIP,
                  sources=list(ref)),
        ]
        findings = validators.check_confirmation_wording(archive)
        assert [f.detail for f in findings] == ["I was born in 1931."]
        assert findings[0].severity is validators.Severity.WARN

    def test_reword_fixes_it_and_keeps_the_old_words(self, tmp_path, monkeypatch):
        from lifestory import cli

        (tmp_path / "cases").mkdir()
        monkeypatch.chdir(tmp_path)
        cli.main(["new", "hale", "--owner", "Margaret Hale", "--preferred", "Peggy"])
        archive, paths = store.load("hale")
        claim = Claim(text="I was born in 1931.", kind=ClaimKind.DATE)
        answered = Claim(text="Peggy married Ron in 1956.", kind=ClaimKind.DATE,
                         confirmed_by="Susan", status=ClaimStatus.CONFIRMED_BY_FAMILY)
        archive.claims += [claim, answered]
        store.save(archive, paths)

        assert cli.main(["reword", "hale", claim.id, "Peggy was born in 1931."]) in (0, None)
        with pytest.raises(SystemExit):
            cli.main(["reword", "hale", answered.id, "Peggy married Ron in 1957."])

        archive, _ = store.load("hale")
        assert archive.claim(claim.id).text == "Peggy was born in 1931."
        assert "was: I was born in 1931." in archive.claim(claim.id).notes
        assert archive.claim(claim.id).status is ClaimStatus.STATED_BY_OWNER
        assert archive.claim(answered.id).text == "Peggy married Ron in 1956."


# ---------------------------------------------------------------------------
# 21. Extracting session 2 re-extracted session 1, and an interrupted run
#     started its session over.
#
# A real case has several sessions weeks apart. `extract` with no arguments
# ran every session again: paying twice for the first, and filling the
# owner's next review with near-duplicates of cards they had already decided
# on. Sessions now record how far extraction got.
# ---------------------------------------------------------------------------


class TestExtractionProgress:
    def _case(self, tmp_path, sessions=2, turns=30):
        from lifestory.models import ConsentRecord, ConsentScope

        (tmp_path / "cases").mkdir()
        archive, paths = store.create_case(CaseMeta(id="prog", owner_name="Margaret"),
                                           root=tmp_path)
        for scope in (ConsentScope.RECORDING, ConsentScope.AI_PROCESSING,
                      ConsentScope.THIRD_PARTY_SERVICES):
            archive.consents.append(ConsentRecord(scope=scope, granted_by="M",
                                                  granted_by_role="story_owner", granted=True))
        for number in range(1, sessions + 1):
            session = Session(case_id="prog", number=number)
            archive.sessions.append(session)
            for i in range(turns):
                archive.utterances.append(Utterance(
                    session_id=session.id, seq=i, speaker="Margaret", role=SpeakerRole.OWNER,
                    text=f"Session {number}, a turn about the mill, number {i}."))
        store.save(archive, paths)
        return archive, paths

    @staticmethod
    def _fake(calls, fail_on=None):
        def fake(window, **kwargs):
            calls.append(window[0].text)
            if fail_on is not None and len(calls) == fail_on:
                raise RuntimeError("API fell over")
            return extract.ExtractionResult(story_cards=[extract.ExtractedStoryCard(
                title=f"Scene at {window[0].text}", setup="Something happened.", strength=4,
                source_utterance_ids=[window[0].id])])
        return fake

    def test_a_second_run_resumes_after_the_last_complete_window(self, tmp_path, monkeypatch):
        archive, paths = self._case(tmp_path, sessions=1)
        session = archive.sessions[0]
        calls: list[str] = []
        monkeypatch.setattr(extract, "extract_window", self._fake(calls, fail_on=3))
        with pytest.raises(RuntimeError):
            extract.extract_session(archive, session.id, window_size=10, overlap=2)
        assert session.extract_windows_done == 2 and session.extracted_at is None

        resumed: list[str] = []
        monkeypatch.setattr(extract, "extract_window", self._fake(resumed))
        report = extract.extract_session(archive, session.id, window_size=10, overlap=2)
        assert resumed[0] == calls[2], "should restart at the window that failed"
        assert len(resumed) == 2  # windows 3 and 4 of 4
        assert session.extracted_at is not None
        assert any("resumed" in note for note in report.notes)

    def test_extract_skips_sessions_already_done(self, tmp_path, monkeypatch, capsys):
        from lifestory import cli

        self._case(tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(extract, "api_key_present", lambda: True)
        calls: list[str] = []
        monkeypatch.setattr(extract, "extract_window", self._fake(calls))

        cli.main(["extract", "prog", "--session", "1"])
        first = len(calls)
        assert all(text.startswith("Session 1") for text in calls)

        cli.main(["extract", "prog"])  # only session 2 is left
        assert calls[first:] and all(text.startswith("Session 2") for text in calls[first:])

        before = len(calls)
        capsys.readouterr()
        cli.main(["extract", "prog"])
        assert len(calls) == before
        assert "already extracted" in capsys.readouterr().out

    def test_a_case_extracted_before_progress_was_recorded_is_not_redone(self):
        from lifestory.models import StoryCard

        archive = make_archive()
        u = add_utterance(archive, "I cycled to the mill.")
        archive.sessions.append(Session(id="ses_reg", case_id="reg", number=1))
        assert archive.unextracted_sessions()
        archive.story_cards.append(StoryCard(title="The mill", setup="x",
                                             sources=[SourceRef(kind="utterance", ref_id=u.id)]))
        assert archive.unextracted_sessions() == []


# ---------------------------------------------------------------------------
# 22. After `transcribe`, `status` still said "transcribe"; and it demanded
#     consent to send transcripts away before anything had been recorded.
# ---------------------------------------------------------------------------


class TestNextStepFollowsTheRealOrder:
    def _case(self, tmp_path, monkeypatch):
        from lifestory import cli

        (tmp_path / "cases").mkdir()
        monkeypatch.chdir(tmp_path)
        cli.main(["new", "wang", "--owner", "王秀英", "--language", "zh"])
        cli.main(["consent", "grant", "wang", "recording", "--by", "王秀英",
                  "--role", "story_owner"])
        return cli

    def test_a_fresh_transcript_is_the_next_thing_to_check_and_ingest(self, tmp_path, monkeypatch):
        cli = self._case(tmp_path, monkeypatch)
        archive, paths = store.load("wang")
        assert "transcribe" in cli._next_step(archive, paths)[1]

        paths.transcripts.mkdir(parents=True, exist_ok=True)
        (paths.transcripts / "第一次访谈.txt").write_text("王秀英: 我十九岁进了纱厂。",
                                                        encoding="utf-8")
        advice, command = cli._next_step(archive, paths)
        assert "第一次访谈.txt" in advice and "against the audio" in advice
        assert command.startswith("lifestory ingest wang")

    def test_ai_consent_is_asked_for_when_something_is_about_to_be_sent(self, tmp_path,
                                                                       monkeypatch):
        cli = self._case(tmp_path, monkeypatch)
        archive, paths = store.load("wang")
        paths.transcripts.mkdir(parents=True, exist_ok=True)
        path = paths.transcripts / "s1.txt"
        path.write_text("王秀英: 我十九岁进了纱厂，一干就是三十年。", encoding="utf-8")
        cli.main(["ingest", "wang", str(path)])

        archive, paths = store.load("wang")
        advice, command = cli._next_step(archive, paths)
        assert "before any transcript is sent" in advice
        assert "ai_processing" in command

        for scope in ("ai_processing", "third_party_services"):
            cli.main(["consent", "grant", "wang", scope, "--by", "王秀英", "--role", "story_owner"])
        archive, paths = store.load("wang")
        advice, command = cli._next_step(archive, paths)
        assert advice == "turn session 1 into story cards"
        assert command == "lifestory extract wang"


# ---------------------------------------------------------------------------
# 23. The book told one passage twice, and nothing pointed the QA read at the
#     sentences the model made up.
#
# Both from the Mandarin demo, whose manuscript passed every check. Chapters
# 5 and 6 each told the doctor passing in his sedan chair. And the prose
# carried lines nobody said -- 病家只得典衣当物 (the family pawned their
# clothes), 这两句话，我并排放着 -- which a spot-check of three random
# passages would most likely have missed.
# ---------------------------------------------------------------------------


class TestBookLevelChecks:
    def _chapters(self, *proses):
        from lifestory.compose import DraftedChapter

        return [DraftedChapter(number=i, title=f"c{i}", prose=p, provenance=[])
                for i, p in enumerate(proses, start=1)]

    def test_a_passage_told_in_two_chapters_is_flagged(self):
        chapters = self._chapters(
            "只在街上有时看见他。听说他现在还康健，一面行医，一面还做中医什么学报。",
            "轿子里是陈莲河。我听说他现在还康健。一面行医，一面还做中医的什么学报。",
        )
        findings = validators.check_repeated_passages(chapters, "zh")
        assert [f.message for f in findings] == ["chapters 1 and 2 tell the same passage"]

    def test_distinct_chapters_are_not_flagged(self):
        chapters = self._chapters("She went to Worcester at nineteen to train as a nurse.",
                                  "He cycled six miles to the mill each way, in all weathers.")
        assert validators.check_repeated_passages(chapters, "en") == []

    def test_invented_sentences_rank_below_supported_ones(self):
        archive = Archive(meta=CaseMeta(id="lx", owner_name="鲁迅", language="zh"))
        add_utterance(archive, "他们只得都依他。待去时，却只是草草地一看，说道不要紧的，"
                               "开一张方，拿了一百元就走。")
        chapters = self._chapters(
            "病家只得典衣当物，把钱凑齐。他只是草草地一看，说道不要紧的，开一张方，拿了一百元就走。"
        )
        flagged = validators.least_supported(archive, chapters)
        assert [u.sentence for u in flagged] == ["病家只得典衣当物，把钱凑齐。"]

    def test_english_paraphrase_is_not_flagged_but_invention_is(self):
        archive = make_archive()
        add_utterance(archive, "My father said I'd not last a winter on those wards. "
                               "I went to Worcester anyway, at nineteen.")
        chapters = self._chapters(
            "At nineteen she went to Worcester anyway. "
            "The rain had not stopped for a week and the station smelled of coal smoke."
        )
        flagged = validators.least_supported(archive, chapters)
        assert [u.sentence for u in flagged] == [
            "The rain had not stopped for a week and the station smelled of coal smoke."
        ]
