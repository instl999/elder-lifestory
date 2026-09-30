# QA checklist — before anything is delivered

中文版：[05-质检清单.md](zh/05-质检清单.md)

Spec v2 §9 Stage 4. Run `lifestory check <case-id> --export` first; it covers
the mechanical half. This sheet covers the half a machine cannot.

**One named human signs this.** Not "the team."

Case: ______________  Reviewer: ______________  Date: ____________

---

## Automated — must be clean

```bash
lifestory check <case-id> --export
```

- [ ] No blocking findings, or every override written down below with a reason
- [ ] Every quoted passage traces to a transcript (`draft_quotes`)
- [ ] Every story card cites a real utterance (`sources`)
- [ ] No chronology impossibilities
- [ ] No owner-only material behind a released card (`access`)
- [ ] Consent live for recording, AI processing, family sharing, print

Overrides used, and why:

_____________________________________________________________________

---

## Facts

- [ ] **Every name spelled as the family spells it.** Check against the
      confirmation queue answers, not against the transcript — ASR gets names
      wrong and confidently.
- [ ] Dates consistent between chapters
- [ ] Relationships correct and consistent (nobody is their own aunt)
- [ ] Place names as they were *then*, with the current name noted if it helps
- [ ] Nobody is described as dead who is alive, or alive who is dead
- [ ] Ages implied by the prose match the stated years

> Names, dates and relationships are the §18 zero-error threshold. Everything
> else on this sheet is judgement; these are pass/fail.

## Voice

- [ ] Read one page aloud. Does it sound like a person or like a model?
- [ ] Their characteristic phrasings survive
- [ ] No vocabulary they would never use
- [ ] No transcription noise left in ("um", false starts, repeated words)
- [ ] Understatement stays understated — the classic failure is inflating a dry
      remark into something sentimental

**The test:** would a granddaughter reading this hear her grandmother? If you
are not sure, you have your answer.

## Fabrication

The model's most dangerous output is the plausible detail nobody asked for.

> **Nothing automated covers this section.** `lifestory check` verifies
> citations and quotes, and it will pass a manuscript clean while a sentence
> like *"I've never had anything clearer from anybody"* — attributed to the
> story owner in first person, never said by her — sits in the middle of it.
> An invented detail that isn't quoted and isn't a claim has nothing to check
> it against. This section is the only thing standing between that sentence
> and the printed book.

- [ ] No invented weather, meals, clothing or interiors
- [ ] No invented dialogue — every quoted line traced
- [ ] No invented interiority. "She must have felt" is fabrication with better
      manners
- [ ] No invented causation: two things happening in sequence is not one
      causing the other
- [ ] Nothing "rounded up" — an approximate year still reads as approximate

**Start with the sentences `lifestory check` lists under "Spot-check these
first"** (also in the provenance sheet, per chapter). They are the sentences
least like anything in the recordings. On the Mandarin demo, 6 of the 11 it
listed were invented, including a family pawning their clothes that the
recording never mentions. The other 5 were fair paraphrase or a word the
transcript had wrong. It cannot see an invented link between two true things
("that was the second time he said it would be fine"), so read for those
yourself.

Then spot-check three more passages of your own choosing against their sources:

| Passage | Card | Traced? |
|---|---|---|
| | | |
| | | |
| | | |

## Dignity

- [ ] Nobody is a punchline, including people who are dead
- [ ] Hardship is not aestheticised
- [ ] Poverty, illness and grief are recorded, not used for effect
- [ ] No closing paragraph explaining the meaning of their life to them
- [ ] The story owner would recognise themselves and not feel handled

## Privacy and consent

- [ ] Nothing marked `restricted` appears anywhere in the book
- [ ] Every released item has a release note naming who decided
- [ ] Living people who are described unflatteringly: is it fair, is it
      necessary, is it defensible?
- [ ] No addresses, financial details or medical specifics that serve no
      narrative purpose
- [ ] Contributors credited as they wish to be

> If the story owner released something sensitive and you are uneasy, ask them
> once more, plainly, before it prints. Releasing is reversible until it is
> printed. After that it is in the family forever.

## Family conflict

- [ ] Contested accounts presented as contested, not adjudicated
- [ ] No living person accused of anything on one person's testimony
- [ ] Estrangements described without taking a side
- [ ] Would each family member named here feel fairly treated?

## Photographs

- [ ] Every caption identifies people correctly (from the confirmation queue)
- [ ] Any restoration or colourisation is labelled as such
- [ ] Originals preserved separately
- [ ] Nothing generated is presented as an authentic photograph
- [ ] Resolution warnings resolved or accepted

## Production

- [ ] Page count and spine width match the printer spec
- [ ] Body text 12pt or larger — the story owner has to be able to read it
- [ ] Front matter: title, copyright, consent notice, contents
- [ ] Back matter: about this book, acknowledgements, contributors
- [ ] Filename and metadata do not leak anything private

---

## The last question

Read the first page and the last page, one after the other.

**Is this a book this family will still want in twenty years?**

- [ ] Yes — deliver
- [ ] Not yet: _______________________________________________

---

## Sign-off

I have read the entire manuscript, not a sample.

Name: ______________________  Signature: ______________  Date: __________

---

## After delivery

- [ ] Recognition test: three family members, "this sounds like her", 1–7,
      blind against a human-written control
- [ ] Labour logged by stage, case closed in the workbench
- [ ] All questions tagged (§17)
- [ ] Six-month satisfaction re-ask diarised — day-one scores are taken in an
      emotional moment and do not predict referral
