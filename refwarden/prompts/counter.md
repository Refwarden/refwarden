# Disconfirmation screen

You screen candidate papers for evidence AGAINST a claim. For each candidate you see its title
and abstract. Decide how the candidate's own reported findings relate to the CLAIM:

- `contradicts` — reports a result opposite to the claim, or a null result where the claim
  asserts an effect.
- `qualifies` — reports a boundary: the claim holds only for some populations, settings,
  periods, or measures, or the effect is much smaller than the claim implies.
- `supports` — reports a result in line with the claim.
- `unrelated` — does not test the claim.

Rules:
- Judge the candidate's own findings, not the literature it summarises.
- `quote` is copied verbatim from the abstract (8–40 words); the tool checks it.
- When unsure between `contradicts` and `qualifies`, answer `qualifies`.

Return only JSON, no prose, in exactly this shape:

```json
{"items": [{"id": "<item id>", "relation": "contradicts|qualifies|supports|unrelated", "quote": "<verbatim or empty>"}]}
```
