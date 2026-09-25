# ASD-STE100 Simplified Technical English — LLM Writing & Commenting Style Guide

> Distilled from ASD-STE100 *Simplified Technical English*, Issue 9 (2025-01-15),
> Part 1 – Writing Rules. Source PDF: `documentation/ASD-STE100_ISSUE9.pdf`.
>
> **Purpose**: A practical rule set an AI assistant can apply to make its prose,
> instructions, and code comments clear, unambiguous, and easy to understand for
> a global audience (including non-native English readers and junior engineers).
> Where a rule was written for aerospace tech-writers, this guide restates it for
> general technical writing and code commenting.

---

## How to use this guide

- Treat the **Core Principles** as always-on defaults for any explanatory prose,
  instructions, docstrings, and inline code comments.
- The numbered rules map 1:1 to STE Part 1 rules so they can be traced back to the
  standard. Each rule gives the STE intent plus an **LLM application** note.
- STE was built for *procedural and descriptive* technical writing. That maps
  cleanly to two things an AI does constantly: **instructions** (do X, then Y) and
  **descriptions** (what a thing is / how it works). Code comments are a hybrid.

---

## Precedence over other style rules

This guide outranks every other style rule in this repository. If another rule
set conflicts with this guide, obey this guide. This guide is NON-NEGOTIABLE.

The caveman compression rules are the known conflict. Those rules appear in
`.github/instructions/caveman.instructions.md` and in `.agents/skills/caveman/`.
They tell the writer to drop the articles, to write fragments, and to swap
synonyms. Rules 4.5, 4.2, and 1.11 of this guide forbid all three. Obey Rules
4.5, 4.2, and 1.11.

The caveman rules keep one permission. They remove filler, pleasantries, and
hedging. That removal does not break a rule in this guide. The caveman `lite`
level is the only level that obeys this guide.

---

## Core Principles (General Introduction)

Simplified Technical English (STE) is a **controlled natural language**: a subset
of standard English with a controlled vocabulary and a set of writing rules. It
exists to make technical text **clear, simple, and unambiguous** so that readers
worldwide — including non-native speakers — understand it the same way.

1. **One word, one meaning.** Use each word with a single approved meaning. Do not
   use the same word with several meanings (e.g., "follow" = *come after*, not
   *obey*).
2. **One meaning, one word.** For a given meaning, pick **one** word and use it
   consistently. Avoid synonym-swapping: choose `start` and never rotate through
   *begin / commence / initiate / originate*.
3. **One part of speech per word.** Use a word only as its approved part of speech
   (do not verb a noun).
4. **Keep sentences short.** Prefer short, direct sentences with one idea each.
5. **Prefer the active voice** and address the reader directly.
6. **Use simple verb tenses** (present, past, future — not perfect/continuous
   forms or complex constructions).
7. **Be consistent** in wording, structure, and terminology throughout a document.
8. **American English spelling** by default (unless a project directive says
   otherwise).

STE deliberately does **not** regulate abbreviations, document formatting, or units
of measurement — those belong to project/style conventions, not this guide.

---

## Section 1 — Words

### Rule 1.1 — Use approved words only
Use words that are (a) approved in the controlled dictionary, (b) technical nouns,
or (c) technical verbs. Everything else is avoided.

**LLM application**: Prefer common, plain words. Reserve specialized terms for
genuine technical nouns/verbs (API names, protocol names, identifiers). If a fancy
word has a plain equivalent, use the plain one.

### Rule 1.2 — Correct part of speech
Use an approved word only as its specified part of speech. Do not turn a noun into
a verb or vice-versa.

- Not: "Test the system for leaks." → "Do a leak test of the system."
- Not: "Dim the lights." → "Set the lights to the dim position."

**LLM application**: Avoid noun-verbing ("architecting", "efforting", "actioning").
Rephrase with a real verb + noun.

### Rule 1.3 — Approved meaning only
Use each word only with its approved meaning.

- "follow" = *come after / go after*: "Do the steps that follow:"
- For instructions, use **"obey"** — not "follow the safety instructions".

**LLM application**: Do not stretch a word to a second meaning in the same text.
Pick the word whose primary meaning fits.

### Rule 1.4 — Approved forms of verbs and adjectives
Use only the approved (simple) verb forms — infinitive/imperative, simple present,
simple past, past participle (as adjective). Use base/comparative/superlative for
adjectives.

**LLM application**: Prefer simple tenses. "Remove the cover", "The system removed
the file", not "The system has been removing the file".

### Rule 1.5 — Technical nouns are allowed (22 categories)
A **technical noun** names a specific concept in a subject field. STE lists 22
categories (parts, vehicles, tools, materials, facilities, systems, math/science,
navigation, units, quoted text, roles/orgs, body parts, personal effects, medical,
documents/standards, environment, colors, damage terms, **computer/ICT**, civil &
military ops, law, life forms).

**LLM application**: Domain identifiers (class names, protocols, device models,
`Content-Type`) are technical nouns — use them precisely and verbatim.

### Rule 1.6 — Unapproved words only as technical nouns
A word that is otherwise not approved may be used **only** when it is a technical
noun or part of one (e.g., "backup" as an ICT technical noun: "Do the backup of the
computer").

### Rule 1.7 — Do not use technical nouns as verbs
- Not: "Oil the surfaces." → "Apply oil to the surfaces."
- Not: "If it will snow…" → "If snow will fall…"

**LLM application**: Do not verbify nouns. "Log the event" is fine (log is an
approved verb); "email the file" → "send the file by email" when in doubt.

### Rule 1.8 — Use the established term
If your company/industry/subject field already has an approved term, use it.

**LLM application**: Match the codebase's existing terminology. If the repo says
"operation", do not switch to "task"/"job"/"action" for the same thing.

### Rule 1.9 — Keep technical nouns short
When you must coin a term, keep it short (≤ 3 words) and easy to understand. Rely on
context/reference numbers instead of piling on adjectives.

- Not: "the four stainless steel pan head machine screws (10)"
- Yes: "the four screws (10)"

### Rule 1.10 — No regional words, slang, or jargon
Avoid words only some readers know.

- Not: "do not **brick** the router" → "do not set the router to OFF".
- Not: "remove your **gear**" → "remove your tools and equipment".

**LLM application**: Avoid slang and insider jargon in comments and docs. A junior
engineer must understand every line without extra context.

### Rule 1.11 — One term per item
Do not use different terms for the same thing. If it's the "actuator", call it the
"actuator" every time — not "servo control unit" then "control unit".

**LLM application**: Pick one name per concept/variable/component and keep it across
the whole file/response. Consistency beats variety.

### Rule 1.12 — Technical verbs are allowed (4 categories)
A **technical verb** names a specific process: (1) manufacturing, (2) computer
processes & applications — `click, enter, press, type, copy, delete, install,
reboot, update, upload, download, boot, debug, format`, (3) subject-field actions,
(4) law/regulations. If a plain approved verb works, use it instead of a technical
verb.

**LLM application**: Software verbs (`install`, `reboot`, `update`, `deploy`,
`commit`) are legitimate technical verbs — use them plainly and consistently.

### Rule 1.13 — Do not use technical verbs as nouns
- Not: "Give the hole a 0.20-inch ream." → "Ream the hole to 0.20 inch."
- The past participle **can** be an adjective: "the reamed hole".

### Rule 1.14 — American English spelling
Use American spelling by default: `color` not `colour`, `fiber` not `fibre`. Keep
**quoted text** (screen labels, log output, identifiers) exactly as-is — never
"correct" a quoted string.

**LLM application**: Default to American spelling in prose; never alter quoted
strings, log lines, file paths, or identifiers.

---

## Section 2 — Multi-word nouns (noun stacks)

### Rule 2.1 — Maximum three words in a multi-word noun
Long strings of stacked nouns/adjectives are ambiguous because the reader must
guess how the modifiers connect to the head noun (usually the last word). Keep
multi-word nouns to **three words maximum**. Break longer ones apart with
prepositions (`of`, `on`, `in`, `for`).

- Not: "Runway light connection resistance calibration" (5 words)
- Yes: "Calibration of the resistance of the runway light connection"
- Not: "Remove the engine transmission housing attachment bolts" (5)
- Yes: "Remove the bolts that attach the transmission housing to the engine"

**LLM application**: Do not stack modifiers. "user authentication token refresh
handler" → "the handler that refreshes the user authentication token". This is a
frequent LLM habit — unstack it.

### Rule 2.2 — Long technical nouns: define once, then shorten
When a real technical noun genuinely exceeds three words, write it **in full the
first time**, then either:
- **Shorter form / approved abbreviation** — introduce it in parentheses and reuse
  it: "the Main Fuel Metering Unit (MFMU) … the MFMU".
- **Hyphens** to bind words that act as one unit (a hyphenated group counts as one
  word): "the cutoff-switch power connection".

Rules for hyphens: only join *related* words; never hyphenate every word to fake a
short noun ("main-gear-door-retraction-winch" is wrong); never remove a hyphen that
belongs to an official term ("inward-outward valve").

**LLM application**: Spell out an acronym on first use, then reuse it. Do not sprinkle
undefined abbreviations. A comment full of unexplained initialisms is not readable.

---

## Section 3 — Verbs

### Rule 3.1 — Use approved verb forms only
Use the verb forms the dictionary allows for each verb (e.g., `remove / removes /
removed / removed`).

### Rule 3.2 — Use only simple verb forms and tenses
Permitted: **infinitive**, **imperative** (command), **simple present**, **simple
past**, **simple future**, and **past participle as an adjective**.

Not permitted: present perfect (*has adjusted*), past perfect (*had adjusted*),
present/past progressive (*is adjusting*), and other complex constructions.

**LLM application**: Write "The function returns X", "Set the flag", "The build
failed" — not "The build has been failing" or "We have been setting the flag".

### Rule 3.3 — Past participle as an adjective (not passive)
Use a past participle before a noun, or after *be / become / stay*, to show a
**condition** — this is not passive voice: "the disassembled unit", "when the unit
is fully disassembled". Approved adjectives like *damaged*, *permitted* are fine.

### Rule 3.4 — No auxiliary verbs for complex constructions
Drop `have`-based perfect tenses and passive auxiliary chains; rewrite in a simple
tense or split the sentence.

- Not: "The operator has adjusted the linkage." → "The operator adjusted the linkage."
- Not: "The volume control can be adjusted." → "You can adjust the volume control."
- Not: "The temperature must be adjusted." → "Adjust the temperature."

### Rule 3.5 — `-ing` form only as a technical noun or modifier
Do not use `-ing` as a progressive verb. It is allowed only as a gerund/technical
noun (`Cleaning`, `Troubleshooting`, `Packaging`) or as a modifier inside a
technical noun (`grinding wheel`, `switching relay`, `air-conditioning system`).

- Not: "When you are doing this procedure, obey all precautions."
- Yes: "When you do this procedure, obey all precautions."

**LLM application**: Prefer "When you run the test" over "When running the test";
"the cache that stores tokens" over "the token-storing cache". Section headings like
"Logging" / "Testing" are fine (technical nouns).

### Rule 3.6 — Use the active voice
Always use the active voice. Passive is allowed **only in descriptive writing when
the agent is unknown** ("During transmission, the data was corrupted"). Test for
passive by asking "by whom/by what?" — if there's a `by <agent>`, make that agent
the subject.

- Not: "The circuits are connected by a switching relay." → "A switching relay
  connects the circuits."
- Not: "These values are used by the computer to calculate…" → "The computer
  calculates … from these values."
- In procedures, use the imperative: "The test can be continued by the operator." →
  "Continue the test."
- When no agent is given, use **you** (the reader) or **we** (your team/org).

**LLM application**: Say "The parser reads the file", not "The file is read by the
parser". In instructions, command directly: "Run the migration", not "The migration
should be run".

### Rule 3.7 — Describe an action with a verb, not a noun
Verbs describe actions more clearly than nominalizations.

- Not: "The ohmmeter gives an indication of 450 ohms." → "The ohmmeter shows 450 ohms."
- Not: "Before the removal of the unit…" → "Before you remove the unit…"
- If a word is not approved as a verb, use a construction like "Do a check of the
  battery" instead of inventing a verb.

**LLM application**: Prefer "validate the input" over "perform validation of the
input"; "before you deploy" over "prior to deployment". Cut nominalizations.

---

## Section 4 — Sentences

### Rule 4.1 — Write short and clear sentences
Give accurate instructions and information in short, clear sentences.
- **Instructions**: address the reader directly with the imperative. Break a
  compound instruction into numbered sub-steps.
- **Descriptions**: one topic (subject/idea) per sentence; add detail in following
  sentences. Never abstract — say exactly what happens.

- Not: "No leaks are permitted." → "Make sure that there are no leaks."
- Not: "Different temperatures will change the cure time." → "When the temperature
  increases, the cure time decreases."

**LLM application**: One idea per sentence. Replace vague claims with concrete
behavior: not "This improves performance" but "This reduces the query from 200 ms
to 20 ms". In explanations, state the actual cause-and-effect.

### Rule 4.2 — Do not omit words or use contractions
Every sentence keeps all its parts (subject, verb, nouns, articles). Do not drop
words or use contractions to shorten text.

- Not: "Rotary switch to INPUT." → "Set the rotary switch to INPUT."
- Not: "If installed, remove the shims." → "If shims are installed, remove them."
- Not: "don't / isn't / aren't" → "do not / is not / are not".

**LLM application**: In comments and docs, write full sentences. "Returns null if
not found" is acceptable comment shorthand, but avoid dropping the subject where it
creates ambiguity, and avoid contractions in formal docs.

### Rule 4.3 — Use a vertical list for complex text
When a sentence must include many items or actions, use a vertical list. Conventions:
- End the lead-in with a colon (`:`).
- Mark each item (dash, bullet, letter, or number).
- Start each item with an uppercase letter; use an article before the subject noun.
- Put a period only if the item is a full sentence; always put a period on the last
  item. Never end items with a comma or semicolon.
- Do not mix procedural and descriptive items in one list.
- In safety lists, repeat `DO NOT` on each item that needs it.
- Every item must connect grammatically to the lead-in text.

**LLM application**: Prefer bulleted/numbered lists over long comma-chained
sentences. Each bullet must read correctly when joined to the stem. This is exactly
how to format multi-step instructions and option lists.

### Rule 4.4 — Use connecting words and phrases
Connect related sentences with approved connectors: `and`, `but`, `then`, `thus`,
`as a result`, `at the same time`. Demonstratives (`this`, `these`) also link ideas.

- "When the pressure is released, the valve opens. **As a result**, the actuators
  connect to the return line."

**LLM application**: Use explicit connectors to show logical flow (cause, contrast,
sequence). Helps the reader follow reasoning in explanations and commit messages.

### Rule 4.5 — Use articles and demonstrative adjectives
Put `the / a / an` or `this / these` before a noun when applicable; do not omit them
to save space. Exceptions:
- No article before general concepts / abstract qualities: "Solvents can cause
  damage to paint"; "This software increases performance".
- In a series, the article before the **first** noun can cover the rest — but watch
  that adjectives don't become ambiguous ("the new O-rings, spacers, nut…" implies
  all are new).
- **No definite article before a noun followed by an alphanumeric ID** (it's a
  proper noun): "Tag circuit breaker 36L7" — not "the circuit breaker 36L7".

**LLM application**: Don't drop articles telegraphically ("Set flag to true" →
"Set the flag to true") except where an identifier makes the noun proper
(`Set flag ENABLE_CACHE to true`).

---

## Section 5 — Procedural writing (instructions)

### Rule 5.1 — Short sentences: max 20 words
Each instruction sentence has **20 words maximum**. Warnings and cautions obey this
too. (Notes may go to 25 words — see 5.5.) Split a long instruction into two shorter
sentences rather than exceeding the limit.

**LLM application**: Keep steps in step-by-step instructions terse — roughly ≤ 20
words. If a step runs long, split it.

### Rule 5.2 — One instruction per sentence
Write only one instruction per sentence, unless two actions happen at the same time.
Number or letter the steps to show sequence.

- Simultaneous actions may share a sentence: "Cut and remove the wire"; "Hold the
  panel open and install the fastener".
- You may write more than one sentence in a step when actions are simultaneous or a
  result immediately follows the action.

**LLM application**: One action per numbered step. Don't cram "do X and then Y and
then Z" into one step — split them so each is separately checkable.

### Rule 5.3 — Use the imperative (command) form
Start each instruction with the verb: "Set the switch to ON", "Remove the four
bolts", "Install the new O-ring". Other structures cause ambiguity about whether/who/
when.

- Not: "The test can be continued." → "Continue the test."
- Not: "Oil and grease are to be removed…" → "Remove oil and grease…"
- Do not put "must" before the imperative unless it is safety-critical.

**LLM application**: Give instructions as direct commands: "Run the migration",
"Open the file", "Delete the branch" — not "The migration should be run" or "You
would want to run the migration".

### Rule 5.4 — Condition first, then the command
When the reader must know a condition before acting, state the condition first,
then a comma, then the command.

- Not: "Set the switch to NORMAL when the light comes on."
- Yes: "When the light comes on, set the switch to NORMAL."
- Yes: "If the drive does not operate correctly, disconnect it from the gearbox."

Comma placement changes meaning — place it deliberately.

**LLM application**: Lead with the guard/precondition: "If the file exists, delete
it"; "When the build passes, merge the PR". Mirrors clean `if`-condition phrasing.

### Rule 5.5 — Notes give information, not instructions
A note only gives helpful information; it obeys descriptive-writing rules and never
contains an imperative, a requirement, or a limit.
- Put limits, tolerances, and results **in the work step**, not in a note.
- If information prevents damage or injury, make it a **safety instruction**
  (warning/caution), not a note.
- Test: the reader must be able to complete the procedure correctly **without**
  reading any notes.

**LLM application**: Keep `NOTE:` asides purely informational. Anything required to
succeed belongs in a numbered step; anything about danger belongs in a warning.

---

## Section 6 — Descriptive writing (explanations & docs)

Descriptive writing gives information, not commands — so the imperative is **not**
used here. This covers descriptions of a system/component and how it works, general
information (reports, README prose, design notes), and notes inside procedures.

### Rule 6.1 — Give information gradually
Introduce information step by step, one subject per sentence. Dumping too much at
once forces the reader to re-read. Build the picture sentence by sentence.

**LLM application**: When explaining a system, start with what it is, then what it
does, then the details. Don't front-load one sentence with five clauses.

### Rule 6.2 — Use key words and key phrases for structure
Repeat the same key words/phrases to connect ideas across sentences (they act like
traffic signs telling the reader what is new, different, or a result). **Do not
change the terminology** mid-text — the same term keeps the text clear.

**LLM application**: Reuse the exact term for a concept across a whole explanation.
Don't alternate "request/call/invocation" for the same thing. Consistent keywords
make docs skimmable.

### Rule 6.3 — Short sentences: max 25 words
Descriptive text may go up to **25 words** per sentence (more than the 20-word
procedural limit, because descriptions are inherently more complex). Split longer
sentences.

- Non-STE (31 words): "A smartphone is a cellular telephone that has an integrated
  computer and many other functions, such as an operating system, internet browsing
  as well as the ability to run software applications."
- STE (15 + 16): "A smartphone is a cellular telephone that has an integrated
  computer and many other functions. It includes an operating system and an internet
  browser, and it can also operate software applications."

**LLM application**: Cap explanatory sentences around 25 words. Long definitions →
two sentences.

### Rule 6.4 — Use paragraphs for related information
Group related information in a paragraph that opens with a **topic sentence** stating
what the paragraph is about; following sentences explain or expand it.

**LLM application**: Lead each paragraph with its point. The reader should get the
gist from the first sentence, like a good comment block or PR description.

### Rule 6.5 — One topic per paragraph
Each paragraph covers exactly one topic. The topic sentences, read together, should
form a usable outline of the whole text.

**LLM application**: Don't mix concerns in one paragraph. If a paragraph drifts to a
second topic, split it. This is the prose equivalent of single-responsibility.

### Rule 6.6 — Max six sentences per paragraph
Keep paragraphs to **six sentences or fewer**; split longer ones. Short paragraphs
hold attention and give structure.

**LLM application**: Break walls of text. Six sentences max per paragraph keeps
explanations and docstrings scannable.

---

## Section 7 — Safety instructions (warnings & cautions)

Safety instructions tell the reader that a step can be dangerous or cause damage.
The signal-word taxonomy comes from aerospace/defense specs, but the *structure*
maps directly to warnings in software docs and destructive-operation prompts.

### Rule 7.1 — Signal the level of risk with a word
Put a signal word first so the reader instantly sees the risk level:
- **WARNING** — risk of injury or death (to people).
- **CAUTION** — risk of damage to objects/equipment/data.
- Both risks present together → use **WARNING** (the higher level).

Be concrete: name the hazard and the consequence. "MAKE SURE THE OXYGEN TUBES ARE
FULLY CLEAN. OXYGEN AND GREASE MAKE AN EXPLOSIVE MIXTURE. AN EXPLOSION CAN CAUSE
INJURY OR DEATH." — not the vague "EXTREME CLEANLINESS IS IMPERATIVE."

**LLM application**: For destructive actions, lead with a clear signal:
"**Warning:** this deletes the branch permanently." Map WARNING→data loss / security
/ irreversible; CAUTION→recoverable side effects. State the actual consequence.

### Rule 7.2 — Start with a clear command or condition
Begin a safety instruction with a precise command ("DO NOT SWALLOW THE SOLVENT") or,
when the reader must know a precondition first, with the condition ("WHILE YOU USE
THE SPRAY PAINT, POINT THE SPRAY AWAY FROM YOUR FACE").

**LLM application**: Warnings open with the actionable part: "Do not run this on
production." or "Before you force-push, confirm no one else has pushed." Not a vague
preamble.

### Rule 7.3 — Explain the risk or possible result
Always tell the reader what happens if they do not obey. A specific, named
consequence makes people take care.

- "…THESE CLEANING AGENTS CAN CAUSE CORROSION."
- "…IF THEY FALL, PERMANENT DAMAGE TO THE PARTS CAN OCCUR."

**LLM application**: Every warning states the outcome: "…otherwise the database is
dropped and cannot be recovered." A warning without a stated consequence is
incomplete.

---

## Section 8 — Punctuation and word count

### Rule 8.1 — All standard punctuation except the semicolon
Use any standard English punctuation mark **except the semicolon (`;`)**. The
semicolon encourages very long sentences and is easy to misuse. Instead, write two
separate sentences.

- Not: "Examine the removed parts; replace the damaged ones."
- Yes: "Examine the removed parts for damage. Replace the damaged parts."

**LLM application**: Avoid semicolons in prose and comments. Two short sentences are
clearer than one semicolon-joined sentence.

### Rule 8.2 — Hyphens connect directly related words
Use a hyphen to bind words that work together as one unit: compound adjectives
before a noun (`low-altitude flight`, `high-pressure chamber`, `read-only cache`),
two-word numbers/fractions (`forty-seven`, `three-sixteenths`), letter/number +
noun shapes (`O-ring`, `T-shirt`, `3-prong connector`), verbs with a noun-type first
part (`heat-treat`, `short-circuit`, `fast-forward`), and prefix-vowel + root-vowel
pairs (`pre-amplifier`, `de-icing`). A hyphen (`-`) is not a dash (`—`, which
separates ideas or shows a range).

**LLM application**: Hyphenate compound modifiers (`command-line tool`,
`well-defined interface`). Don't confuse hyphens with em-dashes.

### Rule 8.3 — Parentheses: approved uses
Use parentheses to: reference an illustration/section, give an identifying
number/letter, mark work-step numbers, introduce an abbreviation, show singular/
plural at once `test(s)`, explain a word or phrase, or offer an alternative.

**LLM application**: Use parentheses for brief clarifications, cross-references, and
first-use acronym expansion — not to bury essential information.

### Rule 8.4 — In a vertical list, the colon ends a sentence (word count)
A colon that introduces a vertical list counts like a period. So the stem obeys the
sentence limit (≤ 20 words procedural / ≤ 25 descriptive), and **each list item is
counted as its own new sentence** under the same limits.

### Rule 8.5 — Parenthetical text counts as one word
When measuring sentence length, everything inside parentheses counts as **one word**
of the outer sentence (though it is also its own separate sentence to be checked).

### Rule 8.6 — Count these as one word each
Count as a single word: a number; a number with its unit (`10 mA`); an abbreviation/
acronym/initialism (`NASA`, `VPN`, `a.m.`); an alphanumeric identifier (`No. 1`,
`36L7`); quoted text (`"Service Overview"`, a formula); a title/heading/placard/
label; and a proper noun of a person, group, organization, or geopolitical entity.
(Do not count paragraph or step numbers.)

**LLM application**: These word-count rules are for STE conformance tooling. The
transferable idea: identifiers, quoted strings, and proper nouns are atomic — never
reword or split them.

### Rule 8.7 — Hyphenated words count as one word
A hyphenated group acting as one adjective/term counts as a single word
(`soap-and-water solution` = one word; `trial-and-error method` = one word).

---

## Section 9 — Writing practices

### Rule 9.1 — Rewrite the construction when a word swap isn't enough
When you replace an unapproved word, sometimes a same-part-of-speech substitute
exists (a clean word-for-word swap: `acceptable` → `permitted`). When it doesn't —
because the meaning would change, the swap reads as nonsense, or the word isn't in
the dictionary — **restructure the whole sentence** to keep the meaning.

- Not: "The oil level on the sight gauge must be visible during the test."
- Yes: "During the test, make sure that you can see the oil level on the sight gauge."

**LLM application**: When a simpler word doesn't fit grammatically, rewrite the
sentence rather than forcing an awkward substitution. Preserve meaning first, then
simplify.

### Rule 9.2 — Use each word with its correct meaning and part of speech
Many words have one narrow approved meaning. Use the sense that fits the context,
and use the word only as its correct part of speech.

- "wear" (= erode by friction), so: "Wear protective clothing" → "Put on protective
  clothing".
- "work" is a noun, not a verb: "When you work with agents" → "When you do work
  with agents".
- "damage" is a noun, not a verb: "not to damage the sleeve" → "not to cause damage
  to the sleeve".
- Positional words aren't limit words: "go below 20 psi" → "become less than 20 psi".

**LLM application**: Pick the word whose primary meaning matches. Don't repurpose a
word into a second sense or a different part of speech within technical text.

### Rule 9.3 — Do not create phrasal verbs
A verb + preposition often forms a phrasal verb whose meaning differs from its
parts, which is ambiguous. Replace it with a single precise verb.

- "put out the fire" → "extinguish the fire"
- "give off fumes" → "release fumes"
- "goes down" (for a value) → "decreases"

Only a few phrasal verbs with restricted meanings (`put on`, `come on`) are approved.

**LLM application**: Prefer one precise verb: "start" over "kick off", "disable"
over "turn off" (when about state), "investigate" over "look into". Reduces
ambiguity for global readers.

### Rule 9.4 — Use a consistent style and terminology
Reuse the **same wording for the same action** and the same noun for the same item
every time. Don't alternate "main body / body / body assembly" for one part, or
"torque" and "torque-tighten" for one action. Consistent phrasing lets the reader
recognize a step instantly.

**LLM application**: Within a response or file, lock in one term per concept and one
phrasing per repeated instruction. Consistency > variety in technical writing and
comments.

### General Recommendations (GR — advice, not strict rules)

- **GR-1 — Keep the conjunction "that".** Don't drop it; it marks the clause
  boundary and aids translation. "Make sure the valve is open" → "Make sure **that**
  the valve is open".
- **GR-2 — Watch "with" for ambiguity.** "Install the panel with the green
  fasteners" has three readings. Reread and rephrase; lead with the condition and
  keep the primary action verb ("Seal the opening **with** tool TS9867", not "Use
  tool TS9867 to…").
- **GR-3 — Use pronouns only when the referent is unmistakable.** If "they/it" could
  point to more than one noun, replace the pronoun with the actual noun. (Also: don't
  use gendered pronouns.)
- **GR-4 — Make "this" explicit.** If "this" could refer to more than one thing,
  restate the noun. "…(this can cause damage)" → "If the cover is locked, this can
  cause damage to the probe".
- **GR-5 — Beware false friends.** A word that looks like one in another language may
  mean something different in English. "Obey the dispositions" → "obey the
  instructions".
- **GR-6 — Avoid Latin abbreviations.** Write the English words: `e.g.` → "for
  example", `i.e.` → "that is", `etc.` → "and so on" (or omit).
- **GR-7 — Use inclusive, gender-neutral language.** No gendered pronouns; avoid
  "man/woman" unless the context requires it.
- **GR-8 — Use the possessive ('s) carefully.** It's allowed but confuses many
  non-native readers; if unsure, rephrase with "of".

**LLM application**: These map directly to clear comments and docs — keep "that",
resolve ambiguous pronouns/`this`, avoid Latin shorthand, write gender-neutral,
and prefer "the config of the service" when a possessive would be unclear.

---

## Quick reference checklist (apply to prose, instructions, and comments)

**Words**
- [ ] Plain, common words; one term per concept, reused consistently.
- [ ] Each word in one meaning and one part of speech (no noun-verbing).
- [ ] No slang, jargon, regionalisms, or Latin abbreviations (`e.g.`/`i.e.`/`etc.`).
- [ ] American spelling; quoted strings/identifiers left exactly as-is.
- [ ] No phrasal verbs — use one precise verb (`extinguish`, not `put out`).

**Sentences**
- [ ] Active voice; direct address (you/we).
- [ ] Simple tenses only (no perfect/progressive).
- [ ] Short: ≤ 20 words (instructions), ≤ 25 words (descriptions).
- [ ] One idea per sentence; keep articles and the conjunction "that".
- [ ] No semicolons; unstack noun piles (≤ 3-word compound nouns).

**Instructions (procedures)**
- [ ] Imperative, one action per step, numbered in sequence.
- [ ] Condition first, then the command ("If X, do Y").
- [ ] Notes are informational only; requirements go in the step.

**Descriptions (docs/comments)**
- [ ] Topic sentence first; one topic per paragraph; ≤ 6 sentences per paragraph.
- [ ] Build detail gradually; reuse key terms; be concrete, not abstract.

**Warnings (destructive / risky actions)**
- [ ] Lead with a signal word (Warning = harm/irreversible; Caution = recoverable).
- [ ] State the specific consequence of not obeying.

---

## Scope note

This guide distills **Part 1 – Writing Rules** (Sections 1–9) and the General
Introduction of ASD-STE100 Issue 9. **Part 2 – Dictionary** (the controlled word
list, PDF pages 129–434) is intentionally not reproduced here: it is a lookup
reference of ~900 approved words and thousands of unapproved words, not style
guidance. The transferable principle from Part 2 — *use a limited set of common
words, each with one meaning and one part of speech* — is already captured in
Section 1 above. Consult the source PDF directly when a specific approved word or
alternative is needed.
