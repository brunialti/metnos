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
    script += "globalThis.ui = {request, countersText, breakdown, loadList, loadDetail, refresh, markStale, renderEngine, startPolling, deactivatePage, restorePage, setEngine: value => {engine = value; renderEngine();}, selected: () => selected};})();"
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
  querySelector() { return null; }
  get childElementCount() { return this.children.length; }
  get lastChild() { return this.children.at(-1); }
}
for (const id of ["durableWorkloads", "dwList", "dwListPager", "dwDetail", "dwPlaceholder", "dwLive", "dwEngine", "dwFreshness"]) { const element = new Element("div"); element.id = id; }
nodes.get("durableWorkloads").dataset.api = "/agent/workloads";
const timers = new Map(); const intervals = new Map(); let timerId = 0;
globalThis.window = {setTimeout(fn, delay) { timers.set(++timerId, {fn, delay}); return timerId; }, clearTimeout(id) { timers.delete(id); }, setInterval(fn, delay) {intervals.set(++timerId, {fn, delay}); return timerId;}, clearInterval(id) {intervals.delete(id);}};
globalThis.document = {getElementById: id => nodes.get(id), createElement: tag => new Element(tag)};
let streamsOpened = 0;
globalThis.EventSource = class { constructor() {streamsOpened++;} addEventListener() {} close() {} };
globalThis.copy = new Proxy({states: {running: "running", needs_attention: "blocked"}, priorities: {}, artifactStates: {}, errorLabels: {budget_exhausted: "exhausted", budget_accounting_incomplete: "accounting incomplete"}, warningsByCode: {}}, {get: (obj, key) => obj[key] || key});
let fetchCount = 0;
const response = value => ({ok: true, json: async () => value});
globalThis.fetch = async () => { fetchCount++; return response({items: []}); };
vm.runInThisContext(SCRIPT);
const counters = {committed: 3, failed: 2, skipped: 1, pending: 4, attention: 1, total: 11};
assert.equal(ui.countersText(counters), "saved: 3 / 11");
assert.ok(ui.breakdown(counters).includes("failedCount: 2"));
ui.setEngine({enabled: false, state: "degraded", reason_code: "feature_disabled", worker_available: false});
assert.ok(nodes.get("dwEngine").textContent.includes("engineDisabled"));
ui.setEngine({enabled: true, state: "ready", worker_available: true});
assert.ok(nodes.get("dwEngine").textContent.includes("engineReady"));
ui.setEngine({enabled: true, state: "ready", worker_available: false});
assert.ok(nodes.get("dwEngine").textContent.includes("engineUnavailable"));
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
  assert.ok(nodes.get("dwList").textContent.includes("C"));
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
