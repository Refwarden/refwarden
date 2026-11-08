# Claim-to-source fit

Each item pairs a CLAIM sentence from a manuscript with ONE source the manuscript cites for it.
You see only what the tool retrieved for that source: metadata and, where available, the
abstract. You have not read the full text, and you must not act as if you had.

For each item decide whether the retrieved source material carries the claim:

- `supported` — the abstract states what the claim attributes to the source.
- `partially_supported` — the source is on point but the claim goes further in scope,
  population, strength, or certainty.
- `overstated` — the claim turns a correlational, preliminary, or bounded finding into a causal,
  general, or settled one.
- `not_supported` — the abstract is on a different point; the citation looks misplaced.
- `contradicted` — the abstract reports the opposite of what the claim says.
- `cannot_assess` — the retrieved material is too thin (no abstract, or the claim concerns a
  detail an abstract would not report). Prefer this over guessing.

Rules:
- `quote` must be copied verbatim from the ABSTRACT text given, 8–40 words, and must be the
  passage your verdict rests on. The tool checks it character by character; a paraphrase counts
  as no quote. Leave it empty for `cannot_assess`.
- `strongest_supported` is the strongest claim this source can carry, in one sentence.
- `overclaim` names the part of the manuscript's claim the source does not carry, or is empty.
- A number in the claim that does not appear in the abstract is not evidence against the claim;
  mark `cannot_assess` for that part rather than `not_supported`.
- Several sources may be cited together for one claim. Judge only this source's share.

Return only JSON, no prose, in exactly this shape:

```json
{"items": [{"id": "<item id>", "verdict": "supported|partially_supported|overstated|not_supported|contradicted|cannot_assess",
  "quote": "<verbatim from abstract or empty>", "strongest_supported": "<one sentence>", "overclaim": "<text or empty>"}]}
```
