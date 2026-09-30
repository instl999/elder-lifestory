"""The workbench as an operator uses it.

Exists because the parser once referenced a constant that a refactor had
removed: every command was broken and all hundred tests still passed, since
none of them built the parser. These run the real entry point.
"""

from __future__ import annotations

import pytest

from lifestory import archive as store
from lifestory import cli
from lifestory.models import (
    Access,
    Claim,
    ClaimKind,
    ClaimStatus,
    Sensitivity,
    Session,
    SourceRef,
    SpeakerRole,
    StoryCard,
    Utterance,
)


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "cases").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def run(*argv: str) -> int:
    try:
        return cli.main(list(argv))
    except SystemExit as exc:  # argparse and error paths exit
        return int(exc.code or 0)


def _answers(monkeypatch, *replies: str) -> None:
    """Script the interactive prompts, in order."""
    queue = list(replies)
    monkeypatch.setattr(cli, "_ask", lambda *a, **k: queue.pop(0))


# ---------------------------------------------------------------------------
# Every command builds and describes itself
# ---------------------------------------------------------------------------


def _subcommands() -> list[str]:
    parser = cli.build_parser()
    action = next(a for a in parser._actions if a.dest == "command")
    return sorted(action.choices)


@pytest.mark.parametrize("command", _subcommands())
def test_every_command_has_working_help(command, capsys):
    assert run(command, "--help") == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_top_level_help(capsys):
    assert run("--help") == 0


# ---------------------------------------------------------------------------
# A case from setup to the first real decision
# ---------------------------------------------------------------------------


def test_new_case_then_status_points_at_consent(project, capsys):
    assert run("new", "wang", "--owner", "王秀英", "--language", "zh",
               "--inheritor", "陈建国") == 0
    archive, _ = store.load("wang")
    assert archive.meta.language == "zh-Hans"

    capsys.readouterr()
    assert run("status", "wang") == 0
    out = capsys.readouterr().out
    assert "王秀英" in out
    assert "consent" in out.lower()


def test_next_step_follows_the_case(project):
    run("new", "wang", "--owner", "王秀英", "--language", "zh")
    for scope in ("recording", "ai_processing", "third_party_services"):
        assert run("consent", "grant", "wang", scope, "--by", "王秀英", "--role", "story_owner") == 0

    archive, paths = store.load("wang")
    assert "transcribe" in cli._next_step(archive, paths)[1]

    session = Session(case_id="wang", number=1)
    archive.sessions.append(session)
    archive.utterances.append(Utterance(session_id=session.id, seq=1, speaker="王秀英",
                                        role=SpeakerRole.OWNER, text="我小时候住在河边。"))
    assert "extract" in cli._next_step(archive, paths)[1]

    archive.story_cards.append(StoryCard(
        title="河边的家", setup="她小时候住在河边。",
        sources=[SourceRef(kind="utterance", ref_id=archive.utterances[0].id)],
    ))
    assert "review" in cli._next_step(archive, paths)[1]


# ---------------------------------------------------------------------------
# review: one decision at a time, saved as it goes
# ---------------------------------------------------------------------------


def _case_with_cards(project):
    run("new", "hale", "--owner", "Margaret Hale")
    archive, paths = store.load("hale")
    session = Session(case_id="hale", number=1)
    archive.sessions.append(session)

    def card(text, title, private=False):
        u = Utterance(session_id=session.id, seq=len(archive.utterances) + 1,
                      speaker="Margaret", role=SpeakerRole.OWNER, text=text)
        if private:
            u.sensitivity = Sensitivity.RESTRICTED
        archive.utterances.append(u)
        c = StoryCard(title=title, setup=text, year=1950 + len(archive.story_cards),
                      sources=[SourceRef(kind="utterance", ref_id=u.id)],
                      sensitivity=Sensitivity.RESTRICTED if private else Sensitivity.ROUTINE)
        archive.story_cards.append(c)
        archive.claims.append(Claim(text=f"claim about {title}", kind=ClaimKind.NAME,
                                    sources=[SourceRef(kind="utterance", ref_id=u.id)]))
        return c

    card("He cycled six miles to the mill.", "The cycle")
    card("The fish supper after the certificate.", "The fish supper")
    card("I had a son before Ron. Susan has never known.", "The adoption", private=True)
    store.save(archive, paths)
    return archive


def test_review_releases_keeps_and_skips(project, monkeypatch):
    _case_with_cards(project)
    # release, keep private, then the private card: release but decline to confirm
    _answers(monkeypatch, "r", "k", "r", "no")
    assert run("review", "hale", "--by", "Margaret") == 0

    archive, _ = store.load("hale")
    by_title = {c.title: c for c in archive.story_cards}
    assert by_title["The cycle"].access is Access.FAMILY
    assert "Margaret" in by_title["The cycle"].released_note
    assert by_title["The fish supper"].sensitivity is Sensitivity.RESTRICTED
    # Declining the YES prompt leaves the private disclosure private.
    assert by_title["The adoption"].access is Access.OWNER_ONLY or \
        by_title["The adoption"].access is Access.OPERATOR
    assert by_title["The adoption"].sensitivity is Sensitivity.RESTRICTED


def test_review_explicit_yes_releases_private_material_cleanly(project, monkeypatch):
    from lifestory import validators

    _case_with_cards(project)
    _answers(monkeypatch, "s", "s", "r", "YES")
    run("review", "hale", "--by", "Margaret")

    archive, _ = store.load("hale")
    adoption = next(c for c in archive.story_cards if c.title == "The adoption")
    assert adoption.access is Access.FAMILY
    assert "explicit release" in adoption.released_note
    assert validators.check_confirmation_safety(archive) == []


def test_review_quit_keeps_decisions_already_made(project, monkeypatch):
    _case_with_cards(project)
    _answers(monkeypatch, "r", "q")
    run("review", "hale", "--by", "Margaret")

    archive, _ = store.load("hale")
    released = [c.title for c in archive.story_cards if c.access is Access.FAMILY]
    assert released == ["The cycle"]


# ---------------------------------------------------------------------------
# answers: the family's replies, one item at a time
# ---------------------------------------------------------------------------


def test_answers_confirms_corrects_and_disputes(project, monkeypatch):
    archive = _case_with_cards(project)
    _, paths = store.load("hale")
    for c in archive.story_cards:
        if c.sensitivity is not Sensitivity.RESTRICTED:
            archive.release_card(c, Access.FAMILY, note="n")
    store.save(archive, paths)

    _answers(monkeypatch, "y", "e", "Correct spelling")
    assert run("answers", "hale", "--by", "Susan") == 0

    archive, _ = store.load("hale")
    statuses = sorted(c.status for c in archive.claims if c.confirmed_by == "Susan")
    assert statuses == [ClaimStatus.CONFIRMED_BY_FAMILY, ClaimStatus.CONFIRMED_BY_FAMILY]
    assert any(c.text == "Correct spelling" for c in archive.claims)
    # The private disclosure's claim was never offered to the family.
    assert all("adoption" not in c.text or c.confirmed_by is None for c in archive.claims)


# ---------------------------------------------------------------------------
# Chinese output never crashes the console
# ---------------------------------------------------------------------------


def test_chinese_case_prints(project, capsys):
    run("new", "wang", "--owner", "王秀英", "--preferred", "王奶奶", "--language", "zh")
    capsys.readouterr()
    assert run("list") == 0
    assert "王秀英" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# confirm: the sheet names the owner, or says which items don't
# ---------------------------------------------------------------------------


def test_confirm_points_at_items_that_speak_as_the_owner(project, capsys):
    run("new", "hale", "--owner", "Margaret Hale", "--preferred", "Peggy")
    archive, paths = store.load("hale")
    session = Session(case_id="hale", number=1)
    archive.sessions.append(session)
    u = Utterance(session_id=session.id, seq=1, speaker="Margaret", role=SpeakerRole.OWNER,
                  text="I was born in 1931, in Cork.", access=Access.FAMILY)
    archive.utterances.append(u)
    ref = [SourceRef(kind="utterance", ref_id=u.id)]
    first_person = Claim(text="I was born in 1931.", kind=ClaimKind.DATE, sources=list(ref))
    archive.claims += [first_person,
                       Claim(text="Peggy was born in Cork.", kind=ClaimKind.PLACE, sources=list(ref))]
    store.save(archive, paths)

    capsys.readouterr()
    assert run("confirm", "hale") == 0
    out = capsys.readouterr().out
    assert "1 item(s) don't name Peggy" in out
    assert first_person.id in out
    assert "lifestory reword hale" in out


def test_draft_length_follows_the_case_language_unless_given(project, monkeypatch):
    # `--words` defaulted to 1400, which silently overrode the 2,200-character
    # target for Chinese chapters.
    from lifestory import compose

    run("new", "wang", "--owner", "王秀英", "--language", "zh")
    archive, paths = store.load("wang")
    paths.drafts.mkdir(parents=True, exist_ok=True)
    direction = compose.NarrativeDirection(
        life_theme="x", proposition="x", point_of_view="third_person", tone="x",
        reading_level="x", structure="chronological", sensitive_handling="x",
        chapters=[compose.ChapterPlan(number=1, title="x", covers="x")],
    )
    (paths.drafts / "direction.json").write_text(direction.model_dump_json(), encoding="utf-8")

    seen = []

    def fake(archive, direction, plan, target_words=None):
        seen.append(target_words)
        raise compose.DraftingError("stop here")

    monkeypatch.setattr(compose, "draft_chapter", fake)
    run("draft", "wang")
    run("draft", "wang", "--words", "3000")
    assert seen == [None, 3000]
