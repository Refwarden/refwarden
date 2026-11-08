---
name: refwarden
metadata:
  version: "0.1"
description: "Run the refwarden reference and citation audit on a manuscript, a single DOI, or a claim. Triggers: 'check my references', 'reference check', 'are any of my citations retracted', 'smart citations for this paper', 'how is this paper cited', 'is this paper contested', 'does this source support my claim', 'find evidence against this claim', 'check citations before submission', 'check figure and table references', 'Error! Reference source not found'. Produces a finding ledger, an author letter, CSV and JSON. Do NOT use for full manuscript review, for writing a literature synthesis, or for citation formatting."
---

# refwarden — reference and citation audit

A tool, not a reviewer. It checks references and citations against open registries (Crossref
with Retraction Watch data, Semantic Scholar, Europe PMC, OpenAlex) and returns findings as a
ledger. Every run is `UNVALIDATED` until a second, independent rater (another session or model)
or the author validates it.

## Pick the job

| Need | Command (CLI) | MCP tool |
|---|---|---|
| Audit a manuscript's references and citations | `refwarden audit FILE [--deep]` | `refwarden_audit_manuscript` |
| Figures, tables, sections, appendices, equations: do the text's pointers resolve | `refwarden xrefs FILE` (offline) | `refwarden_check_crossrefs` |
| How one paper is cited; is it contested or retracted | `refwarden paper DOI` | `refwarden_paper_report` |
| Is this one reference real and correct | `refwarden resolve "REF"` | `refwarden_resolve_reference` |
| Evidence against a claim (conflict register input) | `refwarden counter "CLAIM"` | `refwarden_counter_evidence` |
| Pre-submission sweep of a stored audit | `refwarden recheck RUN_ID` | — |
| Alerts on sources a portfolio relies on | `refwarden watch add DOI…` / `watch run` | — |

If neither the CLI nor the MCP server is available, say so and stop; do not reconstruct registry
results from memory. A reference asserted from recall is exactly as fluent when fabricated.

## Procedure (audit)

1. Run the audit. Read the coverage block first: references resolved, sources used, sources
   skipped, errors. References the registries could not reach are unaudited, not clean.
2. Judge the claim-fit items. When the run has no model, fetch the packet
   (`refwarden_judgement_packet` or `refwarden packet RUN_ID`), judge every item by the packet's
   instructions, and submit with role `primary`. Quote the abstract verbatim; the tool checks.
3. Report the ledger as produced. Do not raise a finding's confidence: `Confirmed` comes only from
   registry records, the manuscript text, or a human validation with a verified quote. Two models
   agreeing is concordance, not confirmation.
4. Hand validation to a different session or model tier (`--role validation`, blind packet), or to
   the author. The session that produced the verdicts never validates them.
5. Author dispositions (`Accepted / Disputed / Deferred / Evidence-required`) are recorded with
   `refwarden status`; they survive `recheck`, and accepted risks are not reopened without new evidence.

## Where the output goes

- **Manuscript review:** copy ledger rows (IDs `REF-nn`, `XREF-nn`) into the review report with their
  IDs, so reruns and `status` decisions line up. A reviewer may lower a row's confidence, not raise it.
- **Literature synthesis:** `independence` findings show how many separate lines of evidence a
  sentence rests on; `counter` output and `contested` findings list evidence against a claim;
  `resolve` checks a reference live instead of from memory.
- **Before sending:** `recheck RUN_ID` re-queries every reference's editorial status without the cache.

## Output modes

`-f md` internal ledger · `-f author` plain-language letter (no IDs or codes; hedges preserved:
Confirmed → "Verified", Probable → "Our assessment, not confirmed", Question → "Please check") ·
`-f csv` · `-f json`.
