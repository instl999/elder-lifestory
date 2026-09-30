# Case runbook

中文版：[00-操作手册.md](zh/00-操作手册.md)

How to run one Phase 0 case end to end. Spec v2 §19 Phase 0.

Phase 0 is delivered **by hand**. The workbench exists to make the operator
faster, not to replace them. If you find yourself wishing a step were
automated, log the time against that stage — that log is how §14 decides what
Phase 1 builds.

Commands below are written `lifestory <command>`; run them as
`python -m lifestory <command>` from the project folder. At any point,
`lifestory status <case-id>` ends with the one thing to do next.

**Chinese-speaking family?** Create the case with `--language zh` (or
`zh-Hant`), use the Chinese consent forms in [`zh/`](zh/), and before the
first session read [`03b-interview-guide-mandarin.md`](03b-interview-guide-mandarin.md)
on forms of address, modesty, dialect and the hard years. Every document in
this kit has a Chinese version in `zh/`.

---

## Before you start

- [ ] `lifestory doctor --online` is all green on the machine you will use.
- [ ] Read spec §12 in full. Not skimmed. The disclosure boundary in §12.2 and
      the capacity question in §12.3 are the two things that can end this
      business, and neither is obvious in the moment.
- [ ] Decide who the operator is. One named human owns the case.
- [ ] Confirm the trigger (§3.1). A transition case runs on a different clock
      from a gift case — 14 days, not 30.

---

## 1. Setup

```bash
lifestory new <case-id> \
  --owner "Full Name" --preferred "What they're called" --language en \
  --birth-year 1938 --sponsor "Who's paying" --operator "You" \
  --trigger transition --inheritor "Named inheritor"
```

`--language` is the family's language (`en`, `zh`, `zh-Hant`): everything the
family reads follows it. Set `--mode assisted` if there is any cognitive
impairment. It is not a label on the person; it changes how the interview runs
(§12.3).

**Designate the archive inheritor now**, not later. §12.4 exists because
someone will die mid-project and improvising that conversation is unforgivable.

## 2. Consent, before anything is recorded

Sit with the story owner. Use `01-consent-story-owner.md`. Read it aloud —
do not hand it over to be signed unread.

```bash
lifestory consent grant <case-id> recording --by "Owner Name" --role story_owner
lifestory consent grant <case-id> ai_processing --by "Owner Name" --role story_owner
lifestory consent grant <case-id> third_party_services --by "Owner Name" --role story_owner
```

`third_party_services` is separate because the transcript leaves this machine
and goes to an outside company, under their retention policy and their
jurisdiction — not ours. Say that plainly when you ask for it. Extraction
refuses without it.

In assisted mode a named human must record the capacity assessment:

```bash
lifestory consent grant <case-id> recording --by "Owner Name" \
  --role story_owner --capacity-assessed-by "Dr Name / You"
```

**Say the §12.2 boundary out loud in this conversation**, in plain language:

> "Anything you tell me stays with me unless you tell me you want it in the
> book. Your family will not see the recordings. If you tell me something you
> don't want them to know, that's yours — I won't pass it on."

That sentence is the product. Say it early, mean it, and hold to it.

## 3. Interview

Use `03-interview-guide-standard.md` or `04-interview-guide-assisted.md`.

**15–25 minutes. Not an hour.** Short sessions completed weekly beat long
sessions abandoned (§7.2).

Log every question you ask — this is the §17 asset and it is the only thing
here a competitor cannot copy:

```bash
lifestory ask <case-id> "What is the first place there you can still picture clearly?" --topic childhood
```

Record the audio. Any format; keep the original file somewhere safe.

## 4. Transcribe — locally

```bash
lifestory transcribe <case-id> recordings/session-1.m4a --hint "Susan,Bewdley,Kidderminster"
```

Whisper runs on this machine. The audio is read, never copied or modified, and
never uploaded. A recording of someone's voice is more exposing than the words
alone, and keeping it local means there is nothing to consent to and nothing
to withdraw.

**Pass the names you already know with `--hint`** — family members, the home
town, the street. They prime Whisper at every 30 seconds of audio. The story
owner's own names are added automatically.

Measured on a hard passage of real Mandarin, hints helped but did not solve
it: 1 of 7 names came back right without them, 2 of 7 with them. And once a
hinted word was written in at the start of a stretch where it was never said.
That matters, because a primed name that was never spoken looks exactly like a
name heard correctly. So primed names are marked on the checklist — **confirm
each one was really said where it appears.** Hints are worth using; they are
not a substitute for listening.

The model defaults to `small` for Chinese and `base` for English; `--model
medium` is slower and more accurate. A 25-minute session takes roughly 25
minutes with `small` on a laptop. Start it and do something else.

### Then check it. This step is not optional.

The command prints every name and date it thinks it heard, each with the time
it is first said so you can jump straight to it. **Check each one against the
audio.** From a real run on clear, synthesised speech:

| Whisper heard | Actually |
|---|---|
| Margarit | Margaret |
| Beautly | Bewdley |
| Kitter-Minster | Kidderminster |
| Heath cycle it | he'd cycle it |
| the severed side | the Severn side |

Every one of those scored an average log-probability of **-0.26** — identical
to the sentences it got perfectly right. Whisper does not know when it has
mangled a name, so there is no confidence number that will save you.

Left unchecked and extracted anyway, that transcript put a person called
*Margarit* and a place called *Kitter-Minster* into the archive as facts.

Also fix the speaker labels. They are guessed from the shape of each turn —
short questions are assumed to be yours — not from voice. Real diarisation is
not running.

*(Lowercase mishearings like "severed" for "Severn" are not on the name list.
Capitalisation cannot find them. Read the transcript.)*

## 5. Ingest and extract

```bash
lifestory ingest <case-id> cases/<case-id>/transcripts/session-1.txt --held-on 2026-09-12 --duration 22
lifestory extract <case-id>
lifestory labour <case-id> story_cards 95
```

**Log the time.** Every time. The Phase 1 decision is only as good as this log.

`extract` only works on sessions it hasn't extracted yet, so after the second
interview it sends only session 2. If it stops partway (network, reboot),
run it again and it continues from the last completed window. To redo a
session on purpose: `lifestory extract <case-id> --session 1`.

Then review what came back:

```bash
lifestory cards <case-id>           # everything
lifestory cards <case-id> --weak    # material that is not yet a scene
lifestory show <case-id> sc_xxxx    # one card with its sources
```

Weak cards are not failures — they are next session's questions. The
`followup_question` on each card is there for exactly that.

## 6. Tag what the questions produced

```bash
lifestory tag <case-id> q_xxxx scene --card sc_xxxx
lifestory tag <case-id> q_yyyy summary
```

A case does not close with untagged questions. `lifestory status` nags about
this on purpose.

## 7. Release material — deliberately, one card at a time

Nothing reaches the family until the story owner releases it. Go through the
cards **with them**:

```bash
lifestory review <case-id>
```

It shows each card in turn — the story, and their own words — and asks: **r**
release to the family, **k** keep private, **s** decide later, **q** stop. It
records who decided on every card and saves after each one, so stopping halfway
loses nothing.

A card marked private (a disclosure the extraction flagged, §12.2) can only be
released by typing `YES`, and only on the story owner's plain say-so. Keeping a
card private also withdraws the words it rests on, so nothing derived from them
reaches the confirmation sheet either.

Often two cards are built from the same sentences. When the owner keeps one
private and another card rests on those same words, `review` names the other
card and asks **"Keep those private too?"** — **y** keeps them private now,
**n** leaves them for the owner to decide. Either way, such a card cannot be
released later without the same typed `YES`: releasing it would release the
private words with it. The review tells you why when it asks.

If they are unsure, the answer is no. It can always be released later; it
cannot be unreleased once printed.

*(Single cards: `lifestory release <case-id> <card-id> --to family --note "..."`
and `lifestory restrict <case-id> <card-id> --note "..."`.)*

## 8. Confirmation queue

```bash
lifestory confirm <case-id>
```

**Read the sheet before you send it.** The family reads every item exactly as
written. Each should be a plain sentence about named people, naming the story
owner. `confirm` lists any item that speaks in the owner's first person ("I was
born in 1931"), which reads to a daughter as though she wrote it. Reword those
items, then run `confirm` again:

```bash
lifestory reword <case-id> <claim-id> "Peggy was born in 1931."
```

The claim keeps its sources and status, and the old wording is kept in its
notes. Once the family has answered on a claim it can't be reworded, because
they agreed to those words; record changes with `answer --correct`.

Send the generated sheet to the family reviewer. **25 items, 15 minutes.**
If you are tempted to add more, re-read §7.4 — the extra items are what kills
the project.

When the answers come back, record them in one pass:

```bash
lifestory answers <case-id>
```

For each item: **y** correct, **e** correct it (type the right version), **n**
wrong but nobody is sure what's right, **s** skip. Disputed items stay on the
list for the next session — do not choose between accounts yourself.

## 9. Direction, then drafting

```bash
lifestory direct <case-id>
```

**The family approves the direction before any chapter is written** (§9 Stage
2). Send them the generated page. It is much cheaper to change the shape now.

Read it yourself first. The chapter summaries are in the model's words, and
every fact in them should be one you heard in a session. On the Mandarin demo,
a detail that was never said (a family pawning their clothes) appeared first
here and was then written into the chapter. The family can't catch that,
because they weren't at the interviews. Delete it from the page, and from
that chapter's `covers` in `cases/<case-id>/drafts/direction.json`, since
drafting follows that file.

```bash
lifestory draft <case-id>
lifestory labour <case-id> drafting 180
```

Then read every chapter yourself. All of it. The model does not know when it
has written something that will hurt someone.

## 10. Check and export

```bash
lifestory check <case-id> --export
lifestory export <case-id>
```

Blocking findings are blocking. `--force` exists for genuine judgement calls,
not for getting to the end of the day.

`check` also lists **"Spot-check these first"**: in each chapter, the sentences
least like anything in the recordings. This is where invented detail usually
sits. Trace every one back to the transcript before you sign off the
Fabrication section of the QA checklist. A warning that two chapters tell the
same passage means one of them needs cutting.

## 11. Print it, and hand it over

Order the hardcover. Blurb, Lulu, or Bookvault; the printer spec is in the
exports folder.

**Deliver it in person if you possibly can.** §19: the moment a daughter hands
her father a bound book with his face on the cover is the thing being tested.
A PDF does not test it. Watch what happens. Write down what you saw.

## 12. Close the case

```bash
lifestory labour <case-id>       # full breakdown
lifestory status <case-id>       # untagged questions?
lifestory portfolio              # what Phase 1 should build
```

Record against the §18 thresholds:

- Did they pay the hypothesised price without discounting?
- Hours of direct labour, by stage
- Errors in names/dates/relationships at delivery (target: zero)
- Recognition test: three family members, "this sounds like her", 1–7, blind
- Diarise the six-month satisfaction re-ask

---

## The two things people get wrong

**Adding more to the confirmation queue.** It feels responsible. It is how the
project stalls. Resolve it yourself or ask next session.

**Softening the §12.2 boundary because the sponsor is paying and asks nicely.**
The sponsor's money does not buy the storyteller's private history. If this is
ever awkward, it is working correctly.
