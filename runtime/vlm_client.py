# SPDX-License-Identifier: AGPL-3.0-only
"""vlm_client — client VLM condiviso per descrivere il CONTENUTO di un'immagine.

Single source of truth della chiamata VLM ad-hoc (vs il path index-build di
`create_images_indices`, che usa un prompt con hint cartella/filename per il
CATALOGAZIONE). Qui il caso d'uso e' «descrivi questa foto» (upload senza testo,
builtin `describe_images`): nessun contesto cartella, solo «cosa c'e' nella
foto» → JSON {description, keywords}.

Config VLM virtualizzata via `virt.get_vlm()` (~/.config/metnos/vlm_tiers.toml),
override env `METNOS_VLM_*`. Endpoint default :8081 (OpenAI-compatible
/v1/chat/completions). Lazy auto-start via `virt.ensure_vlm_up` se il server e'
giu'. Fail-safe deterministico §2.8: su errore ritorna dict con default vuoti +
`_vlm_error` (il chiamante decide).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path


def _resolve_vlm_url(env_val: str | None) -> str:
    """Normalizza l'URL VLM: base (http://h:port) → +/v1/chat/completions."""
    val = (env_val or "http://127.0.0.1:8081").strip()
    if "/v1/" in val or val.endswith("/chat/completions"):
        return val
    return val.rstrip("/") + "/v1/chat/completions"


def _vlm_cfg() -> dict:
    try:
        from virt import get_vlm
        return get_vlm()
    except Exception:
        return {}


_VLM = _vlm_cfg()
_VLM_URL = _resolve_vlm_url(os.environ.get("METNOS_VLM_URL") or _VLM.get("endpoint"))
_VLM_MODEL = os.environ.get("METNOS_VLM_MODEL") or _VLM.get("model", "qwen3vl-2b")
_VLM_TIMEOUT_S = int(os.environ.get("METNOS_VLM_TIMEOUT_S") or _VLM.get("timeout_s", 60))
_VLM_MAX_EDGE = int(os.environ.get("METNOS_VLM_MAX_EDGE") or _VLM.get("max_edge", 1024))
_VLM_MAX_TOKENS = int(os.environ.get("METNOS_VLM_MAX_TOKENS") or _VLM.get("max_tokens", 512))


def _describe_prompt(lang: str) -> str:
    """Prompt ad-hoc «descrivi questa foto» → JSON {description, keywords}.
    La descrizione e' RICCA/DETTAGLIATA di proposito: serve come QUERY di
    RICERCA del contenuto (match cosine/BM25 contro l'indice VLM) — piu'
    dettaglio = piu' recall. Niente cap di parole, niente hint cartella (il
    caso d'uso e' una foto caricata, non un file in un albero indicizzato)."""
    import prompt_loader
    return prompt_loader.get("vlm_describe_image", lang or "it")


def _parse_vlm_text(text: str) -> dict:
    """Estrae JSON dall'output VLM. Robusto a wrapping ```json ...```."""
    s = (text or "").strip()
    if s.startswith("```"):
        lines = s.split("\n")
        if len(lines) >= 2:
            s = "\n".join(
                lines[1:-1] if lines[-1].strip().startswith("```") else lines[1:])
    try:
        d = json.loads(s)
    except json.JSONDecodeError:
        i, j = s.find("{"), s.rfind("}")
        if i >= 0 and j > i:
            try:
                d = json.loads(s[i:j + 1])
            except json.JSONDecodeError:
                return _vlm_fail("invalid_json_text")
        else:
            return _vlm_fail("no_json_found")
    if not isinstance(d, dict):
        return _vlm_fail("not_dict")
    return {
        "description": str(d.get("description", "")).strip(),
        "keywords": [str(k).strip() for k in d.get("keywords", [])
                     if isinstance(k, (str, int, float))],
        "location_hint": str(d.get("location_hint", "")).strip(),
        "activity_hint": str(d.get("activity_hint", "")).strip(),
    }


def _vlm_fail(reason: str) -> dict:
    return {"description": "", "keywords": [], "location_hint": "",
            "activity_hint": "", "_vlm_error": reason}


def _looks_like_connection_refused(err) -> bool:
    s = str(getattr(err, "reason", err)).lower()
    return "refused" in s or "errno 111" in s or "connection refused" in s


def _lazy_start_vlm() -> bool:
    try:
        from virt import ensure_vlm_up
    except ImportError:
        return False
    try:
        return bool(ensure_vlm_up())
    except Exception:
        return False


def describe_image(img_path, *, lang: str | None = None,
                   prompt: str | None = None,
                   url: str | None = None, model: str | None = None,
                   timeout_s: int | None = None,
                   max_tokens: int | None = None) -> dict:
    """Descrive il CONTENUTO di un'immagine col VLM. Ritorna dict
    {description, keywords, location_hint, activity_hint} (+`_vlm_error` su
    fallimento, mai solleva — fail-safe §2.8). `lang` default da config.
    `prompt` override del prompt VLM (default = `_describe_prompt(lang)`,
    ad-hoc per ricerca; create_images_indices passa il suo prompt index-build).
    `max_tokens` default 1024 (descrizione RICCA per ricerca, vs 512 caption)."""
    import base64
    from io import BytesIO

    url = url or _VLM_URL
    model = model or _VLM_MODEL
    timeout_s = timeout_s or _VLM_TIMEOUT_S
    max_tokens = max_tokens or max(1024, _VLM_MAX_TOKENS)
    if lang is None:
        try:
            from config import DEFAULT_LANG as _dl  # type: ignore
            lang = _dl
        except Exception:
            lang = "it"

    img_path = Path(img_path)
    try:
        from PIL import Image
        with Image.open(img_path) as _src:
            _src.load()
            _img = _src if _src.mode == "RGB" else _src.convert("RGB")
            _le = max(_img.size)
            if _le > _VLM_MAX_EDGE:
                _scale = _VLM_MAX_EDGE / float(_le)
                _img = _img.resize(
                    (max(1, int(_img.size[0] * _scale)),
                     max(1, int(_img.size[1] * _scale))), Image.LANCZOS)
            _buf = BytesIO()
            _img.save(_buf, format="JPEG", quality=85)
            b64 = base64.b64encode(_buf.getvalue()).decode("ascii")
    except OSError as e:
        return _vlm_fail(f"read_failed: {e!r}")
    except Exception as e:  # noqa: BLE001
        return _vlm_fail(f"resize_failed: {e!r}")

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "image_url",
             "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            {"type": "text", "text": prompt or _describe_prompt(lang)},
        ]}],
        # temp 0.2 (come il batch-index): tunato per output JSON stabile su
        # qwen3vl. temp=0/seed danno output degenere NON-JSON (no_json_found su
        # questo VLM) → la varieta' di coda nei risultati di ricerca e' un
        # trade-off accettato (la rotta describe→search e l'esito `answer`
        # restano deterministici; solo la coda dei match semantici varia).
        "temperature": 0.2, "max_tokens": max_tokens,
        "top_p": 0.8, "top_k": 20, "presence_penalty": 0.0, "repeat_penalty": 1.0,
    }
    try:
        from utf8_safe import safe_json_dumps as _safe_dumps  # type: ignore
        body = _safe_dumps(payload).encode("utf-8")
    except Exception:
        body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.URLError as e:
        if _looks_like_connection_refused(e) and _lazy_start_vlm():
            try:
                with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                    raw = resp.read().decode("utf-8")
            except (urllib.error.URLError, urllib.error.HTTPError,
                    TimeoutError, OSError) as e2:
                return _vlm_fail(f"http_failed_after_lazy_start: {e2!r}")
        else:
            return _vlm_fail(f"http_failed: {e!r}")
    except (urllib.error.HTTPError, TimeoutError, OSError) as e:
        return _vlm_fail(f"http_failed: {e!r}")

    try:
        out = json.loads(raw)
        text = out["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        return _vlm_fail("resp_unparseable")
    return _parse_vlm_text(text)
