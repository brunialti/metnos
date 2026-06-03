"""Backend `images/google_vision.py` — Google Cloud Vision Web Detection
per reverse image search.

API: https://vision.googleapis.com/v1/images:annotate (feature
`WEB_DETECTION`). Input: immagine come `content` base64 (locale) o
`source.imageUri` (URL pubblico). Output: pagine web che contengono
l'immagine, immagini simili, entita' web (etichette), best guess label.

Token: refresh on-demand dal Google OAuth token in
`~/.hermes/google_token.json` (scope `cloud-vision`).

Quota: free tier 1000 unit/mese (1 unit = 1 feature per image). Oltre:
$1.50/1000 (WEB_DETECTION). Per uso personale resta in gratuito.
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from pathlib import Path

_VISION_URL = "https://vision.googleapis.com/v1/images:annotate"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_TOKEN = Path.home() / ".hermes" / "google_token.json"
_TIMEOUT_S = 30.0


def _refresh_access_token() -> str:
    """Refresh Google OAuth access_token a partire dal refresh_token salvato.

    Raise ValueError con error_class se token assente o refresh fallisce.
    """
    if not _GOOGLE_TOKEN.is_file():
        raise ValueError(f"token Google assente: {_GOOGLE_TOKEN} "
                         "(esegui `hermes auth` per autenticare)")
    tok = json.loads(_GOOGLE_TOKEN.read_text(encoding="utf-8"))
    if "cloud-vision" not in " ".join(tok.get("scopes", [])):
        raise ValueError("scope `cloud-vision` mancante nel token Google. "
                         "Re-OAuth con scope cloud-vision richiesto.")
    body = (f"client_id={tok['client_id']}&"
            f"client_secret={tok['client_secret']}&"
            f"refresh_token={tok['refresh_token']}&"
            f"grant_type=refresh_token")
    req = urllib.request.Request(
        _TOKEN_URL,
        data=body.encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as r:
        return json.loads(r.read())["access_token"]


def _annotate(image_payload: dict, access_token: str,
              max_results: int) -> dict:
    """POST singolo a Vision annotate con WEB_DETECTION feature.

    Raise ValueError(error_msg) se la response contiene `error` field
    (es. Vision non riesce ad accedere all'URL). Il caller decide se
    fare fallback (download locale + retry con content bytes).
    """
    body = json.dumps({
        "requests": [{
            "image": image_payload,
            "features": [{"type": "WEB_DETECTION",
                          "maxResults": max_results}],
        }],
    }).encode("utf-8")
    req = urllib.request.Request(
        _VISION_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as r:
        resp = json.loads(r.read())
    item = (resp.get("responses") or [{}])[0]
    if "error" in item:
        raise ValueError(item["error"].get("message", "vision error"))
    return item


def _resize_for_vision(path: str, *, max_side: int = 1280, quality: int = 85) -> bytes:
    """Resize immagine se lato max > `max_side`. Riduce payload Vision API
    (~80% size reduction tipico), abbassa latency e probabilita' SSL EOF
    su connessioni con MTU constrained.

    §7.3 universal: opt-in passivo — file < max_side ritornati così come sono.
    Pillow opzionale; se assente, ritorna bytes raw.
    """
    try:
        from PIL import Image
    except ImportError:
        return Path(path).read_bytes()
    try:
        img = Image.open(path)
        W, H = img.size
        scale = max_side / max(W, H)
        if scale >= 1.0:
            return Path(path).read_bytes()
        new_w, new_h = int(W * scale), int(H * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS).convert("RGB")
        from io import BytesIO
        out = BytesIO()
        img.save(out, format="JPEG", quality=quality)
        return out.getvalue()
    except (OSError, ValueError):
        return Path(path).read_bytes()


def _crop_face_to_bytes(path: str, face_box, *, pad_pct: float = 0.30) -> bytes:
    """Ritaglia area facciale con padding dal `face_box=[x,y,w,h]` e ritorna
    bytes JPEG. Padding 30% aumenta contesto (capelli/spalle) → Vision Web
    Detection migliora il matching faccia-a-faccia vs scena dominante.

    Universal §7.3: enable face-aware reverse image search. Se face_box
    invalido o Pillow assente, fallback a file completo.
    """
    try:
        from PIL import Image
    except ImportError:
        return Path(path).read_bytes()
    try:
        x, y, w, h = (int(v) for v in face_box[:4])
        if w <= 0 or h <= 0:
            return Path(path).read_bytes()
    except (TypeError, ValueError, IndexError):
        return Path(path).read_bytes()
    img = Image.open(path)
    W, H = img.size
    pad_x = int(w * pad_pct)
    pad_y = int(h * pad_pct)
    left = max(0, x - pad_x)
    top = max(0, y - pad_y)
    right = min(W, x + w + pad_x)
    bottom = min(H, y + h + pad_y)
    crop = img.crop((left, top, right, bottom)).convert("RGB")
    from io import BytesIO
    out = BytesIO()
    crop.save(out, format="JPEG", quality=88)
    return out.getvalue()


def _download_to_bytes(url: str) -> bytes:
    """Scarica URL come bytes con UA standard (Wikipedia/CDN bloccano UA
    generici tipo Vision-Backend). Usato come fallback quando Vision
    non riesce ad accedere direttamente all'URL."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 metnos-vision/1.0"},
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as r:
        return r.read()


def _normalize_response(source: str, vision_resp: dict) -> dict:
    """Estrae i 4 blocchi rilevanti dalla risposta Vision Web Detection."""
    wd = vision_resp.get("webDetection") or {}
    return {
        "source": source,
        "best_guess_label": (wd.get("bestGuessLabels") or [{}])[0].get(
            "label", ""),
        "matching_pages": [
            {"url": p.get("url", ""), "title": p.get("pageTitle", "")}
            for p in (wd.get("pagesWithMatchingImages") or [])
        ],
        "similar_images": [
            i.get("url", "")
            for i in (wd.get("visuallySimilarImages") or [])
        ],
        "web_entities": [
            {"description": e.get("description", ""),
             "score": round(float(e.get("score", 0)), 3)}
            for e in (wd.get("webEntities") or [])
            if e.get("description")
        ],
    }


def find_images_web(args: dict) -> dict:
    """Reverse image search via Google Cloud Vision Web Detection.

    Args:
        paths: list[str] di path locali a immagini (jpg/png/gif/bmp/webp).
        urls:  list[str] di URL pubblici di immagini.
        max_results: int cap per ciascun blocco (default 10).

    Output:
        entries: list[{source, best_guess_label, matching_pages,
                       similar_images, web_entities}]
        ok_count: int
        errors:   list[{source, error, error_class}]
    """
    # §2.4 forgiving: path-like in `urls` → spostati in `paths` e
    # viceversa http(s) in `paths` → `urls`. Patch sistemica del confine
    # NL→determinismo (PLANNER LLM confonde tipo arg su nomi generici
    # tipo "url"). Vedi turn live 2a5f2711.
    try:
        from executor_helpers import normalize_paths_urls
        args = normalize_paths_urls(args)
    except ImportError:
        pass
    paths = args.get("paths") or []
    urls = args.get("urls") or []
    max_results = int(args.get("max_results") or 10)
    # §7.3 face-aware: face_boxes parallel a paths/urls per ritagliare
    # area facciale prima dell'invio a Vision (faccia-a-faccia vs scena
    # dominante). Stesso ordine di paths+urls (paths first).
    face_boxes = args.get("face_boxes") or []
    if isinstance(face_boxes, str):
        face_boxes = [face_boxes] if face_boxes else []

    if not paths and not urls:
        return {"ok": False, "error": "almeno uno fra `paths` e `urls` "
                                       "deve essere non-vuoto",
                "error_class": "invalid_args"}

    # Cap MAX_PATHS_PER_CALL §2.7 (25/5/2026): Google Vision Web Detection
    # processa ogni source con una HTTP request sincrona ~1-3s ciascuna.
    # Con >10 sources la call totale supera il timeout executor (60s) → bug
    # live turn 7c7312e9. Cap a 10 sources/call complessivi (paths+urls);
    # esubero in `truncated:True` con cap_field/cap_value standard.
    _MAX_SOURCES = 10
    _total_requested = len(paths) + len(urls)
    _truncated_sources = _total_requested > _MAX_SOURCES
    if _truncated_sources:
        # Slice deterministico §7.9: paths first, urls fill remaining slots.
        _np = min(len(paths), _MAX_SOURCES)
        paths = list(paths)[:_np]
        urls = list(urls)[:_MAX_SOURCES - _np]

    try:
        access_token = _refresh_access_token()
    except (ValueError, urllib.error.HTTPError, OSError) as e:
        return {"ok": False, "error": str(e),
                "error_class": "auth_required"}

    entries: list[dict] = []
    errors: list[dict] = []

    for idx, path_str in enumerate(paths):
        p = Path(path_str)
        if not p.is_file():
            errors.append({"source": path_str,
                           "error": "file non trovato",
                           "error_class": "not_found"})
            continue
        # §7.3 face crop: se face_boxes[idx] disponibile e valido,
        # invia solo l'area del volto (con padding) a Vision invece
        # del file intero. Migliora face similarity vs scene matching.
        # §7.3 resize: limita payload a 1280px lato max (riduce ~80% size
        # e latency, abbatte rischio SSL EOF su MTU constrained).
        box = face_boxes[idx] if idx < len(face_boxes) else None
        try:
            if box:
                img_bytes = _crop_face_to_bytes(str(p), box)
            else:
                img_bytes = _resize_for_vision(str(p))
            content_b64 = base64.b64encode(img_bytes).decode("ascii")
        except OSError as e:
            errors.append({"source": path_str, "error": str(e),
                           "error_class": "network"})
            continue
        # §7.9 retry transient: SSL EOF, sslv3 alert bad_record_mac, etc
        # sono noti su Vision API con MTU edge cases. Retry 2× con backoff.
        last_err = None
        for attempt in range(3):
            try:
                resp = _annotate({"content": content_b64},
                                 access_token, max_results)
                entries.append(_normalize_response(path_str, resp))
                last_err = None
                break
            except urllib.error.HTTPError as e:
                # 5xx retry, 4xx no.
                if 500 <= e.code < 600 and attempt < 2:
                    import time as _t
                    _t.sleep(1.0 * (attempt + 1))
                    last_err = (e, _classify_http(e.code))
                    continue
                last_err = (e, _classify_http(e.code))
                break
            except (OSError, urllib.error.URLError) as e:
                msg = str(e)
                is_transient = ("SSL" in msg or "EOF" in msg or
                                 "bad_record_mac" in msg or
                                 "timed out" in msg)
                if is_transient and attempt < 2:
                    import time as _t
                    _t.sleep(1.0 * (attempt + 1))
                    last_err = (e, "network")
                    continue
                last_err = (e, "network")
                break
        if last_err is not None:
            errors.append({"source": path_str,
                           "error": str(last_err[0]),
                           "error_class": last_err[1]})

    for url in urls:
        try:
            resp = _annotate({"source": {"imageUri": url}},
                             access_token, max_results)
            entries.append(_normalize_response(url, resp))
        except ValueError:
            # Vision non puo' accedere all'URL (UA bloccato, CDN, ecc.).
            # Fallback §7.3: scarica con UA standard e re-prova con content.
            try:
                img_bytes = _download_to_bytes(url)
                content_b64 = base64.b64encode(img_bytes).decode("ascii")
                resp = _annotate({"content": content_b64},
                                 access_token, max_results)
                entries.append(_normalize_response(url, resp))
            except (ValueError, urllib.error.HTTPError, OSError) as e2:
                errors.append({"source": url, "error": str(e2),
                               "error_class": "network"})
        except urllib.error.HTTPError as e:
            errors.append({"source": url, "error": str(e),
                           "error_class": _classify_http(e.code)})
        except OSError as e:
            errors.append({"source": url, "error": str(e),
                           "error_class": "network"})

    # Semantica `ok` §2.8: True solo se almeno un'entry e' stata prodotta
    # OPPURE nessun errore per-source (input vuoto legitto). Falso se
    # tutti i source hanno fallito (entries=[] AND errors!=[]): senza
    # questa rifrazione, l'auto-final compose emette «completato (0
    # elementi)» disonesto invece di riportare gli errori reali.
    has_entries = bool(entries)
    has_errors = bool(errors)
    # Universal §7.3: attachments rendering chat — esplode similar_images
    # (URL) di ogni entry in attachment kind=image, score decrescente per
    # ordine API. Standard 20 inline + 100 gallery via http_routes_agent.
    attachments: list[dict] = []
    for entry in entries:
        sims = entry.get("similar_images") or []
        label = entry.get("best_guess_label") or ""
        for rank, url in enumerate(sims):
            if not isinstance(url, str) or not url:
                continue
            att: dict = {
                "kind": "image",
                "url": url,
                "basename": url.rsplit("/", 1)[-1][:80],
                "score": round(1.0 - rank * 0.02, 3),
            }
            if label:
                att["caption"] = label
            attachments.append(att)
    out = {
        "ok": has_entries or not has_errors,
        "entries": entries,
        "ok_count": len(entries),
        "errors": errors,
        "attachments": attachments,
    }
    if _truncated_sources:
        out.update({
            "truncated": True,
            "truncated_what": "fonti",
            "used": _MAX_SOURCES,
            "available_total": _total_requested,
            "cap_field": "max_sources_per_call",
            "cap_value": _MAX_SOURCES,
        })
    return out


def _classify_http(code: int) -> str:
    if code in (401, 403):
        return "auth_required"
    if code == 404:
        return "not_found"
    if code == 429:
        return "rate_limited"
    if 500 <= code < 600:
        return "server_error"
    return "unknown"
