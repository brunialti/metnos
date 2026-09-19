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
    script += "globalThis.ui = {request, observeProgress, errorName, availableDate, percentText, estimateText, estimateReason, appendProgress, appendProgressTable, appendCompletionTable, appendPhase, countersText, breakdown, processedCount, durationText, jobStateName, jobTitle, jobFolder, loadList, loadDetail, refresh, markStale, renderEngine, startPolling, deactivatePage, restorePage, setEngine: value => {engine = value; renderEngine();}, selected: () => selected};})();"
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
let monotonicMs = 1000;
Object.defineProperty(globalThis, "performance", {value: {now: () => monotonicMs}});
globalThis.window = {setTimeout(fn, delay) { timers.set(++timerId, {fn, delay}); return timerId; }, clearTimeout(id) { timers.delete(id); }, setInterval(fn, delay) {intervals.set(++timerId, {fn, delay}); return timerId;}, clearInterval(id) {intervals.delete(id);}};
globalThis.document = {getElementById: id => nodes.get(id), createElement: tag => new Element(tag)};
let streamsOpened = 0;
globalThis.EventSource = class { constructor() {streamsOpened++;} addEventListener() {} close() {} };
globalThis.copy = new Proxy({notAvailable: "n.a.", durationValue: "{hours} h {minutes} min {seconds} s", finishedState: "finished", phaseCount: "{completed} of {total}", phaseNumber: "Phase {number}/{total}", states: {running: "running", needs_attention: "blocked", failed: "failed"}, phases: {discover: "Scanning folders"}, estimateReasons: {insufficient_data: "insufficient", data_stale: "data stale", engine_unavailable: "engine unavailable", estimate_overdue: "overdue"}, priorities: {}, artifactStates: {}, errorLabels: {budget_exhausted: "exhausted", budget_accounting_incomplete: "accounting incomplete"}, warningsByCode: {}}, {get: (obj, key) => obj[key] || key});
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
assert.equal(ui.errorName("document_encrypted"), "errorUnknown (document_encrypted)");
assert.equal(ui.errorName("budget_exhausted"), "exhausted");
assert.equal(ui.errorName("<script>private/path</script>"), "errorUnknown");
assert.equal(ui.jobTitle({description: {kind: "unknown", operation: "untrusted"}}), "genericJob");
assert.equal(ui.jobTitle({description: {kind: "image_indexing"}}), "photoIndexing");
assert.equal(ui.jobStateName("completed"), "finished");
assert.equal(ui.jobStateName("completed_with_errors"), "finished");
assert.equal(ui.jobStateName("failed"), "failed");
assert.equal(ui.processedCount(counters), 6);
assert.equal(ui.durationText("2026-09-19T01:00:00Z", "2026-09-19T03:02:04Z"), "2 h 2 min 4 s");
assert.equal(ui.jobFolder({}), null);
assert.equal(ui.jobFolder({description: {target_path: "/photos/<script>alert(1)</script>"}}), "/photos/<script>alert(1)</script>");
const timing = ui.observeProgress({started_at: new Date(Date.now() - 10000).toISOString(), known_units_percent: 30, observed_at: new Date().toISOString(), estimated_end_at: new Date(Date.now() + 60000).toISOString()});
timing.current_phase = {stage_key: "analyze", number: 3, count: 5, committed_units: 8, total_units: 967, known_units_percent: 0.8, estimated_end_at: new Date(Date.now() + 180000).toISOString(), estimated_end_reason: null};
ui.setEngine({enabled: false, state: "degraded", reason_code: "feature_disabled", worker_available: false});
assert.ok(nodes.get("dwEngine").textContent.includes("engineDisabled"));
assert.equal(ui.estimateText(timing), "n.a.");
ui.setEngine({enabled: true, state: "ready", worker_available: true});
assert.ok(nodes.get("dwEngine").textContent.includes("engineReady"));
assert.notEqual(ui.estimateText(timing), "n.a.");
assert.notEqual(ui.estimateText(timing, "phase"), ui.estimateText(timing));
assert.equal(ui.estimateText({...timing, current_phase: null}, "phase"), "n.a.");
const expiredTiming = ui.observeProgress({...timing}, monotonicMs - 31000);
const overdueTiming = {...timing, estimated_end_at: new Date(Date.parse(timing.observed_at) - 1).toISOString()};
assert.equal(ui.estimateText(expiredTiming), "n.a.");
assert.equal(ui.estimateText(overdueTiming), "n.a.");
ui.appendProgress(nodes.get("dwDetail"), timing);
assert.ok(nodes.get("dwDetail").textContent.includes("phaseProgress: 0.8%8 of 967"));
assert.ok(!nodes.get("dwDetail").textContent.includes("30%"), "primary progress excludes other phases");
const tableContainer = new Element("div");
ui.appendProgressTable(tableContainer, {counters: {total: 1935}, progress: {...timing,
  current_phase: {...timing.current_phase, committed_units: 10}}});
const progressTable = tableContainer.children[0];
assert.equal(progressTable.tag, "table");
assert.equal(progressTable["aria-label"], "progress");
assert.deepEqual(progressTable.children[0].children[0].children.map(cell => [cell.textContent, cell.scope]),
  [["metricValue", "col"], ["metricMeaning", "col"]]);
const tableRows = table => table.children[1].children;
const metric = (table, key) => tableRows(table).find(row => row.dataset.metric === key);
assert.equal(tableRows(progressTable).length, 6);
assert.deepEqual(tableRows(progressTable).slice(0, 3).map(row => row.children.map(cell => cell.textContent)),
  [["10", "phaseCompletedMeaning"], ["967", "phaseTotalMeaning"], [(1935).toLocaleString(), "knownTotalMeaning"]]);
assert.ok(tableRows(progressTable).every(row => row.children[1].tag === "th" && row.children[1].scope === "row"));
const discoveryGap = new Element("div");
const discoveryWorkload = {description: {kind: "image_indexing", phase: null}, counters: {total: 1},
  progress: {...timing, current_phase: {...timing.current_phase, stage_key: "discover", number: 1,
    committed_units: 1, total_units: 1, known_units_percent: 100}}};
ui.appendProgressTable(discoveryGap, discoveryWorkload);
assert.equal(metric(discoveryGap.children[0], "phase-completed").children[0].textContent, "1");
assert.equal(metric(discoveryGap.children[0], "phase-total").children[0].textContent, "n.a.",
  "persisted discovery phase stays visible between leases without inventing a final source count");
const discoveryHeading = new Element("div");
ui.appendPhase(discoveryHeading, discoveryWorkload);
assert.ok(discoveryHeading.textContent.includes("Scanning folders"));
const missingContainer = new Element("div");
ui.appendProgressTable(missingContainer, {});
assert.ok(tableRows(missingContainer.children[0]).every(row => row.children[0].textContent === "n.a."),
  "missing counts, dates, percent and estimate are not represented as zero");
const completionContainer = new Element("div");
ui.appendCompletionTable(completionContainer, {
  state: "completed_with_errors", updated_at: "2026-09-19T03:02:04Z",
  progress: {started_at: "2026-09-19T01:00:00Z"}, counters,
});
const completionTable = completionContainer.children[0];
assert.equal(tableRows(completionTable).length, 5);
assert.equal(metric(completionTable, "processed").children[0].textContent, "6 / 11");
assert.equal(metric(completionTable, "successful").children[0].textContent, "3");
assert.equal(metric(completionTable, "duration").children[0].textContent, "2 h 2 min 4 s");
const phaseHeading = new Element("div");
ui.appendPhase(phaseHeading, {progress: timing});
assert.equal(phaseHeading.textContent, "Phase 3/5", "phase numbering is generic, not photo-specific");
assert.notEqual(nodes.get("dwDetail").querySelectorAll(".dw-estimate")[0].textContent, "n.a.");
ui.markStale();
assert.equal(nodes.get("dwDetail").querySelectorAll(".dw-estimate")[0].textContent, "n.a.");
assert.equal(nodes.get("dwDetail").querySelectorAll(".dw-estimate-reason")[0].textContent, "data stale");
ui.setEngine({enabled: true, state: "ready", worker_available: false});
assert.ok(nodes.get("dwEngine").textContent.includes("engineUnavailable"));
assert.equal(ui.estimateText(timing), "n.a.");
assert.equal(nodes.get("dwDetail").querySelectorAll(".dw-estimate-reason")[0].textContent, "engine unavailable");
ui.setEngine({enabled: true, state: "ready", worker_available: true});
assert.equal(nodes.get("dwDetail").querySelectorAll(".dw-estimate-reason")[0].hidden, true);
assert.equal(ui.estimateReason(expiredTiming), "data_stale");
assert.equal(ui.estimateReason(overdueTiming), "estimate_overdue");
assert.equal(ui.estimateReason({...timing, observed_at: "invalid"}), "data_stale");
assert.equal(ui.estimateReason(JSON.parse(JSON.stringify(timing))), "data_stale",
  "unobserved data cannot acquire freshness just by rendering it");
const realWallNow = Date.now;
for (const offset of [-172800000, -3600000, -1000, 1000, 3600000, 172800000]) {
  Date.now = () => realWallNow() + offset;
  assert.equal(ui.estimateReason(timing, "phase"), null,
    "a fresh forecast must not depend on the browser/server wall-clock offset");
  assert.equal(ui.estimateReason(expiredTiming, "phase"), "data_stale");
  assert.equal(ui.estimateReason(overdueTiming), "estimate_overdue");
}
Date.now = realWallNow;
const almostDue = ui.observeProgress({...timing, estimated_end_at: new Date(Date.parse(timing.observed_at) + 10000).toISOString()});
monotonicMs += 11000;
assert.equal(ui.estimateReason(almostDue), "estimate_overdue", "a forecast expires without moving either wall clock");
monotonicMs += 20000;
assert.equal(ui.estimateReason(timing, "phase"), "data_stale", "elapsed time still expires a valid response");
const job = id => ({
  workload: {workload_id: id, state: "needs_attention", version: 1, counters, updated_at: null, created_at: null},
  revision: {execution: {
    blocking_reason: "budget_accounting_incomplete", last_committed_at: null,
    error_categories: [{error_code: "budget_exhausted", count: 2}],
    domain_errors: {nitems: 31, categories: [{error_code: "document_encrypted", count: 7}], truncated: true},
    attempt_errors: {nattempts: 3, categories: [{error_code: "transport.temporarily_unavailable", count: 2},
      {error_code: "execution.runner_failed", cause_code: "image_description_truncated", count: 1}], truncated: false},
  }},
});
(async () => {
  globalThis.fetch = async (_url, options) => {
    assert.equal(options.cache, "no-store");
    monotonicMs += 9000;
    return response({workload: {progress: JSON.parse(JSON.stringify(timing))}});
  };
  const transported = await ui.request("/agent/workloads/transport");
  assert.equal(ui.estimateReason(transported.workload.progress, "phase"), null);
  monotonicMs += 21001;
  assert.equal(ui.estimateReason(transported.workload.progress, "phase"), "data_stale",
    "response age includes transport time, not only time since parsing");
  globalThis.fetch = async () => { throw new Error("offline"); };
  assert.equal(await ui.loadList(true), false);
  assert.equal(nodes.get("dwList").textContent, "readFailed");
  assert.equal(nodes.get("dwPlaceholder").textContent, "readFailed");
  globalThis.fetch = async () => response({items: []});
  assert.equal(await ui.loadList(true), true);
  assert.equal(nodes.get("dwList").textContent, "empty");
  assert.equal(nodes.get("dwPlaceholder").textContent, "empty");
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
  assert.ok(nodes.get("dwDetail").textContent.includes("itemsWithErrors: 31"));
  assert.ok(nodes.get("dwDetail").textContent.includes("document_encrypted) · 7"));
  assert.ok(nodes.get("dwDetail").textContent.includes("moreErrorCategories"));
  assert.ok(nodes.get("dwDetail").textContent.includes("attemptErrors: 3"));
  assert.ok(nodes.get("dwDetail").textContent.includes("transport.temporarily_unavailable) · 2"));
  assert.ok(nodes.get("dwDetail").textContent.includes("execution.runner_failed) — errorUnknown (image_description_truncated) · 1"));
  assert.ok(nodes.get("dwDetail").textContent.includes("attemptErrorsHelp"));
  assert.ok(!nodes.get("dwDetail").textContent.includes("exhausted"));
  assert.ok(nodes.get("dwDetail").textContent.includes("noResult"));
  const missingTable = nodes.get("dwDetail").querySelectorAll(".dw-progress-table")[0];
  for (const key of ["started", "phase-percent", "phase-end", "phase-completed", "phase-total"])
    assert.equal(metric(missingTable, key).children[0].textContent, "n.a.");
  assert.equal(metric(missingTable, "known-total").children[0].textContent, "11");
  assert.ok(nodes.get("dwDetail").textContent.includes("percent: n.a."));
  assert.ok(metric(missingTable, "phase-end").children[1].textContent.startsWith("phaseEstimatedEnd"));
  assert.ok(nodes.get("dwDetail").textContent.includes("wholeEstimatedEnd: n.a."));
  const sections = nodes.get("dwDetail").querySelectorAll("details[data-section]");
  assert.equal(sections.length, 5, "error history, help, events, technical metadata and artifacts are collapsible");
  assert.ok(sections.every(section => !section.open), "verbose details start collapsed");
  const technical = sections.find(section => section.dataset.section === "technical");
  assert.ok(technical.textContent.includes("jobId: B"), "long identifiers stay in technical details");
  assert.ok(technical.textContent.includes("3 / 11"));
  technical.open = true;
  sections.find(section => section.dataset.section === "attempt-errors").open = true;
  await ui.loadDetail("B");
  assert.ok(nodes.get("dwDetail").querySelectorAll("details[data-section]").find(section => section.dataset.section === "technical").open, "refresh preserves opened details");
  assert.ok(nodes.get("dwDetail").querySelectorAll("details[data-section]").find(section => section.dataset.section === "attempt-errors").open, "refresh preserves opened error history");
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
  assert.ok(nodes.get("dwFreshness").textContent.includes("fresh"));
  ui.startPolling();
  const staleCheck = [...intervals.values()].find(timer => timer.delay === 5000);
  Date.now = () => realWallNow() + 172800000;
  staleCheck.fn();
  assert.ok(nodes.get("dwFreshness").textContent.includes("fresh"),
    "a browser clock jump cannot expire a just-received response");
  Date.now = () => realWallNow() - 172800000;
  monotonicMs += 30001;
  staleCheck.fn();
  assert.ok(nodes.get("dwFreshness").textContent.includes("stale"),
    "a backward browser clock jump cannot keep a response fresh indefinitely");
  Date.now = realWallNow;
  const availableFetch = globalThis.fetch;
  globalThis.fetch = async () => { throw new Error("offline"); };
  await ui.loadList(true);
  assert.equal(nodes.get("dwList").children[0].dataset.workloadId, "C", "failed refresh preserves the last known jobs");
  globalThis.fetch = availableFetch;
  assert.ok(!nodes.get("dwList").textContent.includes("jobId"), "list uses human titles instead of technical identifiers");
  assert.ok(nodes.get("dwEngine").textContent.includes("engineUnavailable"));
  assert.ok(nodes.get("dwFreshness").textContent.includes("stale"));
  ui.startPolling(); assert.equal(intervals.size, 2);
  ui.deactivatePage(); assert.equal(intervals.size, 0);
  paths.length = 0; await ui.refresh(); assert.equal(paths.length, 0);
  const previousStreams = streamsOpened;
  ui.restorePage({persisted: true});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(intervals.size, 2, "returning from browser cache restores periodic checks");
  assert.ok(streamsOpened > previousStreams, "returning from browser cache restores event streaming");
  assert.ok(paths.includes("/agent/workloads"));
  globalThis.fetch = async () => ({ok: false, status: 503});
  assert.equal(await ui.loadDetail("B", false), false);
  assert.equal(ui.selected().workload_id, "B", "transient read failure retains known detail");
  globalThis.fetch = async url => url === "/agent/workloads/B"
    ? {ok: false, status: 404} : response({items: []});
  assert.equal(await ui.loadDetail("B", false), true);
  assert.equal(ui.selected(), null, "confirmed deletion clears stale selection");
  assert.equal(nodes.get("dwDetail").hidden, true);
  assert.equal(nodes.get("dwDetail").childElementCount, 0);
  assert.equal(nodes.get("dwPlaceholder").hidden, false);
  assert.equal(nodes.get("dwPlaceholder").textContent, "empty");
  ui.setEngine({enabled: true, state: "ready", worker_available: true});
  globalThis.fetch = async url => {
    if (url !== "/agent/workloads/fresh") return response({items: []});
    const payload = job("fresh");
    payload.workload.progress = JSON.parse(JSON.stringify(timing));
    return response(payload);
  };
  await ui.loadDetail("fresh");
  const estimateNode = nodes.get("dwDetail").querySelectorAll(".dw-estimate")[0];
  assert.notEqual(estimateNode.textContent, "n.a.");
  monotonicMs += 30001;
  ui.setEngine({enabled: true, state: "ready", worker_available: true});
  assert.equal(estimateNode.textContent, "n.a.");
  await ui.loadDetail("fresh", false);
  assert.equal(nodes.get("dwDetail").querySelectorAll(".dw-estimate")[0], estimateNode,
    "unchanged counters do not require replacing the detail DOM");
  assert.notEqual(estimateNode.textContent, "n.a.",
    "a new response renews freshness even when the detail signature is unchanged");
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
        for suffix in ("READ_FAILED", "SELECT_JOB", "ATTENTION_HELP", "PHASE_ESTIMATED_FINISH", "PHASE_FINISH_ESTIMATE", "WHOLE_ESTIMATED_FINISH", "PHASE_TIMING_HELP",
                       "PHASE_PROGRESS", "PHASE_COMPLETED_UNITS", "PHASE_SCOPE_HELP", "PHASE_NUMBER",
                       "WORK_UNITS_HELP", "COMPLETED_WORK_UNITS", "ALL_PHASES_PROGRESS", "ETA_DATA_STALE", "ETA_ENGINE_UNAVAILABLE",
                       "ETA_NEEDS_ATTENTION", "ETA_NO_ACTIVE_PHASE", "ETA_MULTIPLE_ACTIVE_PHASES",
                       "ETA_INVENTORY_OPEN", "ETA_PHASE_EXPANDING", "ETA_UNCERTAIN_PROGRESS",
                       "ETA_STALE_PROGRESS", "ETA_ESTIMATE_OVERDUE", "ITEMS_WITH_ERRORS",
                       "MORE_ERROR_CATEGORIES", "ATTEMPT_ERRORS", "ATTEMPT_ERRORS_HELP",
                       "METRIC_VALUE", "METRIC_MEANING", "PHASE_COMPLETED_MEANING", "PHASE_TOTAL_MEANING",
                       "KNOWN_TOTAL_MEANING", "BATCH_DEFINITION", "ERROR_EXECUTOR_UNKNOWN", "ERROR_RUNNER_FAILED",
                       "DISMISS", "DISMISS_CONFIRM", "JOB_FINISHED", "FINISHED_SUMMARY",
                       "FINISHED", "DURATION", "DURATION_VALUE", "PROCESSED_BATCHES",
                       "CAUSE_IMAGE_DESCRIPTION_UNAVAILABLE", "CAUSE_IMAGE_DESCRIPTION_TRUNCATED",
                       "CAUSE_IMAGE_DESCRIPTION_INVALID", "CAUSE_IMAGE_DESCRIPTION_EMPTY", "CAUSE_IMAGE_DESCRIPTION_SCHEMA_INVALID"):
            rows = conn.execute("SELECT lang,text,needs_translation FROM i18n WHERE key=? AND lang IN ('it','en')", ("UI_DURABLE_" + suffix,)).fetchall()
            assert {row[0] for row in rows} == {"it", "en"}
            assert all(row[1] and not row[2] for row in rows)
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


def test_batch_wording_is_generic_and_can_refresh_released_translations():
    import hashlib
    import re

    with sqlite3.connect(f"file:{ROOT / 'install/data/i18n_seed.sqlite'}?mode=ro", uri=True) as conn:
        rows = conn.execute("SELECT text FROM i18n WHERE key LIKE 'UI_DURABLE_%'").fetchall()
        assert not any(re.search(r"\b(blocco|blocchi|block|blocks)\b", row[0], re.I) for row in rows)
        for key in ("WORK_UNITS_HELP", "PHASE_SCOPE_HELP", "PHASE_COMPLETED_UNITS", "BATCH_DEFINITION",
                    "PHASE_COMPLETED_MEANING", "PHASE_TOTAL_MEANING", "KNOWN_TOTAL_MEANING"):
            rows = conn.execute("SELECT text FROM i18n WHERE key=?", ("UI_DURABLE_" + key,)).fetchall()
            assert not any(re.search(r"\b(foto|photo|photos|file|files)\b", row[0], re.I) for row in rows)
        for language, old_text in (("it", "Blocchi in esecuzione"), ("en", "Running blocks")):
            old_hash = "sha256:" + hashlib.sha256(old_text.encode()).hexdigest()
            assert conn.execute(
                "SELECT 1 FROM i18n_seed_history WHERE key=? AND lang=? AND version_hash=?",
                ("UI_DURABLE_RUNNING_BLOCKS", language, old_hash),
            ).fetchone()


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
    removed = False
    seen_requests = []
    workload["progress"]["current_phase"] = {"stage_key": "discover", "number": 1, "count": 5, "committed_units": 0, "total_units": 1, "known_units_percent": 0, "estimated_end_at": None, "estimated_end_reason": "insufficient_data"}

    def respond(route):
        nonlocal removed
        path = route.request.url.split("example.test", 1)[-1]
        seen_requests.append((route.request.method, path))
        if path == "/admin/workloads":
            route.fulfill(body=html, content_type="text/html")
            return
        if path == "/agent/health":
            value = {"durable_workloads": {"enabled": True, "worker_available": True, "state": "ready"}}
        elif path == "/agent/workloads":
            value = {"items": [] if removed else [workload]}
        elif path == "/agent/workloads/wrk_synthetic_photo_job":
            if removed:
                route.fulfill(status=404, body="{}", content_type="application/json")
                return
            value = payload
        elif path == "/agent/workloads/wrk_synthetic_photo_job/dismiss" and route.request.method == "POST":
            assert workload["state"] in {"cancelled", "failed", "completed_with_errors", "completed"}
            removed = True
            value = {"command": "dismiss", "dismissed": True}
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
            # route.fulfill closes an SSE response immediately; that is an
            # outage, not a healthy stream. Keep synthetic streams open and
            # drive their error callback explicitly below.
            page.add_init_script("""
                window.syntheticWallOffset = 0;
                const originalNow = Date.now;
                Date.now = () => originalNow() + window.syntheticWallOffset;
                window.syntheticStreams = [];
                window.EventSource = class extends EventTarget {
                    constructor() { super(); window.syntheticStreams.push(this); queueMicrotask(() => this.onopen?.()); }
                    close() {}
                };
            """)
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
            table = page.locator("#dwDetail > .dw-progress-table")
            assert table.inner_text().count("n.a.") == 3
            assert table.get_by_role("columnheader").all_text_contents() == [texts["UI_DURABLE_METRIC_VALUE"], texts["UI_DURABLE_METRIC_MEANING"]]
            assert table.get_by_role("rowheader").count() == 6
            assert page.get_by_text(texts["UI_DURABLE_DISCOVERY_HELP"], exact=True).is_visible()
            assert table.get_by_text(texts["UI_DURABLE_ETA_INSUFFICIENT_DATA"], exact=True).is_visible()
            assert choice.get_by_text(texts["UI_DURABLE_ETA_INSUFFICIENT_DATA"], exact=True).is_visible()
            assert page.locator("#dwDetail > .dw-phase").inner_text().startswith(texts["UI_DURABLE_PHASE_NUMBER"].format(number=1, total=5))
            assert table.get_by_text(texts["UI_DURABLE_PHASE_FINISH_ESTIMATE"], exact=True).is_visible()
            assert not page.get_by_text(texts["UI_DURABLE_WHOLE_ESTIMATED_FINISH"] + ":", exact=True).is_visible()
            assert page.get_by_text(texts["UI_DURABLE_RUNNING_BLOCKS"] + ": 1 · " + texts["UI_DURABLE_JOB_LIMIT"] + ": 8", exact=True).is_visible()
            assert page.locator("#dwDetail progress").count() == 0
            assert page.locator("#dwDetail details[open]").count() == 0
            assert not page.get_by_text(workload["workload_id"], exact=True).is_visible()
            assert not page.get_by_text(texts["UI_DURABLE_WORK_UNITS_HELP"], exact=True).is_visible()
            assert page.get_by_role("button", name=texts["UI_DURABLE_PAUSE"], exact=True).is_visible()
            assert page.get_by_role("button", name=texts["UI_DURABLE_RESUME"], exact=True).count() == 0
            assert page.get_by_role("button", name=texts["UI_DURABLE_DISMISS"], exact=True).count() == 0
            page.screenshot(path=str(tmp_path / f"lre-design-{lang}-desktop.png"), full_page=True)
            for width in (1000, 390):
                page.set_viewport_size({"width": width, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), f"horizontal overflow at {width}px"
            page.wait_for_function("document.getElementById('sidebar').getBoundingClientRect().right <= 0")
            page.screenshot(path=str(tmp_path / f"lre-design-{lang}-mobile.png"), full_page=True)
            workload["state"] = "needs_attention"
            workload["description"]["phase"] = "analyze"
            workload["progress"]["parallelism"]["running_units"] = 0
            workload["progress"]["current_phase"] = {"stage_key": None, "estimated_end_at": None, "estimated_end_reason": "needs_attention"}
            workload["progress"]["estimated_end_reason"] = "needs_attention"
            page.locator("#dwRefresh").click()
            page.get_by_text(texts["UI_DURABLE_ATTENTION_HELP"], exact=True).wait_for()
            assert page.locator("#dwEngine").inner_text().endswith(texts["UI_DURABLE_ENGINE_READY"])
            assert page.locator("#dwDetail > .dw-job-heading .dw-state").get_attribute("data-tone") == "error"
            assert table.get_by_text(texts["UI_DURABLE_ETA_NEEDS_ATTENTION"], exact=True).is_visible()
            assert page.get_by_role("button", name=texts["UI_DURABLE_RETRY"], exact=True).is_visible()
            assert page.get_by_role("button", name=texts["UI_DURABLE_PAUSE"], exact=True).count() == 0
            assert page.get_by_role("button", name=texts["UI_DURABLE_DISMISS"], exact=True).count() == 0
            from datetime import datetime, timedelta, timezone
            observed = datetime.now(timezone.utc)
            workload["state"] = "running"
            workload["progress"]["parallelism"]["running_units"] = 1
            workload["progress"].update(observed_at=observed.isoformat(), estimated_end_reason="multi_phase")
            workload["progress"]["known_units_percent"] = 50.4
            workload["counters"].update(committed=976, total=1935, pending=959)
            workload["progress"]["current_phase"] = {"stage_key": "analyze", "number": 3, "count": 5, "committed_units": 8, "total_units": 967, "known_units_percent": 0.8, "estimated_end_at": (observed + timedelta(minutes=5)).isoformat(), "estimated_end_reason": None}
            page.locator("#dwRefresh").click()
            page.wait_for_function("document.querySelector('#dwDetail > .dw-progress-table .dw-estimate').textContent !== 'n.a.'")
            assert table.get_by_text(texts["UI_DURABLE_PHASE_FINISH_ESTIMATE"], exact=True).is_visible()
            assert page.locator("#dwDetail > .dw-phase").inner_text().startswith(texts["UI_DURABLE_PHASE_NUMBER"].format(number=3, total=5))
            assert choice.locator(".dw-phase").inner_text().startswith(texts["UI_DURABLE_PHASE_NUMBER"].format(number=3, total=5))
            assert table.locator("tr[data-metric='phase-completed'] td").inner_text() == "8"
            assert table.locator("tr[data-metric='phase-total'] td").inner_text() == "967"
            assert table.locator("tr[data-metric='known-total'] td").inner_text() == page.evaluate("(1935).toLocaleString(document.documentElement.lang)")
            assert page.locator("#dwDetail progress").get_attribute("value") == "0.8"
            assert table.get_by_text("0,8%" if lang == "it" else "0.8%", exact=True).is_visible()
            assert table.locator(".dw-estimate-reason").is_hidden()
            assert not page.get_by_text(texts["UI_DURABLE_ALL_PHASES_PROGRESS"] + ":", exact=True).is_visible()
            for offset in (-172800000, -1000, 172800000):
                # An outage masks the old forecast first, so the assertion
                # cannot accidentally pass against a previous render.
                page.evaluate("window.syntheticStreams.at(-1).onerror()")
                assert table.locator(".dw-estimate").inner_text() == "n.a."
                page.evaluate("offset => { window.syntheticWallOffset = offset; }", offset)
                page.locator("#dwRefresh").click()
                page.wait_for_function("document.querySelector('#dwDetail > .dw-progress-table .dw-estimate').textContent !== 'n.a.'")
                assert choice.locator(".dw-estimate").inner_text() != "n.a."
                assert table.locator(".dw-estimate-reason").is_hidden()
                assert choice.locator(".dw-estimate-reason").is_hidden()
            page.evaluate("window.syntheticWallOffset = 0")
            page.evaluate("window.syntheticStreams.at(-1).onerror()")
            assert table.locator(".dw-estimate").inner_text() == "n.a."
            assert table.get_by_text(texts["UI_DURABLE_ETA_DATA_STALE"], exact=True).is_visible()
            assert choice.get_by_text(texts["UI_DURABLE_ETA_DATA_STALE"], exact=True).is_visible()
            page.locator("#dwRefresh").click()
            page.wait_for_function("document.querySelector('#dwDetail > .dw-progress-table .dw-estimate').textContent !== 'n.a.'")
            assert table.locator(".dw-estimate-reason").is_hidden()
            # Updated wording and layout stay readable in both languages.
            for width in (1440, 390):
                page.set_viewport_size({"width": width, "height": 1000})
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                page.screenshot(path=str(tmp_path / f"lre-phase-progress-{lang}-{width}.png"), full_page=True)
            page.locator("#dwDetail details[data-section='technical'] summary").click()
            assert page.locator("#dwDetail details[data-section='technical'] .dw-estimate").inner_text() == "n.a."
            assert page.get_by_text(texts["UI_DURABLE_ETA_MULTI_PHASE"], exact=True).is_visible()
            workload["state"] = "completed_with_errors"
            workload["progress"]["parallelism"]["running_units"] = 0
            workload["updated_at"] = "2026-09-16T12:03:04Z"
            workload["counters"].update(committed=1935, failed=0, skipped=0, pending=0, attention=0, total=1935)
            payload["revision"]["execution"]["stages"] = [
                {"stage_key": "analyze", "stage_type": "map", "counters": {"committed": 967, "failed": 0, "skipped": 0, "total": 967}},
                {"stage_key": "publish", "stage_type": "publish", "counters": {"committed": 1, "failed": 0, "skipped": 0, "total": 1}},
            ]
            payload["revision"]["execution"]["domain_errors"] = {
                "nitems": 31, "categories": [{"error_code": "document_encrypted", "count": 7}],
                "truncated": True,
            }
            page.locator("#dwRefresh").click()
            error_heading = texts["UI_DURABLE_ITEMS_WITH_ERRORS"] + ": 31"
            page.get_by_role("heading", name=error_heading, exact=True).wait_for()
            finished_badge = page.locator("#dwDetail > .dw-job-heading .dw-state")
            assert finished_badge.inner_text() == texts["UI_DURABLE_JOB_FINISHED"]
            assert finished_badge.get_attribute("data-tone") == "warning"
            completion = page.locator("#dwDetail > .dw-completion-table")
            assert completion.locator("tr[data-metric='processed'] td").inner_text() == page.evaluate("(1935).toLocaleString(document.documentElement.lang) + ' / ' + (1935).toLocaleString(document.documentElement.lang)")
            assert completion.locator("tr[data-metric='started'] td").inner_text() != "n.a."
            assert completion.locator("tr[data-metric='finished'] td").inner_text() != "n.a."
            assert completion.locator("tr[data-metric='duration'] td").inner_text() != "n.a."
            assert page.locator("#dwDetail > .dw-stage-summary tbody tr").count() == 2
            assert choice.get_by_text(texts["UI_DURABLE_JOB_FINISHED"], exact=True).is_visible()
            assert choice.get_by_text(texts["UI_DURABLE_PROCESSED_BATCHES"] + ":", exact=True).is_visible()
            assert page.locator(".dw-job-dismiss").is_visible()
            assert page.get_by_text(texts["UI_DURABLE_ERROR_UNKNOWN"] + " (document_encrypted) · 7", exact=True).is_visible()
            assert page.get_by_text(texts["UI_DURABLE_MORE_ERROR_CATEGORIES"], exact=True).is_visible()
            page.screenshot(path=str(tmp_path / f"lre-finished-with-errors-{lang}.png"), full_page=True)
            # A completed job remains inspectable after leaving/reloading the console.
            page.reload()
            page.locator(".dw-job-choice").click()
            page.get_by_role("heading", name=error_heading, exact=True).wait_for()
            page.locator(".dw-job-choice").click()
            page.locator("#dwDetail[hidden]").wait_for(state="attached")
            page.locator(".dw-job-choice").click()
            page.get_by_role("heading", name=error_heading, exact=True).wait_for()
            # A recovered transient error remains history, not an active fault
            # or a false item error in an otherwise successful job.
            workload["state"] = "completed"
            payload["revision"]["execution"]["domain_errors"] = {"nitems": 0, "categories": [], "truncated": False}
            payload["revision"]["execution"]["attempt_errors"] = {
                "nattempts": 2,
                "categories": [{"error_code": "execution.runner_failed", "cause_code": "image_description_truncated", "count": 2}],
                "truncated": False,
            }
            page.locator("#dwRefresh").click()
            history = page.locator("#dwDetail details[data-section='attempt-errors']")
            history.locator("summary").wait_for()
            assert history.locator("summary").inner_text() == texts["UI_DURABLE_ATTEMPT_ERRORS"] + ": 2"
            assert history.get_attribute("open") is None
            history.locator("summary").click()
            assert history.get_by_text(texts["UI_DURABLE_ATTEMPT_ERRORS_HELP"], exact=True).is_visible()
            assert history.get_by_text(texts["UI_DURABLE_ERROR_RUNNER_FAILED"] + " — " + texts["UI_DURABLE_CAUSE_IMAGE_DESCRIPTION_TRUNCATED"] + " · 2", exact=True).is_visible()
            assert page.get_by_role("heading", name=error_heading, exact=True).count() == 0
            page.reload()
            page.locator(".dw-job-choice").click()
            history.locator("summary").wait_for()
            assert history.locator("summary").inner_text().endswith(": 2")
            remove = page.locator(".dw-job-dismiss")
            assert remove.is_visible()
            dialogs = []
            def accept_dialog(dialog):
                dialogs.append((dialog.type, dialog.message))
                dialog.accept()
            page.once("dialog", accept_dialog)
            remove.click()
            page.locator("#dwDetail[hidden]").wait_for(state="attached")
            assert dialogs, json.dumps(seen_requests[-12:])
            assert ("POST", "/agent/workloads/wrk_synthetic_photo_job/dismiss") in seen_requests
            assert removed
            assert page.locator("#dwDetail").inner_html() == ""
            assert page.locator("#dwList .dw-job-choice").count() == 0
            assert page.locator("#dwPlaceholder").inner_text() == texts["UI_DURABLE_EMPTY"]
            assert not errors
        finally:
            browser.close()
