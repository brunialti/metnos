"""Execute the actual console script with a small, isolated browser boundary."""
from pathlib import Path
import shutil
import sqlite3
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[3]


def test_console_async_races_progress_and_disconnection(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required to execute the browser script")
    source = (ROOT / "runtime/templates/durable_workloads.html").read_text()
    script = source.split("<script>", 1)[1].split("</script>", 1)[0]
    script = script.replace("{{ copy|tojson }}", "globalThis.copy")
    script = script.split('  document.getElementById("dwRefresh").addEventListener', 1)[0]
    script += "globalThis.ui = {request, availableDate, percentText, estimateText, appendProgress, countersText, breakdown, jobTitle, jobFolder, loadList, loadDetail, refresh, markStale, renderEngine, startPolling, deactivatePage, restorePage, setEngine: value => {engine = value; renderEngine();}, selected: () => selected};})();"
    harness = r'''
const assert = require("node:assert/strict");
const vm = require("node:vm");
const nodes = new Map();
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.dataset = {}; this.listeners = {}; this._text = ""; this.classList = {add(){}, remove(){}, contains(){return false;}}; }
  set id(value) { this._id = value; nodes.set(value, this); }
  get id() { return this._id; }
  set textContent(value) { this._text = value; this.children = []; }
  get textContent() { return this._text + this.children.map(x => x.textContent).join(""); }
  append(...values) { this.children.push(...values); }
  prepend(...values) { this.children.unshift(...values); }
  replaceChildren(...values) { this.children = values; this._text = ""; }
  addEventListener(key, value) { this.listeners[key] = value; }
  setAttribute(key, value) { this[key] = value; }
  querySelector() { return null; }
  querySelectorAll(selector) { return this.children.flatMap(child => [...((selector.startsWith('.') && child.className?.split(' ').includes(selector.slice(1))) || (selector === 'details[data-section]' && child.tag === 'details' && child.dataset.section) ? [child] : []), ...child.querySelectorAll(selector)]); }
  get childElementCount() { return this.children.length; }
  get lastChild() { return this.children.at(-1); }
}
for (const id of ["durableWorkloads", "dwList", "dwListPager", "dwDetail", "dwPlaceholder", "dwLive", "dwEngine", "dwFreshness"]) { const element = new Element("div"); element.id = id; }
nodes.get("durableWorkloads").dataset.api = "/agent/workloads";
nodes.get("durableWorkloads").append(nodes.get("dwList"), nodes.get("dwDetail"));
const timers = new Map(); const intervals = new Map(); let timerId = 0;
globalThis.window = {setTimeout(fn, delay) { timers.set(++timerId, {fn, delay}); return timerId; }, clearTimeout(id) { timers.delete(id); }, setInterval(fn, delay) {intervals.set(++timerId, {fn, delay}); return timerId;}, clearInterval(id) {intervals.delete(id);}};
globalThis.document = {getElementById: id => nodes.get(id), createElement: tag => new Element(tag)};
let streamsOpened = 0;
globalThis.EventSource = class { constructor() {streamsOpened++;} addEventListener() {} close() {} };
globalThis.copy = new Proxy({notAvailable: "n.a.", states: {running: "running", needs_attention: "blocked"}, phases: {discover: "Scanning folders"}, estimateReasons: {insufficient_data: "insufficient"}, priorities: {}, artifactStates: {}, errorLabels: {budget_exhausted: "exhausted", budget_accounting_incomplete: "accounting incomplete"}, warningsByCode: {}}, {get: (obj, key) => obj[key] || key});
let fetchCount = 0;
const response = value => ({ok: true, json: async () => value});
globalThis.fetch = async () => { fetchCount++; return response({items: []}); };
vm.runInThisContext(SCRIPT);
const counters = {committed: 3, failed: 2, skipped: 1, pending: 4, attention: 1, total: 11};
assert.equal(ui.countersText(counters), "saved: 3 / 11");
assert.ok(ui.breakdown(counters).includes("failedCount: 2"));
assert.equal(ui.availableDate(null), "n.a.");
assert.equal(ui.availableDate("invalid"), "n.a.");
assert.equal(ui.percentText(null), "n.a.");
assert.equal(ui.percentText(NaN), "n.a.");
assert.equal(ui.percentText(0), "0%");
assert.equal(ui.percentText(30), "30%");
assert.equal(ui.jobTitle({}), "genericJob");
assert.equal(ui.jobTitle({description: {kind: "unknown", operation: "untrusted"}}), "genericJob");
assert.equal(ui.jobTitle({description: {kind: "image_indexing"}}), "photoIndexing");
assert.equal(ui.jobFolder({}), null);
assert.equal(ui.jobFolder({description: {target_path: "/photos/<script>alert(1)</script>"}}), "/photos/<script>alert(1)</script>");
const timing = {started_at: new Date(Date.now() - 10000).toISOString(), known_units_percent: 30, observed_at: new Date().toISOString(), estimated_end_at: new Date(Date.now() + 60000).toISOString()};
ui.setEngine({enabled: false, state: "degraded", reason_code: "feature_disabled", worker_available: false});
assert.ok(nodes.get("dwEngine").textContent.includes("engineDisabled"));
assert.equal(ui.estimateText(timing), "n.a.");
ui.setEngine({enabled: true, state: "ready", worker_available: true});
assert.ok(nodes.get("dwEngine").textContent.includes("engineReady"));
assert.notEqual(ui.estimateText(timing), "n.a.");
assert.equal(ui.estimateText({...timing, observed_at: new Date(Date.now() - 31000).toISOString()}), "n.a.");
assert.equal(ui.estimateText({...timing, estimated_end_at: new Date(Date.now() - 1).toISOString()}), "n.a.");
ui.appendProgress(nodes.get("dwDetail"), timing);
assert.notEqual(nodes.get("dwDetail").querySelectorAll(".dw-estimate")[0].textContent, "n.a.");
ui.markStale();
assert.equal(nodes.get("dwDetail").querySelectorAll(".dw-estimate")[0].textContent, "n.a.");
ui.setEngine({enabled: true, state: "ready", worker_available: false});
assert.ok(nodes.get("dwEngine").textContent.includes("engineUnavailable"));
assert.equal(ui.estimateText(timing), "n.a.");
const job = id => ({workload: {workload_id: id, state: "needs_attention", version: 1, counters, updated_at: null, created_at: null}, revision: {execution: {blocking_reason: "budget_accounting_incomplete", last_committed_at: null, error_categories: [{error_code: "budget_exhausted", count: 2}]}}});
(async () => {
  let releaseA;
  globalThis.fetch = async url => {
    if (url === "/agent/workloads/A") return new Promise(resolve => { releaseA = () => resolve(response(job("A"))); });
    if (url === "/agent/workloads/B") return response(job("B"));
    return response({items: []});
  };
  const first = ui.loadDetail("A");
  await ui.loadDetail("B");
  releaseA(); await first;
  assert.equal(ui.selected().workload_id, "B", "older responses must never overwrite the selected job");
  assert.ok(nodes.get("dwDetail").textContent.includes("blockedHelp"));
  assert.ok(nodes.get("dwDetail").textContent.includes("3 / 11"));
  assert.ok(nodes.get("dwDetail").textContent.includes("accounting incomplete"));
  assert.ok(!nodes.get("dwDetail").textContent.includes("exhausted"));
  assert.ok(nodes.get("dwDetail").textContent.includes("noResult"));
  assert.ok(nodes.get("dwDetail").textContent.includes("startedn.a."));
  assert.ok(nodes.get("dwDetail").textContent.includes("percentn.a."));
  assert.ok(nodes.get("dwDetail").textContent.includes("estimatedEndn.a."));
  const sections = nodes.get("dwDetail").querySelectorAll("details[data-section]");
  assert.equal(sections.length, 4, "help, events, technical metadata and artifacts are collapsible");
  assert.ok(sections.every(section => !section.open), "verbose details start collapsed");
  const technical = sections.find(section => section.dataset.section === "technical");
  assert.ok(technical.textContent.includes("jobIdB"), "long identifiers stay in technical details");
  assert.ok(technical.textContent.includes("3 / 11"));
  technical.open = true;
  await ui.loadDetail("B");
  assert.ok(nodes.get("dwDetail").querySelectorAll("details[data-section]").find(section => section.dataset.section === "technical").open, "refresh preserves opened details");
  let releaseHealth; fetchCount = 0;
  globalThis.fetch = async url => {
    fetchCount++;
    if (url === "/agent/health") return new Promise(resolve => { releaseHealth = () => resolve(response({durable_workloads: {enabled: false, state: "disabled"}})); });
    if (url === "/agent/workloads/B") return response(job("B"));
    return response({items: []});
  };
  const poll = ui.refresh();
  await ui.refresh();
  assert.equal(fetchCount, 2, "one poll reads health and list independently; polls must not overlap");
  releaseHealth(); await poll;
  globalThis.fetch = async () => { throw new Error("offline"); };
  await ui.refresh();
  assert.ok(nodes.get("dwFreshness").textContent.includes("stale"));
  assert.ok(nodes.get("dwEngine").textContent.includes("engineUnavailable"));
  const paths = [];
  globalThis.fetch = async url => {
    paths.push(url);
    if (url === "/agent/health") return {ok: false};
    if (url === "/agent/workloads") return response({items: [job("C").workload]});
    if (url === "/agent/workloads/B") return response(job("B"));
    return response({items: []});
  };
  await ui.refresh();
  assert.ok(paths.includes("/agent/workloads"), "failed health must not prevent a list refresh");
  assert.ok(paths.includes("/agent/workloads/B"), "failed health must not prevent detail refresh");
  assert.equal(nodes.get("dwList").children[0].dataset.workloadId, "C");
  assert.ok(!nodes.get("dwList").textContent.includes("jobId"), "list uses human titles instead of technical identifiers");
  assert.ok(nodes.get("dwEngine").textContent.includes("engineUnavailable"));
  assert.ok(nodes.get("dwFreshness").textContent.includes("fresh"));
  ui.startPolling(); assert.equal(intervals.size, 2);
  ui.deactivatePage(); assert.equal(intervals.size, 0);
  paths.length = 0; await ui.refresh(); assert.equal(paths.length, 0);
  const previousStreams = streamsOpened;
  ui.restorePage({persisted: true});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(intervals.size, 2, "returning from browser cache restores periodic checks");
  assert.ok(streamsOpened > previousStreams, "returning from browser cache restores event streaming");
  assert.ok(paths.includes("/agent/workloads"));
  ui.deactivatePage();
  globalThis.fetch = async (_url, options) => new Promise((_resolve, reject) => options.signal.addEventListener("abort", () => reject(new Error("timeout"))));
  const hanging = ui.request("/stalled");
  const timeout = [...timers.values()].find(timer => timer.delay === 10000);
  assert.ok(timeout, "every request requires a bounded timeout");
  timeout.fn();
  await assert.rejects(hanging, /timeout/);
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
    import json
    harness = harness.replace("SCRIPT", json.dumps(script))
    result = subprocess.run([node, "-e", harness], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_console_labels_exist_in_both_seed_languages():
    with sqlite3.connect(f"file:{ROOT / 'install/data/i18n_seed.sqlite'}?mode=ro", uri=True) as conn:
        for suffix in ("ENGINE", "ENGINE_READY", "ENGINE_DISABLED", "ENGINE_UNAVAILABLE", "FRESH", "STALE", "REFRESH", "ACTIVITY", "TECHNICAL", "SAVED", "PENDING", "FAILED_COUNT", "SKIPPED", "ATTENTION", "BLOCKED_HELP", "WAITING_HELP", "PROGRESS_HELP", "LAST_RESULT", "NO_RESULT", "ERROR_ACCOUNTING_INCOMPLETE", "ERROR_BUDGET_GUARD", "STATE_PENDING", "STATE_LEASED", "STATE_RETRY_WAIT", "STATE_COMMITTED", "STATE_FAILED_PERMANENT", "STATE_SKIPPED", "ATTEMPTS"):
            rows = conn.execute("SELECT lang,text,needs_translation FROM i18n WHERE key=? AND lang IN ('it','en')", ("UI_DURABLE_" + suffix,)).fetchall()
            assert {row[0] for row in rows} == {"it", "en"}
            assert all(row[1] and not row[2] for row in rows)
        for suffix in ("STARTED", "KNOWN_UNITS_PERCENT", "ESTIMATED_END", "NOT_AVAILABLE", "TIMING_HELP"):
            rows = conn.execute("SELECT lang,text,needs_translation FROM i18n WHERE key=? AND lang IN ('it','en')", ("UI_DURABLE_" + suffix,)).fetchall()
            assert {row[0] for row in rows} == {"it", "en"}
            assert all(row[1] and not row[2] for row in rows)
            if suffix == "NOT_AVAILABLE":
                assert all(row[1] == "n.a." for row in rows)
        for suffix in ("JOB_LIST", "JOB_DETAIL", "PHOTO_INDEXING", "GENERIC_JOB", "FOLDER", "READ_PROGRESS", "JOB_ID", "OPERATION", "COMPLETED_KNOWN_BLOCKS", "ESTIMATED_FINISH", "BLOCKS_NOT_FILES", "CURRENT_PHASE", "PHASE_DISCOVER", "PHASE_FOLDERS", "PHASE_ANALYZE", "PHASE_MERGE", "PHASE_PUBLISH", "DISCOVERY_HELP", "RECOVERY_HELP", "RUNNING_BLOCKS", "JOB_LIMIT", "RESERVED_BLOCKS", "PARALLELISM_HELP", "ETA_MULTI_PHASE", "ETA_NOT_RUNNING", "ETA_INSUFFICIENT_DATA"):
            rows = conn.execute("SELECT lang,text,needs_translation FROM i18n WHERE key=? AND lang IN ('it','en')", ("UI_DURABLE_" + suffix,)).fetchall()
            assert {row[0] for row in rows} == {"it", "en"}
            assert all(row[1] and not row[2] for row in rows)


@pytest.mark.parametrize("lang", ["it", "en"])
def test_console_design_browser_isolated(monkeypatch, tmp_path, lang):
    """Real DOM/layout, with every HTTP route fulfilled from synthetic data."""
    import asyncio
    import json
    import os
    from jinja2 import Environment, FileSystemLoader
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    import http_routes_durable_workloads as routes

    with sqlite3.connect(f"file:{ROOT / 'install/data/i18n_seed.sqlite'}?mode=ro", uri=True) as conn:
        texts = dict(conn.execute("SELECT key,text FROM i18n WHERE lang=?", (lang,)))
    translate = lambda key: texts[key]
    environment = Environment(loader=FileSystemLoader(ROOT / "runtime/templates"), autoescape=True)

    async def owner(_request):
        return "synthetic-owner"

    monkeypatch.setattr(routes, "_owner", owner)
    monkeypatch.setattr(routes, "message", translate)
    monkeypatch.setattr(routes, "render_template", lambda template, **context: environment.get_template(template).render(
        **context, msg=translate, ui_lang=lang, settings_navigation=lambda _: [],
    ))
    html = asyncio.run(routes.workload_console(None)).text
    workload = {
        "workload_id": "wrk_synthetic_photo_job", "state": "running", "version": 3,
        "priority": "normal", "created_at": "2026-09-16T10:00:00Z", "updated_at": "2026-09-16T10:00:10Z",
        "counters": {"committed": 0, "failed": 0, "skipped": 0, "pending": 1, "attention": 0, "total": 1},
        "description": {"kind": "image_indexing", "operation": "images.index.v1", "target_path": "/archive/photos/<script>untrusted</script>/" + "long-folder/" * 12, "phase": "discover"},
        "progress": {"started_at": "2026-09-16T10:00:02Z", "known_units_percent": 0, "estimated_end_at": None, "estimated_end_reason": "multi_phase", "parallelism": {"running_units": 1, "leased_units": 0, "max_concurrency": 8}},
    }
    payload = {"workload": workload, "revision": {"execution": {"last_committed_at": None}}}

    def respond(route):
        path = route.request.url.split("example.test", 1)[-1]
        if path == "/admin/workloads":
            route.fulfill(body=html, content_type="text/html")
            return
        if path == "/agent/health":
            value = {"durable_workloads": {"enabled": True, "worker_available": True, "state": "ready"}}
        elif path == "/agent/workloads":
            value = {"items": [workload]}
        elif path == "/agent/workloads/wrk_synthetic_photo_job":
            value = payload
        elif path.endswith("/stream"):
            route.fulfill(body="", content_type="text/event-stream")
            return
        elif path.startswith("/agent/workloads/"):
            value = {"items": []}
        else:
            route.abort()
            return
        route.fulfill(body=json.dumps(value), content_type="application/json")

    with sync_playwright() as browser_driver:
        executable = os.environ.get("METNOS_TEST_CHROMIUM_EXECUTABLE", browser_driver.chromium.executable_path)
        if not Path(executable).is_file():
            pytest.skip("Browser acceptance requires installed Chromium via METNOS_TEST_CHROMIUM_EXECUTABLE")
        browser = browser_driver.chromium.launch(headless=True, executable_path=executable)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/*", respond)
            page.goto("http://example.test/admin/workloads")
            choice = page.locator(".dw-job-choice")
            choice.wait_for()
            choice.focus()
            page.keyboard.press("Enter")
            page.locator("#dwDetail:not([hidden])").wait_for()
            assert choice.get_attribute("aria-current") == "true"
            assert page.locator("#dwDetail h3").first.inner_text() == texts["UI_DURABLE_PHOTO_INDEXING"]
            assert workload["description"]["target_path"] in page.locator("#dwDetail .dw-folder").inner_text()
            assert page.locator("#dwDetail .dw-folder script").count() == 0
            assert page.locator("#dwDetail > .dw-timing").inner_text().count("n.a.") == 2
            assert page.get_by_text(texts["UI_DURABLE_DISCOVERY_HELP"], exact=True).is_visible()
            assert page.get_by_text(texts["UI_DURABLE_ETA_MULTI_PHASE"], exact=True).is_visible()
            assert page.get_by_text(texts["UI_DURABLE_RUNNING_BLOCKS"] + ": 1 · " + texts["UI_DURABLE_JOB_LIMIT"] + ": 8", exact=True).is_visible()
            assert page.locator("#dwDetail progress").count() == 0
            assert page.locator("#dwDetail details[open]").count() == 0
            assert not page.get_by_text(workload["workload_id"], exact=True).is_visible()
            assert not page.get_by_text(texts["UI_DURABLE_PROGRESS_HELP"], exact=True).is_visible()
            assert page.get_by_role("button", name=texts["UI_DURABLE_PAUSE"], exact=True).is_visible()
            assert page.get_by_role("button", name=texts["UI_DURABLE_RESUME"], exact=True).count() == 0
            page.screenshot(path=str(tmp_path / f"lre-design-{lang}-desktop.png"), full_page=True)
            for width in (1000, 390):
                page.set_viewport_size({"width": width, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), f"horizontal overflow at {width}px"
            page.wait_for_function("document.getElementById('sidebar').getBoundingClientRect().right <= 0")
            page.screenshot(path=str(tmp_path / f"lre-design-{lang}-mobile.png"), full_page=True)
            workload["state"] = "needs_attention"
            workload["description"]["phase"] = "analyze"
            workload["progress"]["parallelism"]["running_units"] = 0
            page.locator("#dwRefresh").click()
            page.get_by_text(texts["UI_DURABLE_BLOCKED_HELP"], exact=True).wait_for()
            assert page.locator("#dwEngine").inner_text().endswith(texts["UI_DURABLE_ENGINE_READY"])
            assert page.locator("#dwDetail > .dw-job-heading .dw-state").get_attribute("data-tone") == "error"
            assert page.get_by_role("button", name=texts["UI_DURABLE_RETRY"], exact=True).is_visible()
            assert page.get_by_role("button", name=texts["UI_DURABLE_PAUSE"], exact=True).count() == 0
            assert not errors
        finally:
            browser.close()
