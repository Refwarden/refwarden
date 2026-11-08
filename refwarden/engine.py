"""The three jobs: manuscript audit, paper citation report, disconfirmation search.

Every job returns one JSON-serialisable result dict, stored as a run in SQLite. Findings follow
one finding contract (ID | Pass | Location | Defect | Evidence | Consequence | Severity |
Confidence | Minimum repair | Status). Machine judgements never exceed `Probable`; `Confirmed`
is reserved for facts checked against a registry record or the manuscript text itself.
"""
import datetime as dt
import hashlib
import re
import uuid
from concurrent.futures import ThreadPoolExecutor

from . import stance as stance_mod
from . import xrefs as xrefs_mod
from .db import DB
from .http import Http
from .llm import LLM
from .parse import parse_manuscript
from .resolve import enrich, ref_label, resolve, work_label
from .sources import crossref, europepmc, openalex, s2
from .textutil import (containment, content_terms, fold, jaccard, quote_in, sentence_spans,
                       spelled_numbers_to_digits, squash)

PASS, XPASS = "REF", "XREF"   # references and citations; internal cross-references
UNVALIDATED = ("UNVALIDATED — produced by one rater. Validation needs a second, independent rater (another model "
               "or session) or the author. Checks that a repair was applied are not validation.")


def pass_labels():
    """Optional relabelling for local review workflows: REFWARDEN_PASS_LABELS="REF=R1/PR,XREF=X1/PX" shows pass
    REF as 'R1' with IDs PR-01, PR-02, … Default: pass names as above, IDs REF-01, XREF-01."""
    from .config import _env
    out = {}
    for part in _env("REFWARDEN_PASS_LABELS").split(","):
        key, _, val = part.partition("=")
        if key.strip() and val.strip():
            label, _, tag = val.strip().partition("/")
            out[key.strip()] = (label, tag or label.replace("-", ""))
    return out


def pass_label(p):
    return pass_labels().get(p, (p, p))[0]
SEV_ORDER = {"Fatal": 0, "Major": 1, "Moderate": 2, "Minor": 3}

CAUSAL = re.compile(r"\b(caus\w*|leads? to|led to|result(?:s|ed)? in|drives?|driven by|effect of|impacts? (?:of|on)|"
                    r"triggers?|prevents?|due to|because of|determines?|attributable to)\b", re.I)
DESIGN = re.compile(r"random|experiment|\btrial\b|causal|instrumental|difference-in-difference|regression discontinuity|"
                    r"natural experiment|quasi-experiment|mendelian|synthetic control|event study|panel|longitudinal", re.I)
CONSENSUS = re.compile(
    r"\b(well[- ]established|widely (?:accepted|recogni[sz]ed|documented|reported)|(?:it is|it's) (?:well )?known|"
    r"(?:studies|research|evidence|literature) (?:consistently |repeatedly )?(?:show|shows|has shown|have shown|demonstrates?|confirms?)|"
    r"consistently (?:shown|found|demonstrated|reported)|a (?:large|growing|substantial|vast) body of|"
    r"there is (?:broad|strong|clear|robust|ample) (?:consensus|evidence|agreement)|numerous studies|many studies|"
    r"extensive (?:evidence|literature)|robustly (?:shown|established))\b", re.I)
PLURAL_EVIDENCE = re.compile(r"\b(studies|several|multiple|various|numerous|many|consistently|body of|literature|meta-analys)", re.I)
NUMBER = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)?)\s?(%|percent|per cent|-fold|fold)?")
CITATION_MARKERS = re.compile(r"\[[\d,\s–-]+\]|\([^()]*\b(?:1[89]\d{2}|20\d{2})[a-z]?[^()]*\)|\b(?:1[89]\d{2}|20\d{2})[a-z]?\b")
NULL_CUES = re.compile(r"no (?:significant |clear |robust )?(?:effect|association|difference|relationship|evidence|impact)|"
                       r"not (?:significantly )?(?:associated|related|correlated|linked)|did not (?:find|affect|change|improve|reduce|increase|predict)|"
                       r"null (?:result|finding|effect)|failed to|inconsistent|contrary to|smaller than (?:previously|expected)|"
                       r"weak(?:er)? (?:evidence|association|effect)", re.I)
BOUND_CUES = re.compile(r"\bonly (?:in|for|among|when|under)\b|limited to|depend(?:s|ed)? on|heterogene|moderated by", re.I)


class Ctx:
    """Everything a job needs: config, store, HTTP, model, progress callback."""

    def __init__(self, cfg, db=None, transport=None, progress=None):
        self.cfg = cfg
        self.db = db or DB(cfg.db_path)
        self.http = Http(cfg, self.db, transport)
        self.llm = LLM(cfg, self.http)
        self.progress = progress or (lambda msg: None)


def now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def new_run(kind, title, **extra):
    r = {"run_id": dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4], "kind": kind,
         "title": title, "created_at": now(), "validation": UNVALIDATED}
    r.update(extra)
    return r


def coverage_block():
    return {"sources_used": set(), "skipped": {}, "errors": [], "notes": []}


def finish_coverage(ctx, cov):
    cov["sources_used"] = sorted(cov["sources_used"])
    cov["http_calls"] = ctx.http.calls
    cov["http_errors"] = [f"{u} → {s}" for u, s in ctx.http.errors[-20:]]
    cov["model"] = ctx.llm.label or "none (heuristics only)"
    return cov


# --- citation statements --------------------------------------------------------

def gather_statements(ctx, work, max_items=100, deep=False, cov=None):
    """Citing sentences for one cited work, from Semantic Scholar and (deep) Europe PMC full text."""
    doi = work.get("doi")
    out, seen = [], set()

    def add(st):
        k = (st.get("citing_doi") or st.get("citing_title") or "", fold(st["context"])[:120])
        if k not in seen and len(st["context"]) > 20:
            seen.add(k)
            out.append(st)

    if not doi or max_items <= 0:
        return out
    s2_failed = False
    try:
        for row in s2.citations(ctx, doi, max_items):
            for c in row["contexts"]:
                add({"cited_doi": doi, "citing_doi": row["citing_doi"], "citing_title": row["citing_title"],
                     "citing_year": row["citing_year"], "context": squash(c), "section": None, "source": "semanticscholar",
                     "intents": row["intents"], "is_influential": row["is_influential"]})
        if cov is not None:
            cov["sources_used"].add("semanticscholar")
    except Exception as e:
        s2_failed = True
        if cov is not None:
            cov["errors"].append(f"semanticscholar citations {doi}: {e}"[:200])
    if deep or s2_failed:   # open-access full text needs no key, so it also stands in when Semantic Scholar refuses
        try:
            rec = {"epmc_id": work.get("epmc_id"), "epmc_source": work.get("epmc_source")}
            if not rec["epmc_id"]:
                rec = europepmc.find(ctx, doi) or {}
            target = {"doi": doi, "pmid": work.get("pmid"), "title": work.get("title")}
            for cp in europepmc.citing_open_access(ctx, rec, n=8 if deep else 3):   # fallback: a small sample
                for c in europepmc.fulltext_contexts(ctx, cp["pmcid"], target):
                    add({"cited_doi": doi, "citing_doi": cp["doi"], "citing_title": cp["title"], "citing_year": cp["year"],
                         "context": c["context"], "section": c["section"], "source": "europepmc-fulltext"})
            if cov is not None:
                cov["sources_used"].add("europepmc")
        except Exception as e:
            if cov is not None:
                cov["errors"].append(f"europepmc full text {doi}: {e}"[:200])
    return out


def note_rate_limits(ctx, cov):
    if ctx.http.tripped:
        cov["notes"].append(f"Rate-limited and skipped for the rest of the run: {', '.join(sorted(ctx.http.tripped))}. "
                            "Citation statements fell back to open-access full text in Europe PMC; "
                            "an S2_API_KEY (free) avoids this.")


def summarize_statements(statements):
    t = stance_mod.tally(statements)
    sections = {}
    for s in statements:
        sections[s.get("section") or "unknown"] = sections.get(s.get("section") or "unknown", 0) + 1
    intents = {}
    for s in statements:
        for i in s.get("intents") or []:
            intents[i] = intents.get(i, 0) + 1
    return {"tallies": t, "sections": sections, "intents": intents,
            "influential": sum(1 for s in statements if s.get("is_influential")),
            "citing_papers": len({s.get("citing_doi") or s.get("citing_title") for s in statements})}


def contested(t):
    classified = t["total"]
    return t["contrasting"] >= 2 and (t["contrasting"] >= 5 or t["contrasting"] / max(classified, 1) >= 0.10)


# --- claim-fit ---------------------------------------------------------------

def strip_markers(sentence):
    return re.sub(r"\s+([.,;:!?])", r"\1", squash(CITATION_MARKERS.sub(" ", sentence)))


def claim_type(claim):
    if CAUSAL.search(claim):
        return "causal"
    if NUMBER.search(strip_markers(claim)):
        return "performance"
    return "interpretation"


def heuristic_claim_flags(claim, work, single=True):
    """Cheap, transparent flags. Each is a question for the author, not a verdict."""
    flags = []
    text = strip_markers(claim)
    abstract = work.get("abstract") or ""
    source_text = f"{work.get('title') or ''} {abstract}"
    if abstract:
        spelled = spelled_numbers_to_digits(abstract)
        for m in NUMBER.finditer(text):
            num = m.group(1)
            # only figures that report a finding: percentages, n-fold, or counts of 100+ (not section numbers,
            # software versions such as 'SPSS 25' or 'Model 15', or small integers)
            if not m.group(2) and not (num.replace(",", "").isdigit() and int(num.replace(",", "")) >= 100):
                continue
            variants = {num, num.replace(",", "."), num.replace(".", ",")}
            if not any(v in abstract or v in spelled for v in variants):
                flags.append({"flag": "number_not_in_abstract", "detail": m.group(0).strip()})
        if CAUSAL.search(text) and not DESIGN.search(abstract):
            flags.append({"flag": "causal_wording_no_design", "detail": CAUSAL.search(text).group(0)})
    terms = content_terms(text)
    # a title alone is too short to judge overlap with a broad claim; only flag against an abstract
    # with several sources in one sentence each carries only part of it, so overlap is judged for sole sources
    if single and abstract and len(terms) >= 5 and not (terms & content_terms(source_text)):
        flags.append({"flag": "no_topical_overlap", "detail": "title+abstract"})
    return flags


VERDICTS = ("supported", "partially_supported", "overstated", "not_supported", "contradicted", "cannot_assess")


def judge_claims(ctx, items, works):
    """Model-based claim-fit on items that have an abstract. Quotes are checked against the abstract."""
    todo = [it for it in items if works[it["ref_no"]].get("abstract")]
    if not (ctx.llm.available and todo):
        return
    payload = []
    for it in todo:
        w = works[it["ref_no"]]
        payload.append({"id": it["kid"], "claim": strip_markers(it["claim"]),
                        "source": {"title": w.get("title"), "year": w.get("year"), "venue": w.get("venue"),
                                   "type": w.get("type")},
                        "abstract": w["abstract"][:4000]})
    try:
        res = ctx.llm.judge("claimfit", "Judge each item independently.", payload, batch=4)   # small batches stay under gateway timeouts (~30 s on NVIDIA)
    except Exception as e:
        for it in todo:
            it["model_error"] = str(e)[:200]
        return
    for it in todo:
        if it["kid"] in res:
            it.pop("model_error", None)
            apply_judgement(it, res.get(it["kid"]), works[it["ref_no"]], "llm:" + ctx.llm.model)
        elif ctx.llm.failures:
            it["model_error"] = ctx.llm.failures[0]


def rejudge(ctx, run_id):
    """Re-run the model on what an earlier run left to heuristics or unjudged, from stored data only."""
    import json as _json
    old = ctx.db.load_run(run_id)
    if not old or old["kind"] != "audit":
        raise ValueError(f"no audit run {run_id}")
    if not ctx.llm.available:
        raise ValueError("no model configured")
    run = dict(old, run_id=new_run("audit", old["title"])["run_id"], created_at=now(), parent_run=old["run_id"])
    works = {r["no"]: r.get("work") or {} for r in run["references"]}
    model = "llm:" + ctx.llm.model
    pending = []
    for r in run["references"]:
        w = works[r["no"]]
        if not w.get("doi"):
            continue
        rows = ctx.db.query("SELECT * FROM statements WHERE cited_doi=?", (w["doi"],))
        for x in rows:
            x["intents"] = _json.loads(x["intents"] or "[]")
        if rows and any(x["stance_method"] != model for x in rows):
            pending.append((r, w, rows))
    ctx.progress(f"Re-judging stance for {len(pending)} references, claim-fit for "
                 f"{sum(1 for k in run['claimfit'] if not k.get('verdict'))} pairs")

    def classify(item):
        stance_mod.classify(item[2], ctx.llm, item[1].get("title") or "")
    with ThreadPoolExecutor(max_workers=3) as ex:
        list(ex.map(classify, pending))
    for r, w, rows in pending:
        ctx.db.add_statements(rows)
        r["statements_summary"] = summarize_statements(rows)
        r["top_contrasting"] = [s for s in rows if s["stance"] == "contrasting"][:5]
        r["top_supporting"] = [s for s in rows if s["stance"] == "supporting"][:3]
    judge_claims(ctx, [k for k in run["claimfit"] if not k.get("verdict")], works)
    run["coverage"] = dict(run["coverage"], model=ctx.llm.label,
                           claim_pairs_judged=sum(1 for k in run["claimfit"] if k.get("verdict")))
    run["findings"] = carry_status(old["findings"], build_findings(run))
    run["summary"] = summarize_run(run)
    ctx.db.save_run(run)
    return run


def apply_judgement(it, r, work, method):
    if not r or r.get("verdict") not in VERDICTS:
        return
    quote = (r.get("quote") or "").strip()
    it.update({"verdict": r["verdict"], "method": method, "quote": quote,
               "quote_verified": quote_in(quote, work.get("abstract") or "") if quote else False,
               "strongest_supported": r.get("strongest_supported") or "", "overclaim": r.get("overclaim") or ""})
    if it["verdict"] == "supported" and it["claim_type"] in ("causal", "performance") and work.get("access") != "fulltext":
        it["ceiling_note"] = ("abstract-level fit only; a causal or quantitative claim needs the full text "
                              "before it can be treated as carried by this source")


# --- independence ------------------------------------------------------------

def _author_keys(w):
    """Name keys for every author, plus ORCID keys where the registry has them."""
    keys = set()
    for a in w.get("authors") or []:
        fam = fold(a.get("family") or "").strip()
        if fam:
            keys.add(("name", f"{fam} {fold(a.get('given') or '')[:1]}"))
        if a.get("orcid"):
            keys.add(("orcid", a["orcid"]))
    keys |= {("orcid", i) for i in w.get("author_ids") or []}
    return keys


def independence(citation, works):
    nos = [n for n in citation["ref_nos"] if works.get(n) and works[n].get("doi")]
    if len(nos) < 2:
        return None
    parent = {n: n for n in nos}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    edges = []
    for i, a in enumerate(nos):
        for b in nos[i + 1:]:
            wa, wb = works[a], works[b]
            shared = _author_keys(wa) & _author_keys(wb)
            if shared:
                orcids = sorted(v for k, v in shared if k == "orcid")
                names = sorted(v for k, v in shared if k == "name")
                edges.append({"a": a, "b": b, "kind": "shared_author", "strong": bool(orcids),
                              "evidence": (f"ORCID {', '.join(orcids)}; " if orcids else "") + ", ".join(names)[:120]})
            ra, rb = set(wa.get("references") or []), set(wb.get("references") or [])
            if wb["doi"] in ra or wa["doi"] in rb or (wa.get("openalex_id") and wa["openalex_id"] in (wb.get("referenced_works") or [])) \
                    or (wb.get("openalex_id") and wb["openalex_id"] in (wa.get("referenced_works") or [])):
                src, dst = (a, b) if wb["doi"] in ra else (b, a)
                edges.append({"a": a, "b": b, "kind": "direct_citation", "strong": True,
                              "evidence": f"[{src}] cites [{dst}] in its registry reference list"})
            if len(ra & rb) >= 5 and jaccard(ra, rb) >= 0.25:
                edges.append({"a": a, "b": b, "kind": "shared_references", "strong": False,
                              "evidence": f"{len(ra & rb)} shared references (Jaccard {jaccard(ra, rb):.2f})"})
    for e in edges:
        # only a shared author team makes two references one line of evidence; a later paper citing an
        # earlier one (or sharing its bibliography) is ordinary scholarly lineage, recorded but not merged
        if e["kind"] == "shared_author":
            parent[find(e["a"])] = find(e["b"])
    groups = {}
    for n in nos:
        groups.setdefault(find(n), []).append(n)
    return {"cid": citation["cid"], "ref_nos": nos, "lines": len(groups), "groups": sorted(groups.values()), "edges": edges}


# --- findings ----------------------------------------------------------------

def F(check, location, defect, evidence, consequence, severity, confidence, repair, method="metadata", pass_=PASS, **extra):
    f = {"check": check, "pass": pass_, "location": location, "defect": defect, "evidence": evidence,
         "consequence": consequence, "severity": severity, "confidence": confidence, "min_repair": repair,
         "status": "Open", "method": method}
    f.update(extra)
    return f


def build_findings(result):
    refs = {r["no"]: r for r in result["references"]}
    cites = {c["cid"]: c for c in result["citations"]}
    out = []

    def ref_loc(r):
        where = [cites[c]["location"] for c in r.get("cited_in", [])][:2]
        return f"Reference [{r['no']}] {ref_label(r)}" + (f"; cited at {', '.join(where)}" if where else "")

    for r in result["references"]:
        w, st, loc = r.get("work") or {}, r["status"], ref_loc(r)
        # existence and accuracy
        if st == "doi_mismatch":
            out.append(F("existence", loc, "The DOI given resolves to a different work",
                         f"Reference: \"{r['raw'][:160]}\"; DOI {r['doi_in_text']} is registered to \"{w.get('title')}\" ({work_label(w)})",
                         "A reader or editor following the DOI lands on an unrelated paper; this is a common trace of AI-drafted references",
                         "Major", "Confirmed", "Replace the DOI with the correct one, or correct the reference to the work the DOI names"))
        elif st == "doi_unknown":
            out.append(F("existence", loc, "The DOI given is not registered, and no matching record was found by bibliographic search",
                         f"Reference: \"{r['raw'][:160]}\"; doi.org handle lookup returned no record for {r['doi_in_text']}",
                         "If the work does not exist, the manuscript cites a fabricated source; a desk editor who clicks it will notice",
                         "Major", "Probable", "Locate the source in hand and correct the DOI, or remove the reference and any claim resting on it"))
        elif st == "not_found":
            grey = (r.get("match") or {}).get("grey_hint")
            out.append(F("existence", loc,
                         "No registry record matches this reference" + (" (it looks like grey literature)" if grey else ""),
                         f"Reference: \"{r['raw'][:160]}\"; best Crossref title fit {(r.get('match') or {}).get('best_title_fit', 0)}"
                         + (f"; closest record \"{r['match']['closest']['title']}\" by {r['match']['closest']['first_author']} "
                            f"({r['match']['closest']['doi']}) has a different author team" if (r.get('match') or {}).get('closest') else ""),
                         "Unverifiable references cannot be checked by reviewers; fabricated references look exactly like this",
                         "Minor" if grey else "Moderate", "Question",
                         "Confirm the source exists (URL, ISBN, or archive link) and add a persistent identifier"))
        elif st == "mismatch":
            diffs = (r.get("match") or {}).get("diffs", [])
            text = "; ".join(f"{d['field']}: manuscript '{d['manuscript']}' vs record '{d['record']}'" for d in diffs)
            minor = all((d["field"] == "first_author" and d.get("is_coauthor")) or d.get("off_by_one") for d in diffs)
            year1 = any(d.get("off_by_one") for d in diffs)
            # a book found by search may match a reprint, edition or reference-work entry with its own year
            edition = ((r.get("match") or {}).get("method") == "search" and all(d["field"] == "year" for d in diffs)
                       and w.get("type") not in ("journal-article", "proceedings-article", "posted-content"))
            out.append(F("accuracy", loc, "The reference year differs from the closest registry record (an edition or reprint?)" if edition
                         else "The reference misstates details of the work it points to",
                         f"{text} (Crossref record {w.get('doi')}, type {w.get('type')}: \"{w.get('title')}\")",
                         "Citation errors signal unchecked references to reviewers and can send readers to the wrong edition or paper"
                         + ("; a one-year difference can also be online-first versus print year" if year1 else ""),
                         "Minor" if (minor or edition) else "Moderate", "Question" if edition else "Confirmed",
                         "Check which edition is cited and give its year" if edition else "Correct the reference fields to match the registry record"))
        elif st == "probable":
            out.append(F("accuracy", loc, "Only a partial registry match was found for this reference",
                         f"Closest record: \"{w.get('title')}\" ({work_label(w)}, {w.get('doi')}); title fit {(r.get('match') or {}).get('title_fit')}",
                         "The reference may be garbled or may point to a different work than intended",
                         "Minor", "Question", "Check the reference against the source and add its DOI"))
        elif st == "doi_elsewhere":
            out.append(F("accuracy", loc, "The DOI is registered outside Crossref, so its metadata was not compared",
                         f"doi.org resolves {r['doi_in_text']}; Crossref holds no record (typical for DataCite: datasets, Zenodo, arXiv)",
                         "Reference details are unverified by this run", "Minor", "Question", "Check the reference details at the DOI landing page"))
        # editorial status
        cited_text = " ".join(cites[c]["sentence"] for c in r.get("cited_in", []))
        for n in w.get("notices") or []:
            ev = f"{n['label']} notice {n.get('doi') or ''} ({n.get('date') or 'date n/a'}; source: {n['source']})"
            if n["type"] in ("retraction", "withdrawal", "removal"):
                acknowledged = "retract" in cited_text.lower()
                out.append(F("retraction", loc, "The cited work has been retracted" + (" (the citing sentence mentions the retraction)" if acknowledged else ""),
                             ev, "A claim resting on retracted work is a standard ground for rejection and, after publication, for correction",
                             "Minor" if acknowledged else "Major", "Confirmed",
                             "Mark the reference as retracted" if acknowledged else "Remove the reliance on this work, or cite it only as retracted with the notice"))
                break
            if n["type"] in ("expression_of_concern", "partial_retraction"):
                out.append(F("retraction", loc, f"The cited work carries a {n['label'].lower()}", ev,
                             "The finding the manuscript relies on may not stand", "Moderate", "Confirmed",
                             "Read the notice and state in the text how it affects the cited finding"))
            elif n["type"] in ("correction", "corrigendum", "erratum"):
                out.append(F("retraction", loc, f"The cited work has a published {n['label'].lower()}", ev,
                             "The cited number or result may have changed after correction", "Minor", "Confirmed",
                             "Check that the manuscript cites the corrected result"))
        # preprints
        pub = (w.get("preprint_of") or [None])[0] or w.get("published_doi")
        if pub:
            out.append(F("supersession", loc, "A preprint is cited although a published version exists",
                         f"Registry links preprint {w.get('doi')} to published version {pub}",
                         "Reviewers expect the version of record; the published version may differ in results",
                         "Minor", "Confirmed", f"Cite the published version (doi:{pub}) and check the cited result still holds"))
        elif w.get("type") == "posted-content":
            out.append(F("supersession", loc, "A preprint is cited with no published version found",
                         f"Crossref type 'posted-content' for {w.get('doi')}; no is-preprint-of relation",
                         "A preprint carries supporting weight only; it cannot be the evidence spine for a claim",
                         "Minor", "Question", "Check for a peer-reviewed version; if none, bound the claim's wording"))
        # contested evidence
        sm = r.get("statements_summary")
        if sm and contested(sm["tallies"]):
            t = sm["tallies"]
            top = r.get("top_contrasting", [])[:2]
            quotes = " | ".join(f"\"{c['context'][:220]}\" ({c.get('citing_doi') or c.get('citing_title')})" for c in top)
            out.append(F("contested", loc, "The cited work is contested in the literature that cites it",
                         f"{t['contrasting']} contrasting / {t['supporting']} supporting / {t['mentioning']} mentioning citation statements "
                         f"(method: {', '.join(t['methods'])}). Examples: {quotes}",
                         "A reviewer who knows the contrasting work will read an unqualified citation as selective",
                         "Moderate", "Question" if t["methods"] == ["heuristic"] else "Probable",
                         "Read the contrasting studies listed in the run and acknowledge them, or bound the claim",
                         method=", ".join(t["methods"])))
        # second-order retraction exposure
        for rw in r.get("cites_retracted", [])[:3]:
            out.append(F("retraction-2nd-order", loc, "The cited work itself relies on a retracted work",
                         f"OpenAlex lists {rw.get('doi') or rw['openalex_id']} (\"{(rw.get('title') or '')[:100]}\") as retracted and cited by {w.get('doi')}",
                         "If the cited finding depends on the retracted work, it may not stand", "Minor", "Question",
                         "Check whether the cited finding depends on the retracted work", method="openalex"))
    # claim-level
    for c in result["citations"]:
        for yc in c.get("year_conflicts", []):
            w = refs[yc["ref_no"]].get("work") or {}
            reg = f"; the registry record says {w['year']}" if w.get("year") else ""
            out.append(F("accuracy", c["location"], "The in-text citation and the reference list give different years",
                         f"Text cites {yc['text']} in: \"{c['sentence'][:200]}\"; reference [{yc['ref_no']}] gives {yc['list_year']}{reg}",
                         "Readers matching citation to reference hit a mismatch; editors and reference-check tools flag it",
                         "Minor", "Confirmed", "Use the same year in the text and the list" + (f" ({w['year']}, per the registry)" if w.get("year") else ""),
                         method="manuscript"))
        if c.get("dangling"):
            out.append(F("dangling", c["location"], "In-text citation with no matching entry in the reference list",
                         f"Citation {', '.join(c['dangling'])} in: \"{c['sentence'][:200]}\"",
                         "A reader cannot find the source; editors return manuscripts for this", "Minor", "Confirmed",
                         "Add the reference or correct the in-text citation", method="manuscript"))
        m = CONSENSUS.search(c["sentence"])
        if m and len(c["ref_nos"]) <= 1:
            out.append(F("calibration", c["location"], "Consensus wording is backed by a single reference",
                         f"\"{m.group(0)}\" in: \"{c['sentence'][:220]}\" — {len(c['ref_nos'])} reference cited",
                         "A reviewer reads consensus wording as a claim about the literature; one source cannot carry it",
                         "Moderate", "Confirmed", "Cite the independent lines of evidence, or reword to what the one source shows",
                         method="manuscript"))
    for k in result["claimfit"]:
        r = refs[k["ref_no"]]
        loc = f"{cites[k['cid']]['location']} → [{k['ref_no']}] {ref_label(r)}"
        claim_q = f"Claim: \"{strip_markers(k['claim'])[:220]}\""
        v = k.get("verdict")
        if v in ("overstated", "not_supported", "contradicted", "partially_supported"):
            conf = "Probable" if k.get("quote_verified") else "Question"
            quote = f"; source abstract: \"{k['quote'][:240]}\"" + ("" if k.get("quote_verified") else " [quote NOT found verbatim in abstract]") if k.get("quote") else ""
            sev = {"contradicted": "Major", "not_supported": "Moderate", "overstated": "Moderate", "partially_supported": "Minor"}[v]
            defect = {"contradicted": "The cited source reports the opposite of the claim",
                      "not_supported": "The cited source does not appear to carry this claim",
                      "overstated": "The claim is stronger than what the cited source reports",
                      "partially_supported": "The claim goes beyond what the cited source reports"}[v]
            repair = f"Bound the claim to what the source carries: \"{k.get('strongest_supported')}\"" if k.get("strongest_supported") \
                else "Re-read the source and align the claim with it, or cite a source that carries it"
            out.append(F("claim-fit", loc, defect, claim_q + quote + (f"; overclaim: {k['overclaim']}" if k.get("overclaim") else ""),
                         "A reviewer who checks this citation will question every other one", sev, conf, repair,
                         method=k.get("method"), item=k["kid"]))
        for fl in k.get("flags", []):
            if fl["flag"] == "number_not_in_abstract":
                out.append(F("claim-fit", loc, f"The figure '{fl['detail']}' does not appear in the cited source's abstract",
                             claim_q + "; abstract retrieved and searched", "The number may come from the full text, another source, or an error",
                             "Minor", "Question", "Confirm the figure against the source's full text and give the page or table",
                             method="heuristic", item=k["kid"]))
            elif fl["flag"] == "causal_wording_no_design":
                out.append(F("claim-fit", loc, f"Causal wording ('{fl['detail']}') attributed to a source whose abstract reports no causal design",
                             claim_q + "; no experimental, quasi-experimental or longitudinal design term in the abstract",
                             "Reviewers attack causal language on correlational evidence first", "Moderate", "Question",
                             "Use associational wording, or confirm the source's identification strategy", method="heuristic", item=k["kid"]))
            elif fl["flag"] == "no_topical_overlap" and not v:
                out.append(F("claim-fit", loc, "The claim shares no content terms with the cited source's " + fl["detail"],
                             claim_q + f"; source: \"{(r.get('work') or {}).get('title')}\"",
                             "The citation may be misplaced (a common trace of reference-list drift)", "Moderate", "Question",
                             "Check that this is the intended source for the claim", method="heuristic", item=k["kid"]))
    for ind in result["independence"]:
        n = len(ind["ref_nos"])
        if ind["lines"] >= n:
            continue
        c = cites[ind["cid"]]
        author_edges = [e for e in ind["edges"] if e["kind"] == "shared_author"]
        strong = all(e["strong"] for e in author_edges)
        plural = bool(PLURAL_EVIDENCE.search(c["sentence"]))
        ev = "; ".join(f"[{e['a']}]–[{e['b']}] {e['kind'].replace('_', ' ')}: {e['evidence']}" for e in ind["edges"])
        out.append(F("independence", c["location"],
                     f"{n} references cited together form {ind['lines']} independent line{'s' if ind['lines'] > 1 else ''} of evidence",
                     f"Claim: \"{strip_markers(c['sentence'])[:200]}\"; {ev}",
                     "Repetition across dependent sources is not confirmation; a reviewer can collapse the support to one line",
                     "Moderate" if (plural or n >= 3) else "Minor", "Confirmed" if strong else "Probable",
                     "Word the claim as resting on one line of evidence, or add an independent source", method="metadata"))
    cited = {n for c in result["citations"] for n in c["ref_nos"]}
    if len(result["citations"]) >= 3:
        for r in result["references"]:
            if r["no"] not in cited:
                out.append(F("uncited", f"Reference [{r['no']}] {ref_label(r)}", "Reference list entry is never cited in the text",
                             f"No in-text citation matched \"{r['raw'][:120]}\" (style: {result['coverage']['citation_style']})",
                             "Editors return manuscripts with orphan references", "Minor", "Probable",
                             "Cite it where it is used, or delete it", method="manuscript"))
    if result.get("xrefs"):
        out += xrefs_mod.to_findings(result["xrefs"], F)
    return number_findings(out)


def number_findings(out):
    """Sort by severity; IDs count per pass (REF-01 … for references, XREF-01 … for cross-references)."""
    out.sort(key=lambda f: (SEV_ORDER[f["severity"]], f["check"], f["location"]))
    labels = pass_labels()
    back = {label: key for key, (label, _) in labels.items()}
    counters = {}
    for f in out:
        key = back.get(f["pass"], f["pass"])
        f["pass"], tag = labels.get(key, (key, key))
        counters[tag] = counters.get(tag, 0) + 1
        f["id"] = f"{tag}-{counters[tag]:02d}"
    return out


# --- jobs ----------------------------------------------------------------------

def audit(ctx, text, title="manuscript", bib_text=None, contexts=100, deep=False, workers=4):
    run = new_run("audit", title, sha256=hashlib.sha256(text.encode()).hexdigest(),
                  settings={"contexts": contexts, "deep": deep, "model": ctx.llm.label})
    cov = coverage_block()
    p = parse_manuscript(text, bib_text)
    refs, citations = p["references"], p["citations"]
    ctx.progress(f"Parsed {len(refs)} references and {len(citations)} citing sentences ({p['style']} style)")
    if not refs:
        cov["notes"].append("No reference list found: add a 'References' heading, or pass a .bib file")

    def work_on(ref):
        try:
            res = resolve(ctx, ref)
            cov["sources_used"].add("crossref")
            if res["work"]:
                enrich(ctx, res["work"], cov)
            ref.update(res)
        except Exception as e:
            ref.update({"status": "error", "work": None, "match": {}, "error": str(e)[:200]})
            cov["errors"].append(f"resolve [{ref['no']}]: {e}"[:200])
        ctx.progress(f"[{ref['no']}] {ref['status']}")
        return ref

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(work_on, refs))
    works = {r["no"]: (r.get("work") or {}) for r in refs}
    for c in citations:
        for n in c["ref_nos"]:
            ref = next((r for r in refs if r["no"] == n), None)
            if ref is not None:
                ref.setdefault("cited_in", []).append(c["cid"])
    # citation statements (the smart-citation layer)
    if contexts > 0:
        gathered = []
        for r in refs:   # fetching is paced by the registries' rate limits, so it stays sequential
            w = works[r["no"]]
            if not w.get("doi"):
                continue
            ctx.progress(f"[{r['no']}] citation statements")
            gathered.append((r, w, gather_statements(ctx, w, contexts, deep, cov)))
        ctx.progress(f"Classifying {sum(len(g[2]) for g in gathered)} citation statements")

        def classify(item):
            stance_mod.classify(item[2], ctx.llm, item[1].get("title") or "")
        with ThreadPoolExecutor(max_workers=6 if ctx.llm.available else 1) as ex:   # model calls in parallel
            list(ex.map(classify, gathered))
        for r, w, sts in gathered:
            if sts and sts[0].get("stance_error"):
                cov["errors"].append(f"model (stance) [{r['no']}]: {sts[0]['stance_error']} — heuristics used")
            ctx.db.add_statements(sts)
            r["statements_summary"] = summarize_statements(sts)
            r["top_contrasting"] = [s for s in sts if s["stance"] == "contrasting"][:5]
            r["top_supporting"] = [s for s in sts if s["stance"] == "supporting"][:3]
    # second-order retraction exposure
    if deep and openalex.enabled(ctx):
        for r in refs:
            w = works[r["no"]]
            if w.get("referenced_works"):
                r["cites_retracted"] = openalex.retracted_among(ctx, w["referenced_works"])
    elif deep:
        cov["skipped"]["second-order retraction"] = "needs OPENALEX_API_KEY"
    # claim-fit items: one per (citing sentence, reference) pair
    items = []
    wrong_work = {r["no"] for r in refs if r.get("status") in ("doi_mismatch", "not_found", "error")}
    for c in citations:
        for n in c["ref_nos"]:
            w = works.get(n) or {}
            if not w or n in wrong_work:
                continue
            items.append({"kid": f"K{len(items) + 1:03d}", "cid": c["cid"], "ref_no": n, "claim": c["sentence"],
                          "claim_type": claim_type(c["sentence"]), "access": w.get("access", "metadata-only"),
                          "flags": heuristic_claim_flags(c["sentence"], w, single=len(c["ref_nos"]) == 1) if w.get("doi") else []})
    ctx.progress(f"Claim-fit: {len(items)} claim–source pairs")
    judge_claims(ctx, items, works)
    errs = {i["model_error"] for i in items if i.get("model_error")}
    cov["errors"] += [f"model (claim-fit): {e} — heuristic flags only" for e in errs]
    run["xrefs"] = xrefs_mod.analyze(text)
    cov["notes"] += run["xrefs"]["notes"]
    run.update({"references": refs, "citations": citations, "claimfit": items,
                "independence": [x for x in (independence(c, works) for c in citations) if x]})
    note_rate_limits(ctx, cov)
    run["coverage"] = finish_coverage(ctx, dict(cov, references_parsed=len(refs), citations_found=len(citations),
                                                 citation_style=p["style"],
                                                 resolved=sum(1 for r in refs if r.get("work")),
                                                 with_abstract=sum(1 for w in works.values() if w.get("abstract")),
                                                 claim_pairs=len(items),
                                                 claim_pairs_judged=sum(1 for i in items if i.get("verdict"))))
    unresolved = sum(1 for r in refs if r.get("status") == "error")
    if unresolved:
        run["coverage"]["notes"].insert(0, f"{unresolved} of {len(refs)} references could not be checked because the registries "
                                           "were unreachable; those references are unaudited, not clean. Run `refwarden doctor`.")
    if not ctx.llm.available:
        run["coverage"]["notes"].append(
            "No model configured: claim-fit verdicts are not produced. Export a judgement packet "
            "(`refwarden packet RUN_ID`) and run it in any AI harness, then `refwarden submit`.")
    run["findings"] = build_findings(run)
    run["summary"] = summarize_run(run)
    ctx.db.save_run(run)
    return run


def summarize_run(run):
    s = {"references": len(run["references"]), "findings": len(run["findings"])}
    for r in run["references"]:
        s[r["status"]] = s.get(r["status"], 0) + 1
    s["retracted"] = sum(1 for r in run["references"] if (r.get("work") or {}).get("is_retracted"))
    for f in run["findings"]:
        s["sev_" + f["severity"]] = s.get("sev_" + f["severity"], 0) + 1
    return s


def crossref_check(ctx, text, title="manuscript"):
    """Offline job: internal cross-references only (figures, tables, sections, appendices, equations)."""
    run = new_run("xrefs", title, sha256=hashlib.sha256(text.encode()).hexdigest())
    run["xrefs"] = xrefs_mod.analyze(text)
    run["coverage"] = {"notes": run["xrefs"]["notes"], "model": "none (rule-based)"}
    run["findings"] = number_findings(xrefs_mod.to_findings(run["xrefs"], F))
    run["summary"] = {"findings": len(run["findings"])}
    ctx.db.save_run(run)
    return run


def paper_report(ctx, doi, contexts=200, deep=False):
    from .textutil import clean_doi
    doi = clean_doi(doi)
    cov = coverage_block()
    w = crossref.work(ctx, doi)
    if w:
        cov["sources_used"].add("crossref")
    else:
        sp = s2.paper(ctx, doi)
        if not sp:
            raise ValueError(f"DOI {doi} not found in Crossref or Semantic Scholar")
        w = dict(sp, doi=doi, notices=[], is_retracted=False, sources=["semanticscholar"])
    enrich(ctx, w, cov)
    ctx.progress(f"{work_label(w)}: fetching citation statements")
    sts = gather_statements(ctx, w, contexts, deep, cov)
    stance_mod.classify(sts, ctx.llm, w.get("title") or "")
    ctx.db.add_statements(sts)
    run = new_run("paper", work_label(w) + " " + (w.get("title") or ""), settings={"contexts": contexts, "deep": deep},
                  work=w, statements=sts, summary=summarize_statements(sts))
    run["coverage"] = finish_coverage(ctx, cov)
    run["findings"] = []
    ctx.db.save_run(run)
    return run


def counter_evidence(ctx, claim, n=15):
    """Disconfirmation search: find candidate papers whose own findings contradict or bound a claim."""
    cov = coverage_block()
    terms = sorted(content_terms(claim), key=lambda t: -len(t))[:8]
    q = " ".join(terms)
    cands = []
    for name, fn, query in (("semanticscholar", s2.search, q),
                            ("europepmc", europepmc.search,
                             f'({q}) AND ("no effect" OR "no association" OR "not associated" OR "failed to replicate" OR "null")'),
                            ("openalex", openalex.search, q)):
        if name == "openalex" and not openalex.enabled(ctx):
            cov["skipped"]["openalex"] = "no OPENALEX_API_KEY"
            continue
        try:
            got = fn(ctx, query, n)
            cov["sources_used"].add(name)
            for g in got:
                g["found_via"] = name
            cands.extend(got)
        except Exception as e:
            cov["errors"].append(f"{name}: {e}"[:200])
    seen, uniq = set(), []
    for c in cands:
        k = c.get("doi") or fold(c.get("title") or "")[:80]
        if k and k not in seen and c.get("abstract"):
            seen.add(k)
            uniq.append(c)
    items = []
    for i, c in enumerate(uniq, 1):
        rel = containment(claim, f"{c.get('title')} {c.get('abstract')}")
        it = {"id": f"X{i:02d}", "doi": c.get("doi"), "title": c.get("title"), "year": c.get("year"),
              "found_via": c["found_via"], "relevance": round(rel, 2), "relation": None, "quote": "", "method": "heuristic"}
        ab = c["abstract"]
        for a, b in sentence_spans(ab):
            s = ab[a:b]
            if containment(claim, s) >= 0.25:
                if NULL_CUES.search(s):
                    it.update(relation="contradicts?", quote=squash(s))
                    break
                if BOUND_CUES.search(s) and not it["relation"]:
                    it.update(relation="qualifies?", quote=squash(s))
        items.append((it, ab))
    if ctx.llm.available and items:
        payload = [{"id": it["id"], "title": it["title"], "abstract": ab[:3000]} for it, ab in items]
        try:
            res = ctx.llm.judge("counter", f"CLAIM: {claim}", payload, batch=10)
            for it, ab in items:
                r = res.get(it["id"])
                if r and r.get("relation"):
                    q = (r.get("quote") or "").strip()
                    it.update(relation=r["relation"], quote=q, quote_verified=quote_in(q, ab) if q else False,
                              method="llm:" + ctx.llm.model)
        except Exception as e:
            cov["errors"].append(f"model: {e}"[:200])
    order = {"contradicts": 0, "contradicts?": 1, "qualifies": 2, "qualifies?": 3}
    out = sorted((it for it, _ in items), key=lambda it: (order.get(it["relation"], 9), -it["relevance"]))
    run = new_run("counter", claim[:120], settings={"n": n, "query": q}, claim=claim, candidates=out)
    run["coverage"] = finish_coverage(ctx, cov)
    run["coverage"]["notes"].append("A disconfirmation screen, not a systematic search: coverage is the top "
                                    f"{n} hits per source for the query '{q}'.")
    run["findings"] = []
    ctx.db.save_run(run)
    return run


def recheck(ctx, run_id):
    """Pre-submission sweep: re-query editorial status of every resolved reference, bypassing the cache."""
    old = ctx.db.load_run(run_id)
    if not old or old["kind"] != "audit":
        raise ValueError(f"no audit run {run_id}")
    changes = []
    for r in old["references"]:
        w = r.get("work") or {}
        if not w.get("doi"):
            continue
        fresh = crossref.work(ctx, w["doi"], max_age=0)
        if not fresh:
            continue
        before = {(n["type"], n.get("doi")) for n in w.get("notices") or []}
        after = {(n["type"], n.get("doi")) for n in fresh.get("notices") or []}
        new = after - before
        if new or fresh.get("preprint_of") != w.get("preprint_of"):
            changes.append({"ref_no": r["no"], "doi": w["doi"], "new_notices": sorted(new),
                            "published_version": fresh.get("preprint_of")})
            w["notices"], w["preprint_of"] = fresh["notices"], fresh.get("preprint_of")
            w["is_retracted"] = w.get("is_retracted") or fresh["is_retracted"]
    run = dict(old, run_id=new_run("audit", old["title"])["run_id"], created_at=now(), parent_run=old["run_id"],
               recheck={"checked_at": now(), "changes": changes})
    run["findings"] = carry_status(old["findings"], build_findings(run))
    run["summary"] = summarize_run(run)
    ctx.db.save_run(run)
    return run


def carry_status(old_findings, new_findings):
    """Author dispositions survive a re-run; accepted risks are not reopened without new evidence."""
    prev = {(f["check"], f["location"], f["defect"]): f["status"] for f in old_findings}
    for f in new_findings:
        f["status"] = prev.get((f["check"], f["location"], f["defect"]), f["status"])
    return new_findings
