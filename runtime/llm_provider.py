#!/usr/bin/env python3
"""
llm_provider.py — astrazione mode-aware del provider LLM (Metnos v1.1 POC).

Decisione M1+D1: ogni call site dell'LLM passa per un'istanza di LLMProvider.
In v1.1 POC esistono:
    OllamaProvider     mode="local"   chiama localhost:11434
    LlamaCppProvider   mode="local"   OpenAI-compat su llama-server (es. :8080)
    AnthropicProvider  mode="online"  Anthropic Messages API
    StubProvider       mode="local"   per testing senza LLM

API:
    chat(system, user, *, max_tokens, temperature, think) -> ChatResult
        chat plain, niente tools (per usi semplici tipo chat_summary).
    chat_with_tools(system, user, tools, history, *, ...) -> ToolUseResult
        chat con tool-call nativo.

Decisioni post-probe (26/4/2026 ciclo finale POC):
    - tool-use nativo come default (vedi memoria metnos_poc_native_tool_use_finding)
    - think parametrizzato per modelli che lo supportano (Qwen 3, Llama 3.1)
    - LlamaCppProvider striiba i marker <|channel>thought ... <channel|> di Gemma 4
"""
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

import llm_telemetry as _telemetry  # universal pass-through observability hook


@dataclass
class ChatResult:
    text: str
    in_tokens: int
    out_tokens: int
    model: str
    provider: str
    latency_ms: int
    thinking: str = ""


@dataclass
class ToolCall:
    name: str
    arguments: dict
    call_id: str = ""
    canonical_query: str = ""  # ADR 0149: by-product planner normalization


@dataclass
class ToolUseResult:
    text: str  # finalText o "" se ha chiamato tool
    tool_calls: list = field(default_factory=list)  # list[ToolCall]
    in_tokens: int = 0
    out_tokens: int = 0
    model: str = ""
    provider: str = ""
    latency_ms: int = 0
    thinking: str = ""


class ProviderError(Exception):
    pass


class OllamaProvider:
    """Provider Ollama HTTP API (deprecato post-ADR 0146).

    Mantenuto per back-compat e per chi serve esplicitamente modelli via
    `ollama serve`. Il modello DEVE essere specificato esplicitamente:
    il vecchio default `qwen3:8b` e' stato rimosso il 18/5/2026 perche'
    era latent-broken (ADR 0148) — ollama.service e' disabilitato su .33
    e i caller hardcoded a qwen3:8b cadevano silenziosamente.

    Per il pianificatore default usa `LlamaCppProvider` o `LLMRouter`
    (vedi ADR 0146).
    """
    mode = "local"
    name = "ollama"

    def __init__(self, model: str, endpoint: str = "http://localhost:11434",
                 think: bool = False):
        if not model:
            raise ValueError(
                "OllamaProvider richiede `model=` esplicito post-ADR 0148 "
                "(no default qwen3:8b). Es. OllamaProvider(model='qwen3:8b')."
            )
        self.model = model
        self.endpoint = endpoint
        self.think = think

    def chat(self, system, user, *, max_tokens=512, temperature=0, think=None):
        payload = {
            "model": self.model,
            "stream": False,
            "think": self.think if think is None else think,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "options": {"num_predict": max_tokens, "temperature": temperature},
        }
        res = self._call_chat(payload, expect_tools=False)
        _telemetry.record(provider="ollama", model=self.model,
                          system=system, user=user, result=res, kind="chat")
        return res

    def chat_with_tools(self, system, user, tools, history=None, *,
                        max_tokens=512, temperature=0, think=None):
        """
        tools: list[dict] in formato OpenAI/Ollama function-calling
            {"type": "function", "function": {"name", "description", "parameters"}}
        history: list di {"role", "content", "tool_calls"?, "tool_call_id"?, "name"?}
        """
        messages = [{"role": "system", "content": system}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user})
        payload = {
            "model": self.model,
            "stream": False,
            "think": self.think if think is None else think,
            "messages": messages,
            "tools": tools,
            "options": {"num_predict": max_tokens, "temperature": temperature},
        }
        res = self._call_chat(payload, expect_tools=True)
        _telemetry.record(provider="ollama", model=self.model,
                          system=system, user=user, result=res, kind="tools")
        return res

    def _call_chat(self, payload, expect_tools):
        # ADR 0121: sanitize surrogates pre-serialization (vedi LlamaCppProvider).
        try:
            from utf8_safe import safe_json_dumps as _safe_dumps  # type: ignore
            body = _safe_dumps(payload).encode("utf-8")
        except Exception:
            body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.endpoint}/api/chat",
            data=body, headers={"Content-Type": "application/json"}, method="POST",
        )
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise ProviderError(f"ollama unreachable: {e}") from e
        latency = int((time.time() - t0) * 1000)
        msg = data.get("message", {})
        text = msg.get("content", "")
        thinking = msg.get("thinking", "")
        in_toks = data.get("prompt_eval_count", 0)
        out_toks = data.get("eval_count", 0)

        if expect_tools:
            tool_calls_raw = msg.get("tool_calls", []) or []
            tcs = [
                ToolCall(
                    name=tc.get("function", {}).get("name", ""),
                    arguments=tc.get("function", {}).get("arguments", {}),
                    call_id=tc.get("id", ""),
                )
                for tc in tool_calls_raw
            ]
            return ToolUseResult(
                text=text, tool_calls=tcs,
                in_tokens=in_toks, out_tokens=out_toks,
                model=self.model, provider="ollama",
                latency_ms=latency, thinking=thinking,
            )
        return ChatResult(
            text=text, in_tokens=in_toks, out_tokens=out_toks,
            model=self.model, provider="ollama",
            latency_ms=latency, thinking=thinking,
        )


# --- LlamaCppProvider (llama-server OpenAI-compatible) -------------------

# Gemma 4 emette pensiero in markers che il template di llama-server non
# parsa: vanno strippati dal content lato client.
_GEMMA_THOUGHT_RE = re.compile(r'<\|channel>.*?<channel\|>', flags=re.DOTALL)


_GEMMA_TC_RE = re.compile(
    r"<\|tool_call>call:([a-zA-Z_][a-zA-Z0-9_]*)\((.*?)\)<tool_call\|>",
    re.DOTALL,
)


def _parse_tool_call_tolerant(text: str) -> dict | None:
    """Parser ADR 0133 grammar-mode: accetta JSON puro o formato Gemma 4
    tool_call (`<|tool_call>call:NAME(k=v,...)<tool_call|>`). Ritorna
    `{"name", "arguments"}` o None se nessun match.

    Gemma 4 args syntax (k=v separati da virgola, valori Python-like):
        find_events_empty(size="1hour", time_windows=["next-week"], max_results=3)
    Parsing: ast.literal_eval per ogni value (sicuro: no eval Python).
    """
    if not text:
        return None
    t = text.strip()
    # (a) JSON puro
    try:
        parsed = json.loads(t)
        if isinstance(parsed, dict) and isinstance(parsed.get("name"), str):
            args = parsed.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"_raw": args}
            elif not isinstance(args, dict):
                args = {}
            # ADR 0149: canonical_query as planner by-product (optional).
            # Solo se presente nel JSON: evita di sporcare l'output con campo
            # "" che il chiamante non aspetta.
            out: dict = {"name": parsed["name"], "arguments": args}
            cq = parsed.get("canonical_query")
            if isinstance(cq, str) and cq:
                out["canonical_query"] = cq
            return out
    except json.JSONDecodeError:
        pass
    # (a.bis) JSON truncated recovery (15/5/2026): llama.cpp grammar-mode
    # talvolta termina prematuramente la generazione (EOS prima della chiusura
    # `}` o di `"arguments"`), lasciando `{"name": "TOOL_X"` o
    # `{"name": "TOOL_X", "arguments": {` non parsabile. Recupero: regex
    # tollerante estrae `name`; gli args default vuoti `{}` sono accettati
    # per i tool con `args_schema.required` vuoto. La validazione semantica
    # avviene poi in `tool_grammar.validate_tool_call` lato runtime.
    if t.startswith("{"):
        m = re.search(r'"name"\s*:\s*"([a-zA-Z_][a-zA-Z0-9_]*)"', t)
        if m:
            name = m.group(1)
            # Tenta anche di estrarre arguments parzialmente se presenti.
            args_obj: dict = {}
            args_m = re.search(r'"arguments"\s*:\s*(\{.*?\})\s*(?:\}|$)', t,
                                 flags=re.DOTALL)
            if args_m:
                try:
                    parsed_args = json.loads(args_m.group(1))
                    if isinstance(parsed_args, dict):
                        args_obj = parsed_args
                except json.JSONDecodeError:
                    pass
            return {"name": name, "arguments": args_obj}
    # (b) Gemma 4 tool_call template
    m = _GEMMA_TC_RE.search(t)
    if m:
        name = m.group(1)
        args_str = m.group(2).strip()
        args: dict = {}
        if args_str:
            # Split su virgole top-level + parse `key=literal` via ast.
            import ast
            depth_p = depth_b = depth_c = 0
            in_str = False
            esc = False
            cur = []
            tokens: list[str] = []
            for ch in args_str:
                if esc:
                    cur.append(ch); esc = False; continue
                if ch == "\\":
                    cur.append(ch); esc = True; continue
                if ch == '"' and depth_p == depth_b == depth_c == 0:
                    in_str = not in_str
                    cur.append(ch); continue
                if not in_str:
                    if ch == "(": depth_p += 1
                    elif ch == ")": depth_p -= 1
                    elif ch == "[": depth_b += 1
                    elif ch == "]": depth_b -= 1
                    elif ch == "{": depth_c += 1
                    elif ch == "}": depth_c -= 1
                    elif ch == "," and depth_p == depth_b == depth_c == 0:
                        tokens.append("".join(cur).strip())
                        cur = []
                        continue
                cur.append(ch)
            if cur:
                tokens.append("".join(cur).strip())
            for tok in tokens:
                if "=" not in tok:
                    continue
                k, v = tok.split("=", 1)
                k = k.strip().strip('"').strip("'")
                v = v.strip()
                try:
                    args[k] = ast.literal_eval(v)
                except (ValueError, SyntaxError):
                    args[k] = v
        return {"name": name, "arguments": args}
    return None


def _strip_thought(s):
    if not s:
        return s
    return _GEMMA_THOUGHT_RE.sub('', s).strip()


class LlamaCppProvider:
    """OpenAI-compatible client per llama.cpp llama-server.

    Pensato per modelli locali grandi tipo Gemma 4 26B, ma funziona con
    qualunque modello servito via /v1/chat/completions.
    """
    mode = "local"
    name = "llamacpp"

    def __init__(self, model="local",
                 endpoint="http://127.0.0.1:8080", id_slot: int | None = None):
        self.model = model
        self.endpoint = endpoint.rstrip("/")
        # ADR 0120: slot affinity per consumer su llama-server condiviso.
        # Metnos = id_slot=1 (batch image enrichment), giorgio/voice = 0.
        # None = comportamento default (selettore LCP-similarity di llama-server).
        self.id_slot = id_slot

    def chat(self, system, user, *, max_tokens=512, temperature=0, think=None,
             reasoning_budget=1024, grammar: str | None = None,
             seed: int | None = None):
        """think semantics (allineato a suprastructure/openai_compat):
            False  → enable_thinking=False, niente reasoning budget. Risposta
                     immediata. Ideale per stage procedurali (lookup, schema).
            True   → enable_thinking=True + reasoning_budget=<reasoning_budget>
                     (default 1024). Ragionamento prima dell'output.
            None   → default del server (per Gemma 4: thinking ON con budget
                     1024). Sconsigliato: passa sempre think esplicito.

        `reasoning_budget` consente di limitare il budget di think (es. 512
        per stage procedurali con think=True). Ignorato se think != True.

        `grammar` (GBNF, opzionale): se non None, vincola l'output del
        modello alla grammatica fornita (ADR 0133). Usato dal telos engine
        Naming Authority per forzare vocab §2.2 in proposed names.
        """
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        # seed fisso DI DEFAULT → determinismo del routing/synth (§7.9, decisione
        # Roberto 8/6: "privilegia SEMPRE il determinismo"). A temperature=0 il
        # server resta NON-deterministico per via di MTP/speculative decoding
        # (draft sampling) col seed random: pinnarlo rende il routing riproducibile
        # (diagnosi 8/6: read_urls_html flaky 7/8 → 8/8 con seed fisso). Override:
        # arg `seed`, o env METNOS_LLM_SEED (=-1 per random/diversità esplicita).
        _seed = seed if seed is not None else int(os.environ.get("METNOS_LLM_SEED", "42"))
        if _seed >= 0:
            payload["seed"] = _seed
        if think is False:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        elif think is True:
            payload["chat_template_kwargs"] = {"enable_thinking": True}
            payload["reasoning_budget"] = reasoning_budget
        if grammar is not None:
            payload["grammar"] = grammar
        return self._call(payload, expect_tools=False,
                          grammar_mode=grammar is not None)

    def chat_with_tools(self, system, user, tools, history=None, *,
                        max_tokens=2048, temperature=0, think=None,
                        reasoning_budget=512, grammar: str | None = None):
        """Chat con tool-use. Due modalita':

        1. **Native tool_call protocol** (default, `grammar=None`):
           passa `tools` + `tool_choice="auto"`. llama-server applica
           chat_template Gemma per il tool_call. Soft-constrained → il
           LLM puo' generare prosa/loop (bug live, vedi ADR 0133).

        2. **Grammar-constrained** (`grammar=<GBNF>`, ADR 0133):
           bypassa `tools` (llama-server rifiuta grammar+tools insieme).
           Forza output JSON `{"name":..., "arguments":...}` tramite GBNF.
           Implica `think=False` (grammar + thinking lungo collide su
           max_tokens, observed empirically).
        """
        messages = [{"role": "system", "content": system}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user})
        if grammar is not None:
            # Grammar-constrained mode: niente tools, niente thinking,
            # niente role=tool/tool_calls in history (triggerano il
            # tool_call template Gemma che emette `<|tool_call>...|>`).
            # Conversione history → messaggi role assistant/user testuali.
            flat_msgs = [{"role": "system", "content": system}]
            for m in (history or []):
                if not isinstance(m, dict):
                    continue
                role = m.get("role")
                if role == "tool":
                    # tool result → assistant text per il prossimo turno
                    flat_msgs.append({
                        "role": "assistant",
                        "content": (
                            f"Tool result for `{m.get('name','?')}`: "
                            f"{m.get('content','')}"
                        ),
                    })
                elif role == "assistant":
                    tcs = m.get("tool_calls") or []
                    if tcs:
                        fc = (tcs[0].get("function") or {})
                        flat_msgs.append({
                            "role": "assistant",
                            "content": (
                                f'{{"name":"{fc.get("name","")}",'
                                f'"arguments":{fc.get("arguments","{}")}}}'
                            ),
                        })
                    elif m.get("content"):
                        flat_msgs.append({"role": "assistant",
                                          "content": m["content"]})
                elif role == "user":
                    flat_msgs.append({"role": "user",
                                      "content": m.get("content", "")})
            flat_msgs.append({"role": "user", "content": user})
            payload = {
                "model": self.model,
                "messages": flat_msgs,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "chat_template_kwargs": {"enable_thinking": False},
                "reasoning_budget": 0,
                "grammar": grammar,
            }
            return self._call(payload, expect_tools=True,
                              grammar_mode=True)
        enable_thinking = bool(think)
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": temperature,
            "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": enable_thinking},
        }
        if enable_thinking:
            payload["reasoning_budget"] = reasoning_budget
        return self._call(payload, expect_tools=True)

    def _call(self, payload, expect_tools, *, grammar_mode: bool = False):
        # ADR 0120: inject id_slot per slot affinity. llama-server passa
        # la richiesta direttamente allo slot N bypassando LCP-similarity.
        if self.id_slot is not None and "id_slot" not in payload:
            payload["id_slot"] = int(self.id_slot)
        # Bench 10/5/2026: cache_prompt esplicito (ridondante col flag
        # --cache-prompt server gia' attivo, ma rende intent chiaro e
        # protegge da future regressioni del default). Combinato con
        # id_slot pinning permette di riusare il prefix system+tools
        # (~24K tokens) cross-turn → 92.8 t/s warm misurati.
        payload.setdefault("cache_prompt", True)
        # ADR 0121: sanitize surrogates UTF-16 invalidi in UTF-8 (RFC 8259
        # 6.2.1). Provider rifiutano payload con code point U+D800..U+DFFF.
        try:
            from utf8_safe import safe_json_dumps as _safe_dumps  # type: ignore
            body = _safe_dumps(payload).encode("utf-8")
        except Exception:
            body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.endpoint}/v1/chat/completions",
            data=body, headers={"Content-Type": "application/json"}, method="POST",
        )
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise ProviderError(f"llama-server unreachable at {self.endpoint}: {e}") from e
        latency = int((time.time() - t0) * 1000)

        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        raw_content = msg.get("content") or ""
        reasoning_content = msg.get("reasoning_content") or ""
        thinking = ""
        m = _GEMMA_THOUGHT_RE.search(raw_content)
        if m:
            thinking = m.group(0)
        elif reasoning_content:
            thinking = reasoning_content
        text = _strip_thought(raw_content)
        if not text and not msg.get("tool_calls") and reasoning_content:
            text = reasoning_content

        usage = data.get("usage") or {}
        in_toks = usage.get("prompt_tokens", 0)
        out_toks = usage.get("completion_tokens", 0)

        if expect_tools:
            tcs: list[ToolCall] = []
            if grammar_mode:
                # ADR 0133: parse content tool_call (no native tool_calls
                # quando grammar e' attiva). Due formati possibili:
                #   (a) JSON puro: {"name":"<tool>","arguments":{...}}
                #   (b) Gemma 4 tool_call: <|tool_call>call:<tool>(k=v,...)<tool_call|>
                # Parser tollerante: prova prima JSON, fallback regex Gemma.
                parsed = _parse_tool_call_tolerant(text)
                if parsed is not None:
                    tcs.append(ToolCall(
                        name=parsed["name"],
                        arguments=parsed["arguments"],
                        call_id=f"grammar_{int(time.time()*1000)}",
                        canonical_query=parsed.get("canonical_query", ""),
                    ))
            else:
                tcs_raw = msg.get("tool_calls") or []
                for tc in tcs_raw:
                    fn = tc.get("function") or {}
                    args_raw = fn.get("arguments") or "{}"
                    if isinstance(args_raw, str):
                        try:
                            args = json.loads(args_raw)
                        except json.JSONDecodeError:
                            args = {"_raw": args_raw}
                    else:
                        args = args_raw
                    tcs.append(ToolCall(
                        name=fn.get("name", ""),
                        arguments=args,
                        call_id=tc.get("id", ""),
                    ))
            res = ToolUseResult(
                text=text, tool_calls=tcs,
                in_tokens=in_toks, out_tokens=out_toks,
                model=self.model, provider="llamacpp",
                latency_ms=latency, thinking=thinking,
            )
        else:
            res = ChatResult(
                text=text, in_tokens=in_toks, out_tokens=out_toks,
                model=self.model, provider="llamacpp",
                latency_ms=latency, thinking=thinking,
            )
        # Universal observability hook (pass-through: never mutates res).
        _msgs = payload.get("messages") or []
        _sys = next((m.get("content", "") for m in _msgs
                     if m.get("role") == "system"), "")
        _usr = next((m.get("content", "") for m in reversed(_msgs)
                     if m.get("role") == "user"), "")
        _telemetry.record(provider="llamacpp", model=self.model,
                          system=_sys, user=_usr, result=res,
                          kind="tools" if expect_tools else "chat")
        return res


# --- AnthropicProvider (Messages API) -------------------------------------

def _read_env_var_from_files(var_name, candidate_paths):
    """Cerca VAR=value in una lista di file env-style, prima riga vince."""
    for raw_path in candidate_paths:
        p = os.path.expanduser(raw_path)
        if not os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith(f"{var_name}="):
                        v = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if v:
                            return v
        except Exception:
            continue
    return None


def _read_api_key_from_store(domain: str) -> str | None:
    """Layer 1 (ADR 0131 extended, 14/5/2026): legge `value` dallo store
    cifrato Fernet. Ritorna None se assente o store non disponibile."""
    try:
        import credentials as _cr
    except ImportError:
        return None
    payload = _cr.load(domain)
    if not isinstance(payload, dict):
        return None
    v = payload.get("value")
    return v if isinstance(v, str) and v else None


def _read_anthropic_key():
    """ANTHROPIC_API_KEY ricerca in 3 layer:
      1. env var (override volatile per debug).
      2. credentials store cifrato (ADR 0131, domain `anthropic_api_key`).
      3. file ~/.config/metnos/{credentials,anthropic}.env (legacy fallback).

    Le chiavi Anthropic hanno formato 'sk-ant-api...' e NON contengono whitespace;
    se ne troviamo (tipico line-wrap da copy-paste browser) lo eliminiamo tutto.
    """
    k = os.environ.get("ANTHROPIC_API_KEY")
    if not k:
        k = _read_api_key_from_store("anthropic_api_key")
    if not k:
        k = _read_env_var_from_files("ANTHROPIC_API_KEY", [
            "~/.config/metnos/credentials.env",
            "~/.config/metnos/anthropic.env",
        ])
    if not k:
        return None
    if k.startswith("sk-ant-") and any(c.isspace() for c in k):
        k = "".join(k.split())
    return k


def _read_openai_key():
    """OPENAI_API_KEY (3 layer come `_read_anthropic_key`)."""
    k = os.environ.get("OPENAI_API_KEY")
    if not k:
        k = _read_api_key_from_store("openai_api_key")
    if not k:
        k = _read_env_var_from_files("OPENAI_API_KEY", [
            "~/.config/metnos/credentials.env",
            "~/.config/metnos/openai.env",
        ])
    if not k:
        return None
    if k.startswith("sk-") and any(c.isspace() for c in k):
        k = "".join(k.split())
    return k


def _temperature_deprecated(model: str) -> bool:
    """True per i modelli Anthropic in cui il parametro `temperature` e'
    stato deprecato dall'API e causa errore 400 se inviato.

    Aggiornare quando Anthropic estende la deprecation ad altri modelli.
    """
    if not isinstance(model, str):
        return False
    deprecated = ("opus-4-7", "claude-opus-4-7")
    return any(d in model for d in deprecated)


class AnthropicProvider:
    """Client per Anthropic Messages API. Tool-use nativo via schema Anthropic.

    Differenze schema rispetto a Ollama/OpenAI:
      - tools: [{name, description, input_schema}]  (no "type":"function" wrapper)
      - response.content: lista di blocchi con type in {"text","tool_use"}
      - system come campo top-level, non come messaggio role=system
    """
    mode = "online"
    name = "anthropic"

    API_VERSION = "2023-06-01"
    API_URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, model="claude-sonnet-4-6", api_key=None):
        self.model = model
        self.api_key = api_key or _read_anthropic_key()

    def _require_key(self):
        if not self.api_key:
            raise ProviderError(
                "ANTHROPIC_API_KEY mancante. Imposta env var o "
                "~/.config/metnos/anthropic.env (chmod 600)."
            )

    def chat(self, system, user, *, max_tokens=1024, temperature=0, think=None):
        self._require_key()
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        # Opus 4.7 ha deprecato il parametro `temperature`: passarlo causa
        # 400 "temperature is deprecated for this model". Omettiamo per i
        # modelli noti incompatibili; resta valido per i modelli precedenti.
        if not _temperature_deprecated(self.model):
            payload["temperature"] = temperature
        data, latency = self._post(payload)
        text = self._extract_text(data)
        in_toks, out_toks = self._extract_usage(data)
        res = ChatResult(
            text=text, in_tokens=in_toks, out_tokens=out_toks,
            model=self.model, provider="anthropic", latency_ms=latency,
        )
        _telemetry.record(provider="anthropic", model=self.model,
                          system=system, user=user, result=res, kind="chat")
        return res

    def chat_with_tools(self, system, user, tools, history=None, *,
                        max_tokens=2048, temperature=0, think=None):
        self._require_key()
        anthropic_tools = self._convert_tools(tools)
        messages = []
        if history:
            messages.extend(self._convert_history(history))
        messages.append({"role": "user", "content": user})
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
            "tools": anthropic_tools,
        }
        if not _temperature_deprecated(self.model):
            payload["temperature"] = temperature
        data, latency = self._post(payload)
        text = self._extract_text(data)
        tcs = self._extract_tool_calls(data)
        in_toks, out_toks = self._extract_usage(data)
        res = ToolUseResult(
            text=text, tool_calls=tcs,
            in_tokens=in_toks, out_tokens=out_toks,
            model=self.model, provider="anthropic", latency_ms=latency,
        )
        _telemetry.record(provider="anthropic", model=self.model,
                          system=system, user=user, result=res, kind="tools")
        return res

    @staticmethod
    def _convert_history(history):
        """OpenAI-shaped history → Anthropic-shaped messages.

        OpenAI uses role="tool" entries; Anthropic rejects those (400
        "Unexpected role"). Convert:
          assistant + tool_calls  → assistant + [text?, tool_use blocks]
          role=tool               → user + [tool_result blocks]
          plain user/assistant    → passed through
        Consecutive role=tool entries get merged into one user message,
        preserving Anthropic's strict user/assistant alternation.
        """
        out: list = []
        pending_tool_results: list = []

        def _flush_tool_results():
            if pending_tool_results:
                out.append({"role": "user", "content": list(pending_tool_results)})
                pending_tool_results.clear()

        for m in history or []:
            if not isinstance(m, dict):
                continue
            role = m.get("role")
            if role == "tool":
                content = m.get("content", "")
                if not isinstance(content, str):
                    try:
                        content = json.dumps(content, ensure_ascii=False)
                    except Exception:
                        content = str(content)
                pending_tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": m.get("tool_call_id") or m.get("id") or "",
                    "content": content,
                })
                continue
            _flush_tool_results()
            if role == "assistant":
                tcs = m.get("tool_calls") or []
                if tcs:
                    blocks = []
                    txt = m.get("content")
                    if isinstance(txt, str) and txt.strip():
                        blocks.append({"type": "text", "text": txt})
                    for tc in tcs:
                        fn = (tc.get("function") or {})
                        name = fn.get("name") or tc.get("name") or ""
                        args_raw = fn.get("arguments")
                        if isinstance(args_raw, str):
                            try:
                                args = json.loads(args_raw) if args_raw else {}
                            except Exception:
                                args = {}
                        elif isinstance(args_raw, dict):
                            args = args_raw
                        else:
                            args = {}
                        blocks.append({
                            "type": "tool_use",
                            "id": tc.get("id") or "",
                            "name": name,
                            "input": args,
                        })
                    out.append({"role": "assistant", "content": blocks})
                else:
                    out.append({"role": "assistant",
                                "content": m.get("content", "")})
            elif role in ("user", "system"):
                out.append({"role": role, "content": m.get("content", "")})
        _flush_tool_results()
        return out

    def _post(self, payload):
        # ADR 0121: sanitize surrogates pre-serialization. Critico per
        # AnthropicProvider perche' l'API Claude rifiuta esplicitamente
        # JSON con code point U+D800..U+DFFF (RFC 8259).
        try:
            from utf8_safe import safe_json_dumps as _safe_dumps  # type: ignore
            body = _safe_dumps(payload).encode("utf-8")
        except Exception:
            body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.API_URL,
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": self.API_VERSION,
            },
            method="POST",
        )
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise ProviderError(
                f"anthropic api error {e.code}: {e.read().decode('utf-8')[:500]}"
            ) from e
        except urllib.error.URLError as e:
            raise ProviderError(f"anthropic unreachable: {e}") from e
        return data, int((time.time() - t0) * 1000)

    @staticmethod
    def _convert_tools(tools):
        """OpenAI/Ollama format -> Anthropic format."""
        out = []
        for t in tools:
            if "function" in t:
                fn = t["function"]
                out.append({
                    "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters", {"type": "object"}),
                })
            else:
                # gia' in formato Anthropic
                out.append(t)
        return out

    @staticmethod
    def _extract_text(data):
        parts = []
        for block in data.get("content", []) or []:
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)

    @staticmethod
    def _extract_tool_calls(data):
        out = []
        for block in data.get("content", []) or []:
            if block.get("type") == "tool_use":
                out.append(ToolCall(
                    name=block.get("name", ""),
                    arguments=block.get("input", {}),
                    call_id=block.get("id", ""),
                ))
        return out

    @staticmethod
    def _extract_usage(data):
        u = data.get("usage") or {}
        return u.get("input_tokens", 0), u.get("output_tokens", 0)


# --- OpenAIProvider (chat/completions API, OpenAI-compatible schema) ------

class OpenAIProvider:
    """Client per OpenAI Chat Completions API.

    Schema identico a Ollama/llama-server (OpenAI-compat): tools come
    [{"type":"function","function":{...}}], response.choices[0].message.tool_calls.
    """
    mode = "online"
    name = "openai"

    API_URL = "https://api.openai.com/v1/chat/completions"

    def __init__(self, model="gpt-4.1", api_key=None):
        self.model = model
        self.api_key = api_key or _read_openai_key()

    def _require_key(self):
        if not self.api_key:
            raise ProviderError(
                "OPENAI_API_KEY mancante. Imposta env var o "
                "~/.config/metnos/credentials.env (chmod 600)."
            )

    def _max_tokens_param(self):
        """gpt-5 e modelli reasoning (o1, o3, o4) usano max_completion_tokens."""
        m = self.model.lower()
        if m.startswith(("gpt-5", "o1", "o3", "o4")):
            return "max_completion_tokens"
        return "max_tokens"

    def _temp_supported(self):
        """gpt-5/reasoning models non accettano temperature != default."""
        m = self.model.lower()
        return not m.startswith(("gpt-5", "o1", "o3", "o4"))

    def chat(self, system, user, *, max_tokens=1024, temperature=0, think=None):
        self._require_key()
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            self._max_tokens_param(): max_tokens,
        }
        if self._temp_supported():
            payload["temperature"] = temperature
        res = self._call(payload, expect_tools=False)
        _telemetry.record(provider="openai", model=self.model,
                          system=system, user=user, result=res, kind="chat")
        return res

    def chat_with_tools(self, system, user, tools, history=None, *,
                        max_tokens=2048, temperature=0, think=None):
        self._require_key()
        messages = [{"role": "system", "content": system}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user})
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            self._max_tokens_param(): max_tokens,
        }
        if self._temp_supported():
            payload["temperature"] = temperature
        return self._call(payload, expect_tools=True)

    def _call(self, payload, expect_tools):
        # ADR 0121: sanitize surrogates pre-serialization (OpenAIProvider).
        try:
            from utf8_safe import safe_json_dumps as _safe_dumps  # type: ignore
            body = _safe_dumps(payload).encode("utf-8")
        except Exception:
            body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.API_URL,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise ProviderError(
                f"openai api error {e.code}: {e.read().decode('utf-8')[:500]}"
            ) from e
        except urllib.error.URLError as e:
            raise ProviderError(f"openai unreachable: {e}") from e
        latency = int((time.time() - t0) * 1000)

        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        text = msg.get("content") or ""
        usage = data.get("usage") or {}
        in_toks = usage.get("prompt_tokens", 0)
        out_toks = usage.get("completion_tokens", 0)

        if expect_tools:
            tcs_raw = msg.get("tool_calls") or []
            tcs = []
            for tc in tcs_raw:
                fn = tc.get("function") or {}
                args_raw = fn.get("arguments") or "{}"
                if isinstance(args_raw, str):
                    try:
                        args = json.loads(args_raw)
                    except json.JSONDecodeError:
                        args = {"_raw": args_raw}
                else:
                    args = args_raw
                tcs.append(ToolCall(
                    name=fn.get("name", ""),
                    arguments=args,
                    call_id=tc.get("id", ""),
                ))
            return ToolUseResult(
                text=text, tool_calls=tcs,
                in_tokens=in_toks, out_tokens=out_toks,
                model=self.model, provider="openai",
                latency_ms=latency,
            )
        return ChatResult(
            text=text, in_tokens=in_toks, out_tokens=out_toks,
            model=self.model, provider="openai", latency_ms=latency,
        )


class StubProvider:
    """Provider sintetico per test deterministici. Supporta:
        scripted_response       testo ritornato da chat()
        scripted_tool_call      dict {name, arguments} ritornato come tool_call
        scripted_tool_calls     list di dict per scripting multi-call (nei test)
    """
    mode = "local"
    name = "stub"

    def __init__(self, scripted_response="OK", scripted_tool_call=None,
                 scripted_tool_calls=None, model="stub"):
        self.scripted_response = scripted_response
        self.model = model
        if scripted_tool_calls is not None:
            self._calls_iter = iter(scripted_tool_calls)
        elif scripted_tool_call is not None:
            self._calls_iter = iter([scripted_tool_call])
        else:
            self._calls_iter = None

    def chat(self, system, user, **kwargs):
        return ChatResult(
            text=self.scripted_response, in_tokens=10, out_tokens=2,
            model="stub", provider="stub", latency_ms=1,
        )

    def chat_with_tools(self, system, user, tools, history=None, **kwargs):
        tcs = []
        if self._calls_iter is not None:
            try:
                spec = next(self._calls_iter)
                tcs = [ToolCall(
                    name=spec.get("name", ""),
                    arguments=spec.get("arguments", {}),
                    call_id=spec.get("id", "stub_call_1"),
                )]
            except StopIteration:
                pass
        return ToolUseResult(
            text="" if tcs else ("(stub) " + self.scripted_response),
            tool_calls=tcs,
            in_tokens=10, out_tokens=2, model="stub", provider="stub", latency_ms=1,
        )


def make_provider_from_config(mode, runtime_config):
    """Deprecated post-ADR 0146: use make_provider_from_spec via LLMRouter."""
    if mode == "local":
        cfg = runtime_config.get("local", {})
        # Default flipped to llamacpp+Gemma per ADR 0146. Pass `provider="ollama"`
        # in cfg to opt back into Ollama (requires `model=` explicit).
        if cfg.get("provider") == "ollama":
            return OllamaProvider(
                model=cfg.get("model"),     # required, no default
                endpoint=cfg.get("endpoint", "http://localhost:11434"),
                think=cfg.get("think", False),
            )
        return LlamaCppProvider(
            model=cfg.get("model", "gemma-4-26B-A4B-it-UD-Q4_K_M.gguf"),
            endpoint=cfg.get("endpoint", "http://127.0.0.1:8080"),
        )
    elif mode == "online":
        cfg = runtime_config.get("online", {})
        return AnthropicProvider(model=cfg.get("model", "claude-haiku-4-5"))
    raise ValueError(f"unknown mode: {mode}")


def make_provider_from_spec(spec):
    """Costruisce un provider da una spec dict {provider, model, ...}.

    Usato dal tier resolver per istanziare un provider concreto. Esempi:
        {"provider": "llamacpp",  "model": "gemma-4-26B...", "endpoint": "http://127.0.0.1:8080"}
        {"provider": "anthropic", "model": "claude-sonnet-4-6"}
        {"provider": "ollama",    "model": "<modello-esplicito>"}  (deprecated)
    """
    # ADR 0146: default provider = llamacpp (era ollama pre-18/5/2026).
    p = spec.get("provider", "llamacpp")
    # `endpoint` and `base_url` are aliases — tiers are abstract bindings and a
    # user config may use either name for the local server URL.
    endpoint = spec.get("endpoint") or spec.get("base_url")
    if p == "ollama":
        # Post-ADR 0148: niente fallback silenzioso a qwen3:8b. Caller
        # deve specificare model. OllamaProvider stesso ora raise se model
        # mancante.
        return OllamaProvider(
            model=spec.get("model"),
            endpoint=endpoint or "http://localhost:11434",
            think=spec.get("think", False),
        )
    elif p == "llamacpp":
        # model is optional: a llama-server serves whatever GGUF it loaded, so
        # an unset/placeholder name is fine (no specific model required).
        return LlamaCppProvider(
            model=spec.get("model") or "local",
            endpoint=endpoint or "http://127.0.0.1:8080",
        )
    elif p == "anthropic":
        return AnthropicProvider(
            model=spec.get("model", "claude-sonnet-4-6"),
            api_key=spec.get("api_key"),
        )
    elif p == "openai":
        return OpenAIProvider(
            model=spec.get("model", "gpt-4.1"),
            api_key=spec.get("api_key"),
        )
    elif p == "stub":
        return StubProvider(scripted_response=spec.get("response", "OK"))
    raise ValueError(f"unknown provider: {p}")


if __name__ == "__main__":
    import sys
    model = sys.argv[1] if len(sys.argv) > 1 else "qwen3:8b"
    p = OllamaProvider(model=model, think=False)
    print(f"--- chat() su {model} (think=false) ---")
    r = p.chat("Sei un assistente conciso.", "Dimmi solo OK.")
    print(f"text={r.text!r}  toks {r.in_tokens}->{r.out_tokens}  {r.latency_ms}ms")

    print(f"\n--- chat_with_tools() su {model} (think=false) ---")
    tools = [{
        "type": "function",
        "function": {
            "name": "get_now",
            "description": "Restituisce l'ora corrente in un fuso IANA.",
            "parameters": {"type": "object", "properties": {"timezone": {"type": "string"}}, "required": []},
        },
    }]
    r = p.chat_with_tools("Sei un assistente che usa tool.", "Che ora è a Tokyo?", tools)
    print(f"text={r.text!r}  tool_calls={r.tool_calls}  toks {r.in_tokens}->{r.out_tokens}  {r.latency_ms}ms")
