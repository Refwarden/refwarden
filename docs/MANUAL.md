# refwarden — User Manual

Version 0.3 · for the command line, the browser app, and AI agent harnesses

refwarden checks the references and citations in a manuscript against open scholarly registries.
It tells you whether each reference exists as cited, whether it has been retracted or superseded,
how other papers cite it, whether it carries the claim you attach to it, and whether "several
studies" are several independent studies. It also checks the manuscript's own cross-references to
figures, tables, sections, appendices and equations. Results come as a finding ledger you can act
on, an author-facing letter, and data exports.

---

## Contents

1. [Install](#1-install)
2. [Configure](#2-configure)
3. [Check the setup](#3-check-the-setup)
4. [Audit a manuscript](#4-audit-a-manuscript)
5. [Read the results](#5-read-the-results)
6. [Check one reference](#6-check-one-reference)
7. [See how a paper is cited](#7-see-how-a-paper-is-cited)
8. [Search for evidence against a claim](#8-search-for-evidence-against-a-claim)
9. [Check internal cross-references](#9-check-internal-cross-references)
10. [Get claim-fit verdicts from an AI model](#10-get-claim-fit-verdicts-from-an-ai-model)
11. [Validate the results independently](#11-validate-the-results-independently)
12. [Record your decisions on findings](#12-record-your-decisions-on-findings)
13. [Recheck before you submit; watch sources over time](#13-recheck-before-you-submit-watch-sources-over-time)
14. [Use refwarden inside an AI agent (MCP)](#14-use-refwarden-inside-an-ai-agent-mcp)
15. [Use refwarden with your review and writing protocols](#15-fit-refwarden-into-a-review-workflow)
16. [Run it for a team or in the cloud](#16-run-it-for-a-team-or-in-the-cloud)
17. [Input formats and citation styles](#17-input-formats-and-citation-styles)
18. [Exports](#18-exports)
19. [HTTP API](#19-http-api)
20. [Data, cache and privacy](#20-data-cache-and-privacy)
21. [Troubleshooting](#21-troubleshooting)
22. [What refwarden judges, and what it leaves to you](#22-what-refwarden-judges-and-what-it-leaves-to-you)
23. [Command reference](#23-command-reference)

---

## 1. Install

You need Python 3.9 or newer. refwarden has no other dependencies.

```bash
git clone https://github.com/refwarden/refwarden.git
cd refwarden
pip install -e .                 # installs the `refwarden` command
pip install -e ".[pdf]"          # optional: adds PDF input (installs pypdf)
```

Until refwarden is merged into the repository's `main` branch, clone its working branch with
`git clone -b <branch> …` instead.

Without installing, run it from the `refwarden/` folder as `python -m refwarden …` (on Windows,
`py -m refwarden …`). Every command in this manual works in both forms.

Global options go **before** the command:

```bash
refwarden -q --db ~/papers/refwarden.db audit draft.docx
#         ^ quiet   ^ database file          ^ command and its options
```

---

## 2. Configure

Set options as environment variables, or put the same keys in `~/.refwarden/config.json`.

| Variable | What it does | Needed? |
|---|---|---|
| `REFWARDEN_MAILTO` | Your email, sent to Crossref as a courtesy contact; gives you the faster "polite" pool | Recommended |
| `OPENALEX_API_KEY` | OpenAlex key (free, required by OpenAlex since February 2026). Adds a second retraction check, author IDs, and the second-order retraction check | Optional |
| `S2_API_KEY` | Semantic Scholar key. Without it, requests are throttled to about one per second | Optional; useful for long reference lists |
| `REFWARDEN_LLM` | `none` (default), `openai`, or `anthropic` — see [section 10](#10-get-claim-fit-verdicts-from-an-ai-model) | Optional |
| `REFWARDEN_LLM_MODEL` | Model ID | With `REFWARDEN_LLM` |
| `REFWARDEN_LLM_API_KEY` | Model API key (falls back to `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`) | With a hosted model |
| `REFWARDEN_LLM_BASE_URL` | Endpoint for OpenAI-compatible hosts and local servers | For non-OpenAI hosts |
| `REFWARDEN_LLM_EFFORT` | Reasoning effort passed to the model (only sent when set) | Optional |
| `REFWARDEN_DB` | Database file (default `~/.refwarden/refwarden.db`) | Optional |
| `REFWARDEN_HOME` | Folder for the default database and `config.json` (default `~/.refwarden`) | Optional |
| `REFWARDEN_CACHE_DAYS` | How long registry answers are reused (default 14) | Optional |
| `REFWARDEN_OFFLINE` | `1` = use cached answers only, no network | Optional |
| `REFWARDEN_TOKEN` | Access token for a shared server ([section 16](#16-run-it-for-a-team-or-in-the-cloud)) | For shared hosts |

**macOS / Linux**

```bash
export REFWARDEN_MAILTO=you@university.edu
export OPENALEX_API_KEY=...
```

**Windows PowerShell**

```powershell
$env:REFWARDEN_MAILTO = "you@university.edu"
$env:OPENALEX_API_KEY = "..."
```

**Config file** (`~/.refwarden/config.json`) — keys are the lower-case field names:

```json
{
  "mailto": "you@university.edu",
  "openalex_key": "...",
  "s2_key": "...",
  "llm_provider": "anthropic",
  "llm_api_key": "..."
}
```

Environment variables are read first; values in `config.json` override them.

---

## 3. Check the setup

```bash
refwarden doctor
```

`doctor` prints your configuration (keys shown as `true`/`false`, never their values), then
queries each registry with a known DOI and pings the model if one is configured:

```
  crossref         ok
  semanticscholar  ok
  europepmc        ok
  openalex         skipped (no key)
  model            none — heuristics + judgement packets
```

Run it first on a new machine and whenever results look thin.

For a complete check of a new installation, run:

```bash
refwarden selftest
```

It runs the offline test suite, probes each source, checks four known answers against the live
registries (a correct reference, the same reference with a wrong year, an invented reference, a
retracted paper), fetches citing sentences for a highly cited paper, and starts the MCP server. It
uses its own database and writes `refwarden-selftest/selftest-report.md` (PASS / FAIL / CHECK per
item) and `selftest-responses.json` (the public registry answers it received). Neither contains
manuscript text. Send both when reporting a problem. `--quick` skips the slower parts.

**In a Claude Code cloud session**, the environment's network access must allow
`api.crossref.org`, `doi.org`, `api.semanticscholar.org` and `www.ebi.ac.uk` (plus
`api.openalex.org` with a key). Set it under the environment's settings → Network access →
Allowed domains.

---

## 4. Audit a manuscript

This is the main use: check every reference and citation before submission, revision, or a
co-author hand-off.

### From the command line

```bash
refwarden audit draft.docx -o ledger.md
```

Progress lines go to the screen; the ledger goes to `ledger.md`. The last line gives the run ID:

```
run 20261002-101512-a3f9 · {'references': 48, 'verified': 41, 'mismatch': 3, ...}
```

Options:

| Option | Effect |
|---|---|
| `--contexts N` | Citing papers sampled per reference for citation statements (default 100; `0` skips this step and makes the run much faster) |
| `--deep` | Adds citing sentences read from open-access full text (Europe PMC, up to 8 citing papers per reference, with section labels) and, with an OpenAlex key, the second-order retraction check |
| `--bib refs.bib` | Use a BibTeX file as the reference list instead of parsing it from the text |
| `--title "Draft v7"` | Name shown on the run |
| `--no-llm` | Ignore a configured model for this run |
| `-f md \| author \| csv \| json` | Output format ([section 18](#18-exports)) |
| `-o FILE` | Write to a file instead of the screen |

**How long it takes.** Most time goes to citation statements: without a Semantic Scholar key,
expect about two seconds per reference at `--contexts 100`, plus retries if the service is busy.
For a quick existence-and-retraction check of a long list, use `--contexts 0`.

### In the browser

```bash
refwarden serve
```

Open <http://127.0.0.1:8765>. On the **Manuscript** tab, upload a `.docx`, `.md`, `.txt` or
`.pdf` file, or paste the full text including the reference list. Optionally paste BibTeX, set the
number of citing papers, tick **Deep**, and press **Audit references**. The progress log shows
while it runs; the result opens below the form. **Cross-references only** runs just the offline
figure/table/section check ([section 9](#9-check-internal-cross-references)) in a few seconds.

### Worked example

The repository includes a fictional manuscript with planted defects:

```bash
refwarden audit examples/sample_manuscript.md
refwarden audit examples/live_trial.md        # real references with planted faults; needs the network
```

It contains a DOI that points to an unrelated paper, a retracted source, a misdated reference, a
reference that matches nothing, a preprint that has since been published, two co-authored papers
cited as "several studies", consensus wording on one source, a citation missing from the
reference list, a reference never cited, and cross-reference faults: a mention of a missing
figure, figures mentioned out of order, a figure never mentioned, a duplicated table number, a
broken Word cross-reference field, and an appendix that is not there. Its DOIs use Crossref's
test prefix, so against the live registries they will not resolve; the test suite runs it against
a fixture registry. `refwarden xrefs examples/sample_manuscript.md` shows the seven
cross-reference faults offline.

---

## 5. Read the results

### The coverage block — read it first

It tells you what the run could and could not check:

```
- References parsed: 48 · resolved: 45 · with abstract: 39
- In-text citing sentences: 112 (author-year) · claim–source pairs: 131 · judged by a model: 0
- Sources used: crossref, semanticscholar · HTTP calls: 212
- Skipped: openalex — no OPENALEX_API_KEY (free key required since Feb 2026)
- Note: Section numbers are mentioned but headings are not numbered; section references were not checked
```

If the registries could not be reached, the coverage block says how many references went
unchecked. **Unchecked references are unaudited, not clean.**

### The finding ledger

Each finding is one row with ten fields:

| Field | Meaning |
|---|---|
| ID | `REF-01`, `REF-02`, … for reference findings and `XREF-01`, … for cross-reference findings, sorted by severity. Stable within a run |
| Pass | `REF` — the reference-and-citation audit; `XREF` — internal cross-references. Both can be relabelled ([section 15](#15-fit-refwarden-into-a-review-workflow)) |
| Location | Reference number and label, or `§ Section ¶paragraph` of the citing sentence, mention or caption |
| Defect | One sentence, one defect |
| Evidence | What was compared: the registry record, the notice DOI, the quoted sentence or caption |
| Consequence | What goes wrong if it is left as is |
| Severity | `Major` changes the submission decision · `Moderate` fix before submission · `Minor` fix if time allows |
| Confidence | `Confirmed` · `Probable` · `Question` (below) |
| Minimum repair | The smallest change that closes it |
| Status | Your disposition: `Open`, `Accepted`, `Disputed`, `Deferred`, `Evidence-required` |

Example row:

| ID | Location | Defect | Evidence | Severity | Confidence |
|---|---|---|---|---|---|
| REF-02 | Reference [9] Jansen (2016); cited at § Evidence ¶1 | The cited work has been retracted | Retraction notice 10.5555/eos.2019.retr (2019-05-02; source: retraction-watch) | Major | Confirmed |

### What the confidence levels mean

| Confidence | Where it comes from |
|---|---|
| **Confirmed** | Checked against a registry record (DOI metadata, a retraction notice, ORCID, a reference list) or against your own manuscript text |
| **Probable** | A machine judgement backed by a verbatim quote from the source that the tool verified; or a metadata inference (shared bibliography) |
| **Question** | A heuristic flag, or a machine judgement whose quote could not be verified. Something for you to check, not a verdict |

A finding raised by a model is never `Confirmed` on the model's word. It becomes `Confirmed` only
through human validation ([section 11](#11-validate-the-results-independently)).

### Reference statuses

| Status | Meaning | What to do |
|---|---|---|
| `verified` | The DOI or search match agrees on title, year and first author | Nothing |
| `mismatch` | The work exists, but the reference misstates a field (year, first author, DOI) | Correct the field shown in the diff |
| `probable` | Only a partial title match was found | Check the reference against the source; add its DOI |
| `doi_mismatch` | The DOI resolves to a different work | Fix the DOI or the reference. Common in AI-drafted lists |
| `doi_unknown` | The DOI is not registered anywhere and search found no match | Confirm the source exists; otherwise remove it and the claim resting on it |
| `doi_elsewhere` | The DOI is registered outside Crossref (DataCite: datasets, Zenodo, arXiv) | Check details on the landing page |
| `not_found` | No registry record matches | Books and reports often land here; add an ISBN, URL or archive link |
| `error` | The registry could not be reached | Re-run later; see `doctor` |

### The checks

| Check | Triggered when | Severity | Confidence |
|---|---|---|---|
| `existence` | Wrong DOI, unregistered DOI, or no match | Major / Moderate / Minor (grey literature) | Confirmed / Probable / Question |
| `accuracy` | Year, first author or DOI differs from the record (a one-year difference is Minor: it can be online-first versus print); in-text year differs from the list entry; partial match; non-Crossref DOI | Moderate / Minor | Confirmed / Question |
| `retraction` | Retraction, withdrawal, removal (Major); expression of concern, partial retraction (Moderate); correction, erratum (Minor). If the citing sentence mentions the retraction, a retraction drops to Minor | as listed | Confirmed |
| `retraction-2nd-order` | A cited work itself cites retracted work (`--deep` + OpenAlex key) | Minor | Question |
| `supersession` | A preprint is cited and a published version exists (Confirmed), or no published version was found (Question) | Minor | as listed |
| `contested` | Citing statements include at least 2 contrasting ones, and either 5 or more or at least 10% of the sample | Moderate | Probable (model stance) / Question (heuristic only) |
| `calibration` | Consensus wording ("well established", "studies consistently show", "a growing body of") backed by one reference | Moderate | Confirmed |
| `independence` | References cited together share authors, cite one another, or share much of their bibliography, so they form fewer independent lines than their count | Moderate (plural wording or 3+ refs) / Minor | Confirmed (ORCID or citation link) / Probable |
| `claim-fit` | Model verdict `contradicted` (Major), `not_supported` or `overstated` (Moderate), `partially_supported` (Minor); heuristic flags: a number not in the abstract (Minor), causal wording on a source with no causal design in its abstract (Moderate), no shared terms with the source (Moderate) | as listed | Probable with verified quote, else Question |
| `dangling` | An in-text citation has no entry in the reference list | Minor | Confirmed |
| `uncited` | A reference list entry is never cited (only reported when at least 3 citations were detected) | Minor | Probable |
| `cross-reference` | A figure, table, section, appendix or equation is mentioned but missing, never mentioned, duplicated, misnumbered or out of order; a broken Word field or LaTeX `??` ([section 9](#9-check-internal-cross-references)) | Moderate / Minor | Confirmed / Question |

### Browser tabs

- **Findings** — the ledger, with a status menu on each row.
- **References** — each reference's registry status, field differences, notices, a
  supporting / contrasting / mentioning bar, and the contrasting statements themselves.
- **Claims** — each claim–source pair: claim type, access level, verdict, the source quote (marked
  ✓ verbatim or not), and heuristic flags.
- **Independence** — each multi-reference sentence: how many references, how many independent
  lines, and the links that join them.
- **Cross-refs** — what the document defines and what the text mentions, per object type.
- **Judgements** — import a model's or a person's judgements ([sections 10](#10-get-claim-fit-verdicts-from-an-ai-model) and [11](#11-validate-the-results-independently)).

---

## 6. Check one reference

Use it when a single reference looks doubtful, or to spot-check an AI-drafted reference list
before it goes anywhere.

```bash
refwarden resolve "Okafor, A., Li, W., & Brandt, H. (2018). Fatigue and error detection in manuscript review. Quality in Publishing, 12(3), 200-219."
```

Output is JSON: the parsed surname and year, the `status`, the `match` block (method, title fit,
and a `diffs` list such as `{"field": "year", "manuscript": 2018, "record": 2016}`), and the
registry record with abstract, notices and identifiers.

---

## 7. See how a paper is cited

Use it before a paper becomes the evidence spine of an argument, or to see whether a result has
held up.

```bash
refwarden paper 10.1234/example.doi -o report.md
refwarden paper https://doi.org/10.1234/example.doi --contexts 300 --deep
```

The report gives editorial notices; counts of supporting / contrasting / mentioning statements;
how many citing papers were sampled; how many citations Semantic Scholar marks as influential;
citation intents (background, method, result); in deep mode, the sections the citations sit in;
and up to 15 contrasting and 15 supporting statements with their citing papers.

In the browser: **Paper** tab → DOI → **Show citation report**. Buttons switch between
contrasting, supporting and mentioning statements.

---

## 8. Search for evidence against a claim

Use it when writing a conflict register, stress-testing a conclusion, or before you call
something "well supported".

```bash
refwarden counter "AI assistance speeds up peer review" -n 20 -o counter.md
```

refwarden builds a query from the claim's content words, searches Semantic Scholar, Europe PMC
(with a second query around null-result wording such as "no effect", "no association", "failed to
replicate"), and OpenAlex if you have a key. Each candidate with an abstract is screened:

- **Without a model:** sentences that overlap the claim and carry null-result or boundary wording
  are marked `contradicts?` or `qualifies?`, with the sentence quoted.
- **With a model:** each candidate is labelled `contradicts`, `qualifies`, `supports` or
  `unrelated`, with a verbatim quote from the abstract; quotes that are not verbatim are flagged.

Contradicting candidates are listed first. The query used is printed in the report, so an empty
result can be recorded with the search that produced it.

---

## 9. Check internal cross-references

Use it on every draft before it leaves your hands, and after any restructuring: figures and tables
renumbered, sections moved, an appendix split off.

refwarden reads the whole document, lists what it **defines** (figure and table captions, numbered
section headings, appendix headings, numbered display equations, boxes) and what the text
**mentions** ("Figure 2", "Figs. 3a–3c", "Tables 1 and 2", "Section 4.2", "§3", "Appendix B",
"Eq. (5)", "Box 1"), and compares the two. It runs offline in seconds and needs no model.

```bash
refwarden xrefs draft.docx                 # cross-references only
refwarden xrefs draft.docx -f author -o xref-letter.md
```

Every `audit` runs the same check as well, so its ledger contains both kinds of finding. In the
browser, **Cross-references only** on the Manuscript tab runs the offline check; audit results have
a **Cross-refs** tab with the inventory.

What it reports:

| Problem | Example | Severity | Confidence |
|---|---|---|---|
| Missing target | The text says "Figure 4" but there is no Figure 4 caption; "Section 5.3" with no such heading | Moderate | Confirmed |
| Duplicate number | Two captions read "Table 2" | Moderate | Confirmed |
| Broken Word field | "Error! Reference source not found." or "Error! Bookmark not defined." printed in the text | Moderate | Confirmed |
| Unresolved LaTeX reference | "Figure ??" or "[?]" printed in the text | Moderate | Confirmed |
| Never mentioned | Table 3 has a caption but the text never refers to it | Minor | Confirmed |
| Numbering gap | Captions run Figure 1, 2, 4 | Minor | Confirmed |
| Out of order | Figure 3 is first mentioned before Figure 2 | Minor | Confirmed |
| Captions absent | Figures are mentioned but no figure captions are in the document | Minor | Question |
| Appendix absent | "Appendix B" is mentioned but the document has no appendix | Minor | Question |

These findings use pass `XREF` and IDs `XREF-01`, `XREF-02`, …, numbered separately from the
reference findings.

How it recognises things:

- **Captions** are paragraphs that begin with the label and a separator: `Figure 3.`,
  `Fig. 3:`, `Table 2 –`, `**Table 2.**`, `Box 1.`, or a Markdown image `![Figure 1: …]`.
  Supplementary and appendix labels (`Table S2`, `Figure A1`) are read too. Figure panels
  (`Figure 3a`) count as mentions of Figure 3.
- **Section numbers** come from numbered headings (`## 3.2 Data`, or a numbered Word heading). If
  the headings are not numbered, section references are not checked and the coverage notes say so.
- **Appendices** come from headings that start with "Appendix".
- **Equations** come from display lines that end in a number in parentheses, such as
  `y = a + bx (1)`. If none are found, equation references are not checked.
- **The reference list is skipped**, so "Table 2" inside a cited article's title is not taken as a
  cross-reference. Mentions inside captions do not count as text mentions.

If your figures and tables are submitted as separate files, the captions-absent and
appendix-absent findings are expected; mark them `Accepted` ([section 12](#12-record-your-decisions-on-findings)).

---

## 10. Get claim-fit verdicts from an AI model

Without a model, refwarden still checks existence, accuracy, retractions, supersession,
independence, calibration and citation statements (with a cue-phrase classifier), and raises the
heuristic claim-fit flags. A model adds claim-fit verdicts and better stance classification. There
are three ways to supply one.

### Option A — any chat, no API key (judgement packet)

1. Export the packet:

   ```bash
   refwarden packet RUN_ID -o packet.md
   ```

   (Browser: **Judgement packet** button.) The packet contains the instructions, then the items:
   each claim, the cited source's title, year and venue, and its abstract. Only pairs whose source
   has an abstract are included.

2. Paste the whole file into any AI chat (Claude, ChatGPT, Gemini, Le Chat, a local model) or give
   it to a colleague. The reply is JSON:

   ```json
   {"items": [{"id": "K004", "verdict": "overstated",
               "quote": "longer review sessions are associated with fewer detected errors",
               "strongest_supported": "Longer sessions are associated with fewer detected errors.",
               "overclaim": "causal wording; the 37% figure"}]}
   ```

3. Save the reply and import it:

   ```bash
   refwarden submit RUN_ID reply.json --rater "<model or harness name>" --role primary
   ```

   (Browser: **Judgements** tab → paste → **Import**.) Fenced JSON, JSON with surrounding text, and
   one JSON object per line are all accepted.

For citation-statement stance, use `--kind stance`. Each item names its target by DOI; the reply
uses `{"id": "S123", "stance": "supporting|contrasting|mentioning", "why": "..."}`.

### Option B — a model over an API

**Any OpenAI-compatible endpoint** (OpenAI, OpenRouter, Groq, Together, Mistral, DeepSeek, and
local servers such as Ollama, LM Studio, vLLM, llama.cpp):

```bash
export REFWARDEN_LLM=openai
export REFWARDEN_LLM_MODEL=<model id>
export REFWARDEN_LLM_API_KEY=...                            # omit for local servers
export REFWARDEN_LLM_BASE_URL=https://openrouter.ai/api/v1  # omit for api.openai.com
```

Local examples: Ollama `http://localhost:11434/v1`, LM Studio `http://localhost:1234/v1`. For
other hosts (including Google Gemini's OpenAI-compatible endpoint) take the base URL from the
provider's documentation.

**Anthropic:**

```bash
export REFWARDEN_LLM=anthropic
export ANTHROPIC_API_KEY=...
export REFWARDEN_LLM_MODEL=claude-opus-5-5     # the default; any Claude model ID works
```

The model is then used automatically for stance, claim-fit and counter-evidence in every run.
Check it with `refwarden doctor`. If a call fails, the run falls back to heuristics and records
the error in the coverage block.

### Option C — the agent you are already using

Inside Claude Code, Cursor, Codex or any MCP-capable agent, the agent's own model judges the
packet. See [section 14](#14-use-refwarden-inside-an-ai-agent-mcp).

### How a verdict becomes a finding

| Verdict | Finding |
|---|---|
| `supported` | none; for causal or quantitative claims the item carries a note that the full text is needed before the claim counts as carried |
| `partially_supported` | Minor |
| `overstated`, `not_supported` | Moderate |
| `contradicted` | Major |
| `cannot_assess` | none |

Confidence is `Probable` if the quote was found verbatim in the abstract, otherwise `Question`.

---

## 11. Validate the results independently

A run is stamped **UNVALIDATED**. The session or model that produced the verdicts does not
validate them. Validation means a second, independent rating of the same items.

1. Export a **blind** packet (the default; the tool's verdicts are left out):

   ```bash
   refwarden packet RUN_ID -o blind.md
   ```

2. Give it to a different model tier or vendor, in a fresh session, or to a person.

3. Import with role `validation`:

   ```bash
   refwarden submit RUN_ID reply.json --rater "other-model" --role validation
   refwarden submit RUN_ID reply.json --rater "<your name>" --rater-kind human --role validation
   ```

What happens:

| Rater | Agrees with the primary verdict | Disagrees |
|---|---|---|
| Model | Recorded as concordance; confidence unchanged | Evidence gets "a validation rater disagrees — adjudicate" |
| Human, quote verified in the abstract | Finding becomes `Confirmed`, marked "validated by …" | Same disagreement note |

`submit` prints a report: items received and applied, agreements, the list of disagreements, and
unknown IDs. Every import is logged in the run's `judgement_log`; validation never overwrites the
primary verdict.

Use `--with-verdicts` on `packet` only when the rater should see the tool's verdicts, for example
for an adjudication session.

---

## 12. Record your decisions on findings

```bash
refwarden status RUN_ID REF-07 Accepted
```

Statuses: `Open`, `Accepted` (you agree; repair pending or done), `Disputed` (you disagree;
note why in your records), `Deferred` (real, accepted as risk for this submission),
`Evidence-required` (cannot be settled without data or a source). In the browser, use the menu in
the Status column.

Your statuses carry over to `recheck` runs, so an accepted risk is not reopened unless the
evidence changes.

---

## 13. Recheck before you submit; watch sources over time

### Pre-submission recheck

Papers are retracted and corrected after you cite them. Right before submission:

```bash
refwarden recheck RUN_ID
```

refwarden re-queries Crossref for every resolved reference, bypassing the cache, and reports new
notices and newly published versions. It saves a new run linked to the old one (`parent_run`)
with your statuses carried over. It does not re-read the manuscript, so cross-reference and
citation findings are carried over unchanged; after editing the text, run a new `audit` (or
`xrefs`). In the browser: **Recheck notices**.

### Watchlist

For sources your work depends on across papers:

```bash
refwarden watch add 10.1234/aaa 10.1234/bbb
refwarden watch run          # first run records a baseline; later runs report changes
refwarden watch list
```

`watch run` reports a new editorial notice, a newly published version of a preprint, a rise in
contrasting citation statements, or a DOI that no longer resolves. To run it on a schedule, add it
to cron (macOS/Linux) or Task Scheduler (Windows), e.g. weekly:

```
0 8 * * 1  REFWARDEN_MAILTO=you@uni.edu refwarden watch run >> ~/refwarden-watch.log
```

---

## 14. Use refwarden inside an AI agent (MCP)

refwarden runs as an MCP server over standard input/output. The agent calls it as a tool, and the
agent's own model judges the claim-fit packet, so no separate API key is needed.

**Claude Code**

```bash
claude mcp add refwarden -e REFWARDEN_MAILTO=you@uni.edu -- refwarden mcp
```

**Claude Desktop, Cursor, Gemini CLI** — add to the `mcpServers` section of the app's JSON settings:

```json
{"mcpServers": {"refwarden": {"command": "refwarden", "args": ["mcp"],
                              "env": {"REFWARDEN_MAILTO": "you@uni.edu"}}}}
```

**Codex CLI** — `~/.codex/config.toml`:

```toml
[mcp_servers.refwarden]
command = "refwarden"
args = ["mcp"]
```

If `refwarden` is not on the agent's PATH, use the full path to the command, or
`"command": "python", "args": ["-m", "refwarden", "mcp"]` with the `refwarden/` folder as working
directory.

**On Windows**

Install Python from python.org (tick *Add python.exe to PATH*) or with
`winget install Python.Python.3.12`, then in PowerShell:

```powershell
cd refwarden
py -m pip install -e .
refwarden doctor          # if "not recognized", use: py -m refwarden doctor
```

If `refwarden` is not found, pip's Scripts folder is not on PATH; either add it (pip prints the
path in a warning) or register the server as `py -m refwarden mcp`, which works from any folder
after `pip install`.

Claude Code (PowerShell, available in every project with `--scope user`):

```powershell
claude mcp add --scope user refwarden -e REFWARDEN_MAILTO=you@uni.edu -- py -m refwarden mcp
claude mcp list
```

Codex (`%USERPROFILE%\.codex\config.toml`):

```toml
[mcp_servers.refwarden]
command = "py"
args = ["-m", "refwarden", "mcp"]
env = { REFWARDEN_MAILTO = "you@uni.edu" }
```

Keep Python, refwarden and the agent on the same side: all native Windows, or all inside WSL.
A Windows agent cannot start a Python that lives only inside WSL, and the reverse. Put API keys
in `%USERPROFILE%\.refwarden\config.json` rather than in the agent's config, so they are not
copied into every agent's settings file.

**Tools the agent sees**

| Tool | Does |
|---|---|
| `refwarden_audit_manuscript` | Audit a manuscript's text; returns the ledger and a run ID (default 50 citing papers per reference) |
| `refwarden_check_crossrefs` | Check internal cross-references in a manuscript's text (offline) |
| `refwarden_paper_report` | Citation report for a DOI |
| `refwarden_resolve_reference` | Check one reference string |
| `refwarden_counter_evidence` | Disconfirmation search for a claim |
| `refwarden_judgement_packet` | Items and instructions for the agent to judge |
| `refwarden_submit_judgements` | Store the agent's judgements (`primary` or `validation`) |
| `refwarden_show_run` | Render a stored run (`md`, `author`, `json`) |

**Prompts that work well**

- "Audit the references in `draft.md` with refwarden, then judge the claim-fit packet and submit
  your verdicts as primary."
- "Is 10.1234/xyz contested? Show the contrasting statements."
- "Before I cite this as settled, run a refwarden counter-evidence search on: …"
- "Show the author version of run 20261002-101512."

For validation, open a **new** session, preferably with a different model, and ask it to judge the
blind packet and submit with role `validation`.

A skill file for agents that read Agent Skills is in `skills/refwarden/SKILL.md`; copy it into
your skills folder so the agent knows when to reach for the tool.

---

## 15. Fit refwarden into a review workflow

| Step in a review | How refwarden fits |
|---|---|
| **Reference and citation check** | Run `audit`. Keep the `REF-nn` IDs when you copy rows into a review report, so later reruns and `status` decisions line up. A reviewer may lower a row's confidence after reading the source, never raise it without new evidence |
| **Internal consistency** | `xrefs` (or the `XREF-nn` rows of an audit) covers the mechanical part: every figure, table, section and equation the text points to exists, once, in order. Reading the figures themselves stays with the reviewer |
| **Two audiences** | `-f md` is the internal ledger; `-f author` is the letter to the author, grouped into *Fix before submission / Should fix / If time allows*, with no IDs or codes and each point marked *Verified*, *Our assessment, not confirmed*, or *Please check* |
| **Evidence synthesis** | `resolve` checks a reference live instead of from memory; `independence` findings show how many separate lines of evidence a sentence rests on; `counter` and `contested` findings list the evidence against a claim |
| **Your own library** | `-f json` and `-f csv` carry every reference, registry record and claim item, for loading into a reference manager or database |
| **Pre-send check** | `recheck RUN_ID` as the last step before submission |

If your workflow numbers its passes differently, relabel the passes and IDs with
`REFWARDEN_PASS_LABELS`, for example `REFWARDEN_PASS_LABELS="REF=2-R/P2R,XREF=2-X/P2X"` gives pass
`2-R` with IDs `P2R-01`, … The default is pass `REF` with IDs `REF-01`, … and pass `XREF` with IDs
`XREF-01`, …

---

## 16. Run it for a team or in the cloud

On any machine with Python (a university server, a VPS, Fly.io, Render):

```bash
export REFWARDEN_TOKEN=<long random string>
export REFWARDEN_DB=/data/refwarden.db       # on a persistent volume
refwarden serve --host 0.0.0.0 --port 8765
```

With a token set, every API call requires `Authorization: Bearer <token>`; the web page asks for
it once and remembers it in that browser. Without a token, refwarden warns when it listens on a
public interface. Put it behind HTTPS (a reverse proxy such as Caddy or nginx) before sharing the
address.

All users share one database, so they see each other's runs. Use separate instances (separate
`REFWARDEN_DB` files) for work that must stay apart.

---

## 17. Input formats and citation styles

| Input | Handling |
|---|---|
| `.docx` | Read directly; Word heading styles become section headings. Caption paragraphs are read, including captions inside text boxes (each read once) |
| `.md`, `.txt` | Read directly; `#` headings mark sections |
| `.pdf` | Needs `pypdf` (`pip install -e ".[pdf]"`). Text extraction from PDFs can split lines oddly; a `.docx` export gives cleaner locations |
| `.bib` (with `--bib`) | Replaces the parsed reference list; each entry's DOI is used when present |
| LaTeX | `\cite{}`, `\ref{}` and `\label{}` commands are not read. Render citations first, e.g. `pandoc paper.tex --citeproc --bibliography refs.bib -t markdown -o paper.md`, then audit `paper.md`. For cross-references, run `xrefs` on the text of the compiled PDF (it catches printed `??`), or use a LaTeX linter such as checkcites or ChkTeX on the source |

**The reference list** is found under the last heading named References, Bibliography, Works
Cited, Literature Cited, Reference List, Sources or Literatura. It ends at an Appendix,
Supplementary, Tables, Figures, Acknowledgements, Funding or Declarations heading. Entries are
split by numbering (`[1]`, `1.`), by blank lines, or one per line.

**In-text citations:**

- numeric: `[1]`, `[1, 3]`, `[2–5]`
- author-year in parentheses: `(Smith, 2020)`, `(Smith & Lee, 2020; Park et al., 2019a)`,
  `(e.g., Smith, 2020)`, `(see Lee, 2019)`
- narrative: `Smith et al. (2020)`, `Smith and Lee (2020, 2021)`

Superscript numerals in plain text carry no marker once exported and are not detected; export
with bracketed numbers for the audit. Authors are matched on the last surname word, ignoring
particles and accents (`van Dijk` → `dijk`, `García Márquez` → `marquez`).

---

## 18. Exports

| Format | Command | Browser | Content |
|---|---|---|---|
| Ledger (Markdown) | `-f md` | Ledger (md) | Coverage, finding ledger, reference table, independence summary, cross-reference inventory |
| Author letter | `-f author` | Author letter | Plain-language findings grouped by urgency, plus what the check did not cover. Includes only `Open` and `Evidence-required` findings |
| CSV | `-f csv` | csv | Finding ledger for spreadsheets |
| JSON | `-f json` | json | The complete run: references, registry records, citations, claim items, independence, findings |
| Judgement packet | `packet RUN_ID` | Judgement packet | Instructions and items for a model or person |

Runs from `xrefs` export as `md`, `author`, `csv` and `json`; runs from `paper` and `counter` as
`md` and `json`.

Re-render any stored run without re-querying:

```bash
refwarden runs                        # list runs
refwarden show 20261002-1015 -f author -o letter.md    # a unique prefix of the run ID is enough
```

---

## 19. HTTP API

`refwarden serve` exposes the JSON API the web page uses. Long jobs return a job ID; poll it.

| Method and path | Body / query | Returns |
|---|---|---|
| `POST /api/audit` | `{"text": "...", "title": "...", "bib": "...", "contexts": 100, "deep": false}` or `{"file_b64": "...", "filename": "draft.docx", ...}` | `{"job": ID}` |
| `POST /api/xrefs` | same body as `/api/audit` | `{"job": ID}` (cross-references only) |
| `POST /api/paper` | `{"doi": "...", "contexts": 200, "deep": false}` | `{"job": ID}` |
| `POST /api/counter` | `{"claim": "...", "n": 15}` | `{"job": ID}` |
| `GET /api/jobs/{job}` | — | `{"status": "running\|done\|error", "log": [...], "run_id": ...}` |
| `GET /api/runs` | — | list of runs |
| `GET /api/runs/{run}` | — | full run JSON |
| `GET /api/runs/{run}/export` | `?fmt=md\|author\|csv\|json\|packet` (`&kind=claimfit\|stance&blind=1`) | file |
| `POST /api/runs/{run}/recheck` | — | `{"job": ID}` |
| `POST /api/runs/{run}/judgements` | `{"reply": "...", "rater": "...", "rater_kind": "model\|human", "role": "primary\|validation"}` | import report |
| `POST /api/runs/{run}/findings/{id}/status` | `{"status": "Accepted"}` | `{"ok": true}` |
| `GET /api/statements?doi=...` | — | stored citation statements for a DOI |
| `GET /api/config` | — | version, configuration (keys as true/false), model |

Example:

```bash
job=$(curl -s -X POST localhost:8765/api/paper -d '{"doi":"10.1234/xyz"}' | python -c 'import sys,json;print(json.load(sys.stdin)["job"])')
curl -s localhost:8765/api/jobs/$job
```

---

## 20. Data, cache and privacy

- **Everything is stored in one SQLite file** (`~/.refwarden/refwarden.db` by default): cached
  registry answers, citation statements, runs, findings, ratings and the watchlist. Back it up by
  copying the file. Use `--db` or `REFWARDEN_DB` to keep one database per project.
- **Cache.** Registry answers are reused for 14 days (`REFWARDEN_CACHE_DAYS`). Not-found answers
  are cached too, so a missing reference is not re-queried every run. `recheck` and `watch` always
  query fresh. `REFWARDEN_OFFLINE=1` works from the cache alone.
- **What leaves your machine.** The cross-reference check (`xrefs`) sends nothing; it runs
  entirely on your computer. For the other checks:
  - To the registries: DOIs, reference strings (for search), a query built from a claim's content
    words (`counter`), and your `REFWARDEN_MAILTO` address.
  - To a configured model: claim sentences, source abstracts and citing sentences. Manuscript text
    beyond the citing sentences is not sent.
  - Judgement packets contain claim sentences and abstracts; check them before pasting into a
    service you do not control.
- API keys are never written to the database or exports; the cache key strips `api_key`, `mailto`
  and `email` from URLs.

---

## 21. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| "No reference list found" | The heading is not recognised. Rename it to "References", or pass `--bib` |
| Few or no citations detected | Superscript numbering, LaTeX `\cite`, or an unusual style. See [section 17](#17-input-formats-and-citation-styles) |
| Many `uncited` findings | Usually the same parsing issue: citations were not detected, so references look orphaned |
| Many "never mentioned" or "missing" cross-reference findings | Captions use a form refwarden does not read (e.g. "Fig 1" with no separator, or a caption that is part of an image), or figures live in separate files. Check the Cross-refs inventory: if "Defined" is empty, the captions were not found |
| "N of M references could not be checked" | Registries unreachable. Run `refwarden doctor`; check network, proxy or firewall rules for `api.crossref.org`, `api.semanticscholar.org`, `www.ebi.ac.uk`, `api.openalex.org`, `doi.org` |
| Run is slow | Semantic Scholar throttling. Lower `--contexts`, use `--contexts 0` for a quick pass, or add `S2_API_KEY` |
| Coverage says "Rate-limited and skipped for the rest of the run" | Semantic Scholar refused three requests in a row (its shared pool is busy). refwarden stopped calling it and took citing sentences from Europe PMC open-access full text instead. Add `S2_API_KEY`, or run again later |
| Most citation statements are `mentioning` | Expected without a model: the cue rules label only explicit evidential wording. Configure a model or use a stance packet (`packet RUN_ID --kind stance`) |
| Citation statements all zero | Semantic Scholar has no contexts for that paper (some publishers withhold them), or it is new. Try `--deep` for open-access citing papers |
| "OpenAlex skipped" | Set `OPENALEX_API_KEY` (free key) |
| Model errors in coverage | Check `REFWARDEN_LLM_*` with `doctor`. The run continues with heuristics |
| `submit` reports unknown IDs | The reply used IDs not in the packet, or the reply is for another run |
| PDF input error | `pip install pypdf`, or export to `.docx` |
| Browser page asks for a token | The server has `REFWARDEN_TOKEN` set; ask whoever runs it |

---

## 22. What refwarden judges, and what it leaves to you

- **Sources are judged on abstracts and registry metadata.** Each claim item states its access
  level (`abstract` or `metadata-only`). A causal or quantitative claim needs the full text before
  it counts as carried; the tool marks that and leaves the reading to you.
- **Citation statements are a sample**: up to `--contexts` citing papers per reference from
  Semantic Scholar, plus up to 8 open-access papers per reference in deep mode. Tallies describe
  that sample.
- **The cue-phrase stance classifier** labels only explicit evidential wording ("in contrast to",
  "we failed to replicate the original effect", "consistent with") and otherwise answers
  `mentioning`. In the first live trial it labelled almost every real citing sentence `mentioning`.
  It is a floor, not a measurement; stance needs a model or a judgement packet.
- **Grey literature** (books, reports, policy documents) is often absent from Crossref and comes
  back as a `Question` asking for an identifier.
- **Cross-references** are recognised from caption, heading and equation patterns
  ([section 9](#9-check-internal-cross-references)). Captions written in an unusual form, unnumbered
  headings, and equations without a trailing number are not read, and the coverage notes say which
  object types were not checked.
- **Independence** is judged from metadata: shared authors, citation links, shared bibliographies.
  Shared funding, data or institutional origin are for you to add.
- **Every run is unvalidated until someone independent validates it** ([section 11](#11-validate-the-results-independently)).

---

## 23. Command reference

```
refwarden [-q] [--db FILE] COMMAND ...

audit FILE [--bib F] [--title T] [--contexts N] [--deep] [--no-llm] [-f FMT] [-o OUT]
xrefs FILE [--title T] [-f md|author|csv|json] [-o OUT]
paper DOI [--contexts N] [--deep] [--no-llm] [-f FMT] [-o OUT]
counter "CLAIM" [-n N] [--no-llm] [-f FMT] [-o OUT]
resolve "REFERENCE"
runs [--limit N]
show RUN_ID [-f FMT] [-o OUT]
packet RUN_ID [--kind claimfit|stance] [--with-verdicts] [--json] [-o OUT]
submit RUN_ID FILE --rater NAME [--rater-kind model|human] [--role primary|validation]
status RUN_ID FINDING_ID Open|Accepted|Disputed|Deferred|Evidence-required
recheck RUN_ID
watch add DOI... | watch list | watch run [--contexts N]
doctor
selftest [--out DIR] [--quick]
serve [--host H] [--port P]
mcp

FMT = md | author | csv | json
```
