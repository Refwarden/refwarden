"""Cached, throttled HTTP with retries. Standard library only.

Tests replace `transport` with a function that serves fixtures, so no network is needed to test.
"""
import hashlib
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import __version__

# Minimum seconds between requests to one host (unauthenticated Semantic Scholar is the tight one).
HOST_INTERVAL = {
    "api.semanticscholar.org": 1.05,
    "api.crossref.org": 0.12,
    "api.openalex.org": 0.12,
    "www.ebi.ac.uk": 0.12,
    "doi.org": 0.2,
    "integrate.api.nvidia.com": 2.0,   # NVIDIA's free tier allows about 40 requests per minute
}
DEFAULT_INTERVAL = 0.1
SECRET_PARAMS = {"api_key", "mailto", "email"}


class HttpError(Exception):
    def __init__(self, status, url, body=""):
        super().__init__(f"HTTP {status} for {url}")
        self.status, self.url, self.body = status, url, body


def urllib_transport(method, url, headers, body, timeout=40):
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b"", dict(e.headers or {})


class Http:
    def __init__(self, cfg, db, transport=None):
        self.cfg, self.db = cfg, db
        self.transport = transport or urllib_transport
        self._host_locks, self._last = {}, {}
        self._guard = threading.Lock()
        self.calls = 0          # network calls made (not cache hits)
        self.errors = []        # (url, status) for the coverage block
        self.busy = {}          # host -> consecutive requests that ended in 429
        self.tripped = set()    # hosts given up on for the rest of the run

    def _throttle(self, host):
        with self._guard:
            lock = self._host_locks.setdefault(host, threading.Lock())
        with lock:
            wait = HOST_INTERVAL.get(host, DEFAULT_INTERVAL) - (time.time() - self._last.get(host, 0))
            if wait > 0:
                time.sleep(wait)
            self._last[host] = time.time()

    @staticmethod
    def cache_key(method, url, body):
        parts = urllib.parse.urlsplit(url)
        q = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query) if k not in SECRET_PARAMS]
        clean = urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(sorted(q))))
        h = hashlib.sha256(body).hexdigest()[:16] if body else ""
        return f"{method} {clean} {h}".strip()

    def request(self, method, url, params=None, headers=None, body=None, cache=True, max_age=None):
        if params:
            url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(
                {k: v for k, v in params.items() if v not in (None, "")})
        data = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
        key = self.cache_key(method, url, data)
        age = self.cfg.cache_days * 86400 if max_age is None else max_age
        if cache:
            hit = self.db.cache_get(key, None if self.cfg.offline else age)
            if hit:
                return hit
        if self.cfg.offline:
            return 599, b""
        if urllib.parse.urlsplit(url).hostname in self.tripped:
            return 429, b"skipped: host rate-limited earlier in this run"
        ua = f"refwarden/{__version__} (https://github.com/refwarden/refwarden"
        ua += f"; mailto:{self.cfg.mailto})" if self.cfg.mailto else ")"
        hdrs = {"User-Agent": ua, "Accept": "application/json"}
        if data is not None:
            hdrs["Content-Type"] = "application/json"
        hdrs.update(headers or {})
        host = urllib.parse.urlsplit(url).hostname or ""
        status, payload = 0, b""
        for attempt in range(5):
            self._throttle(host)
            try:
                status, payload, resp_headers = self.transport(method, url, hdrs, data)
            except Exception as e:  # DNS, TLS, refused, proxy denial: retry once, then give up
                status, payload, resp_headers = 0, str(e).encode(), {}
            self.calls += 1
            # data lookups fail fast on a busy pool; model calls (POST) are worth waiting for
            if (status == 0 and attempt >= 1) or (status == 429 and method == "GET" and attempt >= 2):
                break   # a busy shared pool rarely clears within seconds; fail fast and let the run fall back
            if status == 429:   # the shared pool is busy: slow down for the rest of the run, not just this call
                with self._guard:
                    HOST_INTERVAL[host] = min(8.0, max(HOST_INTERVAL.get(host, DEFAULT_INTERVAL), 0.5) * 2)
            if status in (429, 500, 502, 503, 504, 0):
                retry_after = resp_headers.get("Retry-After") or resp_headers.get("retry-after")
                try:
                    delay = float(retry_after)
                except (TypeError, ValueError):
                    delay = 2 ** attempt
                time.sleep(min(delay, 30))
                continue
            break
        if status == 429 and method == "GET":   # model calls (POST) are retried, never switched off
            self.busy[host] = self.busy.get(host, 0) + 1
            if self.busy[host] >= 3:   # three calls in a row refused after retries: stop paying the wait
                self.tripped.add(host)
        else:
            self.busy[host] = 0
        if status >= 400 or status == 0:
            self.errors.append((url.split("?")[0], status))
        # cache successes and definitive misses (404) so a phantom reference is not re-queried every run
        if cache and (200 <= status < 300 or status == 404):
            self.db.cache_put(key, status, payload)
        return status, payload

    def get_json(self, url, params=None, headers=None, cache=True, max_age=None):
        status, payload = self.request("GET", url, params, headers, cache=cache, max_age=max_age)
        if status == 404:
            return None
        if not 200 <= status < 300:
            raise HttpError(status, url, payload[:300])
        return json.loads(payload)

    def get_text(self, url, params=None, headers=None, cache=True):
        status, payload = self.request("GET", url, params, headers, cache=cache)
        if status == 404:
            return None
        if not 200 <= status < 300:
            raise HttpError(status, url, payload[:300])
        return payload.decode("utf-8", "replace")

    def post_json(self, url, body, headers=None, cache=False):
        status, payload = self.request("POST", url, None, headers, body=body, cache=cache)
        if not 200 <= status < 300:
            raise HttpError(status, url, payload[:500])
        return json.loads(payload)
