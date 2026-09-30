# Samples: measuring transcription on real Mandarin

English | [简体中文](README.zh-CN.md)

`evaluate_asr.py` transcribes a recording locally and scores it against a known
text. Use it whenever you are deciding whether a Whisper model is good enough,
and whenever you have a real session with a human-corrected transcript to
compare against.

```bash
python samples/evaluate_asr.py <audio> <reference.txt> --language zh --model small \
    --hint "names,you,already,know" --save samples/zh/transcript.txt
```

It reports two numbers:

- **CER**, character error rate, over Chinese characters (word error rate for
  other languages). Alignment is free at both ends, so a spoken preamble the
  reference does not contain is not counted against the model.
- **Name recall**: of the proper nouns in the reference, how many came back
  spelled exactly right. This is the one that matters for a memoir: §18 makes
  names a zero-error category, and a low CER that falls entirely on names is
  worse than a higher one that misses none.

With `--save`, the run is checkpointed and an interrupted one resumes.

---

## The test audio

**《朝花夕拾》 by 鲁迅, read in Mandarin by Jing Li for LibriVox.** A memoir of
childhood — his nanny 阿长, the 百草园 garden, his father's illness and death —
which is exactly the kind of material LifeStory handles.

| | |
|---|---|
| Recording | [archive.org/details/chao_hua_si_she_jl_librivox](https://archive.org/details/chao_hua_si_she_jl_librivox) |
| Licence | Public Domain Mark 1.0 (LibriVox dedicates its recordings to the public domain) |
| Text | Public domain (鲁迅 died in 1936); reference texts from [zh.wikisource.org](https://zh.wikisource.org), normalised to Simplified |
| Used | Chapter 02 阿长与《山海经》 (12:36) and chapter 07 父亲的病 (11:43) |

The audio is not committed. To fetch it:

```bash
curl -L --ssl-no-revoke -o samples/zh/chaohuasishe_02_lu_64kb.mp3 https://archive.org/download/chao_hua_si_she_jl_librivox/chaohuasishe_02_lu_64kb.mp3
```

```bash
curl -L --ssl-no-revoke -o samples/zh/chaohuasishe_07_lu_64kb.mp3 https://archive.org/download/chao_hua_si_she_jl_librivox/chaohuasishe_07_lu_64kb.mp3
```

**What this sample cannot tell you.** It is one practised voice reading a
prepared text — not a spontaneous two-person conversation with an 80-year-old.
Real interviews will score worse: older voices, regional accents, false starts,
two people talking. It also cannot test the speaker labels, since there is only
one speaker. Treat the numbers below as a ceiling, and measure real sessions as
soon as you have them.

**Why not an elderly-speech corpus?** The obvious one, SeniorTalk (202 speakers
aged 75–85, across 16 provinces), prohibits "commercial product development" in
its terms, and LifeStory is a priced product. Its Hugging Face tag says
Apache-2.0; the dataset's own terms govern. Most Chinese speech corpora carry
the same non-commercial restriction.

---

## Results

Measured on this machine (4 CPU cores, shared with other work, so speeds
vary). CER counts every character; "names right" uses the fixed lists in
`zh/names_02.txt` and `zh/names_07.txt`.

| Chapter | Model | Hints | CER | Names right | Speed |
|---|---|---|---|---|---|
| 02 阿长与《山海经》 | `base` | — | 15.0% | — | 0.40× real time |
| 02 | `small` | — | **11.0%** | — | 0.88× |
| 02 | `small` | 鲁迅, 长妈妈, 阿长, 绍兴 | 11.4% | 5 / 13 | 0.63× |
| 07 父亲的病 | `small` | 鲁迅, 绍兴, 衍太太 | 16.2% | 4 / 9 | 1.10× |

**`small` is the default for Chinese.** It makes about a quarter fewer errors
than `base`; `base` rendered the book's own title 朝花夕拾 as 招花西石.

**Almost every error is a homophone.** 憎恶 ("loathe") came back as 赠物
("gift"), 陈莲河 as 陈连河 and 陈联合, 凭票付英洋 as 冯瓢复英扬, 衍太太 as
眼太太. Chinese recognition fails by sound, which is why the checklist groups
names by pinyin.

**The checklist points at real errors.** In chapter 07, 7 of its 11 entries
were genuine mishearings of names, each with the time to jump to: 陈连河 ×3,
陈联合, 宣元齐伯 (轩辕岐伯), 卢根 ×2 (芦根), 冯瓢 (凭票), and 眼太太 grouped
under 衍太太. The rest were correct names worth confirming, and one false
positive. It does not catch errors in ordinary words (赠物 for 憎恶); only
reading along with the audio does.

**Hints help a little, and can mislead.** On an 80-second passage with seven
names the first pass got wrong, priming the correct spellings fixed one more
(1 of 7 right without hints, 2 of 7 with). And a hinted word, 茉莉, was written
in at the start of the passage where it was never said. Primed names are
therefore marked on the checklist, to be confirmed like any other.

**Interrupted runs resume.** Chapter 02 was killed at 3:05 on purpose and
restarted; it resumed from the checkpoint and finished with the same CER as
an uninterrupted run.

### A book from these two chapters

`zh/run_demo.py` takes the two transcripts through every workbench command to
a Chinese DOCX (about 10 minutes, using the API key in `.env`). The book it
produced is in [`zh/demo_book/`](zh/demo_book/), and what a close read of it
found is in [the demo section of the main README](../README.md#the-mandarin-demo-end-to-end). The script creates
`cases/luxun-demo`. Delete that afterwards so it does not count in `portfolio`.
