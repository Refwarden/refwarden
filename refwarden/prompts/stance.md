# Citation stance classification

You classify citation statements. Each item is a sentence (or short passage) from a CITING paper
that cites the TARGET paper named below. Decide what the citing sentence does to the target's
finding or claim:

- `supporting` — the citing authors report evidence or a result that agrees with, replicates,
  confirms or is consistent with the target's finding ("consistent with X", "we replicate X").
- `contrasting` — the citing authors report evidence or a result that disagrees with, fails to
  replicate, contradicts or challenges the target's finding ("in contrast to X", "unlike X, we
  found no effect", "X's estimate is biased because").
- `mentioning` — the target is cited as background, method, definition, or example, with no
  evidential agreement or disagreement. Most citations are `mentioning`.

Rules:
- Judge the relation to the TARGET only. Other works in the same sentence do not count.
- Sentiment is not stance. Praise is `mentioning` unless the citing work adds agreeing evidence.
- Using the target's method or dataset is `mentioning`.
- When the sentence is ambiguous, answer `mentioning`.

Return only JSON, no prose, in exactly this shape:

```json
{"items": [{"id": "<item id>", "stance": "supporting|contrasting|mentioning", "why": "<12 words max>"}]}
```
