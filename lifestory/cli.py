"""The operator workbench (spec v2 §14).

Phase 0 is delivered by hand. This is the tool the operator's hands use.

It is deliberately a CLI and not a web app: Phase 2 is when families touch
software (§19), and building a UI now would be building for a user who does
not exist yet.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path


def _force_utf8_console() -> None:
    """Make printing Chinese safe on any Windows console.

    When output is piped, or the console uses a legacy code page, Python on
    Windows encodes stdout as cp1252 (or GBK on a Chinese-locale machine) and
    raises UnicodeEncodeError on the first character it cannot map -- a
    Chinese name, or even the tick used on every success line. Reconfiguring
    to UTF-8 with replacement means the worst case is a garbled glyph, never
    a crash halfway through a command that has already written half its files.
    """
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if encoding != "utf8" and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover - detached streams
                pass


_force_utf8_console()

from rich.console import Console  # noqa: E402  (after the console is made safe)
from rich.markup import escape  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.table import Table  # noqa: E402

from . import archive as store
from . import (
    compose, confirm, extract, ingest, language, llm, measure, render, transcribe,
    validators,
)
from .models import (
    Access,
    Archive,
    AskedQuestion,
    CaseMeta,
    ConsentRecord,
    ConsentScope,
    InterviewMode,
    QuestionOutcome,
    Sensitivity,
    Session,
    Stage,
)

console = Console()


def _err(message: str) -> None:
    console.print(f"[bold red]error[/] {message}")


def _ok(message: str) -> None:
    console.print(f"[green]✓[/] {message}")


def _warn(message: str) -> None:
    console.print(f"[yellow]![/] {message}")


def _load(case_id: str) -> tuple[Archive, store.CasePaths]:
    try:
        return store.load(case_id)
    except FileNotFoundError as exc:
        _err(str(exc))
        raise SystemExit(1) from exc


# ---------------------------------------------------------------------------
# new / status
# ---------------------------------------------------------------------------


def cmd_new(args: argparse.Namespace) -> None:
    meta = CaseMeta(
        id=args.case_id,
        owner_name=args.owner,
        owner_preferred_name=args.preferred,
        owner_birth_year=args.birth_year,
        sponsor_name=args.sponsor,
        operator_name=args.operator,
        language=language.normalize(args.language),
        dialect=args.dialect,
        mode=InterviewMode(args.mode),
        package=args.package,
        trigger=args.trigger,
        archive_inheritor=args.inheritor,
    )
    try:
        archive, paths = store.create_case(meta)
    except FileExistsError as exc:
        _err(str(exc))
        raise SystemExit(1) from exc

    _ok(f"created case '{archive.meta.id}' at {paths.dir}")
    console.print()

    if args.mode == "assisted":
        console.print(
            Panel(
                "This case is in [bold]assisted mode[/] (§12.3).\n\n"
                "Sessions of 10–15 minutes. Photo-elicitation over open questions. "
                "Do not correct confabulation — record it. Revisit consent at "
                "every session, not once at the start.\n\n"
                "A named human must judge whether the story owner can meaningfully "
                "consent, and must be willing to decline the project if they cannot.",
                title="Assisted mode",
                border_style="yellow",
            )
        )
        console.print()

    if not meta.archive_inheritor:
        _warn(
            "no archive inheritor designated. §12.4 says decide this at project "
            "start, not after something happens. Set it with --inheritor."
        )

    console.print("Next: record consent before anything is recorded or processed.")
    console.print(
        f"  [dim]lifestory consent grant {meta.id} recording --by \"{meta.owner_name}\" "
        f"--role story_owner[/]"
    )


def cmd_status(args: argparse.Namespace) -> None:
    archive, paths = _load(args.case_id)
    meta = archive.meta

    cards = archive.story_cards
    scenes = [c for c in cards if c.is_scene()]
    released = archive.usable_story_cards()
    restricted = [c for c in cards if c.sensitivity is Sensitivity.RESTRICTED]

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()

    table.add_row("case", f"{meta.id}  [dim]rev {archive.revision}[/]")
    table.add_row("story owner", meta.owner_name + (f" (b. {meta.owner_birth_year})" if meta.owner_birth_year else ""))
    table.add_row("package / trigger", f"{meta.package}  ·  {meta.trigger}")
    table.add_row("mode", meta.mode.value + ("  [yellow](assisted)[/]" if meta.mode is InterviewMode.ASSISTED else ""))
    table.add_row("sessions", str(len(archive.sessions)))
    table.add_row("utterances", str(len(archive.utterances)))
    table.add_row(
        "story cards",
        f"{len(cards)}  [dim]({len(scenes)} scenes, {len(released)} released to family)[/]",
    )
    table.add_row("people / places", f"{len(archive.people)} / {len(archive.places)}")
    table.add_row("claims", str(len(archive.claims)))
    table.add_row("quotes", str(len(archive.quotes)))
    if restricted:
        table.add_row("restricted", f"[yellow]{len(restricted)}[/] [dim](§12.2 — owner release required)[/]")

    labour = measure.labour_summary(archive)
    table.add_row(
        "labour",
        f"{labour.total_hours}h of {labour.target_hours}h target"
        + ("  [red](over)[/]" if labour.over_target() else ""),
    )

    console.print()
    console.print(table)
    console.print()

    # Consent
    granted = {c.scope.value for c in archive.consents if c.is_live()}
    needed = [s.value for s in ConsentScope]
    consent_line = "  ".join(
        f"[green]✓ {s}[/]" if s in granted else f"[dim]· {s}[/]" for s in needed
    )
    console.print(f"  [dim]consent[/]  {consent_line}")
    console.print()

    untagged = measure.untagged_questions(archive)
    if untagged:
        _warn(
            f"{len(untagged)} questions have no outcome tagged. "
            f"The question bank (§17) is the moat — tag them before the case closes."
        )

    what, command = _next_step(archive, paths)
    console.print(f"[bold]Next:[/] {escape(what)}")
    if command:
        console.print(f"  [dim]{escape(command)}[/]")


def _shown(path: Path) -> str:
    """A path as the operator would type it: relative to here if possible."""
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


def _waiting_transcripts(archive: Archive, paths: store.CasePaths) -> list[Path]:
    """Transcripts `transcribe` wrote that no session has been ingested from.

    A checked copy saved under a longer name (session-1.checked.txt) counts
    as the original having been ingested.
    """
    if not paths.transcripts.exists():
        return []
    ingested = [Path(s.source_file).stem for s in archive.sessions if s.source_file]
    return sorted(
        p for p in paths.transcripts.glob("*.txt")
        if not any(stem == p.stem or stem.startswith(p.stem + ".") for stem in ingested)
    )


def _next_step(archive: Archive, paths: store.CasePaths) -> tuple[str, str | None]:
    """The one thing to do next, as plain advice plus the command for it.

    A case has a dozen stages and the runbook describes them all; at any given
    moment the operator needs exactly one of them.
    """
    cid = archive.meta.id

    def grant(scope: ConsentScope) -> str:
        return (f"lifestory consent grant {cid} {scope.value} "
                f"--by \"{archive.meta.owner_name}\" --role story_owner")

    # All three are normally signed together at the first meeting (runbook
    # step 2), so ask for all of them up front...
    everything = [ConsentScope.RECORDING, ConsentScope.AI_PROCESSING,
                  ConsentScope.THIRD_PARTY_SERVICES]
    if not archive.has_consent(ConsentScope.RECORDING):
        missing = archive.missing_consents(everything)
        return (
            f"record the story owner's consent ({', '.join(s.value for s in missing)}) "
            f"before anything is recorded",
            grant(missing[0]),
        )

    waiting = _waiting_transcripts(archive, paths)
    if waiting:
        return (
            f"check {waiting[0].name} against the audio — every name and date on its "
            f"checklist, and the speaker labels — then ingest it "
            f"(or delete it, if it is not needed)",
            f"lifestory ingest {cid} \"{_shown(waiting[0])}\"",
        )
    if not archive.sessions:
        return (
            "transcribe the first recording, then check the names it lists against the audio",
            f"lifestory transcribe {cid} <recording> --hint \"<names you already know>\"",
        )

    unextracted = archive.unextracted_sessions()
    if unextracted:
        # ...but only insist on the AI consents when something is about to be sent.
        missing = archive.missing_consents(everything)
        if missing:
            return (
                f"record the story owner's consent ({', '.join(s.value for s in missing)}) "
                f"before any transcript is sent to {llm.describe()}",
                grant(missing[0]),
            )
        numbers = ", ".join(str(s.number) for s in unextracted)
        label = "session" if len(unextracted) == 1 else "sessions"
        return (f"turn {label} {numbers} into story cards", f"lifestory extract {cid}")

    undecided = [
        c for c in archive.story_cards
        if c.access.rank() < Access.FAMILY.rank() and c.sensitivity is not Sensitivity.RESTRICTED
    ]
    if undecided and not archive.usable_story_cards():
        return (
            f"go through the {len(undecided)} story cards with the story owner and decide "
            f"what the family may see",
            f"lifestory review {cid}",
        )

    queue = confirm.build(archive)
    unanswered = [
        item for item in queue.items
        if (claim := archive.claim(item.claim_id)) is not None
        and claim.status.value in {"stated_by_owner", "unresolved"}
    ]
    if unanswered:
        return (
            f"{len(unanswered)} details for the family to confirm: send them the sheet, "
            f"then record their answers (you can propose the book's direction meanwhile)",
            f"lifestory confirm {cid}   then   lifestory answers {cid}",
        )

    if not (paths.drafts / "direction.json").exists():
        return ("propose the shape of the book for the family to approve", f"lifestory direct {cid}")
    if not list(paths.drafts.glob("chapter-*.json")):
        return (
            "once the family has approved the direction, draft the chapters",
            f"lifestory draft {cid}",
        )
    return (
        "read every chapter yourself against the QA checklist, then check and export",
        f"lifestory check {cid} --export   then   lifestory export {cid}",
    )


# ---------------------------------------------------------------------------
# review / answers -- guided walkthroughs instead of one command per item
# ---------------------------------------------------------------------------


def _ask(prompt: str, choices: list[str] | None = None, default: str | None = None) -> str:
    """One place every interactive question goes through, so tests can answer."""
    from rich.prompt import Prompt

    return Prompt.ask(prompt, choices=choices, default=default, console=console)


def _show_card(archive: Archive, card) -> None:
    lines = []
    when = f"{card.year}" + (f"–{card.year_end}" if card.year_end else "") if card.year else ""
    if card.period or when:
        lines.append(f"[dim]{escape(' · '.join(x for x in (card.period or '', when) if x))}[/]")
    for beat in ("setup", "conflict", "choice", "consequence", "reflection"):
        value = getattr(card, beat)
        if value:
            lines.append(f"[bold]{beat}[/]  {escape(value)}")
    for qid in card.quote_ids[:3]:
        quote = archive.quote(qid)
        if quote:
            lines.append(f"[italic]“{escape(quote.text)}”[/]")
    border = "yellow" if card.sensitivity is Sensitivity.RESTRICTED else "cyan"
    title = escape(card.title)
    if card.sensitivity is Sensitivity.RESTRICTED:
        title += "  [yellow](marked private)[/]"
    console.print(Panel("\n".join(lines) or "[dim](no detail)[/]", title=title, border_style=border))


def cmd_review(args: argparse.Namespace) -> None:
    """Go through the story cards with the story owner, one at a time (§12.2).

    Replaces one `release` or `restrict` command per card. Every decision is
    saved as it is made, so stopping halfway loses nothing.
    """
    archive, paths = _load(args.case_id)
    pending = [c for c in archive.story_cards if c.access.rank() < Access.FAMILY.rank()]
    if not args.all:
        # Cards already kept private in an earlier review are decided; cards
        # extraction merely *flagged* as private still need the owner's word.
        pending = [
            c for c in pending
            if not (c.sensitivity is Sensitivity.RESTRICTED and c.released_note)
        ]
    pending.sort(key=lambda c: (c.year or 9999, -c.strength))

    if not pending:
        _ok("nothing left to decide: every card is released or already kept private")
        return

    decider = args.by or _ask(
        "Who is deciding? This is recorded on every card",
        default=archive.meta.owner_preferred_name or archive.meta.owner_name,
    )
    note = f"{decider}, {date.today().isoformat()}"
    console.print(
        f"\n{len(pending)} cards. Read each one to the story owner and ask whether the "
        f"family may see it. [dim]r[/] release · [dim]k[/] keep private · "
        f"[dim]s[/] decide later · [dim]q[/] stop\n"
    )

    released = kept = 0
    for index, card in enumerate(pending, start=1):
        console.print(f"[dim]{index}/{len(pending)}[/]")
        _show_card(archive, card)
        choice = _ask("Decision", choices=["r", "k", "s", "q"], default="s")

        if choice == "q":
            break
        if choice == "s":
            continue
        if choice == "k":
            conflicts = archive.restrict_card(card, note=f"kept private: {note}")
            store.save(archive, paths)
            kept += 1
            if conflicts:
                # Settle it now, with the owner in the room, rather than leave
                # a blocked export to be discovered at the end.
                _warn_conflicts(conflicts)
                also = _ask("Keep those private too? y yes · n ask the owner later",
                            choices=["y", "n"], default="y")
                if also == "y":
                    for other in conflicts:
                        archive.restrict_card(other, note=f"kept private with '{card.title}': {note}")
                        kept += 1
                    store.save(archive, paths)
            continue

        # choice == "r"
        force = archive.needs_owner_release(card)
        if force:
            why = (
                "This was marked as a private disclosure."
                if card.sensitivity is Sensitivity.RESTRICTED
                else "This rests on words kept private with another card."
            )
            confirm_word = _ask(
                f"[yellow]{why}[/] Release it only if {escape(decider)} has said so "
                f"plainly. Type YES to release",
                default="no",
            )
            if confirm_word.strip().upper() != "YES":
                console.print("  [dim]left private[/]")
                continue
        archive.release_card(
            card, Access.FAMILY,
            note=note + (" (explicit release of private material)" if force else ""),
            force=force,
        )
        store.save(archive, paths)
        released += 1

    remaining = len([c for c in archive.story_cards if c.access.rank() < Access.FAMILY.rank()
                     and c.sensitivity is not Sensitivity.RESTRICTED])
    console.print()
    _ok(f"{released} released, {kept} kept private, {remaining} still to decide")


def cmd_doctor(args: argparse.Namespace) -> None:
    """Check that everything a case needs is in place, and say how to fix what is not."""
    import importlib
    import platform

    rows: list[tuple[bool, str, str]] = []

    version = sys.version_info
    rows.append((version >= (3, 11), f"Python {platform.python_version()}",
                 "" if version >= (3, 11) else "install Python 3.11 or newer"))

    for module, label in [
        ("openai", "model client"), ("faster_whisper", "local transcription"),
        ("docx", "book export"), ("jieba", "Chinese names"), ("opencc", "Chinese script"),
        ("pypinyin", "Chinese homophones"), ("yaml", "question bank"),
    ]:
        try:
            importlib.import_module(module)
            rows.append((True, f"{label} ({module})", ""))
        except ImportError:
            rows.append((False, f"{label} ({module})", "pip install -e ."))

    root = store.project_root()
    has_cases = (root / store.CASES_DIRNAME).is_dir()
    rows.append((has_cases, f"project folder {root}",
                 "" if has_cases else "run lifestory from the project folder"))

    have_key = llm.credentials_present()
    rows.append((have_key, f"API key for {llm.describe()}",
                 "" if have_key else "put LIFESTORY_API_KEY=... in a .env file in the project folder"))

    if have_key and args.online:
        try:
            llm._client().models.list()
            rows.append((True, "provider reachable", ""))
        except Exception as exc:  # network, auth
            rows.append((False, "provider reachable", f"{type(exc).__name__}: {str(exc)[:80]}"))

    hub = Path.home() / ".cache" / "huggingface" / "hub"
    for size, why in (("base", "English"), ("small", "Chinese")):
        cached = (hub / f"models--Systran--faster-whisper-{size}").is_dir()
        rows.append((cached, f"Whisper '{size}' model ({why})",
                     "" if cached else "downloads automatically on first transcribe"))

    bank = root / "question-bank" / "seed.yaml"
    rows.append((bank.exists(), "seed question bank", "" if bank.exists() else "missing question-bank/seed.yaml"))

    table = Table(box=None, padding=(0, 1), show_header=False)
    table.add_column(width=2)
    table.add_column()
    table.add_column(style="dim")
    for ok, what, fix in rows:
        table.add_row("[green]✓[/]" if ok else "[red]✗[/]", escape(what), escape(fix))
    console.print()
    console.print(table)

    failures = [r for r in rows if not r[0] and "automatically" not in r[2]]
    console.print()
    if failures:
        _warn(f"{len(failures)} thing(s) to fix before running a case")
        raise SystemExit(1)
    _ok("ready" + ("" if args.online else "  [dim](add --online to test the provider connection)[/]"))


def cmd_answers(args: argparse.Namespace) -> None:
    """Record the family's confirmation answers, one item at a time (§7.4).

    Replaces one `answer` command per item. Saved as it goes.
    """
    archive, paths = _load(args.case_id)
    queue = confirm.build(archive)
    if not queue.items:
        _ok("nothing is waiting for family confirmation")
        return

    who = args.by or _ask(
        "Whose answers are these?",
        default=archive.meta.family_reviewer_name or archive.meta.sponsor_name or "",
    )
    console.print(
        f"\n{len(queue.items)} items. [dim]y[/] correct · [dim]e[/] correct it · "
        f"[dim]n[/] wrong, not sure what's right · [dim]s[/] skip · [dim]q[/] stop\n"
    )

    counts = {"confirmed": 0, "corrected": 0, "disputed": 0}
    for index, item in enumerate(queue.items, start=1):
        body = f"[bold]{escape(item.current_value)}[/]"
        if item.context:
            body += f"\n[dim]“{escape(item.context)}”[/]"
        if item.conflicting:
            body += "\n[yellow]also told:[/] " + escape("; ".join(item.conflicting))
        console.print(Panel(body, title=f"{index}/{len(queue.items)} · {item.kind.value}",
                            border_style="cyan"))
        choice = _ask("Answer", choices=["y", "e", "n", "s", "q"], default="s")

        if choice == "q":
            break
        if choice == "s":
            continue
        if choice == "y":
            confirm.apply_answer(archive, item.claim_id, confirmed=True, by=who)
            counts["confirmed"] += 1
        elif choice == "e":
            corrected = _ask("What should it say?")
            if not corrected.strip():
                continue
            confirm.apply_answer(archive, item.claim_id, confirmed=True,
                                 corrected_text=corrected.strip(), by=who)
            counts["corrected"] += 1
        else:
            confirm.apply_answer(archive, item.claim_id, confirmed=False, by=who)
            counts["disputed"] += 1
        store.save(archive, paths)

    console.print()
    _ok(", ".join(f"{v} {k}" for k, v in counts.items()))
    if counts["disputed"]:
        console.print(
            "  [dim]Disputed items stay on the list. Ask about them at the next "
            "session rather than choosing between accounts yourself.[/]"
        )


def cmd_list(args: argparse.Namespace) -> None:
    cases = store.list_cases()
    if not cases:
        console.print("[dim]no cases yet. Start one with `lifestory new`.[/]")
        return

    table = Table(box=None, padding=(0, 2))
    table.add_column("case", style="bold")
    table.add_column("story owner")
    table.add_column("cards", justify="right")
    table.add_column("labour", justify="right")
    table.add_column("pkg", justify="center")

    for case_id in cases:
        archive, _ = store.load(case_id)
        labour = measure.labour_summary(archive)
        table.add_row(
            case_id,
            archive.meta.owner_name,
            str(len(archive.story_cards)),
            f"{labour.total_hours}h",
            archive.meta.package,
        )
    console.print()
    console.print(table)


# ---------------------------------------------------------------------------
# consent
# ---------------------------------------------------------------------------


def cmd_consent_grant(args: argparse.Namespace) -> None:
    archive, paths = _load(args.case_id)
    record = ConsentRecord(
        scope=ConsentScope(args.scope),
        granted_by=args.by,
        granted_by_role=args.role,
        granted=True,
        note=args.note,
        capacity_assessed_by=args.capacity_assessed_by,
    )

    if archive.meta.mode is InterviewMode.ASSISTED and not record.capacity_assessed_by:
        _err(
            "this case is in assisted mode. §12.3 requires a named human to have "
            "assessed capacity to consent. Pass --capacity-assessed-by."
        )
        raise SystemExit(1)

    if args.role == "sponsor" and args.scope in {"recording", "ai_processing"}:
        _warn(
            "recording and AI processing are the story owner's to consent to. "
            "§12.1: family approval never substitutes for the story owner's consent."
        )

    archive.consents.append(record)
    store.save(archive, paths)
    _ok(f"recorded consent: {record.scope.value} by {record.granted_by} ({record.granted_by_role})")


def cmd_consent_list(args: argparse.Namespace) -> None:
    archive, _ = _load(args.case_id)
    if not archive.consents:
        console.print("[dim]no consent records.[/]")
        return

    table = Table(box=None, padding=(0, 2))
    table.add_column("scope")
    table.add_column("by")
    table.add_column("role")
    table.add_column("live", justify="center")
    for record in archive.consents:
        table.add_row(
            record.scope.value,
            record.granted_by,
            record.granted_by_role,
            "[green]yes[/]" if record.is_live() else "[red]no[/]",
        )
    console.print()
    console.print(table)


# ---------------------------------------------------------------------------
# transcribe
# ---------------------------------------------------------------------------


def cmd_transcribe(args: argparse.Namespace) -> None:
    """Audio -> transcript, locally (§11, §12.1)."""
    archive, paths = _load(args.case_id)
    audio = Path(args.audio)
    code = archive.meta.language if args.language is None else args.language

    # The story owner's own names are always worth priming; the operator adds
    # family names and places they already know.
    hints = [archive.meta.owner_name, archive.meta.owner_preferred_name or ""]
    hints += [h for h in re.split("[,，、;；]", args.hint or "") if h.strip()]
    hints = list(dict.fromkeys(h.strip() for h in hints if h and h.strip()))

    console.print(
        f"  [dim]language: {escape(language.display_name(code))} · "
        f"model: {args.model or transcribe.default_model(code)} · "
        f"primed with: {escape('、'.join(hints) if language.is_chinese(code) else ', '.join(hints))}[/]"
    )

    paths.transcripts.mkdir(parents=True, exist_ok=True)
    out = paths.transcripts / f"{audio.stem}.txt"
    # Saved segment by segment: if the machine sleeps or reboots mid-way,
    # running the same command again picks up where it stopped.
    checkpoint = out.with_suffix(".partial.jsonl")

    try:
        result = transcribe.transcribe(
            audio,
            owner_name=archive.meta.owner_preferred_name or archive.meta.owner_name,
            interviewer_name=args.interviewer or archive.meta.operator_name or "Operator",
            model_size=args.model,
            language=None if code == "auto" else code,
            hints=hints,
            progress=lambda m: console.print(f"  [dim]{escape(m)}[/]"),
            checkpoint=checkpoint,
        )
    except KeyboardInterrupt:
        console.print(
            "\n  [dim]stopped. Everything transcribed so far is saved; run the same "
            "command again to continue.[/]"
        )
        raise SystemExit(130)
    except transcribe.TranscriptionError as exc:
        _err(str(exc))
        raise SystemExit(1) from exc

    out.write_text(transcribe.render_transcript(result, audio.name), encoding="utf-8")

    _ok(
        f"{result.minutes} min → {len(result.turns)} lines "
        f"({escape(', '.join(f'{k}: {v}' for k, v in result.speaker_counts().items()))})"
    )
    console.print(f"  [dim]{escape(str(out))}[/]")
    console.print("  [dim]audio not copied or modified; nothing left this machine[/]")

    if result.checklist:
        console.print()
        console.print(
            Panel(
                "[bold]Check every one of these against the audio.[/] The time is "
                "the first place each is said.\n\n"
                + escape("\n".join(line.strip() for line in transcribe.render_checklist(result.checklist)))
                + "\n\n[dim]Whisper mishears names confidently — its own confidence "
                "score rates them the same as the words it gets right. Names and dates "
                "are the §18 zero-error categories, and a wrong one propagates all the "
                "way to print. Not every entry will be a real name; none of the misses "
                "will be flagged at all, so read the transcript too.[/]",
                title=f"To check ({len(result.checklist)})",
                border_style="yellow",
            )
        )

    console.print()
    console.print(
        "  [dim]Speaker labels are guessed from turn shape, not voice. Fix them and "
        "the mishearings in the file. If a name was wrong, add the right spelling "
        "with --hint and transcribe again. It helps, but check primed names too: a "
        "hint can be written in where it was never said.[/]"
    )
    console.print(f"  [dim]Then: lifestory ingest {archive.meta.id} {escape(str(out))}[/]")


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------


def cmd_ingest(args: argparse.Namespace) -> None:
    archive, paths = _load(args.case_id)
    path = Path(args.transcript)
    if not path.exists():
        _err(f"no such file: {path}")
        raise SystemExit(1)

    session = Session(
        case_id=archive.meta.id,
        number=args.session_number or len(archive.sessions) + 1,
        held_on=date.fromisoformat(args.held_on) if args.held_on else None,
        mode=archive.meta.mode,
        duration_minutes=args.duration,
        interviewer=args.interviewer or archive.meta.operator_name,
        source_file=path.name,
    )

    owner_names = [archive.meta.owner_name, archive.meta.owner_preferred_name or ""]
    interviewer_names = [session.interviewer or ""]

    utterances = ingest.load_transcript_file(
        path, session, owner_names=owner_names, interviewer_names=interviewer_names
    )
    if not utterances:
        _err("parsed zero utterances. Check the transcript format (expects 'Speaker: text').")
        raise SystemExit(1)

    duplicate = ingest.already_ingested(archive, utterances)
    if duplicate is not None and not args.again:
        _err(
            f"this transcript is already in the archive as session {duplicate.number}. "
            f"Re-ingesting would double every utterance and the extraction bill with it. "
            f"Pass --again if you really mean to."
        )
        raise SystemExit(1)

    report = ingest.ingest_report(utterances)

    archive.sessions.append(session)
    archive.utterances.extend(utterances)
    store.save(archive, paths)

    _ok(
        f"session {session.number}: {report['utterances']} utterances, "
        f"{report['owner_units']} {report['unit']} from the story owner"
    )
    console.print(f"  [dim]roles: {report['by_role']}[/]")

    if report["suspect_collapse"]:
        _err(
            f"parsed only {report['utterances']} utterance(s), the longest "
            f"{report['longest_utterance_units']} words. The speaker pattern "
            f"almost certainly did not match and the transcript has collapsed "
            f"into one blob. Check the file uses 'Speaker: text' lines."
        )
        raise SystemExit(1)

    if not report["has_timestamps"]:
        _warn("no timestamps found. Quote provenance will be weaker (§8).")
    if report["unknown_role"] > report["utterances"] * 0.3:
        _warn(
            f"{report['unknown_role']} utterances have an unknown speaker role. "
            f"Fix the speaker labels before extracting — extraction quality depends on "
            f"knowing who is talking."
        )
    console.print(
        "\n  [dim]§11: check transcription quality against the audio before extracting. "
        "ASR error on older voices is a named risk.[/]"
    )


# ---------------------------------------------------------------------------
# extract
# ---------------------------------------------------------------------------


def cmd_extract(args: argparse.Namespace) -> None:
    archive, paths = _load(args.case_id)

    if not archive.sessions:
        _err("no sessions ingested yet")
        raise SystemExit(1)

    if args.session:
        # Named explicitly: extract it even if it was done before.
        sessions = [s for s in archive.sessions
                    if s.id == args.session or str(s.number) == args.session]
        if not sessions:
            _err(f"no session matching '{args.session}'")
            raise SystemExit(1)
    else:
        sessions = archive.unextracted_sessions()
        if not sessions:
            _ok(
                "every session is already extracted. To extract one again, name it: "
                f"lifestory extract {archive.meta.id} --session <number>"
            )
            return
        skipped = len(archive.sessions) - len(sessions)
        if skipped:
            console.print(f"  [dim]{skipped} session(s) already extracted — skipping them[/]")

    try:
        extract.check_consent(archive)
    except extract.ConsentError as exc:
        _err(str(exc))
        raise SystemExit(1) from exc

    if not extract.api_key_present():
        _err(
            "no API key. Put LIFESTORY_API_KEY=... in a .env file in the project folder."
        )
        raise SystemExit(1)

    console.print(f"  [dim]provider: {llm.describe()}[/]")

    for session in sessions:
        console.print(f"\n[bold]session {session.number}[/]")

        def progress(index: int, total: int) -> None:
            console.print(f"  [dim]window {index}/{total}[/]")

        try:
            report = extract.extract_session(
                archive,
                session.id,
                window_size=args.window,
                progress=progress,
                checkpoint=lambda: store.save(archive, paths),
            )
        except Exception as exc:
            store.save(archive, paths)
            _err(f"extraction stopped: {exc}")
            console.print(
                f"  [dim]completed windows were saved. Run `lifestory extract "
                f"{archive.meta.id}` again to continue from the last one.[/]"
            )
            raise SystemExit(1) from exc

        _ok(
            f"{report.story_cards_added} story cards, {report.claims_added} claims, "
            f"{report.quotes_added} quotes, {report.people_added} new people"
        )
        if report.duplicates_merged:
            console.print(
                f"  [dim]{report.duplicates_merged} records merged from overlapping "
                f"windows[/]"
            )

        if report.dropped_citations:
            _warn(
                f"dropped {len(report.dropped_citations)} fabricated citations "
                f"(cited utterances that do not exist)"
            )
        if report.unquotable:
            _warn(
                f"{len(report.unquotable)} quotes did not match their source verbatim — "
                f"`lifestory check` will flag them"
            )
        for note in report.notes:
            console.print(f"  [dim]{note}[/]")

        if report.followups:
            console.print("\n  [bold]Follow-up questions for the next session:[/]")
            for question in report.followups[:12]:
                console.print(f"    · {question}")

    store.save(archive, paths)
    console.print(
        f"\n  [dim]Log the time this took: lifestory labour {archive.meta.id} story_cards <minutes>[/]"
    )


# ---------------------------------------------------------------------------
# cards / release
# ---------------------------------------------------------------------------


def cmd_cards(args: argparse.Namespace) -> None:
    archive, _ = _load(args.case_id)
    cards = sorted(archive.story_cards, key=lambda c: (c.year or 9999, -c.strength))

    if args.weak:
        cards = [c for c in cards if not c.is_scene()]
    if args.restricted:
        cards = [c for c in cards if c.sensitivity is Sensitivity.RESTRICTED]

    if not cards:
        console.print("[dim]no story cards match.[/]")
        return

    table = Table(box=None, padding=(0, 1))
    table.add_column("id", style="dim")
    table.add_column("year", justify="right")
    table.add_column("title")
    table.add_column("str", justify="center")
    table.add_column("access")
    table.add_column("missing", style="dim")

    for card in cards:
        strength = f"[green]{card.strength}[/]" if card.strength >= 4 else (
            f"[yellow]{card.strength}[/]" if card.strength == 3 else f"[red]{card.strength}[/]"
        )
        access = card.access.value
        if card.sensitivity is Sensitivity.RESTRICTED:
            access = "[yellow]restricted[/]"
        table.add_row(
            card.id,
            str(card.year or "—"),
            card.title[:52],
            strength,
            access,
            ", ".join(card.missing_beats())[:28],
        )

    console.print()
    console.print(table)
    console.print()
    console.print(
        f"  [dim]{len([c for c in cards if c.is_scene()])} of {len(cards)} are scenes "
        f"(conflict + consequence, strength ≥ 3)[/]"
    )


def cmd_show(args: argparse.Namespace) -> None:
    archive, _ = _load(args.case_id)
    card = archive.story_card(args.card_id)
    if card is None:
        _err(f"no story card {args.card_id}")
        raise SystemExit(1)

    console.print()
    console.print(Panel(f"[bold]{card.title}[/]", border_style="cyan"))
    for label in ("setup", "conflict", "choice", "consequence", "reflection"):
        value = getattr(card, label)
        console.print(f"\n[bold]{label}[/]\n  {value or '[dim]—[/]'}")

    console.print(f"\n[bold]strength[/]  {card.strength}/5   [bold]access[/]  {card.access.value}"
                  f"   [bold]sensitivity[/]  {card.sensitivity.value}")

    if card.quote_ids:
        console.print("\n[bold]quotes[/]")
        for qid in card.quote_ids:
            quote = archive.quote(qid)
            if quote:
                console.print(f"  “{quote.text}” [dim]{quote.source.short()}[/]")

    console.print("\n[bold]sources[/]")
    for ref in card.sources:
        utterance = archive.utterance(ref.ref_id)
        preview = (utterance.text[:80] + "...") if utterance and len(utterance.text) > 80 else (
            utterance.text if utterance else "[missing]"
        )
        console.print(f"  [dim]{ref.short()}[/]  {preview}")

    if card.operator_note:
        console.print(f"\n[bold]follow-up[/]\n  {card.operator_note}")


def cmd_release(args: argparse.Namespace) -> None:
    """Affirmative, per-item release (§12.2). Never bulk."""
    archive, paths = _load(args.case_id)
    card = archive.story_card(args.card_id)
    if card is None:
        _err(f"no story card {args.card_id}")
        raise SystemExit(1)

    if archive.needs_owner_release(card) and not args.force:
        _err(
            f"'{card.title}' is marked restricted, or rests on words kept private with "
            f"another card (§12.2). Releasing it requires the story owner's own "
            f"decision, recorded with --force and a note saying who decided and when."
        )
        raise SystemExit(1)

    if args.force and not args.note:
        _err("--force requires --note recording who authorised the release")
        raise SystemExit(1)

    archive.release_card(card, Access(args.to), note=args.note, force=args.force)
    store.save(archive, paths)
    _ok(
        f"released '{escape(card.title)}' to {args.to}"
        + (f" — {escape(args.note)}" if args.note else "")
    )


def cmd_restrict(args: argparse.Namespace) -> None:
    archive, paths = _load(args.case_id)
    card = archive.story_card(args.card_id)
    if card is None:
        _err(f"no story card {args.card_id}")
        raise SystemExit(1)
    conflicts = archive.restrict_card(card, note=args.note)
    store.save(archive, paths)
    _ok(f"'{escape(card.title)}' and the words it rests on restricted to the story owner")
    _warn_conflicts(conflicts)


def _warn_conflicts(conflicts) -> None:
    if not conflicts:
        return
    _warn(
        f"{len(conflicts)} other released card(s) use the same words and now rest on "
        f"private material. Restrict them too, or ask the story owner:"
    )
    for other in conflicts:
        console.print(f"    [dim]{other.id}[/]  {escape(other.title)}")


# ---------------------------------------------------------------------------
# confirmation queue
# ---------------------------------------------------------------------------


def cmd_confirm(args: argparse.Namespace) -> None:
    archive, paths = _load(args.case_id)
    queue = confirm.build(archive, max_items=args.max)

    if not queue.items:
        console.print("[dim]nothing needs family confirmation.[/]")
        return

    markdown = confirm.render_markdown(queue)
    out = paths.exports / f"{archive.meta.id}-confirmations.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markdown, encoding="utf-8")

    _ok(f"{len(queue.items)} items — about {queue.estimated_minutes()} minutes for the family")
    console.print(f"  [dim]{out}[/]")
    if queue.deferred_count:
        console.print(
            f"  [dim]{queue.deferred_count} further uncertainties left for the editor "
            f"rather than the family (§7.4)[/]"
        )

    awkward = validators.check_confirmation_wording(archive)
    if awkward:
        owner = archive.meta.owner_preferred_name or archive.meta.owner_name
        console.print()
        _warn(f"{len(awkward)} item(s) don't name {escape(owner)} — reword them before sending:")
        for finding in awkward:
            console.print(f"  [bold]{finding.ref_id}[/] {escape(finding.detail or '')}")
            console.print(f"    [dim]{escape(finding.message)}[/]")
        console.print(
            f'  [dim]lifestory reword {archive.meta.id} <claim-id> "new wording"'
            f"   then run confirm again[/]"
        )


def cmd_reword(args: argparse.Namespace) -> None:
    """The operator's fix for a claim the family would misread.

    Not a family answer: the claim keeps its status and its sources, and the
    old wording is kept in its notes. A claim the family has already confirmed
    is refused -- they agreed to those words, and changing them silently would
    make that confirmation a lie. Record their correction with `answer`.
    """
    archive, paths = _load(args.case_id)
    claim = archive.claim(args.claim_id)
    if claim is None:
        _err(f"no claim {args.claim_id}")
        raise SystemExit(1)
    if claim.confirmed_by:
        _err(
            f"{claim.confirmed_by} already answered on this wording — record a change "
            f"with `lifestory answer {archive.meta.id} {claim.id} --correct ...`"
        )
        raise SystemExit(1)
    text = args.text.strip()
    if not text:
        _err("the new wording is empty")
        raise SystemExit(1)
    previous = claim.text
    claim.text = text
    note = f"reworded by the operator; was: {previous}"
    claim.notes = f"{claim.notes}\n{note}" if claim.notes else note
    store.save(archive, paths)
    _ok(escape(claim.text))
    problem = language.wording_problem(claim.text, archive.meta.language)
    if problem:
        _warn(f"it still {problem}")


def cmd_answer(args: argparse.Namespace) -> None:
    archive, paths = _load(args.case_id)
    try:
        claim = confirm.apply_answer(
            archive,
            args.claim_id,
            confirmed=not args.wrong,
            corrected_text=args.correct,
            by=args.by,
        )
    except KeyError as exc:
        _err(str(exc))
        raise SystemExit(1) from exc
    store.save(archive, paths)
    _ok(f"{claim.status.value}: {claim.text}")


# ---------------------------------------------------------------------------
# direction / draft
# ---------------------------------------------------------------------------


def cmd_direct(args: argparse.Namespace) -> None:
    archive, paths = _load(args.case_id)
    try:
        direction = compose.propose_direction(archive, operator_note=args.note)
    except compose.DraftingError as exc:
        _err(str(exc))
        raise SystemExit(1) from exc

    paths.drafts.mkdir(parents=True, exist_ok=True)
    (paths.drafts / "direction.json").write_text(
        direction.model_dump_json(indent=2), encoding="utf-8"
    )
    owner = archive.meta.owner_preferred_name or archive.meta.owner_name
    page = paths.exports / f"{archive.meta.id}-direction.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(compose.render_direction(direction, owner, archive.meta.language), encoding="utf-8")

    console.print()
    console.print(Panel(f"[bold]{direction.life_theme}[/]\n\n{direction.proposition}",
                        title="Proposed direction", border_style="cyan"))
    console.print(f"\n{len(direction.chapters)} chapters · {direction.structure} · "
                  f"{direction.point_of_view.replace('_', ' ')}\n")
    for chapter in direction.chapters:
        console.print(f"  [bold]{chapter.number}.[/] {chapter.title} "
                      f"[dim]({len(chapter.story_card_ids)} cards)[/]")

    if direction.gaps:
        console.print("\n[bold]Thin material — ask about these next session:[/]")
        for gap in direction.gaps:
            console.print(f"  · {gap}")

    console.print(f"\n  [dim]{page}[/]")
    console.print(
        "\n  [dim]§9 Stage 2: the family approves this before any chapter is drafted.[/]"
    )


def cmd_draft(args: argparse.Namespace) -> None:
    archive, paths = _load(args.case_id)
    direction_path = paths.drafts / "direction.json"
    if not direction_path.exists():
        _err(f"no approved direction. Run `lifestory direct {archive.meta.id}` first.")
        raise SystemExit(1)

    direction = compose.NarrativeDirection.model_validate_json(
        direction_path.read_text(encoding="utf-8")
    )

    chapters = direction.chapters
    if args.chapter:
        chapters = [c for c in chapters if c.number == args.chapter]
        if not chapters:
            _err(f"no chapter {args.chapter} in the approved direction")
            raise SystemExit(1)

    for plan in chapters:
        console.print(f"\n[bold]drafting {plan.number}. {plan.title}[/]")
        try:
            drafted = compose.draft_chapter(
                archive, direction, plan, target_words=args.words
            )
        except compose.DraftingError as exc:
            _warn(str(exc))
            continue

        out = paths.drafts / f"chapter-{plan.number:02d}.json"
        out.write_text(drafted.model_dump_json(indent=2), encoding="utf-8")

        length = language.units(drafted.prose)
        _ok(f"{length} {language.unit_label(archive.meta.language)} → {out.name}")

        findings = validators.check_draft_quotes(archive, drafted.prose)
        if findings:
            _warn(f"{len(findings)} quoted passages do not trace to a transcript:")
            for finding in findings:
                console.print(f"    [red]{finding.detail}[/]")

        for note in drafted.editor_notes:
            console.print(f"  [dim]note: {note}[/]")


# ---------------------------------------------------------------------------
# check / export
# ---------------------------------------------------------------------------


def _load_chapters(paths: store.CasePaths) -> list[compose.DraftedChapter]:
    out = []
    for path in sorted(paths.drafts.glob("chapter-*.json")):
        out.append(compose.DraftedChapter.model_validate_json(path.read_text(encoding="utf-8")))
    return out


def _show_spot_checks(archive: Archive, chapters: list[compose.DraftedChapter]) -> None:
    """Where the QA read should start looking for invented detail."""
    weakest = validators.least_supported(archive, chapters)
    if not weakest:
        return
    console.print(
        "\n[bold]Spot-check these first[/] — the sentences least like anything in the "
        "recordings. Most likely invented; some will be fair paraphrase or a word the "
        "transcript has wrong. (QA checklist, Fabrication)"
    )
    for item in weakest:
        console.print(f"  [dim]ch {item.chapter} · {item.support:.0%}[/]  {escape(item.sentence)}")
    console.print()


def cmd_check(args: argparse.Namespace) -> None:
    archive, paths = _load(args.case_id)
    chapters = _load_chapters(paths)

    report = validators.run_all(archive, chapters=chapters, for_export=args.export)
    _show_spot_checks(archive, chapters)

    if not report.findings:
        _ok("all checks clean")
        return

    table = Table(box=None, padding=(0, 1))
    table.add_column("", width=2)
    table.add_column("check", style="dim")
    table.add_column("finding")
    table.add_column("ref", style="dim")

    for finding in sorted(report.findings, key=lambda f: f.severity.value):
        mark = {"block": "[red]✗[/]", "warn": "[yellow]![/]", "info": "[dim]i[/]"}[
            finding.severity.value
        ]
        detail = f"{finding.message}" + (f"\n  [dim]{finding.detail}[/]" if finding.detail else "")
        table.add_row(mark, finding.check, detail, finding.ref_id or "")

    console.print()
    console.print(table)
    console.print(f"\n  [bold]{report.summary()}[/]")

    if report.blocking:
        console.print("  [red]Export is blocked until these are resolved.[/]")
        raise SystemExit(1)


def cmd_export(args: argparse.Namespace) -> None:
    archive, paths = _load(args.case_id)
    chapters = _load_chapters(paths)
    if not chapters:
        _err("no drafted chapters. Run `lifestory draft` first.")
        raise SystemExit(1)

    direction_path = paths.drafts / "direction.json"
    direction = compose.NarrativeDirection.model_validate_json(
        direction_path.read_text(encoding="utf-8")
    )

    draft_text = "\n\n".join(c.prose for c in chapters)
    report = validators.run_all(archive, chapters=chapters, for_export=True)

    if report.blocking and not args.force:
        _err(f"{len(report.blocking)} blocking findings. Run `lifestory check --export` to see them.")
        for finding in report.blocking[:5]:
            console.print(f"  [red]{finding.check}[/] {finding.message}")
        raise SystemExit(1)
    if report.blocking and args.force:
        _warn(f"exporting with {len(report.blocking)} blocking findings unresolved")

    paths.exports.mkdir(parents=True, exist_ok=True)
    stem = archive.meta.id

    manuscript = render.render_markdown(archive, direction, chapters)
    md_path = paths.exports / f"{stem}-manuscript.md"
    md_path.write_text(manuscript, encoding="utf-8")

    prov_path = paths.exports / f"{stem}-provenance.md"
    prov_path.write_text(render.render_provenance(archive, chapters), encoding="utf-8")

    spec_path = paths.exports / f"{stem}-printer-spec.md"
    spec_path.write_text(render.printer_spec(chapters, archive.meta.language), encoding="utf-8")

    written = [md_path, prov_path, spec_path]

    try:
        docx_path = render.render_docx(
            archive, direction, chapters, paths.exports / f"{stem}-memoir.docx"
        )
        written.append(docx_path)
    except RuntimeError as exc:
        _warn(str(exc))

    written.append(store.export_portable(archive, paths))
    store.save(archive, paths)

    pages = render.estimate_pages(chapters, archive.meta.language)
    console.print()
    _ok(f"{len(chapters)} chapters · ~{pages} pages · {language.units(draft_text)} {language.unit_label(archive.meta.language)}")
    for path in written:
        console.print(f"  [dim]{path}[/]")

    console.print()
    console.print(
        Panel(
            "[bold]§19: print it.[/]\n\n"
            "Order the hardcover and put it in the family's hands yourself. "
            "A PDF cannot validate this product — the moment a daughter hands her "
            "father a bound book with his face on the cover is the thing being tested.",
            border_style="cyan",
        )
    )


# ---------------------------------------------------------------------------
# labour / questions / portfolio
# ---------------------------------------------------------------------------


def cmd_labour(args: argparse.Namespace) -> None:
    archive, paths = _load(args.case_id)

    if args.minutes is not None:
        if args.stage is None:
            _err("logging labour needs a stage")
            raise SystemExit(1)
        measure.log_labour(
            archive, Stage(args.stage), args.minutes, who=args.who, note=args.note
        )
        store.save(archive, paths)
        _ok(f"logged {args.minutes} min to {args.stage}")

    summary = measure.labour_summary(archive)
    if not summary.by_stage:
        console.print("[dim]no labour logged yet.[/]")
        return

    table = Table(box=None, padding=(0, 2))
    table.add_column("stage")
    table.add_column("hours", justify="right")
    table.add_column("share", justify="right")

    for stage, minutes in summary.by_stage.items():
        share = minutes / summary.total_minutes if summary.total_minutes else 0
        table.add_row(stage, f"{minutes / 60:.1f}", f"{share:.0%}")

    console.print()
    console.print(table)
    console.print(
        f"\n  [bold]{summary.total_hours}h[/] against a {summary.target_hours}h target"
        + ("  [red](over)[/]" if summary.over_target() else "")
    )
    console.print(f"  [dim]{summary.verdict()}[/]")


def cmd_ask(args: argparse.Namespace) -> None:
    """Log a question that was asked (§17)."""
    archive, paths = _load(args.case_id)
    session = archive.sessions[-1] if archive.sessions else None
    decade = (
        (archive.meta.owner_birth_year // 10) * 10 if archive.meta.owner_birth_year else None
    )
    question = AskedQuestion(
        case_id=archive.meta.id,
        session_id=session.id if session else "",
        text=args.text,
        bank_id=args.bank_id,
        topic=args.topic,
        owner_birth_decade=decade,
        mode=archive.meta.mode.value,
    )
    archive.questions.append(question)
    store.save(archive, paths)
    _ok(f"logged: {question.text}")
    console.print(f"  [dim]tag the outcome later: lifestory tag {archive.meta.id} {question.id} scene[/]")


def cmd_tag(args: argparse.Namespace) -> None:
    archive, paths = _load(args.case_id)
    question = next((q for q in archive.questions if q.id == args.question_id), None)
    if question is None:
        _err(f"no question {args.question_id}")
        raise SystemExit(1)
    question.outcome = QuestionOutcome(args.outcome)
    if args.card:
        question.produced_story_card_ids.append(args.card)
    store.save(archive, paths)
    _ok(f"{question.text} → {args.outcome}")


def cmd_yield(args: argparse.Namespace) -> None:
    archives = [store.load(c)[0] for c in store.list_cases()]
    if not archives:
        console.print("[dim]no cases yet.[/]")
        return

    rows = measure.question_yield(archives)
    if not rows:
        console.print("[dim]no questions logged. §17: this log is the moat.[/]")
        return

    table = Table(box=None, padding=(0, 1))
    table.add_column("question")
    table.add_column("asked", justify="right")
    table.add_column("scene", justify="right")
    table.add_column("rate", justify="right")

    for row in rows[: args.limit]:
        rate = row.scene_rate()
        colour = "green" if rate >= 0.5 else ("yellow" if rate >= 0.25 else "red")
        table.add_row(
            row.text[:64], str(row.asked), str(row.scenes), f"[{colour}]{rate:.0%}[/]"
        )

    console.print()
    console.print(table)
    console.print(
        f"\n  [dim]{len(rows)} distinct questions across {len(archives)} cases. "
        f"Scene rate = produced a usable story card (§17).[/]"
    )


def cmd_bank(args: argparse.Namespace) -> None:
    """Write real case outcomes back into the seed bank (§17).

    Without this the bank is a static list of guesses. With it, the yield
    figures accumulate across cases and become the thing a competitor cannot
    copy.
    """
    path = Path(args.path)
    bank = measure.load_bank(path)
    if not bank:
        _err(f"no question bank at {path}")
        raise SystemExit(1)

    archives = [store.load(c)[0] for c in store.list_cases()]
    if not archives:
        console.print("[dim]no cases yet.[/]")
        return

    measure.refresh_bank_stats(bank, archives)
    measure.save_bank(bank, path)

    scored = [q for q in bank if q.asked]
    _ok(f"updated {len(bank)} seed questions from {len(archives)} cases")
    if not scored:
        _warn(
            "no seed question has been asked yet. Pass --bank-id when logging "
            "questions so they link back here."
        )
        return

    scored.sort(key=lambda q: (-(q.yield_rate() or 0), -q.asked))
    table = Table(box=None, padding=(0, 2))
    table.add_column("question")
    table.add_column("asked", justify="right")
    table.add_column("scene rate", justify="right")
    for question in scored[:15]:
        rate = question.yield_rate() or 0
        colour = "green" if rate >= 0.5 else ("yellow" if rate >= 0.25 else "red")
        table.add_row(question.text[:60], str(question.asked), f"[{colour}]{rate:.0%}[/]")
    console.print()
    console.print(table)


def cmd_portfolio(args: argparse.Namespace) -> None:
    """The cross-case report that decides what Phase 1 builds (§14, §18)."""
    case_ids = store.list_cases()
    if not case_ids:
        console.print("[dim]no cases yet.[/]")
        return

    archives = [store.load(c)[0] for c in case_ids]
    data = measure.portfolio_labour(archives)

    console.print()
    console.print(Panel(
        f"[bold]{data['cases']} cases[/] · {data['total_hours']}h total · "
        f"{data['mean_hours_per_case']}h mean per case",
        title="Phase 0 portfolio", border_style="cyan",
    ))

    table = Table(box=None, padding=(0, 2))
    table.add_column("stage")
    table.add_column("hours", justify="right")
    table.add_column("share", justify="right")
    for stage, hours, share in data["by_stage_hours"]:
        table.add_row(stage, f"{hours}", f"{share:.0%}")
    console.print()
    console.print(table)

    target = data["phase1_target"]
    console.print()
    if target == Stage.STORY_CARDS.value:
        console.print(Panel(
            f"Dominant stage is [bold]{target}[/], matching the §14 prediction.\n\n"
            f"Phase 1 builds the transcript-to-story-card workbench and nothing else.",
            border_style="green",
        ))
    else:
        console.print(Panel(
            f"Dominant stage is [bold]{target}[/], [yellow]not[/] story-card extraction.\n\n"
            f"§14's prediction was wrong. Phase 1 should automate '{target}' instead. "
            f"Update the specification before building.",
            border_style="yellow",
        ))

    console.print()
    console.print("  [dim]§18 thresholds: <20h direct labour by case five; "
                  "40% gross margin; zero name/date/relationship errors at delivery.[/]")

    scenes = sum(len([c for c in a.story_cards if c.is_scene()]) for a in archives)
    untagged = sum(len(measure.untagged_questions(a)) for a in archives)
    console.print(f"  [dim]{scenes} scenes captured across the portfolio.[/]")
    if untagged:
        _warn(f"{untagged} questions untagged across all cases — the §17 asset is leaking")


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lifestory",
        description="LifeStory Phase 0 operator workbench (spec v2 sec 14).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("new", help="start a case")
    p.add_argument("case_id")
    p.add_argument("--owner", required=True, help="story owner's full name")
    p.add_argument("--preferred", help="what they like to be called")
    p.add_argument("--birth-year", type=int)
    p.add_argument("--sponsor", help="who is paying / initiating")
    p.add_argument("--operator", help="who is running the case")
    p.add_argument("--language", default="en",
                   help="the family's language: en, zh (Simplified), zh-Hant (Traditional)")
    p.add_argument("--dialect")
    p.add_argument("--mode", choices=["standard", "assisted"], default="standard")
    p.add_argument("--package", choices=["A", "B", "C"], default="B")
    p.add_argument("--trigger", choices=["gift", "transition", "unknown"], default="unknown")
    p.add_argument("--inheritor", help="who inherits the archive (sec 12.4)")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("status", help="case overview")
    p.add_argument("case_id")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("doctor", help="check everything is installed and configured")
    p.add_argument("--online", action="store_true", help="also test the provider connection")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("list", help="list cases")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("consent", help="record or list consent")
    consent_sub = p.add_subparsers(dest="consent_command", required=True)
    g = consent_sub.add_parser("grant")
    g.add_argument("case_id")
    g.add_argument("scope", choices=[s.value for s in ConsentScope])
    g.add_argument("--by", required=True)
    g.add_argument("--role", required=True,
                   choices=["story_owner", "sponsor", "family_reviewer", "guardian"])
    g.add_argument("--note")
    g.add_argument("--capacity-assessed-by", dest="capacity_assessed_by")
    g.set_defaults(func=cmd_consent_grant)
    l = consent_sub.add_parser("list")
    l.add_argument("case_id")
    l.set_defaults(func=cmd_consent_list)

    p = sub.add_parser("transcribe", help="audio -> transcript, locally (sec 11)")
    p.add_argument("case_id")
    p.add_argument("audio", help="path to the recording; it is read, never modified")
    p.add_argument("--model", default=None, choices=list(transcribe.MODELS),
                   help="larger is slower and more accurate (default: small for "
                        "Chinese, base otherwise)")
    p.add_argument("--language", default=None,
                   help="defaults to the case's language; 'auto' to detect")
    p.add_argument("--hint", default="",
                   help="names and places you already know, comma-separated -- "
                        "they prime every 30 seconds of audio")
    p.add_argument("--interviewer")
    p.set_defaults(func=cmd_transcribe)

    p = sub.add_parser("ingest", help="load a transcript")
    p.add_argument("case_id")
    p.add_argument("transcript")
    p.add_argument("--session-number", type=int)
    p.add_argument("--held-on", help="YYYY-MM-DD")
    p.add_argument("--duration", type=int, help="minutes")
    p.add_argument("--interviewer")
    p.add_argument("--again", action="store_true",
                   help="ingest even if this transcript is already in the archive")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("extract", help="transcript -> story cards")
    p.add_argument("case_id")
    p.add_argument(
        "--session",
        help="extract this session (number or id), even if done before; "
             "default: every session not yet extracted",
    )
    p.add_argument("--window", type=int, default=extract.DEFAULT_WINDOW)
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("cards", help="list story cards")
    p.add_argument("case_id")
    p.add_argument("--weak", action="store_true", help="only cards that are not yet scenes")
    p.add_argument("--restricted", action="store_true", help="only restricted cards")
    p.set_defaults(func=cmd_cards)

    p = sub.add_parser("show", help="inspect one story card with its sources")
    p.add_argument("case_id")
    p.add_argument("card_id")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("release", help="release a story card to the family (sec 12.2)")
    p.add_argument("case_id")
    p.add_argument("card_id")
    p.add_argument("--to", choices=["family", "public"], default="family")
    p.add_argument("--note", help="who authorised this")
    p.add_argument("--force", action="store_true", help="release restricted material")
    p.set_defaults(func=cmd_release)

    p = sub.add_parser("restrict", help="pull a story card back to owner-only")
    p.add_argument("case_id")
    p.add_argument("card_id")
    p.add_argument("--note", help="who asked, and when")
    p.set_defaults(func=cmd_restrict)

    p = sub.add_parser("review", help="go through the story cards with the story owner (sec 12.2)")
    p.add_argument("case_id")
    p.add_argument("--by", help="who is deciding (asked if not given)")
    p.add_argument("--all", action="store_true", help="include cards already kept private")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("answers", help="record the family's confirmation answers (sec 7.4)")
    p.add_argument("case_id")
    p.add_argument("--by", help="whose answers these are (asked if not given)")
    p.set_defaults(func=cmd_answers)

    p = sub.add_parser("confirm", help="build the family confirmation queue (sec 7.4)")
    p.add_argument("case_id")
    p.add_argument("--max", type=int, default=confirm.MAX_ITEMS)
    p.set_defaults(func=cmd_confirm)

    p = sub.add_parser("reword", help="fix a claim's wording before the family sees it")
    p.add_argument("case_id")
    p.add_argument("claim_id")
    p.add_argument("text", help="the new wording, naming the story owner")
    p.set_defaults(func=cmd_reword)

    p = sub.add_parser("answer", help="record a family answer")
    p.add_argument("case_id")
    p.add_argument("claim_id")
    p.add_argument("--by", required=True)
    p.add_argument("--wrong", action="store_true", help="the family says this is wrong")
    p.add_argument("--correct", help="corrected text")
    p.set_defaults(func=cmd_answer)

    p = sub.add_parser("direct", help="propose narrative direction (sec 9 Stage 2)")
    p.add_argument("case_id")
    p.add_argument("--note", help="context for the story director")
    p.set_defaults(func=cmd_direct)

    p = sub.add_parser("draft", help="draft chapters (sec 9 Stage 3)")
    p.add_argument("case_id")
    p.add_argument("--chapter", type=int, help="one chapter only")
    p.add_argument(
        "--words", type=int, default=None,
        help="target length per chapter, in words (characters for Chinese); "
             "default 1400 words or 2200 characters",
    )
    p.set_defaults(func=cmd_draft)

    p = sub.add_parser("check", help="run the deterministic validators (sec 10.4)")
    p.add_argument("case_id")
    p.add_argument("--export", action="store_true", help="also check export consent")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("export", help="manuscript, DOCX, printer spec, archive")
    p.add_argument("case_id")
    p.add_argument("--force", action="store_true", help="export despite blocking findings")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("labour", help="log and report operator time (sec 18)")
    p.add_argument("case_id")
    p.add_argument("stage", nargs="?", choices=[s.value for s in Stage])
    p.add_argument("minutes", nargs="?", type=int)
    p.add_argument("--who")
    p.add_argument("--note")
    p.set_defaults(func=cmd_labour)

    p = sub.add_parser("ask", help="log a question that was asked (sec 17)")
    p.add_argument("case_id")
    p.add_argument("text")
    p.add_argument("--topic")
    p.add_argument("--bank-id")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("tag", help="tag what a question produced (sec 17)")
    p.add_argument("case_id")
    p.add_argument("question_id")
    p.add_argument("outcome", choices=[o.value for o in QuestionOutcome])
    p.add_argument("--card", help="story card it produced")
    p.set_defaults(func=cmd_tag)

    p = sub.add_parser("yield", help="question performance across cases (sec 17)")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(func=cmd_yield)

    p = sub.add_parser("bank", help="fold case outcomes back into the seed question bank (sec 17)")
    p.add_argument("--path", default="question-bank/seed.yaml")
    p.set_defaults(func=cmd_bank)

    p = sub.add_parser("portfolio", help="cross-case report: what Phase 1 should build")
    p.set_defaults(func=cmd_portfolio)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except KeyboardInterrupt:
        console.print("\n[dim]interrupted[/]")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
