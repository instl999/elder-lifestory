"""Language handling (spec v2 §7.1: language and dialect are set per case).

Everything in the pipeline that cares about the *shape* of text -- how long it
is, where a sentence ends, what a question looks like, which script Chinese
should be written in, where the names are -- asks this module instead of
assuming English.

The first version assumed English everywhere, and one real Mandarin recording
exposed it all at once: a 12.6-minute chapter came back as a single
transcript line (Chinese has no capital letters, so the sentence-boundary test
always said "keep going"); the collapse guard waved it through (it counted
spaces, and a 3,000-character blob has about a hundred of those); and the name
checklist was empty (it looked for capital letters).
"""

from __future__ import annotations

import re
from functools import lru_cache

EN = "en"
ZH_HANS = "zh-Hans"  # Simplified: mainland China, Singapore
ZH_HANT = "zh-Hant"  # Traditional: Taiwan, Hong Kong, Macau

_ALIASES = {
    "en": EN, "en-us": EN, "en-gb": EN, "english": EN,
    "zh": ZH_HANS, "zh-cn": ZH_HANS, "zh-sg": ZH_HANS, "zh-hans": ZH_HANS,
    "cmn": ZH_HANS, "chinese": ZH_HANS, "mandarin": ZH_HANS,
    "zh-tw": ZH_HANT, "zh-hk": ZH_HANT, "zh-mo": ZH_HANT, "zh-hant": ZH_HANT,
}

CJK_RANGE = "㐀-䶿一-鿿豈-﫿"
_CJK_CHAR = re.compile(f"[{CJK_RANGE}]")
CJK_PATTERN = _CJK_CHAR
_LATIN_WORD = re.compile(r"[A-Za-z0-9]+(?:['’\-][A-Za-z0-9]+)*")

# Sentence ends in both scripts, allowing a closing quote or bracket after.
_CLOSERS = "\"'”’」』）)"
_SENTENCE_END = re.compile(f"[.!?。！？…]+[{_CLOSERS}]*\\s*$")
_SENTENCE_SPLIT = re.compile(f"(?<=[.!?。！？…])[{_CLOSERS}]*\\s*")


def normalize(code: str | None) -> str:
    """Canonical language code. Unknown codes pass through lower-cased."""
    if not code:
        return EN
    key = code.strip().lower().replace("_", "-")
    if key in _ALIASES:
        return _ALIASES[key]
    if key.startswith("zh"):
        return ZH_HANS
    return key


def is_chinese(code: str | None) -> bool:
    return normalize(code).startswith("zh")


def whisper_code(code: str | None) -> str:
    """What Whisper calls this language. Script is handled separately."""
    return "zh" if is_chinese(code) else normalize(code).split("-")[0]


def display_name(code: str | None) -> str:
    """How to name the language to a model, unambiguously."""
    c = normalize(code)
    return {
        EN: "English",
        ZH_HANS: "Simplified Chinese (简体中文)",
        ZH_HANT: "Traditional Chinese (繁體中文)",
    }.get(c, c)


# ---------------------------------------------------------------------------
# Measuring text
# ---------------------------------------------------------------------------


def units(text: str) -> int:
    """Length as a reader would count it: characters for Chinese, words otherwise.

    Mixed text counts both. This replaces `len(text.split())`, which is zero
    information for Chinese: a whole paragraph has no spaces in it.
    """
    cjk = len(_CJK_CHAR.findall(text))
    latin = len(_LATIN_WORD.findall(_CJK_CHAR.sub(" ", text)))
    return cjk + latin


def unit_label(code: str | None) -> str:
    return "characters" if is_chinese(code) else "words"


def page_units(code: str | None) -> int:
    """How much text fills a 6x9 page at 12pt.

    Chinese: ~26 characters a line at 12pt on a 4.35in measure, ~33 lines,
    less paragraph breaks and indents -- about 650 in practice.
    """
    return 650 if is_chinese(code) else 280


def chapter_target(code: str | None) -> int:
    """A comfortable chapter length for a family memoir."""
    return 2200 if is_chinese(code) else 1400


def quote_min_units(code: str | None) -> int:
    """Shortest quoted span worth checking against the transcript.

    Chinese prose puts quotation marks round terms as well as speech (“文革”,
    “上山下乡”), so short spans are skipped to avoid blocking an export over a
    term. Ten characters is sentence-sized.
    """
    return 10 if is_chinese(code) else 5


# ---------------------------------------------------------------------------
# Sentences and questions
# ---------------------------------------------------------------------------


def ends_sentence(text: str) -> bool:
    return bool(_SENTENCE_END.search(text.strip()))


def split_sentences(text: str) -> list[str]:
    return [s for s in (p.strip() for p in _SENTENCE_SPLIT.split(text)) if s]


_EN_QUESTION_OPENERS = (
    "what", "where", "when", "who", "why", "how", "which", "was ", "were ",
    "did ", "do ", "does ", "can ", "could ", "would ", "is ", "are ",
    "tell me", "and then",
)
_ZH_INTERROGATIVES = (
    "什么", "怎么", "为什么", "为啥", "哪", "谁", "几", "多少", "是不是", "有没有",
    "能不能", "会不会", "要不要", "对不对", "好不好", "是否", "怎样", "如何",
    "甚麼", "為什麼", "誰", "幾", "會不會",
)


def looks_like_question(text: str, code: str | None) -> bool:
    stripped = text.strip()
    if stripped.endswith(("?", "？")):
        return True
    if is_chinese(code):
        # Sentence-final particles, or a short turn built round a question word.
        # A long turn that merely contains 为什么 is usually the story owner
        # explaining something, not being asked.
        if re.search("[吗呢嗎][。，。，]?$", stripped):
            return True
        return units(stripped) <= 25 and any(w in stripped for w in _ZH_INTERROGATIVES)
    lowered = stripped.lower()
    return any(lowered.startswith(opener) for opener in _EN_QUESTION_OPENERS)


# ---------------------------------------------------------------------------
# Chinese script and punctuation
# ---------------------------------------------------------------------------


@lru_cache(maxsize=4)
def _opencc(config: str):
    import opencc

    return opencc.OpenCC(config)


def to_script(text: str, code: str | None) -> str:
    """Put Chinese text into the case's script.

    Whisper mixes the two freely: a single Mandarin chapter came back with 75
    Traditional-only characters scattered through otherwise Simplified text.
    A book cannot ship like that.
    """
    c = normalize(code)
    if c == ZH_HANS:
        return _opencc("t2s").convert(text)
    if c == ZH_HANT:
        return _opencc("s2t").convert(text)
    return text


_ASCII_TO_FULL = {",": "，", "?": "？", "!": "！", ":": "：", ";": "；"}

# Unicode "small form" punctuation (U+FE50-FE57). Whisper emits these now and
# then -- a real chapter came back with ﹐ scattered among ordinary ，-- and
# they print visibly smaller in a book.
_SMALL_FORMS = str.maketrans({
    "﹐": "，", "﹑": "、", "﹒": "。", "﹔": "；",
    "﹕": "：", "﹖": "？", "﹗": "！",
})


def normalize_punctuation(text: str, code: str | None) -> str:
    """Full-width punctuation between Chinese characters, and no stray spaces.

    Whisper writes ASCII commas inside Chinese about as often as it writes the
    proper ones. Only punctuation touching a Chinese character is converted, so
    "1.5" and English names embedded in the text are left alone.
    """
    if not is_chinese(code):
        return text
    text = text.translate(_SMALL_FORMS)
    cjk = f"[{CJK_RANGE}]"
    for ascii_mark, full in _ASCII_TO_FULL.items():
        mark = re.escape(ascii_mark)
        text = re.sub(f"(?<={cjk}){mark}\\s*", full, text)
        text = re.sub(f"\\s*{mark}(?={cjk})", full, text)
    text = re.sub(f"(?<={cjk})\\.(?!\\d)", "。", text)
    # Spaces Whisper leaves between characters.
    text = re.sub(f"(?<=[{CJK_RANGE}，。！？、；：])\\s+(?=[{CJK_RANGE}])", "", text)
    text = re.sub(f"(?<=[{CJK_RANGE}])\\s+(?=[，。！？、；：])", "", text)
    return text.strip()


def clean_transcript_text(text: str, code: str | None) -> str:
    return normalize_punctuation(to_script(text.strip(), code), code)


def whisper_hotwords(code: str | None, hints: list[str] | None = None) -> str | None:
    """Prompt text Whisper sees at the start of *every* 30-second window.

    `initial_prompt` only reaches the first window once previous-text
    conditioning is off (and it is off, because it lets one hallucination
    cascade through a whole recording). `hotwords` is re-sent each window, so
    the script, the punctuation style, and the names the operator already
    knows keep steering the whole transcript.
    """
    hints = [h.strip() for h in (hints or []) if h and h.strip()]
    c = normalize(code)
    parts: list[str] = []
    if c == ZH_HANS:
        parts.append("以下是普通话访谈录音，使用简体中文，带标点符号。")
        if hints:
            parts.append("人名地名：" + "、".join(hints) + "。")
    elif c == ZH_HANT:
        parts.append("以下是普通話訪談錄音，使用繁體中文，帶標點符號。")
        if hints:
            parts.append("人名地名：" + "、".join(hints) + "。")
    elif hints:
        parts.append("Names: " + ", ".join(hints) + ".")
    return " ".join(parts) or None


# ---------------------------------------------------------------------------
# Names and dates -- the §18 zero-error categories
# ---------------------------------------------------------------------------

_CAPITALISED = re.compile(r"[A-Z][a-zA-Z'\-]{2,}")
_CONTRACTION = re.compile(r"'(s|d|ll|ve|re|m|t)$", re.IGNORECASE)
_NOT_A_NAME_EN = {
    "The", "And", "But", "That", "This", "These", "Those", "There", "Then",
    "They", "We", "You", "He", "She", "It", "Its", "My", "His", "Her", "Our",
    "Their", "What", "When", "Where", "Who", "Whose", "Why", "How", "Which",
    "Not", "Yes", "Well", "Oh", "Thank", "Thanks", "Always", "Never", "Just",
    "Because", "After", "Before", "Once", "Only", "Some", "All", "Both",
    "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
    "Ten", "First", "Last", "Next", "Monday", "Tuesday", "Wednesday",
    "Thursday", "Friday", "Saturday", "Sunday", "January", "February",
    "March", "April", "June", "July", "August", "September", "October",
    "November", "December",
}

# jieba part-of-speech tags that mark a proper noun, and what we call them.
_ZH_NAME_TAGS = {
    "nr": "person", "nrfg": "person", "ns": "place", "nsf": "place",
    "nt": "organisation", "nz": "name",
}
# Things jieba's dictionary tags as names that are ordinary words here.
_NOT_A_NAME_ZH = {
    "保姆", "先生", "太太", "老爷", "少爷", "姑娘", "师傅", "阿姨", "叔叔", "婶婶",
    "中国", "中医", "西医", "老师", "同志", "大人", "小孩", "孩子", "东西",
    "阿妈", "阿爸", "阿婆", "阿公", "阿哥", "阿姐", "阿弟", "阿妹", "阿嫂", "阿伯",
    "老太太", "老先生", "老妈子", "老头子", "小伙子", "老人家", "祖母", "祖父",
}

# Tagging alone is not enough. Measured on two chapters of real Mandarin,
# jieba's default tags marked 冷冰冰 ("ice-cold"), 明白 ("understand"), 普通
# ("ordinary") and 换衣服 ("change clothes") as names, and missed 衍太太, the
# central figure of the chapter. A checklist like that is ignored by the third
# entry. So a tag is only a candidate; it must also have the *shape* of a name.

# The hundred-odd commonest surnames, which cover most of the population.
_SURNAMES = set(
    "王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘"
    "于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付傅方白邹孟熊秦邱"
    "江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤常温康施文牛樊葛邢安"
    "齐易乔伍庞颜倪庄聂章鲁岳翟殷詹申欧耿关兰焦俞左柳甘祝包宁尚符舒阮柯纪梅童凌"
    "毕单季裴霍涂成苗谷盛曲翁冉骆蓝路游辛靳管柴蒙鲍华喻祁蒲房滕屈饶解牟艾尤阳时"
    "穆农司卓古吉缪简车项连芦麦褚娄窦戚岑景党宫费卜冷晏席卫米柏宗瞿桂全佟应臧闵"
    "苟邬边卞姬师仇栾隋商刁沙荣巫寇桑郎甄丛仲虞敖巩明佘池查麻苑迟邝洪寿"
)
_COMPOUND_SURNAMES = {
    "欧阳", "司马", "诸葛", "上官", "司徒", "东方", "夏侯", "皇甫", "尉迟",
    "公孙", "慕容", "长孙", "宇文", "令狐", "端木", "独孤", "南宫", "西门",
}
# How elders actually refer to people: 长妈妈, 衍太太, 寿先生, 王老师.
_KIN_SUFFIXES = (
    "妈妈", "太太", "奶奶", "爷爷", "婆婆", "公公", "伯伯", "叔叔", "婶婶", "阿姨",
    "姑姑", "舅舅", "先生", "师傅", "老师", "大爷", "大娘", "大妈", "大姐", "大哥",
    "嫂子", "妈", "伯", "叔", "婶", "姑", "舅", "姨", "嫂", "公", "婆",
)
# Heads that make "X妈妈" a pronoun phrase ("我妈妈", "他太太"), not a name.
_KIN_HEAD_STOP = set("我你他她它的了这那其两几各每某位个姓叫是和跟与给对向把被让老小大本该此")
_PLACE_SUFFIXES = (
    "省", "市", "县", "区", "镇", "乡", "村", "街", "路", "巷", "胡同", "桥", "山",
    "河", "湖", "江", "岛", "城", "庄", "屯", "寨", "坊", "弄", "港", "湾", "州",
    "府", "口", "门", "堡", "营", "沟", "坡", "岭", "塘", "埠", "镇", "里",
)
# Words that end in a place suffix without being places.
_NOT_A_PLACE_ZH = {"大街", "城里", "乡下", "村里", "门口", "路口", "河边", "山上", "街上"}

_BOOK_TITLE = re.compile("《([^》]{1,30})》")
_ALL_CJK = re.compile(f"^[{CJK_RANGE}]+$")


# Function words that jieba sometimes glues onto the front of a name:
# 连阿长 is "even Ah Chang", not a person called 连阿长.
_LEADING_PARTICLES = _KIN_HEAD_STOP | set("连就也都还又才只倒并")


def _strip_particle(word: str) -> str:
    if len(word) >= 3 and word[0] in _LEADING_PARTICLES and word[0] not in _SURNAMES:
        return word[1:]
    if len(word) >= 3 and word[1] == "阿":
        return word[1:]
    return word


def _looks_like_person(word: str, taught: set[str]) -> bool:
    """Whether a word tagged as a person has the shape of a real name.

    Tuned on two chapters of real Mandarin (see samples/). Recall on the kinds
    of names a family memoir turns on -- nicknames, kinship names, rare given
    names -- matters more than precision: an extra checklist line costs the
    operator seconds, and a missed one reaches print.
    """
    if word in taught:
        return True
    if word in _NOT_A_NAME_ZH or not 2 <= len(word) <= 4:
        return False
    if word[:2] in _COMPOUND_SURNAMES and len(word) in (3, 4):
        return True
    if word[0] == "阿" and len(word) in (2, 3):
        return True
    for suffix in _KIN_SUFFIXES:
        if word.endswith(suffix) and len(word) > len(suffix):
            return word[0] not in _KIN_HEAD_STOP
    if word[0] in _SURNAMES and len(word) in (2, 3):
        # Common vocabulary that happens to start with a surname character --
        # 张罗 "arrange", 周旋 "deal with", 明白 "understand" -- is in the
        # dictionary at high frequency. The names a family memoir turns on
        # mostly are not: 陈莲河, 衍太太 and 长妈妈 all score zero.
        if _dictionary_frequency(word) >= 20:
            return False
        # 费功夫 is 费 + 功夫 ("effort"); 陈莲河 is 陈 + 莲河, which is not a word.
        if len(word) == 3 and _dictionary_frequency(word[1:]) >= 100:
            return False
        return True
    return False


def _looks_like_place(word: str, taught: set[str]) -> bool:
    if word in taught:
        return True
    if word in _NOT_A_PLACE_ZH or len(word) < 3:
        return False
    return word.endswith(_PLACE_SUFFIXES)


_TAUGHT: set[str] = set()


@lru_cache(maxsize=1)
def _jieba():
    import jieba
    import jieba.posseg

    jieba.setLogLevel(60)  # it announces its dictionary load on stderr otherwise
    return jieba, jieba.posseg


def teach_names(names: list[str]) -> None:
    """Tell the Chinese segmenter about names the operator already knows.

    Process-wide by design: each CLI command is its own process and works on
    one case, so a case's hints cannot leak into another's.
    """
    jieba, _ = _jieba()
    for name in names:
        name = to_script(name.strip(), ZH_HANS)
        if name and _CJK_CHAR.search(name):
            jieba.add_word(name, freq=2000, tag="nr")
            _TAUGHT.add(name)


def _dictionary_frequency(word: str) -> int:
    jieba, _ = _jieba()
    jieba.initialize()
    return jieba.dt.FREQ.get(word, 0)


def _chinese_proper_nouns(text: str) -> dict[str, str]:
    _, posseg = _jieba()
    simplified = to_script(text, ZH_HANS)

    # Tokens with the matching slice of the *original* text, so a Traditional
    # transcript reports Traditional names.
    pieces: list[tuple[str, str, str]] = []
    offset = 0
    for word, flag in posseg.cut(simplified):
        pieces.append((word, flag, text[offset : offset + len(word)]))
        offset += len(word)

    found: dict[str, str] = {}
    for index, (word, flag, original) in enumerate(pieces):
        kind = _ZH_NAME_TAGS.get(flag)

        if kind == "person":
            stripped = _strip_particle(word)
            if stripped != word:
                original = original[len(word) - len(stripped) :]
                word = stripped
        if word in _TAUGHT or (kind == "person" and _looks_like_person(word, _TAUGHT)):
            found.setdefault(original, "person")
        elif kind == "place" and _looks_like_place(word, _TAUGHT):
            found.setdefault(original, "place")
        elif kind == "organisation" and len(word) >= 3 and _dictionary_frequency(word) < 200:
            found.setdefault(original, "organisation")

        # 长 + 妈妈 -> 长妈妈; 衍 + 太太 -> 衍太太. The segmenter often splits a
        # nickname from its kinship term, and that pairing is how elders name
        # the people who mattered most to them.
        if word in _KIN_SUFFIXES and index > 0:
            prev_word, _, prev_original = pieces[index - 1]
            if (
                1 <= len(prev_word) <= 3
                and _ALL_CJK.match(prev_word)
                and prev_word[0] not in _KIN_HEAD_STOP
                and prev_original not in found
                # A single character is always frequent; only test real words,
                # so 教书先生 ("the schoolteacher") is not taken for a name.
                and (len(prev_word) == 1 or _dictionary_frequency(prev_word) < 50)
            ):
                found.setdefault(prev_original + original, "person")

    for title in _BOOK_TITLE.findall(text):
        found.setdefault(title, "name")
    return found


def proper_nouns(text: str, code: str | None) -> list[tuple[str, str]]:
    """(term, kind) for every proper noun the text seems to contain.

    English uses capitalisation, skipping ordinary capitalised words. Chinese
    has no capitals, so jieba's part-of-speech tagging does the same job.
    Both over-report rather than under-report: an extra line on a checklist
    costs the operator seconds, a missed name reaches print.
    """
    if is_chinese(code):
        return list(_chinese_proper_nouns(text).items())

    found: dict[str, str] = {}
    for sentence in split_sentences(text):
        run: list[str] = []
        for raw in sentence.split():
            match = _CAPITALISED.fullmatch(raw.strip(".,;:!?\"'()"))
            token = match.group(0) if match else None
            stem = _CONTRACTION.sub("", token) if token else None
            if token and stem not in _NOT_A_NAME_EN:
                run.append(token)
                continue
            if run:
                found.setdefault(" ".join(run), "name")
                run = []
        if run:
            found.setdefault(" ".join(run), "name")
    return list(found.items())


# ---------------------------------------------------------------------------
# Referring to the story owner
# ---------------------------------------------------------------------------

# What a model calls the story owner when it forgets their name. On a Mandarin
# run the family's sheet read "长妈妈是一向带领着故事主人的女工": the
# workbench's own jargon, translated, in a sentence meant for the family.
# Only terms that can mean nobody else, and nouns, so a name dropped in their
# place never disturbs the grammar. ("The speaker" and "the narrator" can be
# someone at a wedding or on the radio; those are flagged, not replaced.)
_OWNER_TERMS_EN = re.compile(r"\bthe (?:story[ -]?owner|interviewee)\b", re.IGNORECASE)
_OWNER_TERMS_ZH = ("故事的主人", "故事主人", "被采访者", "受访者", "被访者",
                   "讲述人", "讲述者", "叙述者", "口述者")
# “我” in quotation marks is how Chinese writes "the narrator": 阿长是“我”的保姆.
_SCARE_QUOTED_I = re.compile("[“「『\"]我[”」』\"]")


@lru_cache(maxsize=1)
def _owner_terms_zh() -> tuple[str, ...]:
    terms = set(_OWNER_TERMS_ZH) | {to_script(t, ZH_HANT) for t in _OWNER_TERMS_ZH}
    return tuple(sorted(terms, key=len, reverse=True))


def name_the_owner(text: str, name: str, code: str | None) -> str:
    """Replace unmistakable stand-ins for the story owner with their name."""
    if not text or not name:
        return text
    if is_chinese(code) or _CJK_CHAR.search(text):
        text = _SCARE_QUOTED_I.sub(name, text)
        for term in _owner_terms_zh():
            text = text.replace(term, name)
    return _OWNER_TERMS_EN.sub(name, text)


# Real quotations, not a single scare-quoted word: “我” is still the narrator.
_QUOTATION = re.compile("“[^”]{2,}”|「[^」]{2,}」|『[^』]{2,}』|\"[^\"]{2,}\"")
# Case-sensitive on purpose: "US" and "ME" are places, "World War I" a war.
_FIRST_PERSON_EN = re.compile(
    r"(?<!War )\bI\b|\b(?:[Mm]e|[Mm]y|[Mm]ine|[Mm]yself|[Ww]e|[Uu]s|[Oo]urs?)\b"
)
_FIRST_PERSON_ZH = re.compile("(?<![自忘])我")
_STAND_IN_EN = re.compile(r"\bthe (?:speaker|narrator|storyteller)\b", re.IGNORECASE)


def wording_problem(text: str, code: str | None) -> str | None:
    """Why a sentence meant for the family fails to name the story owner.

    First person cannot be fixed by swapping words -- "I was" does not become
    "Rose was" by substitution in English -- so it is reported for the
    operator to reword. Quotations are exempt: there "I" is whoever spoke.
    """
    unquoted = _QUOTATION.sub("", text)
    if is_chinese(code) or _CJK_CHAR.search(unquoted):
        if _FIRST_PERSON_ZH.search(unquoted):
            return "says 我 instead of naming them"
        return None
    if _FIRST_PERSON_EN.search(unquoted):
        return 'says "I" instead of naming them'
    match = _STAND_IN_EN.search(unquoted)
    if match:
        return f'says "{match.group(0)}" -- if that means them, name them'
    return None


_ZH_DIGITS = "一二三四五六七八九〇零两"
_DATE_PATTERNS_ZH = [
    re.compile(f"(?:[12]\\d{{3}}|[{_ZH_DIGITS}]{{4}})年"),                     # 1957年 / 一九五七年
    re.compile(f"(?<![{_ZH_DIGITS}十])(?:\\d{{2}}|[{_ZH_DIGITS}]{{2}})年(?:代)?"),  # 五七年 / 五十年代
    re.compile(f"(?:\\d{{1,3}}|[{_ZH_DIGITS}十百]{{1,4}})[岁歲]"),                # 十九岁
    re.compile(f"(?:\\d{{1,2}}|[{_ZH_DIGITS}十]{{1,3}})月(?:\\d{{1,2}}|[{_ZH_DIGITS}十]{{1,3}})[日号號]"),
]
_DATE_PATTERNS_EN = [
    re.compile(r"\b(?:1[89]\d\d|20\d\d)s?\b"),
    re.compile(r"\b\d{1,3} years? old\b", re.IGNORECASE),
]


def dates(text: str, code: str | None) -> list[str]:
    """Years, ages and calendar dates -- the other §18 zero-error category."""
    patterns = _DATE_PATTERNS_ZH if is_chinese(code) else _DATE_PATTERNS_EN
    found: dict[str, None] = {}
    for pattern in patterns:
        for match in pattern.finditer(text):
            found[match.group(0)] = None
    return list(found)


def pronunciation_key(name: str) -> str:
    """How a name *sounds*, for catching homophone transcription errors.

    Chinese speech recognition fails by homophone: 王秀英 heard as 王秀瑛, 章
    as 张. Both spellings read "wang xiu ying". Two records with the same key
    and different characters are almost certainly one person, misheard once.
    """
    if _CJK_CHAR.search(name):
        from pypinyin import lazy_pinyin

        return " ".join(lazy_pinyin(to_script(name, ZH_HANS)))
    return re.sub(r"[\W_]", "", name.lower())


def chinese_numeral(n: int) -> str:
    """1-99 as Chinese numerals, for chapter labels (第十二章)."""
    digits = "零一二三四五六七八九"
    if n < 10:
        return digits[n]
    tens, ones = divmod(n, 10)
    head = "十" if tens == 1 else f"{digits[tens]}十"
    return head + (digits[ones] if ones else "")
