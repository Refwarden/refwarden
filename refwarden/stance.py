"""Stance of a citation statement: supporting / contrasting / mentioning.

Two methods, always labelled in the output:
  heuristic  cue-phrase rules with negation handling; transparent, fast, low recall
  llm:<id>   the stance prompt run on any configured model
"""
import re

# Cues must describe the citing authors' evidence relative to the cited work, not the topic. A live trial on
# meta-science papers showed topic words ('replication', 'challenge', 'fail to replicate' said of the field)
# producing mostly false stances, so bare topic words are excluded and evidence verbs need an object.
OBJ = r"(?:the|their|these|this|those|its|previous|earlier|prior|original|published|reported)\s+(?:\w+\s+)?(?:finding|result|effect|estimate|conclusion|observation|claim|evidence|pattern)s?"
CONTRAST = [
    r"\bcontrary to\b", r"\bin contrast (?:to|with)\b", r"\bunlike\s+(?:[A-Z]|the|their|previous|earlier|prior)", r"\binconsistent with\b",
    r"\bat odds with\b", r"\bcontradict(?:s|ed|ing)?\b", r"\bconflicts? with\b", r"\brefut(?:e|es|ed|ing)\b",
    rf"\b(?:we|our \w+|this study)\s+(?:did not|do not|could not|failed to|fail to)\s+(?:replicate|reproduce|confirm|find|observe|detect)",
    rf"\b(?:failed to|fail to|did not|could not)\s+(?:replicate|reproduce|confirm)\s+{OBJ}",
    r"\bnot (?:consistent|in line|in agreement) with\b", r"\bdisagree(?:s|d)? with\b", r"\bas opposed to\b",
    rf"\bcall(?:s|ed)? into question\s+{OBJ}", rf"\bquestion(?:s|ed)?\s+{OBJ}", r"\bcounter to\b",
]
SUPPORT = [
    r"\bconsistent with\b", r"\bin line with\b", r"\bin (?:agreement|accordance|keeping) with\b", r"\bagree(?:s|d)? with\b",
    rf"\bconfirm(?:s|ed|ing)?\s+{OBJ}", rf"\bcorroborat(?:e|es|ed|ing)\s+{OBJ}", rf"\breplicat(?:e|es|ed|ing)\s+{OBJ}",
    rf"\b(?:we|our \w+|this study)\s+(?:replicate|replicated|confirm|confirmed|reproduce|reproduced)\b",
    rf"\bsupport(?:s|ed|ing)?\s+{OBJ}", r"\bsimilar to (?:those|that|the \w+) (?:reported|found|observed)",
    r"\bas (?:previously |also )?(?:reported|shown|found|observed|demonstrated) (?:by|in)\b",
    r"\bmirror(?:s|ed)?\s+(?:the|those|their)", rf"\breinforc(?:e|es|ed|ing)\s+{OBJ}", r"\bconverg(?:e|es|ed|ing) with\b",
]
RELATIONAL = re.compile(r"(?:consistent|in line|in (?:agreement|accordance|keeping)|agree)", re.I)
NEGATORS = re.compile(r"\b(?:not|no|never|neither|nor|hardly|fail\w*|lack\w*)\b(?:\s+\w+){0,2}\s*$", re.I)
_C = [re.compile(p) if "[A-Z]" in p else re.compile(p, re.I) for p in CONTRAST]
_S = [re.compile(p, re.I) for p in SUPPORT]


def heuristic(context):
    """Return (stance, cue). Negated support cues ('not consistent with') count as contrast."""
    text = context or ""
    words = re.findall(r"[a-z]+", text.lower())
    if words and sum(w in ("the", "and", "of", "to", "in", "is", "that", "with", "we") for w in words) / len(words) < 0.04:
        return "mentioning", "not English; cues not applied"
    c_hits, s_hits = [], []
    for rx in _C:
        m = rx.search(text)
        if m:
            c_hits.append(m.group(0))
    for rx in _S:
        for m in rx.finditer(text):
            if NEGATORS.search(text[max(0, m.start() - 30):m.start()]):
                # only a negated relation ('not consistent with X') is a contrast; 'do not support the original
                # conclusions' usually restates the cited replication result, so it says nothing either way
                if RELATIONAL.match(m.group(0)):
                    c_hits.append("not " + m.group(0))
            else:
                s_hits.append(m.group(0))
    if c_hits and not s_hits:
        return "contrasting", c_hits[0]
    if s_hits and not c_hits:
        return "supporting", s_hits[0]
    if c_hits and s_hits:
        return "mentioning", f"mixed cues: {c_hits[0]} / {s_hits[0]}"
    return "mentioning", ""


def classify(statements, llm=None, target_title=""):
    """Set stance/stance_method/stance_note on each statement dict in place."""
    use_llm = llm is not None and llm.available and statements
    results = {}
    if use_llm:
        items = [{"id": str(i), "context": s["context"][:1200]} for i, s in enumerate(statements)]
        try:
            results = llm.judge("stance", f"TARGET paper: {target_title}", items, batch=12)
            if len(results) < len(items) and statements:
                statements[0]["stance_error"] = f"model judged {len(results)} of {len(items)}; rest by heuristic"
        except Exception as e:  # fall back to heuristics, but say so
            results, use_llm = {}, False
            for s in statements:
                s["stance_error"] = str(e)[:200]
    for i, s in enumerate(statements):
        r = results.get(str(i))
        if r and r.get("stance") in ("supporting", "contrasting", "mentioning"):
            s["stance"], s["stance_method"], s["stance_note"] = r["stance"], "llm:" + llm.model, r.get("why", "")
        else:
            s["stance"], cue = heuristic(s["context"])
            s["stance_method"], s["stance_note"] = "heuristic", cue
    return statements


def tally(statements):
    t = {"supporting": 0, "contrasting": 0, "mentioning": 0}
    for s in statements:
        t[s.get("stance") or "mentioning"] += 1
    t["total"] = sum(t.values())
    t["methods"] = sorted({s.get("stance_method") or "heuristic" for s in statements})
    return t
