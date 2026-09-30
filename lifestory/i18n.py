"""Everything the *family* reads, in their language.

Operator-facing output -- the CLI, the provenance sheet, the printer spec --
stays in English. Anything that reaches the story owner or their family
follows the case language: the confirmation sheet, the proposed shape of the
book, and the book's own front and back matter.

The Chinese is written for its readers rather than translated line by line:
the confirmation sheet has to sound like a considerate person asking, not a
form. Traditional Chinese is derived from the Simplified text, so there is one
version to keep right.
"""

from __future__ import annotations

from datetime import date
from typing import Callable

from . import language as lang


def _en_confirm_intro(n: int, minutes: int) -> str:
    return (
        f"There {'is' if n == 1 else 'are'} {n} of these and they should take about "
        f"{minutes} {'minute' if minutes == 1 else 'minutes'}. These are the details "
        f"that are awkward to get wrong in a printed book -- spellings, dates, who is who."
    )


def _en_about_body(owner: str, sessions: int) -> str:
    recorded = f", recorded across {sessions} sessions" if sessions > 1 else ""
    return (
        f"This book was made from conversations with {owner}{recorded}.\n\n"
        f"Every account in it comes from those conversations, from family "
        f"recollections, or from documents and photographs the family provided. "
        f"Where memories differed, we have said so rather than choosing between them."
    )


def _zh_count(n: int) -> str:
    """A count as Chinese prose writes it: 两次, not 2次 or 二次."""
    if n == 2:
        return "两"
    return lang.chinese_numeral(n) if 0 < n < 100 else str(n)


def _zh_about_body(owner: str, sessions: int) -> str:
    recorded = f"，前后共录音{_zh_count(sessions)}次" if sessions > 1 else ""
    return (
        f"本书根据与{owner}的谈话整理而成{recorded}。\n\n"
        f"书中每一段叙述，都来自这些谈话、家人的回忆，或家人提供的文件与照片。"
        f"记忆有出入的地方，我们如实写明，没有替谁做出取舍。"
    )


_STRINGS: dict[str, dict[str, str | Callable[..., str]]] = {
    "en": {
        # -- the family confirmation sheet (§7.4) --------------------------
        "confirm.title": "A few things to check about {name}",
        "confirm.intro": _en_confirm_intro,
        "confirm.not_the_book": "You do not need to read or approve the book itself. We handle that.",
        "confirm.heading.name": "Names and spellings",
        "confirm.heading.date": "Dates",
        "confirm.heading.relationship": "Who was who",
        "confirm.heading.place": "Places",
        "confirm.heading.photo_subject": "People in photographs",
        "confirm.from_recording": "From the recording: “{text}”",
        "confirm.also_told": "We have also been told:",
        "confirm.correct_prompt": "Correct? If not, what should it say? ____________________",
        "confirm.deferred": "({n} further uncertainties were left for the editor rather than added to this list.)",
        # -- the proposed shape of the book (§9 Stage 2) ---------------------
        "direction.title": "Proposed shape for {name}'s book",
        "direction.intro": (
            "Before we write anything, we would like you to tell us whether this is "
            "the right book. Nothing here is final."
        ),
        "direction.thread": "The thread running through it",
        "direction.how_told": "How it would be told",
        "direction.voice": "Voice",
        "direction.tone": "Tone",
        "direction.reading_level": "Reading level",
        "direction.structure": "Structure",
        "direction.chapters": "Chapters",
        "direction.chapter_item": "**{number}. {title}**",
        "direction.opens_on": "*Opens on: {text}*",
        "direction.opening_lines": "Possible opening lines",
        "direction.sensitive": "Sensitive material",
        "direction.still_ask": "What we would still like to ask about",
        "direction.closing": (
            "*If this is the wrong shape, tell us plainly. It is much cheaper to "
            "change now than after the chapters are written.*"
        ),
        "pov.first_person": "first person",
        "pov.third_person": "third person",
        "pov.oral_history": "oral history, in their own words",
        "structure.chronological": "chronological",
        "structure.thematic": "by theme",
        "structure.mixed": "mostly chronological, grouped by theme where it helps",
        # -- the book itself (§9 Stage 5) --------------------------------------
        "book.subtitle": "The life of {owner}",
        "book.contents": "Contents",
        "book.chapter_label": "{number}",
        "book.contents_item": "{number}.  {title}",
        "book.notice": (
            "© {year} {owner} and family. Private family edition.\n\n"
            "This book was made from recorded conversations with {owner}, together "
            "with photographs and recollections contributed by the family. It was "
            "prepared with {owner}'s consent.\n\n"
            "Accounts here are as they were told. Where family members remembered "
            "events differently, both accounts have been kept.\n\n"
            "Prepared {date}. Archive revision {revision}."
        ),
        "book.about_heading": "About this book",
        "book.about_body": _en_about_body,
        "book.thanks": "With thanks to {names}.",
        "book.name_separator": ", ",
        "book.prepared": "Prepared {date} · archive revision {revision}",
    },
    "zh": {
        "confirm.title": "关于{name}，有几处想请您核对",
        "confirm.intro": lambda n, minutes: (
            f"一共{n}处，大约需要{minutes}分钟。这些都是印成书以后，一旦出错会很尴尬的"
            f"细节——名字怎么写、日期、谁是谁。"
        ),
        "confirm.not_the_book": "您不需要通读或审定整本书，那部分由我们负责。",
        "confirm.heading.name": "姓名与写法",
        "confirm.heading.date": "日期",
        "confirm.heading.relationship": "人物关系",
        "confirm.heading.place": "地名",
        "confirm.heading.photo_subject": "照片里的人",
        "confirm.from_recording": "录音原话：“{text}”",
        "confirm.also_told": "我们也听到过另一种说法：",
        "confirm.correct_prompt": "是否正确？如不正确，应当是：____________________",
        "confirm.deferred": "（另有{n}处不确定的细节由编辑处理，没有列入此表。）",
        "direction.title": "{name}的书：初步构想",
        "direction.intro": "在动笔之前，想请您先告诉我们：这是不是您心目中的那本书。这里的一切都还可以改。",
        "direction.thread": "贯穿全书的主线",
        "direction.how_told": "讲述方式",
        "direction.voice": "视角",
        "direction.tone": "语气",
        "direction.reading_level": "阅读难度",
        "direction.structure": "结构",
        "direction.chapters": "章节",
        "direction.chapter_item": "**第{numeral}章　{title}**",
        "direction.opens_on": "*开篇：{text}*",
        "direction.opening_lines": "可能的开头",
        "direction.sensitive": "敏感内容的处理",
        "direction.still_ask": "我们还想再问问的",
        "direction.closing": "*如果方向不对，请直接告诉我们。现在调整，比写完再改容易得多。*",
        "pov.first_person": "第一人称",
        "pov.third_person": "第三人称",
        "pov.oral_history": "口述实录，用讲述者自己的话",
        "structure.chronological": "按时间顺序",
        "structure.thematic": "按主题",
        "structure.mixed": "大体按时间顺序，必要时按主题归并",
        "book.subtitle": "{owner}的一生",
        "book.contents": "目　录",
        "book.chapter_label": "第{numeral}章",
        "book.contents_item": "第{numeral}章　{title}",
        "book.notice": (
            "© {year} {owner}及家人。家庭私藏版。\n\n"
            "本书根据与{owner}的录音谈话整理而成，并收录了家人提供的照片与回忆。"
            "本书的编写已征得{owner}本人同意。\n\n"
            "书中所记均为当事人所述。家人记忆有出入之处，两种说法都予以保留。\n\n"
            "整理于{date}。档案版本{revision}。"
        ),
        "book.about_heading": "关于本书",
        "book.about_body": _zh_about_body,
        "book.thanks": "谨此感谢{names}。",
        "book.name_separator": "、",
        "book.prepared": "整理于{date} · 档案版本{revision}",
    },
}


def t(key: str, language: str | None, **values) -> str:
    """The family-facing text for `key`, in the case's language.

    Languages without their own table fall back to English, which is honest:
    it is better to hand a family English than a machine's guess at theirs.
    """
    code = lang.normalize(language)
    table = _STRINGS["zh"] if lang.is_chinese(code) else _STRINGS["en"]
    entry = table.get(key, _STRINGS["en"][key])

    if "numeral" not in values and "number" in values and isinstance(values["number"], int):
        values["numeral"] = lang.chinese_numeral(values["number"])

    text = entry(**values) if callable(entry) else entry.format(**values)
    return lang.to_script(text, code) if code == lang.ZH_HANT else text


_MONTHS_EN = ("January February March April May June July August September "
              "October November December").split()


def format_date(day: date, language: str | None) -> str:
    """A date as the family writes it: 2026年9月29日, or 29 September 2026."""
    if lang.is_chinese(language):
        return f"{day.year}年{day.month}月{day.day}日"
    return f"{day.day} {_MONTHS_EN[day.month - 1]} {day.year}"


def join_names(names: list[str], language: str | None) -> str:
    return t("book.name_separator", language).join(names)
