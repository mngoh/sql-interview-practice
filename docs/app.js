// SQL interview practice, browser edition: the same problems, data and grader
// as the Streamlit app, running on DuckDB-WASM. All content comes from
// site-data.json, which build_site.py generates from the Python sources.
import * as duckdb from "https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@1.32.0/+esm";
import { marked } from "https://cdn.jsdelivr.net/npm/marked@18.0.14/lib/marked.esm.js";

const STORE_KEY = "sql-practice-v1";
const ICON = { "not tried": "⬜", wrong: "❌", correct: "✅" };
const ARROW_DATE = 8;
const ARROW_TIMESTAMP = 10;

const $ = (sel) => document.querySelector(sel);
const esc = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

let DATA, S, PROBLEMS;
let db, conn;
let ready = false;
let queue = Promise.resolve();
const results = {}; // pid -> last grade result (kept for this page view only)
const state = loadState();

// ---------------------------------------------------------------------------
// Saved progress (drafts, statuses, timers) lives in localStorage.
// ---------------------------------------------------------------------------

function loadState() {
  let saved = {};
  try {
    saved = JSON.parse(localStorage.getItem(STORE_KEY)) || {};
  } catch {}
  return { current: 1, tab: "sql", status: {}, checks: {}, drafts: {}, revealed: {}, timers: {}, previews: [], ...saved };
}

function save() {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(state));
  } catch {}
}

// ---------------------------------------------------------------------------
// Database
// ---------------------------------------------------------------------------

async function startDb() {
  const bundle = await duckdb.selectBundle(duckdb.getJsDelivrBundles());
  const workerUrl = URL.createObjectURL(
    new Blob([`importScripts("${bundle.mainWorker}");`], { type: "text/javascript" }),
  );
  db = new duckdb.AsyncDuckDB(new duckdb.VoidLogger(), new Worker(workerUrl));
  await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
  URL.revokeObjectURL(workerUrl);
  await db.open({ query: { castBigIntToDouble: true, castDecimalToDouble: true } });
  conn = await db.connect();
  for (const sql of DATA.seed_sql) await conn.query(sql);
}

async function restartDb() {
  ready = false;
  renderReady();
  try {
    await db.terminate();
  } catch {}
  await startDb();
  ready = true;
  renderReady();
}

// Database calls run one at a time, so transactions never interleave.
function withDb(fn) {
  const run = queue.then(fn);
  queue = run.catch(() => {});
  return run;
}

class QueryError extends Error {}
class Timeout extends Error {}

// Run sql in a transaction that is always rolled back, so a stray DELETE or
// DROP can't damage the seeded data. A query that runs too long is abandoned
// and the database restarted, since the worker can't be interrupted.
async function runQuery(sql) {
  await conn.query("BEGIN TRANSACTION");
  let timer;
  let timedOut = false;
  try {
    const timeout = new Promise((_, reject) => {
      timer = setTimeout(() => {
        timedOut = true;
        reject(new Timeout());
      }, S.query_timeout_s * 1000);
    });
    return extract(await Promise.race([conn.query(sql), timeout]));
  } finally {
    clearTimeout(timer);
    if (!timedOut) {
      try {
        await conn.query("ROLLBACK");
      } catch {} // the query itself ended the transaction
    }
  }
}

function extract(table) {
  const fields = table.schema.fields;
  if (!fields.length) throw new QueryError("The statement ran but returned no result set. Write a SELECT.");
  const vectors = fields.map((_, j) => table.getChildAt(j));
  const rows = [];
  for (let i = 0; i < Math.min(table.numRows, S.max_rows + 1); i++) {
    rows.push(vectors.map((v, j) => normalize(v.get(i), fields[j].type)));
  }
  return { cols: fields.map((f) => f.name), rows, n: table.numRows };
}

// ---------------------------------------------------------------------------
// Grading: columns by position, row order ignored, floats rounded to 4
// decimals, NULL == NULL. Mirrors normalize() and compare() in app.py.
// ---------------------------------------------------------------------------

const toMs = (v) => (v instanceof Date ? v.getTime() : Number(v));
const isoDate = (ms) => new Date(ms).toISOString().slice(0, 10);

function isoTimestamp(ms) {
  const s = new Date(ms).toISOString();
  const frac = s.slice(20, 23);
  return `${s.slice(0, 10)} ${s.slice(11, 19)}${frac === "000" ? "" : "." + frac}`;
}

function normalize(v, type) {
  if (v === null || v === undefined) return null;
  if (type.typeId === ARROW_DATE) return isoDate(toMs(v));
  if (type.typeId === ARROW_TIMESTAMP) {
    const ms = toMs(v);
    return ms % 86400000 === 0 ? isoDate(ms) : isoTimestamp(ms);
  }
  if (typeof v === "bigint") v = Number(v);
  if (typeof v === "boolean") return v ? 1 : 0;
  if (typeof v === "number") {
    if (Number.isNaN(v)) return null;
    const x = Math.round(v * 1e4) / 1e4;
    return Object.is(x, -0) ? 0 : x;
  }
  if (typeof v === "string") return v;
  return JSON.stringify(v, (_, x) => (typeof x === "bigint" ? Number(x) : x));
}

function counter(rows) {
  const m = new Map();
  for (const row of rows) {
    const key = JSON.stringify(row);
    const entry = m.get(key);
    if (entry) entry.n++;
    else m.set(key, { row, n: 1 });
  }
  return m;
}

function subtract(a, b) {
  const out = [];
  for (const [key, { row, n }] of a) {
    for (let i = 0; i < n - (b.get(key)?.n || 0); i++) out.push(row);
  }
  return out;
}

function cmpRows(a, b) {
  for (let i = 0; i < a.length; i++) {
    if (a[i] === b[i]) continue;
    if (a[i] === null) return 1;
    if (b[i] === null) return -1;
    return a[i] < b[i] ? -1 : 1;
  }
  return 0;
}

function compare(user, ref) {
  const res = {
    userCols: user.cols, refCols: ref.cols, nUser: user.n, nExpected: ref.n,
    preview: user.rows.slice(0, 50), missing: [], extra: [], nMissing: 0, nExtra: 0,
  };
  if (user.n > S.max_rows) {
    return { ...res, correct: false, tooMany: true,
      problem: `Your query returned more than ${S.max_rows.toLocaleString()} rows. That's usually a join fan-out.` };
  }
  if (user.cols.length !== ref.cols.length) {
    return { ...res, correct: false,
      problem: `You returned ${user.cols.length} columns; expected ${ref.cols.length}: ${ref.cols.join(", ")}.` };
  }
  const got = counter(user.rows);
  const want = counter(ref.rows);
  const missing = subtract(want, got).sort(cmpRows);
  const extra = subtract(got, want).sort(cmpRows);
  Object.assign(res, {
    correct: !missing.length && !extra.length,
    missing: missing.slice(0, S.show_rows), extra: extra.slice(0, S.show_rows),
    nMissing: missing.length, nExtra: extra.length,
  });
  const lower = (cols) => cols.map((c) => c.toLowerCase());
  if (!res.correct && got.size === want.size && [...got.keys()].every((k) => want.has(k))) {
    res.hint = "Same distinct rows, different duplicates. Look for a join fan-out or a missing DISTINCT.";
  } else if (!res.correct && lower(user.cols).join() !== lower(ref.cols).join()
             && lower(user.cols).sort().join() === lower(ref.cols).sort().join()) {
    res.hint = `Columns are compared by position. Expected order: ${ref.cols.join(", ")}.`;
  }
  return res;
}

async function grade(sql, problem) {
  return withDb(async () => {
    let user;
    try {
      user = await runQuery(sql);
    } catch (e) {
      if (e instanceof Timeout) {
        await restartDb();
        return { error: `Query cancelled after ${S.query_timeout_s}s. Is a join exploding?` };
      }
      return { error: e.message || String(e) };
    }
    return compare(user, await runQuery(problem.solution));
  });
}

// ---------------------------------------------------------------------------
// UI
// ---------------------------------------------------------------------------

const problem = () => PROBLEMS.find((p) => p.id === state.current) || PROBLEMS[0];
const statusOf = (pid) => state.status[pid] || "not tried";

function cellHtml(v) {
  if (v === null) return '<td class="null">NULL</td>';
  return typeof v === "number" ? `<td class="num">${v}</td>` : `<td>${esc(v)}</td>`;
}

function tableHtml(rows, cols) {
  const head = cols.map((c) => `<th>${esc(c)}</th>`).join("");
  const body = rows
    .map((r) => "<tr>" + r.map(cellHtml).join("") + "</tr>")
    .join("");
  return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function renderNav() {
  $("#nav").innerHTML = PROBLEMS.map(
    (p) => `<button class="nav-item${p.id === state.current ? " active" : ""}" data-pid="${p.id}"
      ${p.id === state.current ? 'aria-current="true"' : ""}>
      <span class="icon">${ICON[statusOf(p.id)]}</span><span>${p.id}. ${esc(p.title)}</span></button>`,
  ).join("");
  const solved = PROBLEMS.filter((p) => statusOf(p.id) === "correct").length;
  $("#progress").textContent = `${solved} / ${PROBLEMS.length} correct · ⬜ not tried · ❌ wrong · ✅ correct`;
}

function renderReady() {
  $("#check").disabled = !ready;
  $("#check").textContent = ready ? "Check" : "Loading DuckDB…";
  $("#reset-db").disabled = !ready;
  renderPreviews();
}

function renderProblem() {
  const p = problem();
  $("#p-title").textContent = `${p.id}. ${p.title}`;
  $("#p-prompt").innerHTML = marked.parse(p.prompt);
  $("#p-return").innerHTML = "<strong>Return:</strong> " + p.columns.map((c) => `<code>${esc(c)}</code>`).join(", ");
  $("#sql").value = state.drafts[p.id] || "";
  $("#sql").setAttribute("aria-label", `Your SQL for problem ${p.id}`);
  renderNav();
  renderTimer();
  renderAfterCheck();
}

function renderAfterCheck() {
  const p = problem();
  const checks = state.checks[p.id] || 0;
  $("#reveal").disabled = checks === 0;
  $("#reveal").title = checks === 0 ? "Available after your first Check." : "";
  $("#result").innerHTML = results[p.id] ? resultHtml(results[p.id]) : "";
  const revealed = !!state.revealed[p.id];
  $("#edge").innerHTML =
    statusOf(p.id) === "correct" || revealed
      ? `<h3>Edge cases to say out loud</h3><ul>${p.edge_cases.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>`
      : "";
  $("#solution").innerHTML = revealed
    ? `<h3>Reference solution</h3><pre class="code">${esc(p.solution)}</pre>`
    : "";
}

function resultHtml(res) {
  if (res.empty) return `<div class="banner warn">Write a query first.</div>`;
  if (res.error) {
    return `<div class="banner bad"><strong>SQL error.</strong> Your query didn't run:</div>
      <pre class="code error">${esc(res.error)}</pre>`;
  }
  const metric = (label, value) => `<div class="metric"><div class="label">${label}</div><div class="value">${value}</div></div>`;
  let html = res.correct
    ? `<div class="banner ok">✅ <strong>Correct.</strong> Your result matches the reference.</div>`
    : `<div class="banner bad">❌ <strong>Incorrect.</strong></div>`;
  html += `<div class="metrics">
    ${metric("Your rows", res.tooMany ? `&gt;${S.max_rows.toLocaleString()}` : res.nUser)}
    ${metric("Expected rows", res.nExpected)}
    ${metric("Missing rows", res.nMissing)}
    ${metric("Extra rows", res.nExtra)}</div>`;
  if (res.problem) html += `<div class="banner warn">${esc(res.problem)}</div>`;
  if (res.hint) html += `<div class="banner info">${esc(res.hint)}</div>`;
  if (res.missing.length) {
    html += `<p><strong>Missing rows</strong> (expected but not in yours), first ${res.missing.length} of ${res.nMissing}:</p>`
      + tableHtml(res.missing, res.refCols);
  }
  if (res.extra.length) {
    html += `<p><strong>Extra rows</strong> (in yours but not expected), first ${res.extra.length} of ${res.nExtra}:</p>`
      + tableHtml(res.extra, res.userCols);
  }
  html += `<details class="panel"><summary>Your result (first ${res.preview.length} rows)</summary>
    ${tableHtml(res.preview, res.userCols)}</details>`;
  return html;
}

async function renderPreviews() {
  $("#preview-buttons").innerHTML = DATA.tables.map(
    (t) => `<button class="btn small${state.previews.includes(t.name) ? " primary" : ""}" data-table="${t.name}">${t.name}</button>`,
  ).join("");
  if (!ready) {
    $("#previews").innerHTML = state.previews.length ? `<p class="muted">Loading DuckDB…</p>` : "";
    return;
  }
  const parts = [];
  for (const name of state.previews) {
    const { cols, rows } = await withDb(() => runQuery(`SELECT * FROM ${name} LIMIT 10`));
    parts.push(`<p><strong>${esc(name)}</strong></p>${tableHtml(rows, cols)}`);
  }
  $("#previews").innerHTML = parts.join("");
}

// The timer starts on the first keystroke and never blocks anything.
function renderTimer() {
  const limit = S.timer_minutes * 60;
  const start = state.timers[state.current];
  const fmt = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  let clock = fmt(limit);
  let note = "starts on first keystroke";
  let pct = 0;
  let over = false;
  if (start) {
    const elapsed = Math.floor((Date.now() - start) / 1000);
    const left = limit - elapsed;
    over = left < 0;
    clock = over ? "+" + fmt(-left) : fmt(left);
    note = over ? "over time" : "left";
    pct = Math.min(100, (100 * elapsed) / limit);
  }
  $("#timer").classList.toggle("over", over);
  $("#timer-clock").textContent = clock;
  $("#timer-note").textContent = note;
  $("#timer-fill").style.width = pct + "%";
}

function renderTab() {
  for (const b of document.querySelectorAll("[data-tab]")) {
    b.setAttribute("aria-selected", String(b.dataset.tab === state.tab));
  }
  $("#tab-sql").hidden = state.tab !== "sql";
  $("#tab-py").hidden = state.tab !== "py";
}

function renderStatic() {
  $("#schema").textContent = DATA.schema_text;
  $("#dialect").innerHTML = marked.parse(DATA.dialect_notes);
  $("#cards").innerHTML = DATA.flashcards.map(
    (c) => `<details class="panel card"><summary>${marked.parseInline(c.q)}</summary><div class="md">${marked.parse(c.a)}</div></details>`,
  ).join("");
}

// ---------------------------------------------------------------------------
// Events
// ---------------------------------------------------------------------------

async function onCheck() {
  const p = problem();
  const sql = $("#sql").value.trim();
  if (!ready) return;
  if (!sql) {
    results[p.id] = { empty: true };
    renderAfterCheck();
    return;
  }
  $("#check").disabled = true;
  $("#check").textContent = "Checking…";
  const res = await grade(sql, p);
  results[p.id] = res;
  state.checks[p.id] = (state.checks[p.id] || 0) + 1;
  state.status[p.id] = res.correct ? "correct" : "wrong";
  save();
  renderReady();
  renderNav();
  renderAfterCheck();
}

function bindEvents() {
  $("#nav").addEventListener("click", (e) => {
    const b = e.target.closest("[data-pid]");
    if (!b) return;
    state.current = Number(b.dataset.pid);
    save();
    renderProblem();
    if (window.matchMedia("(max-width: 820px)").matches) $("#p-title").scrollIntoView({ behavior: "smooth" });
  });
  $("#sql").addEventListener("input", (e) => {
    const pid = state.current;
    state.drafts[pid] = e.target.value;
    if (!state.timers[pid] && e.target.value) state.timers[pid] = Date.now();
    save();
    renderTimer();
  });
  $("#sql").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      onCheck();
    }
  });
  $("#check").addEventListener("click", onCheck);
  $("#reveal").addEventListener("click", () => {
    state.revealed[state.current] = true;
    save();
    renderAfterCheck();
  });
  $("#timer-reset").addEventListener("click", (e) => {
    e.preventDefault();
    delete state.timers[state.current];
    save();
    renderTimer();
  });
  $("#preview-buttons").addEventListener("click", (e) => {
    const b = e.target.closest("[data-table]");
    if (!b) return;
    const name = b.dataset.table;
    state.previews = state.previews.includes(name)
      ? state.previews.filter((t) => t !== name)
      : [...state.previews, name];
    save();
    renderPreviews();
  });
  for (const b of document.querySelectorAll("[data-tab]")) {
    b.addEventListener("click", () => {
      state.tab = b.dataset.tab;
      save();
      renderTab();
    });
  }
  $("#reset-db").addEventListener("click", () => withDb(restartDb));
  $("#reset-progress").addEventListener("click", () => {
    if (!confirm("Clear your drafts, statuses and timers?")) return;
    for (const key of ["status", "checks", "drafts", "revealed", "timers"]) state[key] = {};
    for (const key of Object.keys(results)) delete results[key];
    save();
    renderProblem();
  });
}

async function main() {
  DATA = await (await fetch("site-data.json")).json();
  S = DATA.settings;
  PROBLEMS = DATA.problems;
  renderStatic();
  renderTab();
  renderProblem();
  renderReady();
  bindEvents();
  setInterval(renderTimer, 250);
  try {
    await withDb(startDb);
    ready = true;
    const [[today]] = (await withDb(() => runQuery("SELECT CURRENT_DATE"))).rows;
    $("#dbinfo").textContent = `Data is seeded relative to CURRENT_DATE (${today}). Your queries run in a rolled-back transaction.`;
  } catch (e) {
    $("#dbinfo").textContent = `DuckDB failed to load: ${e.message || e}`;
  }
  renderReady();
  if (new URLSearchParams(location.search).has("test")) window.__practice = { grade, PROBLEMS, query: (sql) => withDb(() => runQuery(sql)) };
}

main();
