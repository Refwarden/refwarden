# Changelog

## 0.4.0 — 2026-11-05

- Public release under the Apache License 2.0.
- Renamed from `citetrail` to `refwarden` (package, command, MCP tools `refwarden_*`, variables
  `REFWARDEN_*`). Old `CITETRAIL_*` variables and data in `~/.citetrail` are still read.
- Pass names are neutral: `REF` (references and citations, IDs `REF-nn`) and `XREF` (internal
  cross-references, IDs `XREF-nn`). `REFWARDEN_PASS_LABELS` relabels them for a local workflow.
- The workflow-specific SQL export is no longer part of the package; use `-f json` or `-f csv`.

## 0.3.0 — 2026-11-04

First run against the live registries (Crossref, Semantic Scholar, Europe PMC, doi.org).
`refwarden selftest` passes 11/11; a trial manuscript with real references and planted faults
(`examples/live_trial.md`) is audited correctly. Fixes from what the live data showed:

- **`refwarden selftest`**: one command that runs the offline suite, probes each source, checks
  known answers (a correct reference, a wrong year, an invented reference, a retracted paper) and
  writes a report plus the raw public responses, with no manuscript text.
- **Invented references**: a search hit with a partial title match and a different author team is
  now `not_found` (it had been `probable`), and the closest record is named in the evidence.
- **Abstracts**: registries disagree on what the abstract is (Science's Crossref record holds the
  editor's summary), so all distinct abstracts are kept and searched. Numbers written as words
  ("thirty-six percent") match figures, and term matching uses word stems.
- **Years**: a one-year difference from the registry is reported (Minor) instead of ignored, and an
  in-text year that differs by one from the list entry is reported as one year conflict instead of a
  dangling citation plus an uncited reference.
- **Stance heuristic**: on real citing sentences the 0.1 cue rules were mostly wrong (topic words
  such as "replication" read as agreement). Cues now need an evidential object or first-person
  framing; non-English sentences are left as `mentioning`; a `contested` finding based on the
  heuristic alone is a `Question`. On the 59 live statements the heuristic now labels 0 as
  supporting or contrasting: use a model or a judgement packet for stance.
- **Rate limits**: after a 429 the host is slowed for the rest of the run; after three refusals in a
  row it is skipped, and citation statements fall back to Europe PMC open-access full text.
- Tests: 35, including replays of real recorded responses (`tests/fixtures/live_*.json`).

## 0.2.0 — 2026-10-01

- **Internal cross-reference check.** `refwarden xrefs FILE` (offline) and part of every `audit`:
  figures, tables, sections, appendices, equations and boxes that are mentioned but missing,
  never mentioned, duplicated, gapped or out of order; broken Word cross-reference fields and
  unresolved LaTeX `??`. Findings use a separate pass for cross-references. Also as MCP tool
  `refwarden_check_crossrefs`, `POST /api/xrefs`, a browser button and a Cross-refs tab.
- `.docx` reading: text boxes are read once (Word stores them twice, inside their anchor
  paragraph), so a boxed caption is no longer reported as a duplicate.
- Finding IDs are numbered per pass.
- Locations: when a document has marked headings (`#` or Word heading styles), only those are used
  as section markers.
- Windows: command-line and MCP output are always UTF-8, so piped output with `→` or accented text
  no longer fails; manual covers Claude Code and Codex setup on Windows.
- Model failures during an audit are recorded in the coverage block; the run falls back to
  heuristics.

## 0.1.0 — 2026-09-30

First version: reference existence and accuracy, retractions and notices, preprint supersession,
citation statements (supporting / contrasting / mentioning), claim-to-source fit, independence of
co-cited sources, consensus-wording calibration, dangling and uncited references, disconfirmation
search, recheck and watchlist; ledger, author letter, CSV and JSON exports;
judgement packets and validation; CLI, browser app, HTTP API and MCP server.
