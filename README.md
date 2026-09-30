# LifeStory — Phase 0 operator kit

English | [简体中文](README.zh-CN.md)

Tooling for delivering memoir cases by hand, built to the LifeStory product
specification v2. Section references (§) throughout the code and documents
point to that specification, which is kept outside this repository.

---

## What this is, and what it deliberately is not

The specification is explicit that the first version is **not** a consumer app.
§14 says deliver five to eight cases by hand, measure where the operator hours
actually went, and then automate the single most expensive step. §19 puts the
family-facing interview interface in Phase 2.

So this is an **operator's workbench**, not a product families touch:

- a CLI for one person running one case at a time
- the Phase 0 documents that case needs — consent, interview guides, QA
- the instruments that decide what Phase 1 builds

Building a family-facing UI now would be building for a user who does not
exist yet.

### What it does

| | |
|---|---|
| **Transcribe** | Audio → transcript via local Whisper, resumable, with a checklist of names and dates to verify |
| **Ingest** | Transcript → utterances with speaker and timestamp refs |
| **Extract** | Utterances → story cards, people, places, claims, quotes — every record citing its sources |
| **Review** | Go through the cards with the story owner: release, keep private, or decide later |
| **Verify** | Deterministic validators: chronology, names, quote fidelity, consent, access |
| **Confirm** | A 25-item family confirmation sheet, capped at 15 minutes, and their answers recorded in one pass |
| **Compose** | Narrative direction for approval, then chapters from approved material only |
| **Export** | Markdown manuscript, editable DOCX, printer spec, portable archive |
| **Measure** | Labour by stage and question yield — the two Phase 0 instruments |

---

## Install

Python 3.11+. From the project folder:

```bash
pip install -e .
```

Put your API key in a file called `.env` in the project folder — one line,
`LIFESTORY_API_KEY=...` (Notepad is fine; `.env` is gitignored). Then:

```bash
python -m lifestory doctor --online
```

`doctor` checks the Python version, every dependency, the key, the provider
connection and the transcription models, and says how to fix whatever is
missing. Run commands as `python -m lifestory <command>`; the `lifestory`
shortcut only works if Python's Scripts folder is on your PATH.

Extraction and drafting call a language model through
[`llm.py`](lifestory/llm.py), the only module that knows a vendor exists (§11).
It targets `deepseek-flash` over DeepSeek's OpenAI-compatible endpoint;
`LIFESTORY_API_BASE` and `LIFESTORY_MODEL` in `.env` override that.
Transcription runs **locally** with Whisper and needs no key. Everything else
— validators, confirmation sheet, exports, measurement — runs offline.

**Sending transcripts to an outside provider needs its own consent record.**
Extraction refuses without `third_party_services` (§12.1), and the story
owner's consent form should name the provider.

Case data lives in `cases/` and is gitignored. It is real people's lives and it
does not go in version control (§12.1).

---

## Running a case

Full walkthrough: [`operating-kit/00-case-runbook.md`](operating-kit/00-case-runbook.md)
(in Chinese: [`operating-kit/zh/00-操作手册.md`](operating-kit/zh/00-操作手册.md)).
**You do not need to remember the order:** `python -m lifestory status <case>`
always ends with the one thing to do next, and the command for it.

```bash
# 1. Start the case: the family's language, and who inherits the archive (§12.4).
python -m lifestory new wang --owner "王秀英" --preferred "王奶奶" --language zh \
  --birth-year 1941 --sponsor "陈建国" --inheritor "陈建国"

# 2. Consent, before anything is recorded (§12.1).
python -m lifestory consent grant wang recording --by "王秀英" --role story_owner
python -m lifestory consent grant wang ai_processing --by "王秀英" --role story_owner
python -m lifestory consent grant wang third_party_services --by "王秀英" --role story_owner

# 3. Transcribe locally, primed with the names you already know.
#    Then CHECK every name and date it lists against the audio.
python -m lifestory transcribe wang recordings/session-1.m4a --hint "陈建国,东昌坊口"
python -m lifestory ingest wang cases/wang/transcripts/session-1.txt

# 4. Story cards.
python -m lifestory extract wang

# 5. Go through them with the story owner: release, keep private, or later (§12.2).
python -m lifestory review wang

# 6. The family's confirmation sheet, then their answers (§7.4).
python -m lifestory confirm wang
python -m lifestory answers wang

# 7. The shape of the book for the family to approve, then the chapters (§9).
python -m lifestory direct wang
python -m lifestory draft wang

# 8. Check, export, print.
python -m lifestory check wang --export
python -m lifestory export wang
```

English cases work the same way with `--language en` (the default).

### Chinese cases

Set `--language zh` (Simplified) or `--language zh-Hant` (Traditional) when the
case is created, and everything the family sees follows it: the confirmation
sheet, the proposed shape of the book, and the book itself — front matter,
第一章-style chapter labels, two-character paragraph indents, and SimSun/SimHei
(or PMingLiU for Traditional) set as the Chinese fonts in the DOCX.
Operator-facing output stays in English.

- **Transcription** uses Whisper `small` for Chinese (`base` rendered the
  book's own title 朝花夕拾 as 招花西石), converts everything to the case's
  script (Whisper mixes Simplified and Traditional freely), fixes punctuation,
  and primes every 30 seconds of audio with the names you pass to `--hint`.
- **The name checklist** uses jieba part-of-speech tagging plus the shape of a
  Chinese name — surname, 阿- nickname, or kinship form like 长妈妈 or 衍太太 —
  and groups homophones, which is how Chinese recognition fails (王秀英 heard
  as 王秀瑛).
- **Transcripts from other tools** work too: full-width colons (王秀英：……),
  iFlytek's 说话人1/说话人2 labels, and 采访者/受访者 are all understood, while
  a colon after a speech verb (他说：“……”) is correctly left inside the line.
- **The paperwork** is in [`operating-kit/zh/`](operating-kit/zh/): the story
  owner's consent form and the sponsor's agreement written in Chinese for
  reading aloud, and Chinese versions of the runbook, the interview guides,
  the QA checklist and the death policy. The Mandarin interview questions are
  adapted for this generation — forms of address, modesty, dialect, and how
  to handle the hard years. For an English-speaking operator interviewing in
  Mandarin, [`03b-interview-guide-mandarin.md`](operating-kit/03b-interview-guide-mandarin.md)
  gives the same guidance in English. [`operating-kit/README.md`](operating-kit/README.md)
  pairs every English document with its Chinese counterpart.

Measured accuracy, and how to measure your own recordings:
[`samples/README.md`](samples/README.md).

### The Mandarin demo, end to end

[`samples/zh/run_demo.py`](samples/zh/run_demo.py) takes two public-domain
Mandarin recordings (鲁迅 reading from 《朝花夕拾》, 25 minutes in all) through
every command, from `new` to the printable DOCX, against the live DeepSeek
API. Two human steps are scripted, and the script prints them as it goes: the
operator's checks against the audio (checklist corrections only, with no
read-along), and the owner's and family's decisions.

| Stage | Result |
|---|---|
| Ingest | 2 sessions, 159 lines, 5,158 characters of the owner's words |
| Extract | 16 story cards, 51 claims, 38 verbatim quotes — 3½ minutes |
| Review | 12 released, 2 kept private, 2 left for the owner (they share words with a private card) |
| Confirm | 3 items for the family, each naming 鲁迅: "长妈妈是鲁迅的保姆，又叫阿长。" |
| Direct | 6 chapters around one thread, 谁肯把真话说给他听, with gaps to ask about next session |
| Draft + check | 2,633 characters, about 19 pages; every quote traced; one repeated passage flagged |

The output is in [`samples/zh/demo_book/`](samples/zh/demo_book/). Open
the DOCX to see what a family would hold: 6×9 layout, SimSun, 第一章
labels, and the paragraph indent Chinese readers expect.

What a close read found, and what it means for real cases:

- **The book is short.** 25 minutes of speech makes about 19 pages, most of
  them front matter and chapter openers. The drafter was told not to pad and
  didn't. It said so in its notes ("素材偏薄（两张卡，强度 3–4），本章短于
  1400 字"). A real case needs the full set of sessions.
- **Transcription errors that the checklist doesn't flag reach the book.**
  "他胖胖的，和矮" (和蔼) and "推它呢" (她) survived, because the scripted
  pass fixed only what the checklist listed. A real operator reads along
  with the audio.
- **Invented detail is still the risk.** Six lines were never said, among
  them "病家只得典衣当物" and "这两句话，我并排放着". All six appear in the
  spot-check list `check` now prints. See [Known gaps](#known-gaps).
- **The drafter's editor notes are worth reading.** It flagged its own
  character corrections ("“震怂”按通行写法作“震悚”，请核对") and the quotes it
  refused to use because the transcript had them wrong.

---

## The four constraints the code enforces

These are not configuration. They are the parts of the spec that are load-bearing,
and they are enforced in code because they are exactly the things that erode
under deadline pressure.

**Nothing reaches the family that the story owner has not released.**
Utterances default to `owner_only` and move outward only by an explicit,
per-item release that records who decided. This applies to *everything derived
from them*, not just story cards: a claim is only as releasable as the
utterances underneath it, computed fresh on every read rather than stored, so
it cannot go stale in the permissive direction. Material marked `restricted`
— the §12.2 disclosure case — propagates that mark to its source utterances,
and cannot be released without `--force` and a written note.

Three independent locks: `Archive.visible_to` in
[`models.py`](lifestory/models.py), `confirm.build`'s audience filter, and
`check_access` / `check_confirmation_safety` in
[`validators.py`](lifestory/validators.py). The first implementation had only
the first of these and leaked — see [Review findings](#review-findings).

**Nothing is invented.** Every story card cites utterance ids, and those
citations are verified against the archive after extraction — a fabricated
citation is dropped, not stored. Quotes are diffed against their source
transcript. Any quoted passage in a draft that does not trace to a real
utterance blocks the export.

**No transcript text leaves the machine without live consent.** Extraction
refuses without `recording`, `ai_processing` **and `third_party_services`** —
the last because the model provider is an outside company operating under its
own retention policy and jurisdiction. Export additionally requires
`family_sharing` and `print`.

**The family confirmation queue is hard-capped at 25 items.** Not a default —
a cap. §7.4 is explicit that claim-level family review is where these projects
die, and the temptation to add "just a few more" is the failure mode.

---

## Layout

```
lifestory/
  models.py       The Life Archive schema (§8). Access, release and consent live here.
  archive.py      Local-first persistence, versioned on every write
  language.py     Everything that depends on how text is shaped: length, sentences,
                  questions, Chinese script and punctuation, names, dates, homophones
  i18n.py         Every sentence the family reads, in their language
  transcribe.py   Local Whisper; audio never leaves the machine (§11, §12.1)
  ingest.py       Transcript parsing, including other tools' Chinese formats
  extract.py      Transcript → story cards, with citation verification
  compose.py      Narrative direction (§9 Stage 2) and chapter drafting (Stage 3)
  validators.py   The deterministic checks (§10.4) — plain code, never models
  confirm.py      The 25-item family queue (§7.4)
  render.py       Markdown, DOCX, provenance sheet, printer spec
  measure.py      Question yield (§17) and labour by stage (§18)
  llm.py          The only module that knows a vendor exists (§11)
  cli.py          The workbench (27 commands; `status` always says what's next)

operating-kit/    Phase 0 documents: runbook, consent, interview guides, QA, policy
operating-kit/zh/ The same, in Chinese, for Mandarin-speaking families and operators
question-bank/    51 seed questions in English and Chinese, tagged for yield (§17)
samples/          Public-domain Mandarin test audio and an accuracy evaluator
tests/            Safety, pipeline, CLI, and regressions from every review
```

Three agents, not eleven (§10): an **Interviewer** (not yet built — Phase 0
interviews are conducted by a human), an **Archivist** (`extract.py`), and a
**Writer** (`compose.py`), plus deterministic validators that are code rather
than models because they must never be creative.

---

## Tests

```bash
python -m pytest tests/ -q
```

Four files:

- `test_safety.py` — citation verification, quote fidelity, the access
  boundary, consent gating, chronology, the confirmation cap
- `test_pipeline.py` — a full ingest→extract→release→draft→check→export run
  with the model calls stubbed
- `test_cli.py` — the real entry point: every command's `--help`, the
  `review` and `answers` walkthroughs driven end to end, and `confirm`'s wording check
- `test_regressions.py` — one class per defect found in review, kept
  separate so the history of what went wrong stays legible

What the tests do **not** cover is prose quality. That is the recognition test
in §18 — three family members, blind, "does this sound like her" — and it needs
human readers.

---

## Review findings

The first implementation was reviewed before any real case ran. Ten defects
were found and fixed; each has a regression test in
[`tests/test_regressions.py`](tests/test_regressions.py).

**One was serious.** The confirmation sheet — the one artifact that goes to the
family *before* the book — drew on claims without checking access. A restricted
story card was correctly blocked, but a claim extracted from the same utterance
went into the family's sheet quoting the disclosure verbatim. In the test case
that reproduced it, the sheet would have told a daughter about an adoption her
mother had specifically said she had never known about.

The cause was scope: access control was built for story cards, and claims were
treated as metadata rather than as derived content. Visibility is now computed
from sources for *any* derived record, and there are two independent checks
behind `build`.

The rest, in descending order of consequence:

| | Defect | Effect |
|---|---|---|
| 2 | Window overlap produced duplicate cards, claims and quotes | ~30% of a case's cards were duplicates, inflating the §18 counts |
| 3 | `card.claim_ids` was never populated | Confirmation ranking and the excluded-claim validator were both dead code |
| 4 | `merge` and the validator disagreed on "verbatim" | merge did a raw substring test, flagging almost every real quote |
| 5 | `check_names` skipped identical names | The strongest duplicate signal was the one case it ignored |
| 6 | Extraction discarded all work if one window failed | The most expensive operation had no checkpoint |
| 7 | Every save wrote a full archive copy | 30 MB per 40 commands on a normal case |
| 8 | Re-ingesting a transcript silently doubled everything | Doubled the extraction bill too |
| 9 | The question-bank write-back was never wired to the CLI | The §17 moat was half-built |
| 10 | `output_config` passed alongside `output_format` | Risked clobbering the schema the SDK helper sets |

One suspicion did **not** hold up: `check_draft_quotes` looked quadratic, but
measures 0.6s on 1,800 utterances and 40 quoted passages, because the corpus
substring test short-circuits every genuine quote. Left alone.

### Second round: running it in Chinese

Feeding the pipeline one real Mandarin recording showed that it had assumed
English everywhere, usually silently. Each of these has a regression test.

**The pipeline**

| Defect | Effect |
|---|---|
| Sentence breaks were detected by capital letters | A 12.6-minute chapter came back as **one** transcript line |
| The collapse guard counted spaces | It waved that line through as "100 words" |
| The name checklist looked for capitals | Empty for every Chinese transcript |
| Whisper's mixed scripts were kept | 75 Traditional characters scattered through Simplified text |
| `base` was the default for Chinese | The book's own title 朝花夕拾 came back as 招花西石 |
| No prompt said which language to write in | Chinese families would have got English cards, sheets and books |
| Family-facing text and the DOCX font were English-only | Georgia has no Chinese glyphs; Word substituted silently |

**Checks that quietly switched themselves off**

| Defect | Effect |
|---|---|
| Duplicate-name check kept only `a-z` | Skipped Chinese names entirely — the language whose recognition fails by homophone |
| Story-card titles kept only `a-z` | Title-based de-duplication off for Chinese |
| Quote check compared against the whole line | A 15-character quote one character off scored 0.2 and would block export |

**Wider problems found along the way**

| Defect | Effect |
|---|---|
| The CLI printed Chinese to a cp1252 console | Crashed mid-command on the first Chinese character |
| The parser referenced a removed constant | **Every command broken**; no test built the parser, so all 100 passed. `test_cli.py` now does |
| `restrict` changed only the card | Claims from words released earlier kept reaching the family after the owner asked for privacy |
| Force-release left the words marked restricted | The owner's own decision to release could never pass export |
| Transcription could not resume | Two reboots each threw away a long run; it now checkpoints every segment |

### Third round: two recordings to a finished book

Found while taking the two recordings all the way to a printable book
([the demo](#the-mandarin-demo-end-to-end)):

| Defect | Effect |
|---|---|
| A card built on the same sentences as a private card could be released with no prompt | Releasing it released the private words too. The first demo run caught this only at `check`, after the whole book was drafted |
| Claims called the owner 故事主人 or “我” | The family's sheet read like internal notes: "长妈妈是一向带领着故事主人的女工" |
| A narrator's rhetorical question was taken for the interviewer's | Lines of a monologue were given to an interviewer who did not exist |
| Names passed to `--hint` looked like names actually heard | A primed word, written in where it was never said, would have passed as checked |
| One Mandarin session overflowed a single model response | Extraction stopped the case; long windows are now split in two and retried |
| Question-bank write-back dropped the `zh` field | The first `bank` update would have erased every Chinese question |
| `extract` re-ran every session, every time | Session 2 paid for session 1 again, and the owner's next review filled with near-duplicates of cards already decided; an interrupted run started its session over |
| After `transcribe`, `status` still said "transcribe" | The one command meant to say what's next pointed backwards at the most important manual step, checking the transcript |
| Two chapters told the same passage and every check passed | The book repeated a paragraph a page apart |
| Nothing pointed the QA read at likely inventions | "Spot-check three passages" at random would probably have missed a family pawning their clothes, which the recording never mentions |
| Chinese back matter wrote 录音2次 and 2026-09-29; the direction page used ASCII colons | Small things, but they are what makes a book look machine-made |

---

## Known gaps

**Drafted prose can contain invented texture, and nothing can prove it.**
This is the one gap that matters. The validators verify *provenance* —
citations and quotes — and both held perfectly on live runs in English and
Mandarin. They do not verify *texture*. A line like "I've never had anything
clearer from anybody", attributed to the story owner in first person and
never said by her, passes every check clean. So did the Mandarin demo's
病家只得典衣当物 (the family pawned their clothes to pay the doctor), which
the recording never says. It first appeared on the direction page the family
is asked to approve.

That is structural, not a property of any one model: a fabricated detail that
isn't quoted and isn't a claim has nothing to check it against. A stronger
model invents less; the gap stays open.

What the code *can* do is aim the human. `check` lists, per chapter, the
sentences least like anything in the recordings. On the demo, 6 of the 11 it
listed were inventions. It is blind to an invented link between two true
things. The Fabrication section of
[`05-qa-checklist.md`](operating-kit/05-qa-checklist.md) starts from that
list and then asks for three passages of the reader's own choosing. That step
is load-bearing. Do not skip it.

**Transcription has no real diarisation.** Speaker labels are guessed from
turn shape — short questions are assumed to be the interviewer — not from
voice. On a clean two-speaker recording that is mostly right and the operator
fixes the rest during the pass they have to do anyway. On a recording with a
family member joining in, expect to relabel by hand. Adding `pyannote.audio`
would fix it properly at the cost of a heavy dependency and a HuggingFace
token; not worth it until a real case says otherwise.

**The name checklist is not a complete check.** In English it finds capitalised
mishearings and cannot find lowercase ones ("the severed side" for "the
Severn side"). In Chinese it uses part-of-speech tagging plus the shape of a
name; on two chapters of real Mandarin it caught every family-style name
(长妈妈, 阿长, 衍太太, 陈莲河) at 67–77% precision, and missed famous figures
(洪秀全, 刚毅), which recognition tends to get right anyway. Read the transcript.

**Dialects are the hardest case, and the one that matters most.** Whisper
handles standard Mandarin reasonably; an 80-year-old speaking Shanghainese,
Cantonese or Hokkien-accented Mandarin will need heavy correction. If real
cases show that, the upgrade path is SenseVoice or Paraformer (FunASR), which
are far stronger on Chinese and dialects and also run locally — a swap inside
`transcribe.py`, not a redesign.

**The accuracy numbers come from read speech.** The Mandarin test audio is one
practised voice reading a prepared text ([`samples/`](samples/README.md)).
Spontaneous interviews with older speakers will score worse. Measure the first
real sessions with `samples/evaluate_asr.py` against a corrected transcript.

**Photo handling is stubbed.** The `Artifact` model carries originals,
derivatives, transformations and resolution, and the print validator checks
them — but there is no ingestion or restoration pipeline. Photo work is manual
in Phase 0, which is correct: §19 puts restoration in Phase 3.

**No interviewer agent.** Phase 0 interviews are conducted by a human, which
also sidesteps the ASR risk in §11 entirely.

**The consent forms are templates, not legal documents.** They need review in
your jurisdiction before a paying customer signs one. §22 lists archive
ownership and long-term preservation commitments as open legal questions.

---

## The thing worth protecting

Per §17, the durable asset here is not the code — it is the question bank and
what gets written back into it. Which questions produce a *scene* rather than a
summary, from someone born in 1943 versus 1958, is knowledge that compounds
over cases and that a competitor cannot copy.

`lifestory ask` and `lifestory tag` exist for that, `lifestory status` nags
about untagged questions on purpose, and `lifestory yield` is where it pays off.

Do not let a case close untagged.
