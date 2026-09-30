# Interview guide — standard mode

Spec v2 §7.2. For assisted mode (cognitive impairment) use `04` instead.
Interviewing in Mandarin: add [`03b-interview-guide-mandarin.md`](03b-interview-guide-mandarin.md).
中文版：[`zh/03-访谈指南.md`](zh/03-访谈指南.md)。

**15–25 minutes.** Not an hour. Short sessions completed weekly beat long
sessions abandoned, and an older adult who finishes a session feeling capable
comes back next week.

---

## The one thing that matters

You are not collecting information. You are looking for **scenes**.

A scene has a setup, something that goes wrong or gets decided, and a
consequence. "We were poor but happy" is not a scene. "The morning he borrowed
the foreman's car to drive to my certificate ceremony and didn't say a word the
whole way" is.

Summary is what people give you when they are telling you *about* their life.
Scenes are what they give you when they are back inside a moment. Your job is
to get them back inside moments.

---

## How to get there

**Ask small, not big.** "Tell me about your childhood" produces summary every
time. "What did the kitchen smell like on a Monday?" produces a scene.

**Follow the specific noun.** When they mention a thing — a chair, a bicycle, a
name, a road — that is the door. Go through it. "You said your mother had a
chair by the range. Tell me about that chair."

**One follow-up at a time.** Two questions in one breath gets you an answer to
the easier one.

**Let the silence sit.** Older adults often need three or four seconds. If you
fill it, you get the shallow answer. Count to five before you speak.

**Reflect back to check.** "So you'd have been about six when the boys arrived?"
This catches errors early and shows you are listening.

**Do not correct them.** If a date is wrong or they contradict themselves,
record it and raise it later as a claim. Their version is the material.

**Ask what it was like, not how they felt.** "What was that like?" opens up.
"How did that make you feel?" sounds like therapy and closes people down.

---

## Opening a session

Start somewhere safe and sensory. Never open on hardship.

- "What did your kitchen smell like?"
- "What was the first job you were ever paid for?"
- "Who was the best cook in your family?"
- "What did you do on a Sunday?"
- "What was the first thing you ever bought with your own money?"

If they brought photographs, start there. A photograph in the hand is the
single most reliable way into a memory.

---

## Question bank by territory

The seed bank is in `question-bank/seed.yaml` with yield rates once you have
run cases. These are the starting set.

### Childhood and home

- What is the first place you can still picture clearly?
- Who else was in the house? Where did everybody sleep?
- What was the rule in your house that you didn't dare break?
- What did you do when it rained?
- Was there somewhere you were not allowed to go?

### Family

- Who were you most like, growing up?
- Who was the difficult one?
- What did your mother do when she was angry?
- Tell me about a time the family disagreed about something.
- Was there anyone the family didn't talk about?

*(That last one goes near private territory. Ask it once, gently, and take the
first answer. If they open a door, follow. If they don't, leave it.)*

### Work

- What was the first job you were paid for? What did it pay?
- Who taught you how to do it?
- Tell me about a day at that job that went wrong.
- Was there someone there you were frightened of?
- When did you know you were good at it?

### Turning points — where the book lives

- What is the bravest thing you ever did?
- Was there a time you went against what someone wanted for you?
- What is a decision you have thought about since?
- Tell me about a time you were certain and wrong.
- Was there a moment you knew things had changed?

### Love and partnership

- Where did you first see them?
- What did you think of them at first?
- What did your family make of them?
- What were they like when nobody else was watching?
- What did you argue about?

### Things and places

- Is there an object you would run back into the house for?
- What did your street look like then?
- Is there a smell that takes you straight back?
- What is gone now that you miss?

### Late-session, once trust exists

- What do you know now that you wish you had known at twenty?
- What would you want your grandchildren to understand about you?
- Is there anything you have never told anybody?

*(Ask the last one once, near the end of a later session, and then be quiet.
Whatever comes back is covered by §12.2 and stays with them unless they
release it.)*

---

## Closing a session

- "Is there anything I should have asked about today?"
- "What should we talk about next time?"

Then tell them what happens next, in one sentence. Certainty is comforting.

---

## When to stop

Stop when they tire, not when your list runs out. Signs: shorter answers,
repeating an earlier story, looking at the door, checking with a family member
before answering.

Stop immediately and without comment if they become distressed. Do not push
through a difficult moment to "get the good material." That is the line
between an oral history and an extraction, and §6.1 is on the other side of it.

---

## Log everything you ask

```bash
lifestory ask <case-id> "What did the kitchen smell like on a Monday?" --topic childhood
```

Later, tag what it produced:

```bash
lifestory tag <case-id> q_xxxx scene --card sc_xxxx
```

This is the §17 asset. After fifty cases, knowing which questions actually
produce scenes from someone born in 1943 is the thing that makes our
interviews better than anyone else's — and it is the only part of this business
that compounds.
