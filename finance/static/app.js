const $ = (s, el = document) => el.querySelector(s);
const money = (n) => (n < 0 ? "-" : "") + "$" + Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const api = async (path, opts = {}) => {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};
const monthName = (m) => new Date(m + "-15").toLocaleDateString(undefined, { month: "long", year: "numeric" });
const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

let categories = [];
let knownAccounts = {};
const KINDS = { checking: "Checking", savings: "Savings", credit: "Credit card", retirement: "Retirement (401k, IRA)", investment: "Investment / brokerage" };
const kindOptions = (sel) => Object.entries(KINDS).map(([k, v]) => `<option value="${k}" ${k === sel ? "selected" : ""}>${v}</option>`).join("");
let months = [];

// ---------- tabs ----------
document.querySelectorAll(".sidebar nav button").forEach((b) =>
  b.addEventListener("click", () => showTab(b.dataset.tab)));
function showTab(name) {
  document.querySelectorAll(".sidebar nav button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll("main > section").forEach((s) => (s.hidden = s.id !== "tab-" + name));
  try { localStorage.setItem("tab", name); } catch {}
  ({ budget: loadBudget, goals: loadGoals, accounts: loadAccounts, ask: loadChat, transactions: loadTransactions,
     import: loadImports })[name]();
}

// ---------- shared ----------
async function loadStatus() {
  const s = await api("/api/status");
  const el = $("#model-status");
  el.textContent = s.model_ok ? `Local model ready (${s.model.split(":")[0]})` : "Local model offline";
  el.className = s.model_ok ? "ok" : "bad";
  $("#demo-badge").hidden = !s.demo;
  if (s.demo) document.title = "Finance (demo)";
  $("#accounts").innerHTML = s.accounts.map((a) => `<option value="${esc(a.name)}">`).join("");
  knownAccounts = Object.fromEntries(s.accounts.map((a) => [a.name.toLowerCase(), a.kind]));
  return s;
}
async function loadMeta() {
  [categories, months] = await Promise.all([api("/api/categories"), api("/api/months")]);
  const now = new Date().toISOString().slice(0, 7);
  const monthOpts = (months.includes(now) ? months : [now, ...months]);
  const cur = $("#month").value;
  $("#month").innerHTML = monthOpts.map((m) => `<option value="${m}">${monthName(m)}</option>`).join("");
  $("#month").value = cur && monthOpts.includes(cur) ? cur : (months[0] || now);
  const txm = $("#tx-month").value;
  $("#tx-month").innerHTML = `<option value="">All months</option>` + months.map((m) => `<option value="${m}">${monthName(m)}</option>`).join("");
  $("#tx-month").value = txm;
  const txc = $("#tx-cat").value;
  $("#tx-cat").innerHTML = `<option value="">All categories</option><option value="__none">Uncategorized</option>` +
    categories.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("");
  $("#tx-cat").value = txc;
}

async function pollJob(job, onUpdate) {
  while (job.status === "running") {
    onUpdate(job);
    await new Promise((r) => setTimeout(r, 1000));
    job = await api(`/api/jobs/${job.id}`);
  }
  onUpdate(job);
  return job;
}

// ---------- budget ----------
async function loadBudget() {
  const b = await api(`/api/budget?month=${$("#month").value}`);
  $("#t-income").textContent = money(b.income);
  $("#t-spent").textContent = money(b.spent);
  $("#t-net").textContent = money(b.net);
  $("#t-net").className = b.net < 0 ? "neg" : "pos";

  const budgeted = b.categories.filter((r) => r.budget != null);
  const left = budgeted.reduce((s, r) => s + r.budget - r.spent, 0);
  $("#left-label").textContent = budgeted.length ? "Left to spend in budgeted categories" : "Spent this month";
  $("#t-left").textContent = money(budgeted.length ? left : b.spent);
  $("#t-left").className = budgeted.length && left < 0 ? "over" : "";

  const note = $("#uncat-note");
  note.hidden = !b.uncategorized.count;
  note.innerHTML = `${b.uncategorized.count} transactions this month aren't categorized yet. Their ${money(b.uncategorized.spent)} of spending is counted under Uncategorized. <a href="#" id="see-uncat">Review them</a> or use Categorize uncategorized on the Transactions page.`;
  const seeUncat = $("#see-uncat");
  if (seeUncat) seeUncat.onclick = (e) => { e.preventDefault(); $("#tx-cat").value = "__none"; $("#tx-month").value = b.month; showTab("transactions"); };

  // Every category with a budget or with spending gets an envelope; budgeted ones first.
  const envs = b.categories.filter((r) => r.budget != null || r.spent > 0)
    .sort((x, y) => (y.budget != null) - (x.budget != null) || y.spent - x.spent);
  $("#envelopes").innerHTML = envs.map((r) => {
    if (r.budget == null) {
      return `<button class="env unset" data-cat="${r.category_id}" title="Click to set a monthly budget">
        <div class="env-top"><span class="env-name">${esc(r.category)}</span><span class="env-of">no budget</span></div>
        <div class="env-left">${money(r.spent)}</div>
        <div class="env-label">spent &middot; <span class="env-set">Set a budget</span></div>
        <div class="env-bar"><div style="width:0"></div></div>
      </button>`;
    }
    const rem = r.budget - r.spent;
    const cls = rem < 0 ? "over" : rem / r.budget < 0.15 ? "near" : "";
    const pct = r.budget ? Math.min(100, (r.spent / r.budget) * 100) : 100;
    return `<button class="env ${cls}" data-cat="${r.category_id}" title="Spent ${money(r.spent)}. Click to change the budget.">
      <div class="env-top"><span class="env-name">${esc(r.category)}</span><span class="env-of">of ${money(r.budget)}</span></div>
      <div class="env-left">${money(Math.abs(rem))}</div>
      <div class="env-label">${rem < 0 ? "over budget" : "left"}</div>
      <div class="env-bar"><div style="width:${pct}%"></div></div>
    </button>`;
  }).join("") + (b.uncategorized.spent ? `<button class="env over" id="env-uncat" title="Not categorized yet. Click to review.">
      <div class="env-top"><span class="env-name">Uncategorized</span><span class="env-of">${b.uncategorized.count} transactions</span></div>
      <div class="env-left">${money(b.uncategorized.spent)}</div>
      <div class="env-label">spent &middot; <span class="env-set">Review</span></div>
      <div class="env-bar"><div style="width:0"></div></div></button>` : "")
    || `<div class="env-empty">No spending this month yet.</div>`;
  const envUncat = $("#env-uncat");
  if (envUncat) envUncat.onclick = () => { $("#tx-cat").value = "__none"; $("#tx-month").value = b.month; showTab("transactions"); };

  const idle = b.categories.filter((r) => r.budget == null && !(r.spent > 0));
  $("#loose-card").hidden = !idle.length;
  $("#loose").innerHTML = idle.map((r) => `<button class="chip quiet" data-cat="${r.category_id}">${esc(r.category)}</button>`).join("");

  const byId = Object.fromEntries(b.categories.map((r) => [r.category_id, r]));
  document.querySelectorAll("#envelopes [data-cat], #loose [data-cat]").forEach((el) =>
    el.onclick = () => editBudget(byId[el.dataset.cat]));
}

async function showIncome() {
  const month = $("#month").value;
  const inc = await api(`/api/income?month=${month}`);
  const incomeCat = categories.find((c) => c.kind === "income");
  $("#inc-title").textContent = `Income in ${monthName(month)}`;
  $("#inc-counted-total").textContent = money(inc.counted_total);
  $("#inc-other-total").textContent = money(inc.not_counted_total);
  const opts = (sel) => categories.map((c) => `<option value="${c.id}" ${c.id === sel ? "selected" : ""}>${esc(c.name)}</option>`).join("");
  const row = (r, counted) => `<tr><td class="nowrap">${new Date(r.date + "T12:00").toLocaleDateString(undefined, { month: "short", day: "numeric" })}</td><td class="desc" title="${esc(r.description)}">${esc(r.description)}</td>
    <td class="muted">${esc(r.account)}</td><td class="num pos">${money(r.amount)}</td>
    <td>${counted ? `<select data-inc="${r.id}" aria-label="Category">${opts(r.category_id)}</select>` +
        (r.looks_transfer ? ` <span class="warn small">Looks like a card payment or transfer</span>` : "")
      : `<span class="muted small">${esc(r.looks_transfer ? "Card payment or transfer" : r.category || "Uncategorized")}</span>
         <button type="button" class="${r.looks_transfer ? "linkish" : "btn"}" data-count="${r.id}">Count as income</button>`}</td></tr>`;
  $("#inc-counted tbody").innerHTML = inc.counted.map((r) => row(r, true)).join("") || `<tr><td class="muted">Nothing counted as income this month.</td></tr>`;
  $("#inc-other tbody").innerHTML = inc.not_counted.map((r) => row(r, false)).join("") || `<tr><td class="muted">No other money came in.</td></tr>`;
  const set = async (id, cat) => { await api(`/api/transactions/${id}`, json("PATCH", { category_id: cat, remember: true })); showIncome(); loadBudget(); };
  document.querySelectorAll("[data-count]").forEach((b) => b.onclick = () => set(+b.dataset.count, incomeCat.id));
  document.querySelectorAll("[data-inc]").forEach((sel) => sel.onchange = () => set(+sel.dataset.inc, +sel.value));
  if (!$("#income-dialog").open) $("#income-dialog").showModal();
}
$("#income-tile").onclick = showIncome;

function editBudget(r) {
  const dlg = $("#budget-dialog");
  $("#bd-title").textContent = r.category;
  $("#bd-hint").textContent = `Spent ${money(r.spent)} this month` + (r.avg3 ? `, about ${money(r.avg3)} a month lately.` : ".");
  $("#bd-amount").value = r.budget ?? (r.avg3 ? Math.ceil(r.avg3 / 10) * 10 : "");
  $("#bd-clear").hidden = r.budget == null;
  dlg.returnValue = "";
  dlg.onclose = async () => {
    if (dlg.returnValue === "cancel" || !dlg.returnValue) return;
    let n = null;
    if (dlg.returnValue === "save") {
      const raw = $("#bd-amount").value.trim();
      if (raw === "") n = null;
      else { n = parseFloat(raw.replace(/[$,]/g, "")); if (isNaN(n)) return alert("Enter a number"); }
    }
    await api(`/api/budget/${r.category_id}`, json("PUT", { monthly_amount: n }));
    loadBudget();
  };
  dlg.showModal();
  $("#bd-amount").select();
}
$("#month").onchange = loadBudget;
const stepMonth = (d) => { const s = $("#month"); const i = s.selectedIndex - d; if (i >= 0 && i < s.options.length) { s.selectedIndex = i; loadBudget(); } };
$("#prev-month").onclick = () => stepMonth(-1);
$("#next-month").onclick = () => stepMonth(1);

// ---------- transactions ----------
async function loadTransactions() {
  const p = new URLSearchParams();
  if ($("#tx-month").value) p.set("month", $("#tx-month").value);
  const c = $("#tx-cat").value;
  if (c === "__none") p.set("uncategorized", "true"); else if (c) p.set("category_id", c);
  if ($("#tx-q").value) p.set("q", $("#tx-q").value);
  const rows = await api(`/api/transactions?${p}`);
  const total = rows.reduce((s, r) => s + r.amount, 0);
  $("#tx-summary").textContent = `${rows.length} transactions, net ${money(total)}`;
  const opts = (sel) => `<option value="">—</option>` + categories.map((c) =>
    `<option value="${c.id}" ${c.id === sel ? "selected" : ""}>${esc(c.name)}</option>`).join("");
  $("#tx-table tbody").innerHTML = rows.map((r) => `<tr>
    <td>${r.date}</td><td title="Merchant key: ${esc(r.merchant)}">${esc(r.description)}</td><td class="muted">${esc(r.account)}</td>
    <td class="num ${r.amount < 0 ? "" : "pos"}">${money(r.amount)}</td>
    <td><select data-tx="${r.id}">${opts(r.category_id)}</select>
      <span class="src src-${r.category_source || "none"}" title="${{ user: "Set by you", rule: "From your rule for this merchant", model: "Suggested by the local model" }[r.category_source] || "Not categorized"}">${{ user: "you", rule: "rule", model: "AI" }[r.category_source] || ""}</span></td>
  </tr>`).join("");
  document.querySelectorAll("[data-tx]").forEach((s) => s.onchange = async () => {
    if (!s.value) return;
    const r = await api(`/api/transactions/${s.dataset.tx}`, json("PATCH", { category_id: +s.value, remember: true }));
    if (r.also_updated) flash(`Also applied to ${r.also_updated} other transaction(s) from this merchant.`);
    loadTransactions();
  });
}
let qTimer;
$("#tx-q").oninput = () => { clearTimeout(qTimer); qTimer = setTimeout(loadTransactions, 250); };
$("#tx-month").onchange = loadTransactions;
$("#tx-cat").onchange = loadTransactions;
$("#recat").onclick = async () => {
  const btn = $("#recat"); btn.disabled = true;
  const job = await pollJob(await api("/api/categorize", { method: "POST" }), (j) => (btn.textContent = j.message));
  btn.disabled = false; btn.textContent = "Categorize uncategorized";
  if (job.status === "error" || job.categorize?.error) alert(job.message || job.categorize.error);
  loadTransactions();
};
function flash(msg) { $("#tx-summary").textContent = msg; }

// ---------- import: drop, let the model read, review, save ----------
let batch = null;          // {id, items}
let decisions = {};        // item id -> {choice: "acct:<id>"|"new"|"skip", newAcct, contributing, balanceDate}
let accountList = [];

async function addFiles(files) {
  const ok = [...files].filter((f) => /\.(csv|pdf|txt)$/i.test(f.name));
  if (!ok.length) return alert("Only CSV and PDF statements can be imported.");
  if (batch && batch.items.some((i) => i.status === "reading" || i.status === "waiting"))
    return alert("Still reading the last batch. Drop these again when it's done.");
  const fd = new FormData();
  ok.forEach((f) => fd.append("files", f));
  accountList = (await api("/api/status")).accounts;
  batch = await api("/api/stage", { method: "POST", body: fd });
  decisions = {};
  $("#review").hidden = false;
  renderReview();
  pollBatch();
}

async function pollBatch() {
  while (batch && batch.status !== "ready") {
    await new Promise((r) => setTimeout(r, 1200));
    if (!batch) return;
    batch = await api(`/api/stage/${batch.id}`);
    renderReview();
  }
}

const acctLabel = (a) => [a.name, a.institution && !a.name.includes(a.institution) ? a.institution : "",
  a.last4 && !a.name.includes(a.last4) ? `...${a.last4}` : ""].filter(Boolean).join(" · ");

function decisionFor(it) {
  if (!decisions[it.id] && it.status === "ready") {
    const m = it.match || {};
    decisions[it.id] = {
      choice: m.account_id ? `acct:${m.account_id}` : m.confidence === "ambiguous" ? "" : "new",
      newAcct: { name: it.suggested_name, kind: it.ident.account_type || "checking", institution: it.ident.institution || "",
                 last4: it.ident.last4 || "", subtype: it.ident.subtype || "" },
      contributing: null, balanceDate: "",
    };
    // A second file for the same new account joins the first one's choice.
    for (const other of batch.items) {
      const d = decisions[other.id];
      if (other.id !== it.id && d?.choice === "new" && it.ident.last4 && d.newAcct.last4 === it.ident.last4 &&
          (d.newAcct.institution || "") === (it.ident.institution || "")) decisions[it.id].newAcct = d.newAcct;
    }
  }
  return decisions[it.id];
}

function renderReview() {
  if (!batch) { $("#review").hidden = true; return; }
  const reading = batch.items.filter((i) => i.status === "waiting" || i.status === "reading").length;
  $("#review-title").textContent = reading ? `Reading statements (${batch.items.length - reading} of ${batch.items.length} done)`
    : `Review ${batch.items.length} statement${batch.items.length === 1 ? "" : "s"}`;
  // Keep focus and typing intact: only re-render items whose state changed.
  for (const it of batch.items) {
    let el = document.getElementById(`ritem-${it.id}`);
    const key = `${it.status}|${it.message}`;
    if (el && el.dataset.key === key) continue;
    const html = reviewItemHtml(it);
    if (!el) { el = document.createElement("div"); el.id = `ritem-${it.id}`; $("#review-list").append(el); }
    el.dataset.key = key;
    el.className = "ritem";
    el.innerHTML = html;
    wireItem(it, el);
  }
  const ready = batch.items.filter((i) => i.status === "ready");
  $("#review-save").disabled = !!reading || !ready.length;
}

function reviewItemHtml(it) {
  if (it.status !== "ready") {
    const err = it.status === "error";
    return `<div class="ritem-top"><span class="ritem-file">${esc(it.filename)}</span></div>
      <div class="ritem-status ${err ? "neg" : ""}">${esc(it.status === "saved" ? "Saved" : it.message || "Waiting")}</div>`;
  }
  const d = decisionFor(it), m = it.match || {};
  const opts = accountList.map((a) => `<option value="acct:${a.id}" ${d.choice === `acct:${a.id}` ? "selected" : ""}>${esc(acctLabel(a))}</option>`).join("");
  const qs = (it.questions || []).filter((q) => q.id !== "which" && q.id !== "type").map((q) => {
    if (q.id === "contributing") return `<div class="rq" data-q="contributing">${esc(q.text)}<div class="rq-opts">
      ${[["Yes", 1], ["No", 0], ["Not sure", null]].map(([l, v]) => `<button type="button" data-v="${v}" class="${d.contributing === v ? "on" : ""}">${l}</button>`).join("")}</div></div>`;
    if (q.id === "balance_date") return `<div class="rq">${esc(q.text)}<label>Balance date <input type="date" data-bdate value="${esc(d.balanceDate)}"></label></div>`;
    return `<div class="rq">${esc(q.text)}</div>`;
  }).join("");
  return `<div class="ritem-top"><span class="ritem-file" title="${esc(it.filename)}">${esc(it.filename)}</span>
      <label><input type="checkbox" data-skip ${d.choice === "skip" ? "checked" : ""}> Skip</label></div>
    <div class="ritem-summary">${(it.summary || []).map((x, i) => i ? esc(x) : `<b>${esc(x)}</b>`).join(" · ")}</div>
    ${m.reason ? `<div class="ritem-match ${m.confidence}">${esc(m.confidence === "sure" ? "Matched: " : m.confidence === "likely" ? "Probably: " : "")}${esc(m.reason)}</div>` : ""}
    <div class="ritem-fields">
      <label>Account<select data-choice class="wide">
        ${d.choice === "" ? `<option value="" selected>Choose an account</option>` : ""}
        <option value="new" ${d.choice === "new" ? "selected" : ""}>New account</option>${opts}</select></label>
      <div data-newfields class="newfields" ${d.choice === "new" ? "" : "hidden"}>
        <label>Name<input data-n="name" value="${esc(d.newAcct.name)}" class="wide"></label>
        <label>Type<select data-n="kind">${kindOptions(d.newAcct.kind)}</select></label>
        <label>Institution<input data-n="institution" value="${esc(d.newAcct.institution)}" size="12"></label>
        <label>Last 4<input data-n="last4" value="${esc(d.newAcct.last4)}" size="5" inputmode="numeric" maxlength="4"></label>
      </div>
    </div>${qs}`;
}

function wireItem(it, el) {
  if (it.status !== "ready") return;
  const d = decisionFor(it);
  const sel = el.querySelector("[data-choice]");
  sel.onchange = () => {
    d.choice = sel.value;
    el.querySelectorAll("[data-newfields]").forEach((x) => (x.hidden = d.choice !== "new"));
  };
  el.querySelector("[data-skip]").onchange = (e) => {
    d.choice = e.target.checked ? "skip" : (it.match?.account_id ? `acct:${it.match.account_id}` : "new");
    el.classList.toggle("skip", e.target.checked);
    if (!e.target.checked) sel.value = d.choice;
  };
  el.querySelectorAll("[data-n]").forEach((inp) => inp.oninput = inp.onchange = () => (d.newAcct[inp.dataset.n] = inp.value));
  el.querySelectorAll("[data-q=contributing] button").forEach((b) => b.onclick = () => {
    d.contributing = b.dataset.v === "null" ? null : +b.dataset.v;
    el.querySelectorAll("[data-q=contributing] button").forEach((x) => x.classList.toggle("on", x === b));
  });
  const bd = el.querySelector("[data-bdate]");
  if (bd) bd.onchange = () => (d.balanceDate = bd.value);
}

$("#review-cancel").onclick = () => { batch = null; decisions = {}; $("#review-list").innerHTML = ""; renderReview(); };
$("#file-input").onchange = (e) => { addFiles(e.target.files); e.target.value = ""; };

$("#review-save").onclick = async () => {
  const items = [];
  for (const it of batch.items.filter((i) => i.status === "ready")) {
    const d = decisionFor(it);
    if (d.choice === "") return alert(`Choose an account for ${it.filename}.`);
    if (d.choice === "new" && !d.newAcct.name.trim()) return alert(`Name the new account for ${it.filename}.`);
    if ((it.questions || []).some((q) => q.id === "balance_date") && !d.balanceDate && d.choice !== "skip")
      return alert(`Give the balance date for ${it.filename}, or skip it.`);
    items.push({ id: it.id, skip: d.choice === "skip",
      account_id: d.choice.startsWith("acct:") ? +d.choice.slice(5) : null,
      new_account: d.choice === "new" ? { ...d.newAcct, last4: d.newAcct.last4 || null, institution: d.newAcct.institution || null,
                                          subtype: d.newAcct.subtype || null } : null,
      contributing: d.contributing, balance_date: d.balanceDate || null, sign: $("#review-sign").value });
  }
  const btn = $("#review-save"); btn.disabled = true;
  const job = await pollJob(await api(`/api/stage/${batch.id}/commit`, json("POST", { items })), (j) => (btn.textContent = j.message || "Saving"));
  btn.textContent = "Save";
  for (const r of (job.results || []).slice().reverse()) {
    const line = document.createElement("div");
    line.className = "log-line";
    line.innerHTML = r.not_imported ? `<b>${esc(r.filename)}</b>: skipped.` : r.error ? `<b>${esc(r.filename)}</b>: <span class="neg">${esc(r.error)}</span>`
      : describe({ ...r, categorize: job.categorize }, r.filename);
    $("#import-log").prepend(line);
  }
  if (job.status === "error") alert(job.message);
  batch = null; decisions = {}; $("#review-list").innerHTML = ""; renderReview();
  await Promise.all([loadMeta(), loadStatus()]);
  loadImports();
};

// Drag and drop anywhere in the window; never let a dropped file navigate the page away.
let dragDepth = 0;
const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
window.addEventListener("dragenter", (e) => { if (hasFiles(e)) { dragDepth++; $("#drop-overlay").hidden = false; } });
window.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; $("#drop-overlay").hidden = true; } });
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => {
  e.preventDefault();
  dragDepth = 0; $("#drop-overlay").hidden = true;
  if (!e.dataTransfer?.files?.length) return;
  if ($("#tab-import").hidden) showTab("import");
  addFiles(e.dataTransfer.files);
});

function describe(job, name) {
  const c = job.categorize || {};
  const bits = [job.found ? `found ${job.found} transactions, added ${job.added}` : "balance only"];
  if (job.skipped) bits.push(`skipped ${job.skipped} already imported`);
  if (job.flipped) bits.push("flipped signs (purchases were positive)");
  if (job.dropped) bits.push(`ignored ${job.dropped} rows the model read that weren't on the page`);
  let html = `<b>${esc(name)}</b>: ${bits.join(", ")}.`;
  if (job.period_start && job.period_end) html += ` Period ${job.period_start} to ${job.period_end}.`;
  else if (job.period_end) html += ` As of ${job.period_end}.`;
  if (job.ending_balance != null) html += ` Ending balance ${money(job.ending_balance)}.`;
  if (job.balances_added || job.balances_confirmed)
    html += ` Balance history: ${job.balances_added} new point${job.balances_added === 1 ? "" : "s"}` +
      (job.balances_confirmed ? `, ${job.balances_confirmed} already known` : "") + ".";
  if (job.account_kind === "retirement" || job.account_kind === "investment") html += " Kept out of the budget.";
  else html += ` Categorized ${(c.by_pattern || 0) + (c.by_rule || 0)} by rules and ${c.by_model || 0} by the local model.`;
  if (c.error) html += ` <span class="neg">${esc(c.error)}</span>`;
  const notes = [...(job.skipped_examples || []).map((x) => `Skipped as overlap: ${esc(x)}`),
                 ...(job.balance_conflicts || []).map((x) => `<span class="neg">Balance mismatch ${esc(x)}</span>`)];
  if (notes.length) html += `<ul>${notes.map((n) => `<li>${n}</li>`).join("")}</ul>`;
  return html;
}

async function loadImports() {
  const rows = await api("/api/imports");
  $("#imports-table tbody").innerHTML = rows.map((r) => `<tr>
    <td>${r.created_at.slice(0, 16)}</td><td>${esc(r.filename)}</td><td>${esc(r.account)}</td>
    <td class="muted">${r.period_start && r.period_end ? `${r.period_start} to ${r.period_end}` : ""}</td>
    <td class="num">${r.rows_found}</td><td class="num">${r.rows_added ?? ""}</td><td class="num muted">${r.rows_skipped || ""}</td>
    <td><button class="linkish neg" data-del="${r.id}">Remove</button></td></tr>`).join("") ||
    `<tr><td colspan="8" class="muted">Nothing imported yet.</td></tr>`;
  document.querySelectorAll("[data-del]").forEach((b) => b.onclick = async () => {
    if (!confirm("Remove this import, the transactions it added and its balances?")) return;
    await api(`/api/imports/${b.dataset.del}`, { method: "DELETE" });
    await loadMeta(); loadImports();
  });
}

// ---------- accounts ----------
function sparkline(hist) {
  if (hist.length < 2) return "";
  const w = 300, h = 64, pad = 4;
  const xs = hist.map((p) => new Date(p.date).getTime()), ys = hist.map((p) => p.balance);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const X = (x) => pad + ((x - x0) / (x1 - x0 || 1)) * (w - 2 * pad);
  const Y = (y) => h - pad - ((y - y0) / (y1 - y0 || 1)) * (h - 2 * pad);
  const pts = hist.map((p, i) => `${X(xs[i]).toFixed(1)},${Y(ys[i]).toFixed(1)}`);
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="Balance from ${money(ys[0])} to ${money(ys.at(-1))}">
    <path class="spark-fill" d="M${pts[0]} L${pts.join(" L")} L${X(x1).toFixed(1)},${h} L${X(x0).toFixed(1)},${h} Z"></path>
    <polyline class="spark" points="${pts.join(" ")}"></polyline></svg>`;
}

async function loadAccounts() {
  const list = await api("/api/accounts");
  const total = (kinds) => list.filter((a) => kinds.includes(a.kind) && a.latest).reduce((s, a) => s + a.latest.balance, 0);
  const tiles = [["Retirement", ["retirement"]], ["Investments", ["investment"]], ["Cash and savings", ["checking", "savings"]]]
    .filter(([, k]) => list.some((a) => k.includes(a.kind) && a.latest));
  $("#acct-tiles").innerHTML = tiles.map(([label, k]) =>
    `<div class="card tile"><span>${label}</span><strong>${money(total(k))}</strong></div>`).join("");
  $("#acct-list").innerHTML = list.map((a) => {
    const l = a.latest, first = a.history[0];
    const change = l && first && a.history.length > 1 ? l.balance - first.balance : null;
    const facts = [];
    if (change != null) facts.push(`${change >= 0 ? "Up" : "Down"} ${money(Math.abs(change))} since ${niceDate(first.date)}`);
    if (a.transactions) facts.push(`${a.transactions} transactions, ${a.first_date} to ${a.last_date}`);
    if (!a.in_budget && a.in_12m) facts.push(`${money(a.in_12m)} in over the last 12 months (contributions, dividends)`);
    const ident = [a.institution, a.subtype, a.last4 ? `ending ${a.last4}` : ""].filter(Boolean).join(" · ");
    if (!a.in_budget && a.contributing != null) facts.push(a.contributing ? "You're contributing" : "Not contributing now");
    return `<div class="acct">
      <div class="acct-top"><span class="acct-name" title="${esc(a.name)}">${esc(a.name)}</span>
        <select data-kind="${a.id}" aria-label="Type of ${esc(a.name)}">${kindOptions(a.kind)}</select></div>
      ${ident ? `<div class="muted small">${esc(ident)}</div>` : ""}
      <div class="acct-bal">${l ? money(l.balance) : "No balance yet"} ${l ? `<span>as of ${l.date}</span>` : ""}</div>
      ${sparkline(a.history)}
      <div class="acct-facts">${facts.map((f) => `<span>${f}</span>`).join("")}${a.in_budget ? "" : `<span class="tag">Not in budget</span>`}</div>
      <div class="acct-actions"><button class="linkish" data-bal="${a.id}">Add a balance</button></div>
    </div>`;
  }).join("") || `<div class="env-empty">No accounts yet. Import a statement to create one.</div>`;
  document.querySelectorAll("[data-kind]").forEach((el) => el.onchange = async () => {
    await api(`/api/accounts/${el.dataset.kind}`, json("PATCH", { kind: el.value }));
    await loadStatus(); loadAccounts();
  });
  document.querySelectorAll("[data-bal]").forEach((el) => el.onclick = () => addBalance(list.find((a) => a.id == el.dataset.bal)));
}

function addBalance(a) {
  const dlg = $("#balance-dialog");
  $("#bal-title").textContent = `Balance for ${a.name}`;
  $("#bal-date").value = new Date().toISOString().slice(0, 10);
  $("#bal-amount").value = "";
  dlg.returnValue = "";
  dlg.onclose = async () => {
    if (dlg.returnValue !== "save") return;
    const n = parseFloat($("#bal-amount").value.replace(/[$,]/g, ""));
    if (isNaN(n)) return alert("Enter a number");
    await api(`/api/accounts/${a.id}/balances`, json("POST", { date: $("#bal-date").value, balance: n }));
    loadAccounts();
  };
  dlg.showModal();
}

// ---------- goals ----------
let accounts = [];
const niceDate = (d) => d ? new Date(d + "T12:00").toLocaleDateString(undefined, { month: "short", year: "numeric" }) : "";
const STATE_TEXT = { on_track: "On track", behind: "Behind pace", done: "Reached", no_pace: "" };

async function loadGoals() {
  const gl = await api("/api/goals");
  $("#goals").innerHTML = gl.map((g) => {
    const facts = [];
    if (g.target_date) facts.push(`Target ${niceDate(g.target_date)}` + (g.needed_per_month ? `: needs ${money(g.needed_per_month)} a month` : ""));
    if (g.account) facts.push(`${esc(g.account)} is getting ${money(g.pace || 0)} a month lately`);
    else facts.push("Not linked to an account; update the saved amount as you go");
    if (g.projected_date && g.state !== "done") facts.push(`At this pace: ${niceDate(g.projected_date)}`);
    return `<div class="goal ${g.state}">
      <div class="goal-top"><span class="goal-name">${esc(g.name)}</span><button class="linkish" data-goal="${g.id}">Edit</button></div>
      <div class="goal-amt">${money(g.saved)} <span>of ${money(g.target_amount)}</span></div>
      <div class="env-bar"><div style="width:${g.pct}%"></div></div>
      <div class="goal-state">${STATE_TEXT[g.state] || ""}</div>
      <div class="goal-facts">${facts.map((f) => `<span>${f}</span>`).join("")}</div>
    </div>`;
  }).join("") || `<div class="env-empty goal-empty">No goals yet. Add one here, or tell Ask about something you're saving for.</div>`;
  document.querySelectorAll("[data-goal]").forEach((b) => b.onclick = () => editGoal(gl.find((g) => g.id == b.dataset.goal)));
}

async function editGoal(g) {
  const s = await api("/api/status");
  accounts = s.accounts;
  $("#gd-account").innerHTML = `<option value="">No account</option>` +
    accounts.map((a) => `<option value="${a.id}">${esc(a.name)}</option>`).join("");
  $("#gd-title").textContent = g ? "Edit goal" : "New goal";
  $("#gd-name").value = g?.name || "";
  $("#gd-amount").value = g?.target_amount ?? "";
  $("#gd-date").value = g?.target_date || "";
  $("#gd-saved").value = g ? g.saved : "";
  $("#gd-account").value = g?.account_id || "";
  $("#gd-delete").hidden = !g;
  const dlg = $("#goal-dialog");
  dlg.returnValue = "";
  dlg.onclose = async () => {
    const v = dlg.returnValue;
    if (v === "delete") {
      if (confirm(`Delete the goal "${g.name}"?`)) await api(`/api/goals/${g.id}`, { method: "DELETE" });
    } else if (v === "save") {
      const num = (x) => x.trim() === "" ? null : parseFloat(x.replace(/[$,]/g, ""));
      const body = { name: $("#gd-name").value.trim(), target_amount: num($("#gd-amount").value),
                     target_date: $("#gd-date").value || null, account_id: +$("#gd-account").value || null };
      const saved = num($("#gd-saved").value);
      if (!g || saved !== g.saved) body.saved_so_far = saved ?? 0;
      if (!body.name || !(body.target_amount > 0)) return alert("A goal needs a name and an amount");
      await api(g ? `/api/goals/${g.id}` : "/api/goals", json(g ? "PATCH" : "POST", body));
    }
    loadGoals();
  };
  dlg.showModal();
}
$("#new-goal").onclick = () => editGoal(null);

// ---------- ask ----------
const STARTERS = ["Where did most of our money go last month?", "Which subscriptions do we pay for?",
  "We want to save $10,000 for a trip by next summer", "Are we on track for our goals?"];

function md(text) {
  // Tiny, safe markdown: escape first, then bold and bullet lists.
  const lines = esc(text).split("\n");
  let html = "", list = false;
  for (const raw of lines) {
    const line = raw.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
    const m = line.match(/^\s*[-*•]\s+(.*)/);
    if (m) { if (!list) { html += "<ul>"; list = true; } html += `<li>${m[1]}</li>`; continue; }
    if (list) { html += "</ul>"; list = false; }
    if (line.trim()) html += `<p>${line}</p>`;
  }
  return html + (list ? "</ul>" : "");
}

function proposalHtml(p) {
  const x = p.payload;
  let kind, title, body = "";
  if (p.kind === "goal") {
    kind = "Suggested goal"; title = esc(x.name);
    body = `<table><tr><td class="muted">Target</td><td class="num">${money(x.target_amount)}${x.target_date ? " by " + niceDate(x.target_date) : ""}</td></tr>` +
      (x.saved_so_far ? `<tr><td class="muted">Already saved</td><td class="num">${money(x.saved_so_far)}</td></tr>` : "") +
      (x.account ? `<tr><td class="muted">Counts deposits into</td><td class="num">${esc(x.account)}</td></tr>` : "") + "</table>";
  } else if (p.kind === "goal_update") {
    kind = "Suggested goal change"; title = esc(x.goal);
    const label = { target_amount: "Target", target_date: "Target date", saved_so_far: "Saved so far", name: "Name", status: "Status" };
    body = "<table>" + Object.entries(x.changes).map(([k, v]) => `<tr><td class="muted">${label[k] || k}</td><td class="num">${
      typeof v === "number" ? money(v) : k === "target_date" ? niceDate(v) : esc(v)}</td></tr>`).join("") + "</table>";
  } else {
    kind = "Suggested budget changes"; title = "";
    body = (x.reason ? `<div class="muted small">${esc(x.reason)}</div>` : "") +
      `<table><tr><td></td><td class="num muted small">Budget now</td><td class="num muted small">Spent lately</td><td class="num muted small">New budget</td></tr>` +
      x.changes.map((c) => `<tr><td>${esc(c.category)}</td><td class="num muted">${c.from != null ? money(c.from) : "none"}</td>
      <td class="num muted">${c.avg_spent != null ? money(c.avg_spent) : ""}</td><td class="num"><b>${money(c.to)}</b></td></tr>`).join("") + "</table>" +
      (x.freed ? `<div class="small">Frees about <b>${money(x.freed)}</b> a month compared with recent spending.</div>` : "");
  }
  const actions = p.status === "pending"
    ? `<button class="btn primary" data-accept="${p.id}">${p.kind === "budgets" ? "Apply budgets" : p.kind === "goal" ? "Save goal" : "Update goal"}</button>
       <button class="btn" data-dismiss="${p.id}">Dismiss</button>`
    : `<span class="proposal-done ${p.status === "accepted" ? "pos" : "muted"}">${p.status === "accepted" ? "Accepted" : "Dismissed"}</span>`;
  return `<div class="proposal ${p.status}"><div class="proposal-kind">${kind}</div>
    ${title ? `<div class="proposal-title">${title}</div>` : ""}${body}<div class="proposal-actions">${actions}</div></div>`;
}

function renderChat(msgs) {
  $("#chat").innerHTML = msgs.map((m) => `<div class="msg ${m.role}">${m.role === "user" ? esc(m.content) : md(m.content)}</div>` +
    (m.proposals || []).map(proposalHtml).join("")).join("");
  $("#starters").innerHTML = msgs.length ? "" : STARTERS.map((q) => `<button class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("");
  document.querySelectorAll("[data-q]").forEach((b) => b.onclick = () => { $("#ask-input").value = b.dataset.q; $("#ask-form").requestSubmit(); });
  document.querySelectorAll("[data-accept],[data-dismiss]").forEach((b) => b.onclick = async () => {
    const id = b.dataset.accept || b.dataset.dismiss;
    await api(`/api/proposals/${id}/${b.dataset.accept ? "accept" : "dismiss"}`, { method: "POST" });
    loadChat();
  });
}

let chatMsgs = [];
async function loadChat() {
  chatMsgs = await api("/api/chat");
  renderChat(chatMsgs);
  window.scrollTo(0, document.body.scrollHeight);
}

$("#ask-form").onsubmit = async (e) => {
  e.preventDefault();
  const input = $("#ask-input"), text = input.value.trim();
  if (!text) return;
  input.value = ""; autoGrow();
  const btn = $("button[type=submit]", e.target); btn.disabled = true;
  renderChat([...chatMsgs, { role: "user", content: text, proposals: [] }]);
  $("#chat").insertAdjacentHTML("beforeend", `<div class="msg assistant thinking">Looking at your numbers…</div>`);
  window.scrollTo(0, document.body.scrollHeight);
  try {
    await api("/api/chat", json("POST", { message: text }));
  } catch (err) {
    alert(err.message);
  }
  btn.disabled = false;
  await loadChat();
  input.focus();
};
function autoGrow() { const t = $("#ask-input"); t.style.height = "auto"; t.style.height = t.scrollHeight + "px"; }
$("#ask-input").addEventListener("input", autoGrow);
$("#ask-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#ask-form").requestSubmit(); }
});
$("#clear-chat").onclick = async () => {
  if (!confirm("Clear the conversation? Saved goals and budgets stay.")) return;
  await api("/api/chat", { method: "DELETE" });
  loadChat();
};

// ---------- boot ----------
(async () => {
  const s = await loadStatus();
  await loadMeta();
  let tab = s.transactions ? "budget" : "import";
  try { tab = localStorage.getItem("tab") || tab; } catch {}
  showTab(s.transactions ? tab : "import");
})();
// Enter in the amount field saves (the form's first button is "Remove").
$("#bd-amount").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); $("#budget-dialog").close("save"); }
});
