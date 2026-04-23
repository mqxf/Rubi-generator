# Rubi lang format

Rubi reads `§^word(reading)` annotations directly out of resource-pack `lang/*.json` files
(or any `Component` text the game renders). The annotations are inert text — strip
the mod and the strings keep working as plain Minecraft formatting.

## Syntax

```
§^<word>(<reading>)
```

* `§^` — the rubi sentinel (`§` is U+00A7, the Minecraft formatting prefix).
* `<word>` — the base text the user reads in-line. Usually one or more kanji,
  optionally with okurigana (e.g. `漢字`, `独り言`, `お母さん`, `食べ`). See
  the "Conjugating words" rules below for how verb/adjective stems are chosen.
* `<reading>` — the furigana hint. Hiragana or katakana matching `<word>`
  exactly — nothing more, nothing less.
* Whitespace around `<word>` and `<reading>` is trimmed.

The pattern is `§\^\s*(.+?)\s*\(\s*(.+?)\s*\)` (non-greedy). The first matching
parenthesis closes the reading; if your reading itself contains `(` or `)`, escape
the source text with extra backslashes or rewrite it without parens.

## One word, one annotation

The known/unknown lookup matches **the exact `<word>` + `<reading>` pair**, so each
lexical unit should be its own `§^…(…)` group — even when several kanji words appear
back to back.

Good (each word is its own annotation):

```json
{
    "menu.singleplayer": "§^一人(ひとり)で§^遊(あそ)ぶ"
}
```

Bad (one giant annotation; reader can never mark just `一人` as known):

```json
{
    "menu.singleplayer": "§^一人で遊(ひとりであそ)ぶ"
}
```

### Non-conjugating words (nouns, adverbs, names, compounds)

Wrap the full lexical unit, including any okurigana that's part of the word's
dictionary form:

| Source         | Annotation                           |
|----------------|--------------------------------------|
| `漢字`         | `§^漢字(かんじ)`                       |
| `独り言`       | `§^独り言(ひとりごと)`                   |
| `お母さん`     | `§^お母さん(おかあさん)`                  |
| `日本語`       | `§^日本語(にほんご)`                     |
| `日本語の本`   | `§^日本語(にほんご)の§^本(ほん)`          |
| `静か` (na-adj) | `§^静か(しずか)`                      |

### Conjugating words (verbs, i-adjectives)

Annotate only the **stem** — the invariant part that doesn't change across
conjugated forms. Leave the conjugating suffix *outside* the annotation. This
way, one stem+reading pair in your known list covers every conjugation of that
word, and the mod never has to guess which reading applies to which form —
every occurrence already carries the correct annotation.

**Godan verbs** — stem is the kanji; the trailing kana character is the conjugation:

| Form         | Annotation         |
|--------------|--------------------|
| `食う`       | `§^食(く)う`         |
| `食った`     | `§^食(く)った`       |
| `食わない`   | `§^食(く)わない`      |
| `食って`     | `§^食(く)って`       |
| `書く`       | `§^書(か)く`         |
| `書いた`     | `§^書(か)いた`       |
| `読む`       | `§^読(よ)む`         |
| `遊ぶ`       | `§^遊(あそ)ぶ`       |

**Ichidan verbs** — stem is the kanji plus any okurigana *before* the final `る`;
only the final `る` (and what replaces it in conjugation) stays outside:

| Form           | Annotation           |
|----------------|----------------------|
| `食べる`       | `§^食べ(たべ)る`       |
| `食べた`       | `§^食べ(たべ)た`       |
| `食べて`       | `§^食べ(たべ)て`       |
| `食べられる`   | `§^食べ(たべ)られる`    |
| `食べさせる`   | `§^食べ(たべ)させる`    |
| `見る`         | `§^見(み)る`           |
| `見た`         | `§^見(み)た`           |
| `起きる`       | `§^起き(おき)る`       |
| `起きた`       | `§^起き(おき)た`       |

**I-adjectives** — stem is the kanji; `い` / `くない` / `かった` / `くて` /
`ければ` / … are all conjugation:

| Form         | Annotation          |
|--------------|---------------------|
| `暗い`       | `§^暗(くら)い`        |
| `暗くない`   | `§^暗(くら)くない`    |
| `暗かった`   | `§^暗(くら)かった`    |
| `暗くて`     | `§^暗(くら)くて`      |
| `暗ければ`   | `§^暗(くら)ければ`    |

**Irregulars (`来る`, the kanji form `為る` of する, etc.)** — the same kanji can
read differently across conjugations. Annotate each occurrence with its form's
reading, and the reader will accumulate the full set of readings under one
kanji in their known list:

| Form       | Annotation       |
|------------|------------------|
| `来る`     | `§^来(く)る`       |
| `来た`     | `§^来(き)た`       |
| `来ない`   | `§^来(こ)ない`     |
| `来て`     | `§^来(き)て`       |

Once the reader has learned `来` with all three readings (`く`, `き`, `こ`),
every conjugation of the verb is covered. These verbs are learnt very early,
and there are no other irregulars so they will be covered by the user very quickly.

**Suru-verbs** — treat `勉強する`, `愛する`, etc. as `<kanji-noun>` + `する`.
Only the kanji gets annotated; `する` / `した` / `して` stay outside:

| Form         | Annotation         |
|--------------|--------------------|
| `勉強する`   | `§^勉強(べんきょう)する` |
| `勉強した`   | `§^勉強(べんきょう)した` |
| `愛する`     | `§^愛(あい)する`    |
| `愛した`     | `§^愛(あい)した`    |

### Not annotated

Particles (`は`, `を`, `に`, `で`, `から`, …), pure-kana words (`これ`,
`おはよう`, `する` on its own), numbers written in ASCII digits, and
punctuation are left untouched.

## How "known" works in-game

Use `/rubi known add <word> <reading>` to mark a reading as learned (use the
exact same text that appears between the `§^` and `(`/`)` in the lang file).

* If a single ruby word in a line is **known**, its furigana is suppressed and the
  word renders at full size in line — no scale-down, no overlap, no extra spacing.
* If **every** ruby word in a line is known (or the line has no rubi at all), the
  whole line renders identically to vanilla — no Y-shift, no transforms.
* Mixed lines — some known, some unknown — keep the line-level Y shift so the
  unknown words have headroom for their furigana, and known words simply render
  as plain kanji within that shifted line.

## Commands

```
/rubi known add <word> <reading>          add a (word, reading) pair
/rubi known remove <word>                  remove all readings for a word
/rubi known remove <word> <reading>        remove a single (word, reading) pair
/rubi known list                           list everything you know
/rubi known list <word>                    list known readings for one word
/rubi known reload                         reload from disk after manual edits
```

`<word>` and `<reading>` are non-whitespace tokens (kanji and kana need no quotes).
Wrap with `"…"` if you ever need to include a literal space.

Storage lives at `<gameDir>/config/rubi/known_readings.json`:

```json
{
    "漢字": ["かんじ"],
    "私": ["わたし", "わたくし"],
    "独り言": ["ひとりごと"],
    "食": ["く"],
    "食べ": ["たべ"],
    "暗": ["くら"],
    "来": ["く", "き", "こ"]
}
```

The stems from conjugating words (`食`, `食べ`, `暗`, `来`) sit alongside whole
noun entries (`漢字`, `独り言`). A single kanji can appear in multiple entries
with different readings — `食` as godan stem (`く`) is a different entry from
`食べ` as ichidan stem (`たべ`). That's intentional: each is a distinct
learning unit.

The same word can have multiple readings (one per array entry). The file is
re-saved after every command; you can also hand-edit it and run
`/rubi known reload`.

## Authoring lang strings with an LLM

The most reliable prompt I've found for converting plain Japanese into rubi-annotated
lang strings, designed for Claude / GPT-class models:

> You are converting Japanese sentences into Minecraft lang-file strings annotated
> with rubi furigana. Output **only** the converted string — no explanations, no code
> fences, no extra whitespace.
>
> Rules:
> 1. Wrap every kanji-bearing lexical unit as `§^<word>(<reading>)`. `<word>` is
>    whatever kanji (plus any okurigana included per the rules below) appears in
>    the source; `<reading>` is the hiragana/katakana reading for exactly that
>    stretch of characters.
> 2. **Non-conjugating words** (nouns, adverbs, names, na-adjective stems,
>    compounds) — annotate the whole word including its okurigana:
>    - `漢字` → `§^漢字(かんじ)`
>    - `独り言` → `§^独り言(ひとりごと)`
>    - `お母さん` → `§^お母さん(おかあさん)`
>    - `静か` → `§^静か(しずか)` (na-adjective; `な`/`に`/`だ` stay outside)
> 3. **Conjugating words** (godan verbs, ichidan verbs, i-adjectives,
>    sa-irregular, kuru) — annotate **only the stem**, and leave the
>    conjugating suffix OUTSIDE the annotation:
>    - *Godan verbs*: stem = kanji only; the single trailing kana is
>      conjugation. `食う` → `§^食(く)う`, `食った` → `§^食(く)った`,
>      `食わない` → `§^食(く)わない`, `書いた` → `§^書(か)いた`,
>      `遊ぶ` → `§^遊(あそ)ぶ`, `読む` → `§^読(よ)む`.
>    - *Ichidan verbs*: stem = kanji + any okurigana *before* the final `る`;
>      only the `る` (and what replaces it in conjugation) stays outside.
>      `食べる` → `§^食べ(たべ)る`, `食べた` → `§^食べ(たべ)た`,
>      `食べられる` → `§^食べ(たべ)られる`, `見る` → `§^見(み)る`,
>      `起きる` → `§^起き(おき)る`, `起きた` → `§^起き(おき)た`.
>    - *I-adjectives*: stem = kanji only; `い` / `くない` / `かった` / `くて`
>      / `ければ` etc. are all conjugation and stay outside.
>      `暗い` → `§^暗(くら)い`, `暗くない` → `§^暗(くら)くない`,
>      `暗かった` → `§^暗(くら)かった`, `暗ければ` → `§^暗(くら)ければ`.
>    - *Suru-verbs*: `<kanji-noun>` + `する`. `する`/`した`/`して` stay
>      outside. `勉強する` → `§^勉強(べんきょう)する`,
>      `勉強した` → `§^勉強(べんきょう)した`, `愛する` → `§^愛(あい)する`.
>    - *Irregular `来る`*: the kanji has three readings (`く`, `き`, `こ`)
>      depending on form — annotate each occurrence with the reading that
>      matches this form: `来る` → `§^来(く)る`, `来た` → `§^来(き)た`,
>      `来ない` → `§^来(こ)ない`, `来て` → `§^来(き)て`.
> 4. Multiple adjacent kanji words are **separate annotations**.
>    `日本語の本` → `§^日本語(にほんご)の§^本(ほん)`, never
>    `§^日本語の本(にほんごのほん)`.
> 5. Do **not** annotate words with no kanji (pure hiragana/katakana,
>    particles like `は`/`を`/`に`/`で`/`から`, punctuation, ASCII numbers).
> 6. The `<reading>` must match only the characters inside `<word>`, not any
>    okurigana/conjugation left outside. For `§^食べ(たべ)る`, the reading is
>    `たべ` (not `たべる`). For `§^暗(くら)かった`, the reading is `くら` (not
>    `くらかった`).
> 7. Preserve all surrounding whitespace, punctuation, and non-Japanese text
>    exactly.
> 8. Use hiragana for native Japanese readings, katakana only for genuine
>    katakana words (loanwords, onomatopoeia, etc.).
>
> Convert the following text:
>
> ```
> {paste your Japanese here}
> ```

If you produce lang files in bulk, the same prompt works as a system instruction —
feed it once, then send each plain sentence as the user message.

## Format invariants the parser relies on

* `§^` must be literal — `§` is the section sign (U+00A7), not `&` or `§`.
* The `(` and `)` are ASCII parentheses (U+0028 / U+0029), not full-width `（`/`）`.
* The match is non-greedy and bounded by the first `)`, so you cannot nest
  annotations.
* Anything outside `§^…(…)` is rendered as ordinary text. Rubi changes nothing
  about colour codes, bold, italic, etc., so existing `§a`/`§l` formatting still
  works alongside rubi annotations.
