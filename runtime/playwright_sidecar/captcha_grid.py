# SPDX-License-Identifier: MIT
"""A reCAPTCHA 3x3 image grid answered by a public local classifier.

Roberto, 29/10/2026: «usa solo risolutore pubblico senza usare frontier; se non
passa da solo passa all'utente il captcha». The public classifier
recaptcha-classification-57k (YOLO, ONNX; the model card says MIT, the
export metadata written by Ultralytics says AGPL-3.0) labels the numbered tiles;
this code decides and acts. A fresh 3x3 grid holds three tiles with the
object (480 of 497 real 2026 grids): it is answered only when exactly three
tiles stand out; a replacement tile only when it is clearly in or clearly out.
A grid in doubt, a 4x4 grid or an unknown target is reloaded, as a person
would, up to MAX_DRAWS grids; then, or with an absent model, the person
answers. On 662 real 2026 grids (private report of 29/10) the first view is
57% exact and 0.8% with a wrong tile, against 41% and 1.1% for the earlier
all-tiles-clear rule; five-fold cross-validation, thresholds chosen inside each
fold, gives 58% and 1.0%. Averaging mirrored tiles measured worse.

No cloud model is involved. The instruction's object, in the page language,
is the classifier's class when the words are identical; otherwise the local
model labels it, in any language.
"""
from __future__ import annotations

import ast
import asyncio
import base64
from dataclasses import dataclass
import hashlib
import io
import json
import math
import random
import threading
import time

from spin_supervisor import (Budget, BudgetExhausted, CallbackBroker, DrainIncomplete,
                             Fence, FenceChanged, ResourceBusy)
from playwright_sidecar import captcha_cases, challenges

MODEL_FILE = ("captcha-grid", "recaptcha_classification_57k.onnx")
MODEL_SHA256 = "4092e8917ee8c2963895d66ba10a97d6ef975c468a95858a8a7bd9e70681b65d"
INPUT_SIZE = 640          # the export's input; parity with Ultralytics verified
MATCHES = 3               # tiles with the object in a fresh 3x3 grid
TOP_AT = 0.70             # the third highest tile at or above,
REST_AT = 0.15            # the fourth at or below: the three are selected
SELECT_AT = 0.90          # a replacement tile at or above is selected,
REJECT_AT = 0.05          # at or below it is left; between, the grid is reloaded
MAX_DRAWS = 6             # grids looked at in one appearance: first, reloads, next grids
MAX_EFFECTS = 40          # tile clicks, verify and reload presses in one appearance
MAX_MODEL_CALLS = 2 * MAX_DRAWS  # two local questions per new instruction word
LABEL_TOKENS = 512        # reserved per question, prompt included
LABEL_CALL_S = 15.0       # one local question at most
READY_WAIT_S = 5.0        # a grid and its tiles appear within this
REPLACE_WAIT_S = 6.0      # a dynamic grid replaces every clicked tile within this
VERIFY_WAIT_S = 8.0       # outcome of a verify press
RELOAD_WAIT_S = 6.0       # a reload shows a new grid within this
SETTLE_S = 0.4

REASONS = frozenset({
    "grid_accepted", "grid_model_unavailable", "grid_frame_not_unique", "grid_not_ready",
    "grid_layout_unsupported", "grid_target_unknown", "grid_uncertain",
    "grid_tiles_unavailable", "grid_rejected", "grid_round_limit", "grid_effect_budget",
    "grid_incomplete", "grid_reload_failed", "grid_page_changed", "grid_page_moved_on",
    "grid_command_failed", "grid_observer_error", "grid_outcome_timeout", "grid_time_budget",
})
# A person asks for another grid on these; so does the solver while draws remain.
RELOADABLE = frozenset({"grid_layout_unsupported", "grid_target_unknown", "grid_uncertain",
                        "grid_incomplete"})

HARD_CASES = RELOADABLE | {"grid_rejected"}

_FRAMES_JS = "els=>{" + challenges._HELPERS_JS + """
  return els.map((el,index)=>({index,info:providerFrame(el),visible:visible(el)}))
    .filter(i=>i.visible&&i.info&&i.info.provider==='recaptcha'&&i.info.kind==='interactive');
}"""

# Observed inside the provider's challenge frame only.
_GRID_JS = r"""() => {
  const shown = el => { if (!el) return false; const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    for (let p = el; p; p = p.parentElement) { const s = getComputedStyle(p);
      if (s.display === 'none' || s.visibility === 'hidden') return false; }
    return true; };
  const root = document.querySelector('#rc-imageselect');
  if (!shown(root)) return {state: 'absent'};
  const cells = [...document.querySelectorAll('#rc-imageselect-target table td')];
  const strong = document.querySelector(
    '.rc-imageselect-desc-wrapper strong, .rc-imageselect-desc strong, .rc-imageselect-desc-no-canonical strong');
  const errors = [...document.querySelectorAll(
    '.rc-imageselect-incorrect-response, .rc-imageselect-error-select-more, ' +
    '.rc-imageselect-error-dynamic-more, .rc-imageselect-error-select-something')].some(shown);
  return {state: 'grid', count: cells.length,
    keyword: strong ? strong.textContent.replace(/\s+/g, ' ').trim().slice(0, 80) : '',
    errors, verify: shown(document.querySelector('#recaptcha-verify-button')),
    tiles: cells.map(td => { const img = td.querySelector('img');
      return {src: img ? img.src : '', single: Boolean(img && img.classList.contains('rc-image-tile-11')),
        ready: Boolean(img && img.complete && img.naturalWidth > 0
                       && Number(getComputedStyle(img).opacity) > 0.99
                       && !td.classList.contains('rc-imageselect-dynamic-selected')),
        selected: td.classList.contains('rc-imageselect-tileselected')}; })};
}"""

# Pixels of the tiles as the provider sent them: no overlay, no page capture.
_TILES_JS = r"""indices => {
  const cells = [...document.querySelectorAll('#rc-imageselect-target table td')];
  const side = Math.round(Math.sqrt(cells.length));
  return indices.map(i => { const img = cells[i].querySelector('img');
    const canvas = document.createElement('canvas');
    let sx = 0, sy = 0, sw = img.naturalWidth, sh = img.naturalHeight;
    if (!img.classList.contains('rc-image-tile-11')) {
      sw = img.naturalWidth / side; sh = img.naturalHeight / side;
      sx = (i % side) * sw; sy = Math.floor(i / side) * sh; }
    canvas.width = Math.round(sw); canvas.height = Math.round(sh);
    canvas.getContext('2d').drawImage(img, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
    return {src: img.src, png: canvas.toDataURL('image/png').split(',')[1]}; });
}"""


class GridUnavailable(Exception):
    pass


@dataclass(frozen=True)
class GridResult:
    status: str  # accepted | manual | blocked; never logged_in.
    reason: str
    attempted: bool


# ---------------------------------------------------------------- classifier

_model_lock = threading.Lock()
_model = None  # (session, names) once verified


def _load_model():
    global _model
    with _model_lock:
        if _model is not None:
            return _model
        import config as C
        path = C.PATH_MODELS.joinpath(*MODEL_FILE)
        if not path.is_file():
            return None
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
        if digest.hexdigest() != MODEL_SHA256:
            return None
        import onnxruntime as ort
        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        session = ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
        names = ast.literal_eval(session.get_modelmeta().custom_metadata_map["names"])
        _model = (session, {int(k): str(v) for k, v in names.items()})
        return _model


def model_classes() -> dict[int, str] | None:
    loaded = _load_model()
    return loaded[1] if loaded else None


def _tensor(png: bytes):
    import numpy as np
    from PIL import Image
    image = Image.open(io.BytesIO(png)).convert("RGB")
    width, height = image.size
    ratio = INPUT_SIZE / min(width, height)
    image = image.resize((max(INPUT_SIZE, round(width * ratio)),
                          max(INPUT_SIZE, round(height * ratio))), Image.BILINEAR)
    width, height = image.size
    left, top = (width - INPUT_SIZE) // 2, (height - INPUT_SIZE) // 2
    crop = image.crop((left, top, left + INPUT_SIZE, top + INPUT_SIZE))
    return (np.asarray(crop, dtype=np.float32) / 255.0).transpose(2, 0, 1)


def score(pngs: list[bytes]) -> list[list[float]]:
    """Class probabilities per tile, in the model's class order."""
    import numpy as np
    session, _names = _load_model()
    batch = np.stack([_tensor(png) for png in pngs])
    output = session.run(None, {session.get_inputs()[0].name: batch})[0]
    return [[float(x) for x in row] for row in output]


def decide_grid(probabilities: list[float]) -> list[int] | None:
    """The MATCHES tiles that stand out of a fresh grid, or None."""
    if not all(math.isfinite(p) for p in probabilities):
        return None
    order = sorted(range(len(probabilities)), key=lambda i: -probabilities[i])
    if len(order) <= MATCHES:
        return None
    if probabilities[order[MATCHES - 1]] >= TOP_AT and probabilities[order[MATCHES]] <= REST_AT:
        return sorted(order[:MATCHES])
    return None


def decide_tiles(probabilities: list[float]) -> list[int] | None:
    """Replacement tiles to select, or None when any is neither clearly in nor out."""
    if not all(math.isfinite(p) for p in probabilities) or any(REJECT_AT < p < SELECT_AT for p in probabilities):
        return None
    return [i for i, p in enumerate(probabilities) if p >= SELECT_AT]


def exact_class(keyword: str, names: dict[int, str]) -> int | None:
    """The class whose name is the keyword itself, in any script; no word rules."""
    key = " ".join(keyword.casefold().split())
    hits = [i for i, name in names.items()
            if name.casefold() != "other" and " ".join(name.casefold().split()) == key]
    return hits[0] if len(hits) == 1 else None


_labels: dict[str, int | None] = {}


def _ask(provider, budget: Budget, role: str, **values):
    import i18n
    import prompt_loader
    lease = budget.reserve_model(LABEL_TOKENS)
    timeout = max(1.0, min(LABEL_CALL_S, lease.deadline - time.monotonic()))
    prompt = prompt_loader.get(role, i18n.current_lang(), **values)
    reply = provider.chat(prompt, "", max_tokens=24, request_timeout_s=timeout)
    reply = json.loads(str(getattr(reply, "text", "") or ""))
    return reply if isinstance(reply, dict) else {}


def label_target(keyword: str, names: dict[int, str], budget: Budget) -> int | None:
    """Local model labels which numbered class names the instruction's object.

    Two questions, in whatever language the page writes: which class the word
    names, then whether every member of that class is named by the word, so a
    subtype (a taxi among cars) selects no class. Only a local tier is
    accepted: the grid never reaches a cloud model.
    """
    key = " ".join(keyword.casefold().split())
    if key in _labels:
        return _labels[key]
    numbered = [(i, name) for i, name in sorted(names.items()) if name.casefold() != "other"]
    try:
        from llm_router import LLMRouter
        from llm_workloads import tier_for
        provider = LLMRouter().provider(tier_for("sites.challenge_target"))
        if getattr(provider, "mode", "") != "local":
            return None
        word = json.dumps(keyword[:80], ensure_ascii=False)
        forward = _ask(provider, budget, "sites_challenge_target", keyword_json=word,
                       classes_json=json.dumps({str(n + 1): name for n, (_i, name) in enumerate(numbered)}))
        number = forward.get("class")
        if not (type(number) is int and 1 <= number <= len(numbered)) or forward.get("subtype") is not False:
            _labels[key] = None
            return None
        index, name = numbered[number - 1]
        reverse = _ask(provider, budget, "sites_challenge_target_reverse", keyword_json=word,
                       class_json=json.dumps(name))
    except Exception:  # noqa: BLE001 -- no label: the person answers the grid
        return None
    _labels[key] = index if reverse.get("every") is True else None
    return _labels[key]


async def resolve_target(keyword: str, names: dict[int, str], budget: Budget) -> int | None:
    if not keyword:
        return None
    found = exact_class(keyword, names)
    if found is not None:
        return found
    return await asyncio.to_thread(label_target, keyword, names, budget)


# ------------------------------------------------------------------- broker

class GridBroker:
    """Trusted adapter for one provider challenge frame under the session loan."""

    def __init__(self, *, page, session: str, owner: str, resource_lock,
                 resource_loan, binding_valid, site_accepted):
        self.page, self.session, self.owner = page, session, owner
        self.binding_valid, self.site_accepted = binding_valid, site_accepted
        self.revision = 0
        self.frame = self.iframe = None
        self.source = ""
        self.attempted = False
        self.case = None  # the grid being answered, for the hard-cases store
        self.draws = 0
        self.reloads = 0
        self.clicks = 0
        self.main_navigations = 0
        self.verify_mark = None   # main navigations seen when Verify was last pressed
        self.callback = CallbackBroker(resource_lock=resource_lock, resource_loan=resource_loan,
                                       current_fence=self.fence, apply=self._apply,
                                       verify=self._never)
        self.listener = self._navigated
        self.page.on("framenavigated", self.listener)
        self._closed = False

    def _navigated(self, frame):
        # The page or the held challenge frame changed document: revoke.
        if frame is self.page.main_frame:
            self.main_navigations += 1
            self.revision += 1
        elif self.frame is not None and frame is self.frame:
            self.revision += 1

    def navigated_after_verify(self) -> bool:
        """The site moved on after an answer: the page, not the grid, tells the outcome."""
        return self.verify_mark is not None and self.main_navigations > self.verify_mark

    def fence(self):
        if self.binding_valid() is not True or self.page.is_closed():
            self.callback.revoke()
        return Fence(self.session, self.owner, self.revision)

    async def _never(self):
        return False

    async def bind(self, budget: Budget):
        self.expected = self.fence()
        self.callback.check(self.expected, budget)
        frames = await self.page.locator("iframe").evaluate_all(_FRAMES_JS)
        if len(frames) != 1:
            raise GridUnavailable("grid_frame_not_unique")
        self.iframe = await self.page.locator("iframe").nth(frames[0]["index"]).element_handle(
            timeout=min(500, max(1, (budget.deadline - time.monotonic()) * 1000)))
        self.frame = await self.iframe.content_frame() if self.iframe is not None else None
        if self.frame is None:
            raise GridUnavailable("grid_frame_not_unique")
        self.source = self.frame.url
        self.callback.check(self.expected, budget)

    async def observe(self, budget: Budget, *, wait: float = READY_WAIT_S,
                      appear: bool = False) -> dict:
        """The grid once its tiles have loaded, waiting at most `wait` seconds.

        An absent grid is returned at once, unless `appear` waits for it too.
        """
        stop = min(budget.deadline, time.monotonic() + wait)
        while True:
            self.callback.check(self.expected, budget)
            try:
                grid = await self.frame.evaluate(_GRID_JS)
            except Exception as error:  # noqa: BLE001
                raise GridUnavailable("grid_observer_error") from error
            if not isinstance(grid, dict):
                raise GridUnavailable("grid_observer_error")
            loaded = grid.get("count") and all(t.get("ready") for t in grid.get("tiles", []))
            if (grid.get("state") == "grid" and loaded) or (grid.get("state") != "grid" and not appear):
                return grid
            if time.monotonic() >= stop:
                return grid
            await asyncio.sleep(0.1)

    async def tiles(self, indices: list[int], budget: Budget) -> list[tuple[str, bytes]]:
        """(image source, pixels) of each tile, read together."""
        self.callback.check(self.expected, budget)
        try:
            encoded = await self.frame.evaluate(_TILES_JS, indices)
            return [(str(item["src"]), base64.b64decode(item["png"])) for item in encoded]
        except Exception as error:  # noqa: BLE001 -- a tainted or missing image
            raise GridUnavailable("grid_tiles_unavailable") from error

    async def _apply(self, action, budget):
        import sites_audit
        if action.get("action") == "tile":
            locator = self.frame.locator("#rc-imageselect-target table td").nth(action["index"])
        elif action.get("action") == "verify":
            locator = self.frame.locator("#recaptcha-verify-button")
        elif action.get("action") == "reload":
            locator = self.frame.locator("#recaptcha-reload-button")
        else:
            raise GridUnavailable("grid_command_failed")
        fields = {"owner": self.owner, "session_id": self.session, "action": "click",
                  "action_origin": {"actor_type": "executor", "executor": "playwright_sidecar",
                                    "source": "public_grid_classifier"},
                  "decision_actor": {"actor_type": "executor", "executor": "playwright_sidecar",
                                     "source": "public_grid_classifier"}}

        async def command():
            self.attempted = True
            sites_audit.record("captcha_action", outcome="requested", **fields)
            await locator.click(timeout=min(1500, max(1, (budget.deadline - time.monotonic()) * 1000)),
                                force=False, no_wait_after=True)
        try:
            await self.callback.native_command(command, self.expected, budget, name="captcha-grid-click")
            sites_audit.record("captcha_action", outcome="completed", **fields)
        except BaseException:
            if self.attempted:
                sites_audit.record("captcha_action", outcome="unconfirmed", **fields)
            raise

    async def click(self, action: dict, budget: Budget):
        await self.callback.effect(action, self.expected, budget)
        self.clicks += 1
        await asyncio.sleep(random.uniform(0.25, 0.6))  # a person's pace between clicks

    async def _dispose(self):
        if not self._closed:
            self.page.remove_listener("framenavigated", self.listener)
            self._closed = True
        if self.iframe is not None:
            await self.iframe.dispose()
            self.iframe = None

    async def close(self):
        self.callback.revoke()
        self.callback.defer_cleanup(self._dispose)
        await self.callback.drained()
        self.callback.release_resource()


async def _score_target(tiles: list[bytes], target: int) -> list[float]:
    return [row[target] for row in await asyncio.to_thread(score, tiles)]


def _fresh(grid: dict, shown: set[str]) -> bool:
    """A new challenge, loaded: none of the shown images, no replacement tile."""
    tiles = grid.get("tiles", [])
    return bool(grid.get("state") == "grid" and tiles and all(
        t.get("ready") and not t.get("single") and t.get("src") not in shown for t in tiles))


def _unchanged(grid: dict, keyword: str, scored: dict[int, str]) -> bool:
    """The grid still shows the instruction and exactly the pixels that were scored."""
    tiles = grid.get("tiles", [])
    return (grid.get("state") == "grid" and grid.get("keyword") == keyword and len(tiles) == 9
            and all(t.get("ready") and t.get("src") == scored.get(i) for i, t in enumerate(tiles)))


async def _answer_grid(broker: GridBroker, grid: dict, names: dict, budget: Budget) -> str | dict:
    """Select and verify one 3x3 grid: the grid as verified, or why it is not answered."""
    if not all(tile.get("ready") for tile in grid["tiles"]) or any(t["selected"] for t in grid["tiles"]):
        return "grid_not_ready"
    keyword = grid.get("keyword", "")
    shown = await broker.tiles(list(range(len(grid["tiles"]))), budget)
    broker.case = {"tiles": [png for _src, png in shown], "keyword": keyword, "scores": []}
    if grid.get("count") != 9:
        return "grid_layout_unsupported"
    target = await resolve_target(keyword, names, budget)
    if target is None:
        return "grid_target_unknown"
    scored = {i: src for i, (src, _png) in enumerate(shown)}
    first_view = await _score_target([png for _src, png in shown], target)
    broker.case["scores"] = first_view
    chosen = decide_grid(first_view)
    if chosen is None:
        return "grid_uncertain"
    pending = list(chosen)
    while pending:
        before = await broker.observe(budget)
        if not _unchanged(before, keyword, scored):
            return "grid_uncertain"
        for index in pending:
            await broker.click({"action": "tile", "index": index}, budget)
        # A dynamic grid replaces each clicked tile; a static one marks it.
        replaced = await _replacements(broker, before, pending, budget)
        if replaced is None:
            return "grid_uncertain"
        if not replaced:
            # A static grid has no fixed count of matches: every tile must be clear.
            if decide_tiles(first_view) != chosen:
                return "grid_uncertain"
            break
        shown = await broker.tiles(replaced, budget)
        scored.update({i: src for i, (src, _png) in zip(replaced, shown)})
        verdict = decide_tiles(await _score_target([png for _src, png in shown], target))
        if verdict is None:
            return "grid_uncertain"
        pending = [replaced[i] for i in verdict]
    verified = await broker.observe(budget)
    if not _unchanged(verified, keyword, scored):
        return "grid_uncertain"  # a tile that was never scored: no answer
    broker.verify_mark = broker.main_navigations
    await broker.click({"action": "verify"}, budget)
    return verified


async def _keep_case(broker: GridBroker, reason: str) -> None:
    """Keep the grid no rule answered; the login never depends on it."""
    case, broker.case = broker.case, None
    if case is not None and reason in HARD_CASES:
        try:
            await asyncio.to_thread(captcha_cases.save, reason=reason, **case)
        except Exception:  # noqa: BLE001 - a lost sample, not a failed login
            pass


async def _replacements(broker: GridBroker, before: dict, clicked: list[int],
                        budget: Budget) -> list[int] | None:
    """Every clicked tile replaced and loaded, [] when a static grid marked exactly
    the clicked tiles, None when neither happened in time."""
    until = min(budget.deadline, time.monotonic() + REPLACE_WAIT_S)
    old = {i: before["tiles"][i]["src"] for i in clicked}
    while time.monotonic() < until:
        await asyncio.sleep(0.2)
        grid = await broker.observe(budget, wait=0)
        if grid.get("state") != "grid" or grid.get("count") != 9:
            return None
        tiles = grid["tiles"]
        selected = {i for i, tile in enumerate(tiles) if tile["selected"]}
        if selected == set(clicked) and all(tiles[i]["src"] == old[i] for i in clicked):
            return []
        if not selected and all(tiles[i]["src"] != old[i] and tiles[i]["single"] and tiles[i]["ready"]
                                for i in clicked):
            await asyncio.sleep(SETTLE_S)
            return list(clicked)
    return None


async def _reload(broker: GridBroker, budget: Budget) -> dict:
    """Ask the provider for another grid: the new grid once it has loaded."""
    current = await broker.observe(budget, wait=0)
    shown = {tile.get("src") for tile in current.get("tiles", [])}
    await broker.click({"action": "reload"}, budget)
    broker.reloads += 1
    until = min(budget.deadline, time.monotonic() + RELOAD_WAIT_S)
    while time.monotonic() < until:
        grid = await broker.observe(budget, wait=max(0.0, until - time.monotonic()), appear=True)
        if _fresh(grid, shown):
            return grid
        await asyncio.sleep(0.2)
    raise GridUnavailable("grid_reload_failed")


async def _accepted(broker: GridBroker) -> bool:
    try:
        return await broker.site_accepted() is True
    except Exception:  # noqa: BLE001 -- the page is looked at again
        return False


async def _outcome(broker: GridBroker, before: dict, budget: Budget) -> str:
    """accepted | moved_on | new_grid | incomplete | rejected | timeout after Verify.

    `before` is the grid as verified: a dynamic grid already holds replacements.
    A cleared challenge counts only once the site accepts it. A page that left
    its document after the press ended this appearance: what it shows next is
    looked at afresh. A provider message counts only when it appears after the
    press, not when it stayed from the grid before.
    """
    until = min(budget.deadline, time.monotonic() + VERIFY_WAIT_S)
    shown = {tile.get("src") for tile in before.get("tiles", [])}
    first = [tile["src"] for tile in before.get("tiles", [])]
    message_gone = not before.get("errors")
    cleared = False
    while time.monotonic() < until:
        await asyncio.sleep(0.25)
        if broker.navigated_after_verify() and (broker.frame is None or broker.frame.is_detached()):
            return "moved_on"
        try:
            if not broker.navigated_after_verify():
                broker.callback.check(broker.expected, budget)
                observed = await challenges.observe(broker.page)
                if observed.get("status") != "clear":
                    grid = await broker.observe(budget, wait=min(1.0, max(0.0, until - time.monotonic())))
                    now = [tile["src"] for tile in grid.get("tiles", [])]
                    if _fresh(grid, shown):
                        return "new_grid"
                    if grid.get("state") == "grid" and now == first:
                        if grid.get("errors") and message_gone:
                            return "incomplete"  # the same grid with a new provider message
                        message_gone = message_gone or not grid.get("errors")
                    continue
        except FenceChanged:
            if not broker.navigated_after_verify():
                raise
        except GridUnavailable:
            if not broker.navigated_after_verify():
                continue  # the frame went away: the page is looked at again
        if await _accepted(broker):
            return "accepted"
        cleared = True
    return "rejected" if cleared else "timeout"


async def solve_grid(*, budget: Budget, state: dict | None = None, **binding) -> GridResult:
    """Answer the open grid or reload it; what stays in doubt goes to the person."""
    try:
        names = await asyncio.to_thread(model_classes)  # first load hashes 113 MB
    except Exception:  # noqa: BLE001 - an unusable model is a person's grid, not a crash
        names = None
    if names is None:
        return GridResult("manual", "grid_model_unavailable", False)
    broker = None
    try:
        broker = GridBroker(**binding)
        await broker.callback.claim_resource()
        await broker.bind(budget)
        grid = await broker.observe(budget, appear=True)
        while True:
            if grid.get("state") != "grid":
                return GridResult("manual", "grid_not_ready", broker.attempted)
            if broker.draws >= MAX_DRAWS:
                return GridResult("manual", "grid_round_limit", broker.attempted)
            broker.draws += 1
            verdict = await _answer_grid(broker, grid, names, budget)
            if isinstance(verdict, dict):
                outcome = await _outcome(broker, verdict, budget)
                if outcome == "accepted":
                    return GridResult("accepted", "grid_accepted", True)
                if outcome == "moved_on":
                    # The challenge left with its page; the login looks at the next one.
                    return GridResult("accepted", "grid_page_moved_on", True)
                if outcome == "new_grid":
                    grid = await broker.observe(budget)
                    continue
                verdict = {"incomplete": "grid_incomplete", "rejected": "grid_rejected"}.get(
                    outcome, "grid_outcome_timeout")
                if verdict == "grid_incomplete":
                    grid = await broker.observe(budget)
            await _keep_case(broker, verdict)
            if verdict not in RELOADABLE or broker.draws >= MAX_DRAWS:
                return GridResult("manual", verdict, broker.attempted)
            grid = await _reload(broker, budget)
    except ResourceBusy:
        return GridResult("blocked", "resource_busy", bool(broker and broker.attempted))
    except FenceChanged:
        if broker is not None and broker.binding_valid() is True and not broker.page.is_closed():
            # The page changed under the grid; it is still the person's own session.
            return GridResult("manual", "grid_page_changed", broker.attempted)
        return GridResult("blocked", "stale_authority", bool(broker and broker.attempted))
    except BudgetExhausted as error:
        return GridResult("manual", "grid_time_budget" if str(error) == "deadline"
                          else "grid_effect_budget", bool(broker and broker.attempted))
    except (GridUnavailable, TimeoutError) as error:
        reason = str(error) if isinstance(error, GridUnavailable) else "grid_outcome_timeout"
        return GridResult("manual", reason, bool(broker and broker.attempted))
    except (asyncio.CancelledError, DrainIncomplete):
        raise
    except Exception:  # noqa: BLE001
        return GridResult("manual", "grid_command_failed" if broker and broker.attempted
                          else "grid_observer_error", bool(broker and broker.attempted))
    finally:
        if state is not None and broker is not None:
            state.update(attempted=broker.attempted, draws=broker.draws, reloads=broker.reloads,
                         clicks=broker.clicks)
        if broker is not None:
            await broker.close()
