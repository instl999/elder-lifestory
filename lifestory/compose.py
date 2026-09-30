"""Narrative direction and chapter drafting (spec v2 §9, §10.3).

Two stages, deliberately separated so a human approves the shape of the book
before any prose is written (§9 Stage 2: "The family and story owner approve
this direction before drafting").

The writer may only draw on story cards the story owner has released and that
are not marked restricted (§12.2). Everything it produces carries provenance
that the editor can inspect and the reader never sees (§6.2).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from . import language as lang
from . import llm
from .i18n import t
from .models import Archive, Quote, StoryCard


class DraftingError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Stage 2: narrative direction
# ---------------------------------------------------------------------------


class ChapterPlan(BaseModel):
    number: int
    title: str
    covers: str = Field(description="What this chapter is about, in one or two sentences.")
    story_card_ids: list[str] = Field(default_factory=list)
    opens_with: str | None = Field(
        default=None, description="The moment or image this chapter should open on."
    )


class NarrativeDirection(BaseModel):
    life_theme: str = Field(description="One phrase. The spine of the book.")
    proposition: str = Field(
        description="What this book argues about this life, in one sentence."
    )
    point_of_view: Literal["first_person", "third_person", "oral_history"]
    tone: str
    reading_level: str = Field(description="e.g. 'plain, accessible to a 12-year-old reader'")
    structure: Literal["chronological", "thematic", "mixed"]
    chapters: list[ChapterPlan]
    sensitive_handling: str = Field(
        description="How contested or tender material should be treated in this book."
    )
    opening_line_options: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(
        default_factory=list,
        description="What is thin and should be asked about in the next interview.",
    )


DIRECTION_SYSTEM = """\
You are the Story Director on a memoir production. You have a set of verified \
story cards drawn from interviews with an older adult, and you are proposing \
the shape of their book before anyone writes a word of it.

Your proposal goes to the story owner and their family for approval. Write it \
as something a non-specialist can say yes or no to.

Rules:

1. Work only from the story cards given to you. Do not assume events that are \
not there. If the material is thin in a period, say so in `gaps` rather than \
inventing a chapter to cover it.
2. The life theme must come out of the material, not out of a stock list of \
memoir themes. "Perseverance" is what you write when you have not read \
carefully. Look for what this particular person actually returns to.
3. Chapters should be built around scenes, not topics. A chapter that is just \
"his working life" will produce a dull chapter. A chapter built around the \
morning he walked out of the foundry will not.
4. Assign every strong story card to exactly one chapter. Weak cards may be \
left unassigned.
5. The proposition is an argument, not a summary. "A life of hard work and \
family" is not an argument. "That leaving was the bravest thing he ever did, \
and he spent forty years unsure it was right" is.\
"""


def _card_brief(archive: Archive, card: StoryCard) -> str:
    people = ", ".join(
        p.name for p in (archive.person(pid) for pid in card.person_ids) if p
    )
    places = ", ".join(
        p.name for p in (archive.place(pid) for pid in card.place_ids) if p
    )
    dating = ""
    if card.year:
        dating = f" ({card.year}{'-' + str(card.year_end) if card.year_end else ''}"
        dating += ", approximate)" if card.approximate else ")"

    parts = [f"[{card.id}] {card.title}{dating} -- strength {card.strength}/5"]
    if card.period:
        parts.append(f"  period: {card.period}")
    parts.append(f"  setup: {card.setup}")
    for label in ("conflict", "choice", "consequence", "reflection"):
        value = getattr(card, label)
        if value:
            parts.append(f"  {label}: {value}")
    if people:
        parts.append(f"  people: {people}")
    if places:
        parts.append(f"  places: {places}")
    if card.sensitivity.value != "routine":
        parts.append(f"  sensitivity: {card.sensitivity.value}")
    return "\n".join(parts)


def propose_direction(archive: Archive, *, operator_note: str | None = None) -> NarrativeDirection:
    cards = archive.usable_story_cards()
    if not cards:
        raise DraftingError(
            "no usable story cards. Release material to the family with "
            "`lifestory release` before proposing a direction."
        )

    cards = sorted(cards, key=lambda c: (c.year or 9999, -c.strength))
    briefs = "\n\n".join(_card_brief(archive, c) for c in cards)

    owner = archive.meta.owner_preferred_name or archive.meta.owner_name
    born = f", born {archive.meta.owner_birth_year}" if archive.meta.owner_birth_year else ""
    note = f"\n\nNote from the operator: {operator_note}" if operator_note else ""

    prompt = (
        f"The story owner is {owner}{born}.{note}\n\n"
        f"Here are the {len(cards)} story cards available for the book. "
        f"Strength is the editor's rating of how much of a scene each one is.\n\n"
        f"---\n{briefs}\n---\n\n"
        f"Propose the narrative direction for this memoir.\n\n"
        f"{writing_instruction(archive.meta.language)} The family reads this "
        f"proposal and approves it, so it must be in their language."
    )

    return llm.complete_json(
        system=DIRECTION_SYSTEM,
        prompt=prompt,
        output_model=NarrativeDirection,
        max_tokens=llm.EXTRACTION_TOKENS,
    )


def render_direction(
    direction: NarrativeDirection, owner_name: str, language: str | None = lang.EN
) -> str:
    """The direction as a page the family can approve or push back on.

    In the family's language: they are being asked whether this is the right
    book, and they cannot answer that about a page they cannot read.
    """
    colon = "：" if lang.is_chinese(language) else ": "
    lines = [
        f"# {t('direction.title', language, name=owner_name)}",
        "",
        t("direction.intro", language),
        "",
        f"## {t('direction.thread', language)}",
        "",
        f"**{direction.life_theme}**",
        "",
        direction.proposition,
        "",
        f"## {t('direction.how_told', language)}",
        "",
        f"- {t('direction.voice', language)}{colon}{t(f'pov.{direction.point_of_view}', language)}",
        f"- {t('direction.tone', language)}{colon}{direction.tone}",
        f"- {t('direction.reading_level', language)}{colon}{direction.reading_level}",
        f"- {t('direction.structure', language)}{colon}{t(f'structure.{direction.structure}', language)}",
        "",
        f"## {t('direction.chapters', language)}",
        "",
    ]

    for chapter in direction.chapters:
        lines.append(t("direction.chapter_item", language, number=chapter.number, title=chapter.title))
        lines.append("")
        lines.append(chapter.covers)
        if chapter.opens_with:
            lines.append(t("direction.opens_on", language, text=chapter.opens_with))
        lines.append("")

    if direction.opening_line_options:
        lines.extend([f"## {t('direction.opening_lines', language)}", ""])
        lines.extend(f"- “{option}”" for option in direction.opening_line_options)
        lines.append("")

    lines.extend([f"## {t('direction.sensitive', language)}", "", direction.sensitive_handling, ""])

    if direction.gaps:
        lines.extend([f"## {t('direction.still_ask', language)}", ""])
        lines.extend(f"- {gap}" for gap in direction.gaps)
        lines.append("")

    lines.extend(["---", "", t("direction.closing", language)])
    return "\n".join(lines)


def writing_instruction(language: str | None, *, prose: bool = False) -> str:
    """Tell the model which language to write in, and how.

    Without this the model writes in whatever language the prompt is in --
    English -- and a Chinese family receives English story cards, an English
    confirmation sheet, and an English book about their Chinese grandmother.
    """
    code = lang.normalize(language)
    instruction = (
        f"Write every text field in {lang.display_name(code)}. "
        f"Quotations stay exactly as the speaker said them."
    )
    if prose and lang.is_chinese(code):
        # Style guidance in the language of the prose it governs.
        instruction += (
            "\n\n写作要求：朴实、具体、温暖。保留讲述者的口头语和说话方式，"
            "不要改成书面腔或华丽的散文，短句也可以。不要加入素材里没有的细节、"
            "心理活动或议论。引用讲述者的原话时用中文引号“”，一字不改。"
        )
    return instruction


# ---------------------------------------------------------------------------
# Stage 3: chapter drafting
# ---------------------------------------------------------------------------


class DraftedChapter(BaseModel):
    number: int
    title: str
    prose: str = Field(description="The chapter itself, in Markdown. No headings.")
    provenance: list[str] = Field(
        default_factory=list, description="Story card ids this chapter draws on."
    )
    quotes_used: list[str] = Field(default_factory=list)
    editor_notes: list[str] = Field(
        default_factory=list,
        description="Anything the editor should check: thin material, a guessed transition.",
    )


DRAFT_SYSTEM = """\
You are writing one chapter of a memoir for an older adult's family. This book \
will be printed once and kept for decades. Treat it accordingly.

You are given story cards -- verified episodes from interviews -- and the \
speaker's own words. Write the chapter from those, and from nothing else.

Rules:

1. **Introduce no facts.** Every concrete thing in your prose must be in the \
story cards. No invented weather, no invented dialogue, no plausible-sounding \
detail that "must have been" true. If you need a transition and the material \
does not support one, write a shorter chapter.
2. **Quote exactly or not at all.** When you use the speaker's words in \
quotation marks, they must be reproduced character for character from the \
quotes provided. An approximate quote is a fabricated quote. If you want the \
sense of what they said without exact wording, write it as narration instead.
3. **Keep the speaker's voice.** They have characteristic phrasings, rhythms, \
and ways of understating things. Preserve those. Do not translate them into \
literary prose. Do not reproduce transcription noise -- false starts, "um", \
repeated words -- but do not smooth away the personality underneath it.
4. **Scenes, not summary.** Where a story card gives you setup, conflict, \
choice and consequence, write the scene. Where it gives you only general \
recollection, write briefly and move on rather than padding.
5. **No sentiment the material has not earned.** Do not tell the reader this \
was a remarkable life. Show what happened and let them decide. Avoid the \
closing paragraph that explains the meaning of everything they just read.
6. **Write for the family, not for a prize.** Plain, warm, specific. Short \
sentences are fine. The test is whether a granddaughter reads it and hears \
her grandmother.

Note anything you were unsure about in `editor_notes` rather than papering \
over it.\
"""


def _quotes_for(archive: Archive, cards: list[StoryCard]) -> list[Quote]:
    wanted = {qid for card in cards for qid in card.quote_ids}
    return [q for q in archive.quotes if q.id in wanted]


def draft_chapter(
    archive: Archive,
    direction: NarrativeDirection,
    chapter: ChapterPlan,
    *,
    target_words: int | None = None,
) -> DraftedChapter:
    """Draft one chapter from approved material only.

    `target_words` is in reader units -- characters for Chinese, words
    otherwise -- and defaults to a comfortable chapter in the case language.
    """
    language = archive.meta.language
    target = target_words or lang.chapter_target(language)
    usable = {c.id: c for c in archive.usable_story_cards()}
    cards = [usable[cid] for cid in chapter.story_card_ids if cid in usable]

    if not cards:
        raise DraftingError(
            f"chapter {chapter.number} ('{chapter.title}') has no usable story cards. "
            f"Either assign some, or release the ones it references."
        )

    briefs = "\n\n".join(_card_brief(archive, c) for c in cards)
    quotes = _quotes_for(archive, cards)
    quote_block = (
        "\n".join(f"- [{q.id}] “{q.text}”" for q in quotes)
        if quotes
        else "(no verbatim quotes are available for this chapter)"
    )

    owner = archive.meta.owner_preferred_name or archive.meta.owner_name

    prompt = (
        f"Book: the life of {owner}.\n"
        f"Life theme: {direction.life_theme}\n"
        f"Proposition: {direction.proposition}\n"
        f"Voice: {direction.point_of_view.replace('_', ' ')}. Tone: {direction.tone}. "
        f"Reading level: {direction.reading_level}.\n"
        f"Sensitive material: {direction.sensitive_handling}\n\n"
        f"You are writing chapter {chapter.number}, “{chapter.title}”.\n"
        f"What it covers: {chapter.covers}\n"
        + (f"It should open on: {chapter.opens_with}\n" if chapter.opens_with else "")
        + f"\nTarget length: roughly {target} {lang.unit_label(language)} -- "
        f"shorter if the material is thin. Never pad.\n\n"
        f"STORY CARDS -- the only facts you may use:\n\n---\n{briefs}\n---\n\n"
        f"VERBATIM QUOTES -- reproduce exactly if you use them:\n\n{quote_block}\n\n"
        f"{writing_instruction(language, prose=True)}\n\n"
        f"Write the chapter."
    )

    drafted = llm.complete_json(
        system=DRAFT_SYSTEM,
        prompt=prompt,
        output_model=DraftedChapter,
        max_tokens=llm.DRAFTING_TOKENS,
        # Prose wants a little more room than extraction, but not so much that
        # it starts inventing texture no validator can catch.
        temperature=0.4,
    )
    # Trust the archive over the model for provenance.
    drafted.number = chapter.number
    drafted.title = chapter.title
    drafted.provenance = [c.id for c in cards]
    return drafted
