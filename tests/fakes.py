"""Offline registry: responses shaped like Crossref, Semantic Scholar, Europe PMC and doi.org.

Shapes follow the public API documentation; the records are fictional (DOI prefix 10.5555 is
Crossref's test prefix). A live smoke test is `python -m refwarden doctor`.
"""
import json
import urllib.parse


def cr(doi, title, authors, year, venue="Test Journal", abstract=None, typ="journal-article", refs=(), **extra):
    m = {"DOI": doi.upper(), "title": [title], "type": typ, "container-title": [venue],
         "author": [{"family": f, "given": g, **({"ORCID": f"https://orcid.org/{o}"} if o else {})} for f, g, o in authors],
         "issued": {"date-parts": [[year]]}, "reference": [{"key": f"r{i}", "DOI": d} for i, d in enumerate(refs)],
         "reference-count": len(refs), "is-referenced-by-count": 10, "URL": f"https://doi.org/{doi}"}
    if abstract:
        m["abstract"] = f"<jats:p>{abstract}</jats:p>"
    m.update(extra)
    return m


CROSSREF = {
    "10.5555/jsw.2021.001": cr("10.5555/jsw.2021.001", "AI assistance and review speed in journal workflows",
                               [("Alvarez", "M.", None)], 2021, "Journal of Scholarly Workflow",
                               "We compare review turnaround in 40 journals before and after adopting AI assistance. "
                               "Turnaround fell in journals that adopted assistance, but review quality was not measured."),
    "10.5555/rs.2020.007": cr("10.5555/rs.2020.007", "Automation bias among peer reviewers",
                              [("Berg", "K.", "0000-0001-0000-0001"), ("Ncube", "T.", None)], 2020, "Review Science",
                              "In a survey of 300 reviewers, respondents reported accepting automated suggestions without verification."),
    "10.5555/rs.2022.009": cr("10.5555/rs.2022.009", "Automation bias revisited: reviewer trust in model suggestions",
                              [("Berg", "K.", "0000-0001-0000-0001"), ("Ncube", "T.", None), ("Park", "S.", None)], 2022,
                              "Review Science", "A follow-up survey of the same reviewer panel finds reviewers accept model suggestions.",
                              refs=["10.5555/rs.2020.007"]),
    "10.5555/ml.2019.002": cr("10.5555/ml.2019.002", "Reviewer reliance on automated checks", [("Chen", "L.", None)], 2019,
                              "Metascience Letters", "Log data show reviewers skip manual checks when automated checks pass."),
    "10.5555/okafor.2016": cr("10.5555/okafor.2016", "Fatigue and error detection in manuscript review",
                              [("Okafor", "A.", None), ("Li", "W.", None), ("Brandt", "H.", None)], 2016, "Quality in Publishing",
                              "Cross-sectional data from 120 reviewers show that longer review sessions are associated with fewer detected errors."),
    "10.5555/sssr.2017.003": cr("10.5555/sssr.2017.003", "Soil nitrogen dynamics under alpine grazing", [("Moser", "U.", None)], 2017,
                                "Alpine Ecology"),
    "10.5555/preprint.2022.010": cr("10.5555/preprint.2022.010", "Larger effects of AI assistance in review: preliminary evidence",
                                    [("Ivanova", "E.", None)], 2022, "MetaArXiv", typ="posted-content",
                                    relation={"is-preprint-of": [{"id-type": "doi", "id": "10.5555/pub.2023.011", "asserted-by": "subject"}]}),
    "10.5555/eos.2016.008": cr("10.5555/eos.2016.008", "Incentives in peer review: a structural account", [("Jansen", "P.", None)], 2016,
                               "Economics of Science",
                               **{"updated-by": [{"DOI": "10.5555/eos.2019.retr", "type": "retraction", "source": "retraction-watch",
                                                  "label": "Retraction", "updated": {"date-parts": [[2019, 5, 2]]}}]}),
    "10.5555/pq.2014.005": cr("10.5555/pq.2014.005", "An uncited paper on journal peer review", [("Kowalski", "Z.", None)], 2014,
                              "Publishing Quarterly"),
}

SEARCH = {  # query keyword -> DOIs returned by query.bibliographic
    "okafor": ["10.5555/okafor.2016", "10.5555/ml.2019.002"],
    "dimitrov": ["10.5555/sssr.2017.003"],
}

S2_CITATIONS = {
    "10.5555/jsw.2021.001": [
        {"contexts": ["In contrast to Alvarez (2021), we find no reduction in turnaround once submission volume is controlled."],
         "intents": ["result"], "isInfluential": True, "citingPaper": {"title": "Volume, not tools", "year": 2023, "externalIds": {"DOI": "10.5555/c1"}}},
        {"contexts": ["We failed to replicate the speed gains reported by Alvarez."],
         "intents": ["result"], "isInfluential": False, "citingPaper": {"title": "A replication attempt", "year": 2024, "externalIds": {"DOI": "10.5555/c2"}}},
        {"contexts": ["Our estimates are consistent with Alvarez (2021)."],
         "intents": ["result"], "isInfluential": False, "citingPaper": {"title": "Speed in physics journals", "year": 2022, "externalIds": {"DOI": "10.5555/c3"}}},
        {"contexts": ["AI tools are now used in editorial offices (Alvarez 2021)."],
         "intents": ["background"], "isInfluential": False, "citingPaper": {"title": "Editorial tooling", "year": 2022, "externalIds": {"DOI": "10.5555/c4"}}},
        {"contexts": ["The speed gains reported earlier are not consistent with our registry data."],
         "intents": ["result"], "isInfluential": False, "citingPaper": {"title": "Registry evidence", "year": 2024, "externalIds": {"DOI": "10.5555/c5"}}},
    ],
}

JATS = """<?xml version="1.0"?><article><body>
<sec><title>Introduction</title><p>Assistance tools spread quickly <xref ref-type="bibr" rid="B1">1</xref>. Unrelated sentence here.</p></sec>
<sec><title>Discussion</title><p>Unlike earlier reports <xref ref-type="bibr" rid="B1">1</xref>, we see no speed gain. See also <xref ref-type="bibr" rid="B2">2</xref>.</p></sec>
</body><back><ref-list>
<ref id="B1"><element-citation><article-title>AI assistance and review speed</article-title><pub-id pub-id-type="doi">10.5555/JSW.2021.001</pub-id></element-citation></ref>
<ref id="B2"><element-citation><article-title>Something else</article-title></element-citation></ref>
</ref-list></back></article>"""


class Fake:
    def __init__(self, llm_reply=None):
        self.llm_reply = llm_reply     # callable(body_dict) -> reply text
        self.log = []

    def __call__(self, method, url, headers, body):
        self.log.append((method, url))
        u = urllib.parse.urlsplit(url)
        q = dict(urllib.parse.parse_qsl(u.query))
        path = urllib.parse.unquote(u.path)
        J = lambda obj, status=200: (status, json.dumps(obj).encode(), {})
        if u.hostname == "api.crossref.org":
            if path.startswith("/works/"):
                doi = path[len("/works/"):].lower()
                return J({"message": CROSSREF[doi]}) if doi in CROSSREF else J({}, 404)
            if path == "/works":
                text = q.get("query.bibliographic", "").lower()
                dois = next((v for k, v in SEARCH.items() if k in text), [])
                return J({"message": {"items": [dict(CROSSREF[d], score=50) for d in dois]}})
        if u.hostname == "doi.org":
            return J({"responseCode": 100}, 404)
        if u.hostname == "api.semanticscholar.org":
            if path.endswith("/citations"):
                doi = path.split("DOI:")[1].rsplit("/citations", 1)[0].lower()
                return J({"offset": 0, "data": S2_CITATIONS.get(doi, [])})
            if path.startswith("/graph/v1/paper/search"):
                return J({"data": [{"paperId": "p1", "title": "Assistance does not speed review", "year": 2024,
                                    "externalIds": {"DOI": "10.5555/x1"},
                                    "abstract": "Across 60 journals, AI assistance in peer review showed no effect on review turnaround time."}]})
            return J({}, 404)
        if u.hostname == "www.ebi.ac.uk":
            if path.endswith("/fullTextXML"):
                return 200, JATS.encode(), {}
            return J({"resultList": {"result": []}})
        if u.hostname == "api.anthropic.com" and self.llm_reply:
            return J({"content": [{"type": "text", "text": self.llm_reply(json.loads(body))}], "stop_reason": "end_turn"})
        return J({}, 404)
