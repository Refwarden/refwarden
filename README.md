# refwarden

Every citation gets a paper trail. refwarden checks a manuscript's references and in-text
citations against open scholarly registries and reports, per reference and per claim:

- whether the reference exists as cited (right DOI, year, first author, title);
- whether it has been retracted, corrected, given an expression of concern, or superseded
  by a published version;
- how the literature cites it: citing sentences classified supporting / contrasting /
  mentioning, with the section they sit in (the Scite "smart citation" layer, rebuilt on open data);
- whether the cited source carries the claim the manuscript hangs on it;
- whether "several studies show" rests on several independent lines of evidence or on one;
- whether the manuscript's own cross-references hold: every figure, table, section, appendix and
  equation the text points to exists, once, in order, and every captioned figure and table is
  mentioned (offline, `refwarden xrefs`).

Output is a finding ledger (`ID | Pass | Location | Defect | Evidence | Consequence | Severity |
Confidence | Minimum repair | Status`), with reference findings under pass `REF` and
cross-reference findings under pass `XREF`; an author-facing letter; CSV; and JSON.

Standard-library Python 3.9+, no dependencies. Runs as a CLI, a browser app (local or on any
host), and an MCP server; any AI model, or none, can do the judging.

## Quick start

```bash
cd refwarden
pip install -e .            # optional: gives you the `refwarden` command; otherwise use `python -m refwarden`
export REFWARDEN_MAILTO=you@university.edu     # polite-pool contact for Crossref
refwarden doctor                               # checks every source and the model
refwarden selftest                             # full installation check; writes a report to send back
refwarden audit paper.docx -o ledger.md        # finding ledger
refwarden xrefs paper.docx                     # figures/tables/sections only, offline, seconds
refwarden -q show RUN_ID -f author -o letter.md
refwarden serve                                # browser UI at http://127.0.0.1:8765
```

On Windows use `py -m refwarden …` if the `refwarden` command is not on PATH. Full instructions
for every use case, including Windows agent setup: [docs/MANUAL.md](docs/MANUAL.md). Changes by
version: [CHANGELOG.md](CHANGELOG.md).

Try it on the fictional test manuscript with planted defects: `refwarden audit examples/sample_manuscript.md`.

## What it replicates from Scite, and from where

| Scite feature | refwarden | Open data behind it |
|---|---|---|
| Smart citations (supporting / contrasting / mentioning) | `paper DOI`; per-reference tallies in `audit` | Semantic Scholar citation contexts; Europe PMC open-access full text (`--deep`) |
| Citation section (intro / methods / results / discussion) | section per statement in deep mode | JATS full text, Europe PMC |
| Reference Check | `audit` | Crossref metadata, doi.org handle lookup |
| Retraction and editorial-notice alerts | notices in `audit`, `watch`, `recheck` | Crossref `updated-by` incl. Retraction Watch; OpenAlex `is_retracted` |
| Dashboards / saved searches | stored runs, `runs`, browser Runs tab | local SQLite |
| Assistant that answers with citations | not replicated; `counter` does the opposite job (below) | — |

refwarden is independent of Scite and uses none of its code or data; the comparison describes
functions only.

## What it adds

1. **Claim-to-source fit.** Each (citing sentence, reference) pair becomes an item. Deterministic
   flags: a figure in the claim that is absent from the source's abstract; causal wording on a source
   whose abstract names no causal design; no shared content terms. With a model: a verdict
   (`supported / partially_supported / overstated / not_supported / contradicted / cannot_assess`)
   that must quote the abstract. The quote is checked character by character; an unverifiable
   quote drops the finding to `Question`.
2. **Independence of co-cited sources.** References cited together are linked when they share an
   author (ORCID where the registry has one), when one cites the other in its registry reference
   list, or when they share much of their bibliography. The finding states how many independent
   lines the sentence actually has.
3. **Calibration of consensus wording.** "Well established", "studies consistently show" and similar
   wording backed by one reference.
4. **Phantom-reference detection.** A DOI that resolves to an unrelated work, a DOI that is not
   registered anywhere, a reference whose title matches a real paper but whose year or first author
   does not. These are the traces AI-drafted reference lists leave.
5. **Disconfirmation search** (`counter "claim"`). Looks for candidate papers whose own findings
   contradict or bound a claim, including a query built around null-result wording. It feeds the
   conflict register rather than hunting for support.
6. **Internal cross-reference check** (`xrefs`, also part of every audit). Missing, duplicated,
   unmentioned, gapped or out-of-order figures and tables; section, appendix and equation numbers
   that point nowhere; broken Word cross-reference fields ("Error! Reference source not found.") and
   unresolved LaTeX `??`. Rule-based, works on Word and Markdown as well as LaTeX-derived text.
7. **Second-order retraction exposure** (`--deep`, OpenAlex key). Flags cited works that themselves
   cite retracted work.
8. **Separation of duties built in.** Every run is marked `UNVALIDATED`. Judgement packets carry the
   items to a different session, model or person; `--role validation` records their ratings without
   overwriting the primary verdict. Agreement from another model is recorded as concordance and
   leaves confidence unchanged; a human rating that agrees and quotes the source verbatim upgrades a
   finding to `Confirmed`.

## Confidence rules

| Evidence behind the finding | Highest confidence |
|---|---|
| Registry record compared field by field (DOI, notices, relations, ORCID, reference list) | Confirmed |
| Manuscript text itself (dangling citation, consensus wording, cross-references) | Confirmed |
| Machine judgement with a verified verbatim quote | Probable |
| Machine judgement without a verified quote; heuristic flags | Question |
| Human validation that agrees and quotes the source | Confirmed |

Stance classification is machine output in both modes and is labelled with its method
(`heuristic`, `llm:<model>`, `harness:<rater>`) wherever it appears.

## Any harness, any model

**No model.** Heuristics run; claim-fit items are exported as a packet:

```bash
refwarden packet RUN_ID -o packet.md            # paste into any chat: ChatGPT, Gemini, Claude, Le Chat, a local model
refwarden submit RUN_ID reply.json --rater <model or harness name> --role primary
refwarden packet RUN_ID -o blind.md             # packets are blind by default: no tool verdicts inside
refwarden submit RUN_ID reply2.json --rater "<your name>" --rater-kind human --role validation
```

**A model over HTTP.** Set once, used for stance, claim-fit and counter-evidence:

```bash
# any OpenAI-compatible endpoint: OpenAI, OpenRouter, Groq, Together, Mistral, DeepSeek,
# Gemini (OpenAI-compatible endpoint), Ollama, LM Studio, vLLM, llama.cpp
export REFWARDEN_LLM=openai REFWARDEN_LLM_MODEL=<model id> REFWARDEN_LLM_API_KEY=...
export REFWARDEN_LLM_BASE_URL=http://localhost:11434/v1     # e.g. Ollama; omit for api.openai.com

# Anthropic Messages API (default model claude-opus-5-5)
export REFWARDEN_LLM=anthropic ANTHROPIC_API_KEY=...
```

For Anthropic's current top models the adapter enables server-side refusal fallback
(`fallbacks: "default"`), so a safety-classifier refusal is retried on a suitable model
instead of failing the batch. `REFWARDEN_LLM_EFFORT` is passed through only when set.

**Inside an agent harness (MCP).** The host model does the judging; no API key needed.

```bash
claude mcp add refwarden -e REFWARDEN_MAILTO=you@uni.edu -- refwarden mcp       # Claude Code
```

On Windows, register `py -m refwarden mcp` as the command (manual, section 14).

Claude Desktop, Cursor, Gemini CLI (`mcpServers` in their JSON settings):

```json
{"mcpServers": {"refwarden": {"command": "refwarden", "args": ["mcp"],
                              "env": {"REFWARDEN_MAILTO": "you@uni.edu"}}}}
```

Codex CLI (`~/.codex/config.toml`):

```toml
[mcp_servers.refwarden]
command = "refwarden"
args = ["mcp"]
```

Tools: `refwarden_audit_manuscript`, `refwarden_check_crossrefs`, `refwarden_paper_report`, `refwarden_resolve_reference`,
`refwarden_counter_evidence`, `refwarden_judgement_packet`, `refwarden_submit_judgements`,
`refwarden_show_run`. A skill wrapper for Agent-Skills-aware harnesses is in `skills/refwarden/`.

## Configuration

| Variable | Purpose |
|---|---|
| `REFWARDEN_MAILTO` | contact address sent to Crossref and in the user agent (polite pool) |
| `OPENALEX_API_KEY` | OpenAlex has required a free key since February 2026; enables retraction cross-check, author IDs, second-order checks |
| `S2_API_KEY` | Semantic Scholar key; without one the shared pool is throttled to about one request per second |
| `REFWARDEN_DB` | SQLite file (default `~/.refwarden/refwarden.db`) |
| `REFWARDEN_HOME` | folder for the default database and `config.json` (default `~/.refwarden`) |
| `REFWARDEN_CACHE_DAYS` | HTTP cache lifetime (default 14); `recheck` and `watch` bypass it |
| `REFWARDEN_OFFLINE=1` | serve from cache only |
| `REFWARDEN_TOKEN` | bearer token required by `serve` on a shared host |
| `REFWARDEN_LLM`, `_MODEL`, `_BASE_URL`, `_API_KEY`, `_EFFORT` | model adapter (above) |

The same keys can live in `~/.refwarden/config.json`.

## Cloud

`refwarden serve --host 0.0.0.0 --port 8765` with `REFWARDEN_TOKEN` set runs on any host with
Python (a VPS, Fly.io, Render, a university server). The database is one SQLite file on a
persistent volume. The page asks for the token once.

## Scope

- Claim-fit is judged on abstracts and registry metadata; full texts are not read. Items carry
  `access: abstract | metadata-only`, and a causal or quantitative claim judged `supported` carries
  a note that the full text is needed before it counts as carried.
- Citation statements are a sample: up to `--contexts` citing papers per reference from Semantic
  Scholar (some publishers withhold contexts) plus, in deep mode, up to 8 open-access citing papers
  from Europe PMC per reference. Tallies describe that sample.
- The heuristic stance classifier labels a sentence only on explicit evidential wording ("in
  contrast to", "we failed to replicate the original effect", "consistent with") and otherwise
  answers `mentioning`. On real citing sentences it rarely labels anything, so tallies from it are
  close to all-`mentioning`; use a configured model or a judgement packet for stance.
- Books, reports and grey literature are often absent from Crossref; they surface as `Question`
  findings asking for a persistent identifier.
- The cross-reference check reads captions that start with the label (`Figure 3.`, `Table 2:`),
  numbered headings, appendix headings and display equations ending in `(n)`. Object types it could
  not read are named in the coverage notes. LaTeX `\ref{}` in source files is not read.
- Citation parsing covers numeric (`[1]`, `[2–4]`) and author-year styles; superscript numerals
  in plain text are not detected. A `.bib` file can replace the parsed reference list.

## Data sources and terms

Crossref REST API (metadata, CC0; Retraction Watch data via Crossref), OpenAlex (CC0, key required),
Semantic Scholar Academic Graph API (see its license agreement), Europe PMC REST API (open-access
subset for full text), doi.org handle API. The tool caches responses locally and throttles per host.

## Tests

```bash
python -m unittest discover -s tests
```

35 tests run offline. Most use a fixture registry shaped like the public APIs (`tests/fakes.py`);
the rest replay real Crossref, Semantic Scholar and Europe PMC answers recorded on 2026-11-04
(`tests/fixtures/live_*.json`), including a full audit of `examples/live_trial.md`, a trial
manuscript with real references and planted faults.

`refwarden selftest` checks a new installation end to end against the live services and writes
`refwarden-selftest/selftest-report.md`. In a Claude Code cloud environment the network policy
must allow `api.crossref.org`, `doi.org`, `api.semanticscholar.org`, `www.ebi.ac.uk` and, with a
key, `api.openalex.org`.

## How far it has been tested

- 35 offline tests, and `refwarden selftest` against the live registries (11/11 known answers).
- One planted-fault trial manuscript (`examples/live_trial.md`) and one published journal article,
  audited from its publisher PDF. What that article exposed in PDF parsing
  and reference matching is fixed in 0.3.0 (see CHANGELOG).
- Claim-fit and stance verdicts depend on the model that gives them. refwarden checks that each
  verdict quotes the source abstract verbatim and keeps every run `UNVALIDATED` until a second
  rater or the author confirms it.
- Rater agreement on the journal article: two language models of different size, the second
  working blind through a judgement packet, gave the same claim-fit verdict on 52 of 78
  claim–source pairs (67%). All 26 disagreements were between neighbouring verdicts (most often
  `partially_supported` vs `not_supported`); none set `supported` against `not_supported` or
  `contradicted`. The disagreements go to the author, whose decision with a verified quote makes a
  finding `Confirmed`.

## Not affiliated

refwarden is an independent open-source project. It is not affiliated with or endorsed by Scite,
Crossref, Semantic Scholar, OpenAlex or Europe PMC; their names are used only to describe what the
tool does and which data it reads. Each user queries the public APIs directly under those services'
terms; refwarden ships no third-party data.

## Author

Prof. Nikša Alfirević, University of Split, Croatia (nalf@efst.hr). Developed with the
assistance of Claude Code (Anthropic).

To cite refwarden, use the metadata in [CITATION.cff](CITATION.cff).

## License

Apache License 2.0 — see [LICENSE](LICENSE).
