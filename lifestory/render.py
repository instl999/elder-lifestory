"""Manuscript output (spec v2 §9 Stage 5, "Export formats").

Two outputs matter in Phase 0:

* A Markdown manuscript, which needs no dependencies and is what the operator
  actually edits.
* An editable DOCX, which goes to a designer or straight to a print-on-demand
  service. Export it to PDF from Word or WPS with fonts embedded.

"Print-ready" here means technically ready for professional review and
printing -- not accepted by a commercial publisher (§9 "On the word
publication").

The book is in the case language, front matter included; the provenance
sheet and printer spec are operator documents and stay in English. Provenance
is written to its own sheet, never into the reader's book (§6.2).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from . import language as lang
from .compose import DraftedChapter, NarrativeDirection
from .i18n import format_date, join_names, t
from .models import Archive
from .validators import least_supported

# Trim size for a keepsake hardcover, in inches.
PAGE_WIDTH = 6.0
PAGE_HEIGHT = 9.0
MARGIN_OUTER = 0.75
MARGIN_INNER = 0.9      # extra for the gutter

# Below this, no print-on-demand service will case-bind the book. Worth
# catching before the operator sends a spec that comes back rejected.
MIN_HARDCOVER_PAGES = 32

# Fonts per script: (Latin, body CJK, heading CJK). SimSun/SimHei ship with
# Windows and with Office for Mac; PMingLiU/Microsoft JhengHei for Traditional.
_FONTS = {
    lang.EN: ("Georgia", None, None),
    lang.ZH_HANS: ("Times New Roman", "SimSun", "SimHei"),
    lang.ZH_HANT: ("Times New Roman", "PMingLiU", "Microsoft JhengHei"),
}


def _fonts(language: str | None) -> tuple[str, str | None, str | None]:
    code = lang.normalize(language)
    return _FONTS.get(code, _FONTS[lang.ZH_HANS] if lang.is_chinese(code) else _FONTS[lang.EN])


def estimate_pages(chapters: list[DraftedChapter], language: str | None = lang.EN) -> int:
    units = sum(lang.units(c.prose) for c in chapters)
    # Chapter openers and photo pages add roughly 1.5 pages per chapter.
    return round(units / lang.page_units(language) + len(chapters) * 1.5) + 6


# ---------------------------------------------------------------------------
# Markdown manuscript
# ---------------------------------------------------------------------------


def _contributors(archive: Archive) -> list[str]:
    return sorted({a.contributed_by for a in archive.artifacts if a.contributed_by})


def render_markdown(
    archive: Archive,
    direction: NarrativeDirection,
    chapters: list[DraftedChapter],
) -> str:
    language = archive.meta.language
    owner = archive.meta.owner_preferred_name or archive.meta.owner_name
    ordered = sorted(chapters, key=lambda c: c.number)

    lines = [
        f"# {direction.life_theme}",
        "",
        f"*{t('book.subtitle', language, owner=archive.meta.owner_name)}*",
        "",
        "---",
        "",
        f"## {t('book.contents', language)}",
        "",
    ]
    lines.extend(t("book.contents_item", language, number=c.number, title=c.title) for c in ordered)
    lines.extend(["", "---", ""])

    for chapter in ordered:
        label = t("book.chapter_label", language, number=chapter.number)
        lines.extend([f"## {label}　{chapter.title}" if lang.is_chinese(language)
                      else f"## {label}. {chapter.title}", "", chapter.prose.strip(), ""])

    lines.extend(["---", "", f"## {t('book.about_heading', language)}", ""])
    lines.append(t("book.about_body", language, owner=owner, sessions=len(archive.sessions)))
    lines.append("")

    contributors = _contributors(archive)
    if contributors:
        lines.extend([t("book.thanks", language, names=join_names(contributors, language)), ""])

    lines.append(
        f"*{t('book.prepared', language, date=format_date(date.today(), language), revision=archive.revision)}*"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Operator provenance sheet -- never shipped to the family
# ---------------------------------------------------------------------------


def render_provenance(archive: Archive, chapters: list[DraftedChapter]) -> str:
    language = archive.meta.language
    spot_checks = least_supported(archive, chapters)
    lines = [
        f"# Provenance sheet — {archive.meta.id}",
        "",
        "**Operator use only.** This is the audit trail behind the manuscript. "
        "It is not part of the family's book.",
        "",
        f"Archive revision: {archive.revision}  ·  language: {lang.display_name(language)}",
        "",
    ]

    for chapter in sorted(chapters, key=lambda c: c.number):
        lines.extend([f"## {chapter.number}. {chapter.title}", ""])
        lines.extend([f"Length: {lang.units(chapter.prose)} {lang.unit_label(language)}", ""])
        lines.extend(["**Story cards**", ""])
        for cid in chapter.provenance:
            card = archive.story_card(cid)
            if card is None:
                lines.append(f"- `{cid}` — **missing from archive**")
                continue
            sources = ", ".join(s.short() for s in card.sources) or "none"
            lines.append(f"- `{cid}` {card.title} (strength {card.strength}/5) ← {sources}")
        lines.append("")

        if chapter.editor_notes:
            lines.extend(["**Editor notes**", ""])
            lines.extend(f"- {note}" for note in chapter.editor_notes)
            lines.append("")

        weakest = [u for u in spot_checks if u.chapter == chapter.number]
        if weakest:
            lines.extend([
                "**Spot-check first** — least like anything in the recordings "
                "(QA checklist, Fabrication)",
                "",
            ])
            lines.extend(f"- {u.support:.0%} · {u.sentence}" for u in weakest)
            lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------


def _set_fonts(target, latin: str, east_asian: str | None) -> None:
    """Set both the Latin and the East Asian font on a style or a run.

    Word picks a separate font for CJK characters. Setting only `font.name`
    left Chinese text in whatever Word substituted -- sometimes a font the
    printer does not have.
    """
    from docx.oxml.ns import qn

    target.font.name = latin
    if east_asian:
        element = target.element if hasattr(target, "element") else target._element
        rpr = element.get_or_add_rPr()
        rpr.get_or_add_rFonts().set(qn("w:eastAsia"), east_asian)


def render_docx(
    archive: Archive,
    direction: NarrativeDirection,
    chapters: list[DraftedChapter],
    out_path: Path,
) -> Path:
    """Write an editable DOCX laid out for a 6x9 keepsake hardcover."""
    try:
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
        from docx.shared import Inches, Pt
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("DOCX export needs python-docx: pip install python-docx") from exc

    language = archive.meta.language
    chinese = lang.is_chinese(language)
    latin, body_cjk, heading_cjk = _fonts(language)
    owner = archive.meta.owner_name

    document = Document()

    # Word shows these under File > Info. Left alone, a family's book says it
    # was written by "python-docx" (QA checklist: metadata must not leak or
    # jar). The title and the person the book is about, nothing else.
    properties = document.core_properties
    properties.title = direction.life_theme
    properties.author = archive.meta.owner_preferred_name or owner
    properties.comments = ""
    properties.last_modified_by = ""

    section = document.sections[0]
    section.page_width = Inches(PAGE_WIDTH)
    section.page_height = Inches(PAGE_HEIGHT)
    section.top_margin = Inches(MARGIN_OUTER)
    section.bottom_margin = Inches(MARGIN_OUTER)
    section.left_margin = Inches(MARGIN_INNER)
    section.right_margin = Inches(MARGIN_OUTER)

    # 12pt for older readers. Chinese convention indents every paragraph by
    # two characters, the first included; English does not indent the first.
    normal = document.styles["Normal"]
    _set_fonts(normal, latin, body_cjk)
    normal.font.size = Pt(12)
    normal.paragraph_format.line_spacing = 1.5 if chinese else 1.35
    normal.paragraph_format.space_after = Pt(0)
    body_indent = Pt(24) if chinese else Inches(0.25)
    normal.paragraph_format.first_line_indent = body_indent

    def plain(paragraph):
        paragraph.paragraph_format.first_line_indent = Inches(0)
        return paragraph

    def heading_run(paragraph, text, size, bold=True, italic=False):
        run = paragraph.add_run(text)
        run.font.size = Pt(size)
        run.bold = bold
        run.italic = italic and not chinese  # CJK has no true italic
        _set_fonts(run, latin, heading_cjk if bold else body_cjk)
        return run

    def page_break():
        document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    # --- title page ---
    for _ in range(6):
        plain(document.add_paragraph())
    title = plain(document.add_paragraph())
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    heading_run(title, direction.life_theme, 26)

    subtitle = plain(document.add_paragraph())
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    heading_run(subtitle, t("book.subtitle", language, owner=owner), 14, bold=False, italic=True)
    page_break()

    # --- copyright and consent notice (§9 Stage 5) ---
    notice = plain(document.add_paragraph())
    notice_run = notice.add_run(
        t(
            "book.notice",
            language,
            year=date.today().year,
            owner=owner,
            date=format_date(date.today(), language),
            revision=archive.revision,
        )
    )
    notice_run.font.size = Pt(9)
    page_break()

    # --- contents ---
    heading_run(plain(document.add_paragraph()), t("book.contents", language), 18)
    plain(document.add_paragraph())
    ordered = sorted(chapters, key=lambda c: c.number)
    for chapter in ordered:
        plain(document.add_paragraph()).add_run(
            t("book.contents_item", language, number=chapter.number, title=chapter.title)
        )
    page_break()

    # --- chapters ---
    for chapter in ordered:
        opener = plain(document.add_paragraph())
        opener.paragraph_format.space_before = Pt(48)
        if chinese:
            opener.alignment = WD_ALIGN_PARAGRAPH.CENTER
        heading_run(
            opener, t("book.chapter_label", language, number=chapter.number) + "\n",
            11, bold=False, italic=True,
        )
        heading_run(opener, chapter.title, 20)
        plain(document.add_paragraph())

        for index, block in enumerate(p.strip() for p in chapter.prose.split("\n\n") if p.strip()):
            joiner = "" if chinese else " "
            paragraph = document.add_paragraph(joiner.join(block.split("\n")))
            if index == 0 and not chinese:
                plain(paragraph)
        page_break()

    # --- about ---
    heading_run(plain(document.add_paragraph()), t("book.about_heading", language), 16)
    body = t("book.about_body", language, owner=archive.meta.owner_preferred_name or owner,
             sessions=len(archive.sessions))
    for block in body.split("\n\n"):
        document.add_paragraph(block)

    contributors = _contributors(archive)
    if contributors:
        document.add_paragraph(t("book.thanks", language, names=join_names(contributors, language)))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(out_path))
    return out_path


def printer_spec(chapters: list[DraftedChapter], language: str | None = lang.EN) -> str:
    """The specification sheet a print-on-demand service needs (§9 Stage 5)."""
    pages = estimate_pages(chapters, language)
    # Standard 80gsm cream stock: ~0.0043 inches per page, plus cover boards.
    spine = round(pages * 0.0043 + 0.08, 3)
    latin, body_cjk, _ = _fonts(language)
    typeface = f"{body_cjk} (Chinese) with {latin} (Latin)" if body_cjk else latin

    lines = [
        "# Printer specification",
        "",
        f"- Trim size: {PAGE_WIDTH}″ × {PAGE_HEIGHT}″",
        f"- Estimated page count: {pages} (round up to a multiple of 4)",
        f"- Estimated spine width: {spine}″ at 80gsm cream",
        f"- Interior margins: {MARGIN_INNER}″ inner, {MARGIN_OUTER}″ outer/top/bottom",
        "- Bleed: 0.125″ on all outer edges for any full-bleed photograph",
        f"- Body text: 12pt {typeface} (set for older readers)",
        "- Binding: case laminate hardcover",
        "- Colour: interior mono unless photographs are placed in a colour section",
        "- Export the DOCX to PDF from Word or WPS with fonts embedded before sending",
        "",
        "Confirm the spine width with the printer once the final page count is fixed.",
    ]

    if lang.is_chinese(language):
        lines.extend([
            "",
            "If printing in mainland China, check the printer's content-review "
            "requirements before promising a delivery date: some print shops review "
            "privately printed books, and a memoir that touches on sensitive historical "
            "periods can be delayed or refused.",
        ])

    if pages < MIN_HARDCOVER_PAGES:
        lines.extend([
            "",
            "## Too short to case-bind",
            "",
            f"At roughly {pages} pages this is below the ~{MIN_HARDCOVER_PAGES}-page "
            f"minimum most print-on-demand services will hardcover.",
            "",
            "Options, in the order worth trying:",
            "",
            "1. **Go back for more material.** A thin book usually means thin "
            "interviews, not a thin life. The direction's `gaps` list says where.",
            "2. **Add photographs with full captions** — they carry story and pages "
            "at once.",
            "3. **Saddle-stitch or softcover instead**, and say so to the family "
            "before they see it rather than after.",
            "",
            "For an assisted-mode case (§12.3) a short book may be the honest "
            "outcome. Set that expectation with the sponsor early.",
        ])

    return "\n".join(lines)
