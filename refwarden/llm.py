"""Vendor-neutral model adapter. Raw HTTP, no SDKs, so the tool stays dependency-free.

Providers:
  openai     any OpenAI-compatible /chat/completions endpoint: OpenAI, Azure/OpenRouter/Together/Groq/
             DeepSeek/Mistral, Google Gemini's OpenAI-compatible endpoint, and local servers
             (Ollama, LM Studio, vLLM, llama.cpp). Set REFWARDEN_LLM_BASE_URL for non-OpenAI hosts.
  anthropic  Anthropic Messages API.
  none       heuristics only; judgement items can still be exported as a packet for any harness.
"""
import json
import os
import re

PROMPT_DIR = os.path.join(os.path.dirname(__file__), "prompts")
ANTHROPIC_FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5"}


def prompt(name):
    with open(os.path.join(PROMPT_DIR, name + ".md"), encoding="utf-8") as f:
        return f.read()


class LLMError(Exception):
    pass


def extract_json(text):
    """Pull the first JSON object/array out of a model reply (tolerates fences and preambles)."""
    text = re.sub(r"```(?:json)?", "", text or "")
    dec = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "{[":
            try:
                obj, _ = dec.raw_decode(text[i:])
                return obj
            except ValueError:
                continue
    raise LLMError("model reply contained no JSON")


class LLM:
    def __init__(self, cfg, http):
        self.cfg, self.http = cfg, http
        self.provider = (cfg.llm_provider or "none").lower()
        self.model = cfg.llm_model or {"anthropic": "claude-opus-5-5"}.get(self.provider, "")

    @property
    def available(self):
        if self.provider == "openai":
            return bool(self.model and (self.cfg.llm_api_key or self.cfg.llm_base_url))
        if self.provider == "anthropic":
            return bool(self.cfg.llm_api_key)
        return False

    @property
    def label(self):
        return f"{self.provider}:{self.model}" if self.available else None

    def complete(self, system, user, max_tokens=8000):
        if self.provider == "openai":
            base = (self.cfg.llm_base_url or "https://api.openai.com/v1").rstrip("/")
            headers = {"Authorization": f"Bearer {self.cfg.llm_api_key}"} if self.cfg.llm_api_key else {}
            body = {"model": self.model, "messages": [{"role": "system", "content": system},
                                                      {"role": "user", "content": user}]}
            if self.cfg.llm_effort:
                body["reasoning_effort"] = self.cfg.llm_effort
            data = self.http.post_json(f"{base}/chat/completions", body, headers)
            return (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        if self.provider == "anthropic":
            base = (self.cfg.llm_base_url or "https://api.anthropic.com").rstrip("/")
            headers = {"x-api-key": self.cfg.llm_api_key, "anthropic-version": "2023-06-01"}
            body = {"model": self.model, "max_tokens": max_tokens, "system": system,
                    "messages": [{"role": "user", "content": user}]}
            if self.cfg.llm_effort:
                body["output_config"] = {"effort": self.cfg.llm_effort}
            if self.model in ANTHROPIC_FALLBACK_MODELS and "api.anthropic.com" in base:
                # server-side fallback: a safety-classifier refusal is retried on a suitable model
                headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
                body["fallbacks"] = "default"
            data = self.http.post_json(f"{base}/v1/messages", body, headers)
            if data.get("stop_reason") == "refusal":
                raise LLMError("model declined the request (stop_reason=refusal)")
            return "".join(b.get("text", "") for b in data.get("content") or [] if b.get("type") == "text")
        raise LLMError("no model configured (REFWARDEN_LLM=none)")

    def judge(self, prompt_name, header, items, batch=15):
        """Run a judgement prompt over items in batches. Returns {item_id: result dict}."""
        from concurrent.futures import ThreadPoolExecutor
        system = prompt(prompt_name)
        out = {}

        self.failures = []

        def one(chunk):
            user = header + "\n\nITEMS:\n" + json.dumps(chunk, ensure_ascii=False, indent=1)
            try:
                obj = extract_json(self.complete(system, user))
            except Exception as e:   # one failed batch leaves the others standing; its items stay unjudged
                self.failures.append(str(e)[:200])
                return []
            return obj.get("items", []) if isinstance(obj, dict) else obj
        chunks = [items[i:i + batch] for i in range(0, len(items), batch)]
        with ThreadPoolExecutor(max_workers=2) as ex:   # batches are independent; providers allow parallel calls
            for rows in ex.map(one, chunks):
                for r in rows:
                    if isinstance(r, dict) and r.get("id") is not None:
                        out[str(r["id"])] = r
        if self.failures and not out:
            raise LLMError(self.failures[0])
        return out
