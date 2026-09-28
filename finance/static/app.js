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
let months = [];

// ---------- tabs ----------
document.querySelectorAll(".sidebar nav button").forEach((b) =>
  b.addEventListener("click", () => showTab(b.dataset.tab)));
function showTab(name) {
  document.querySelectorAll(".sidebar nav button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll("main > section").forEach((s) => (s.hidden = s.id !== "tab-" + name));
  try { localStorage.setItem("tab", name); } catch {}
  ({ budget: loadBudget, goals: loadGoals, ask: loadChat, transactions: loadTransactions, import: loadImports })[name]();
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
  note.innerHTML = `${b.uncategorized.count} uncategorized transactions (${money(b.uncategorized.amount)}) aren't counted yet. <a href="#" id="see-uncat">Review them</a>`;
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
  }).join("") || `<div class="env-empty">No spending this month yet.</div>`;

  const idle = b.categories.filter((r) => r.budget == null && !(r.spent > 0));
  $("#loose-card").hidden = !idle.length;
  $("#loose").innerHTML = idle.map((r) => `<button class="chip quiet" data-cat="${r.category_id}">${esc(r.category)}</button>`).join("");

  const byId = Object.fromEntries(b.categories.map((r) => [r.category_id, r]));
  document.querySelectorAll("#envelopes [data-cat], #loose [data-cat]").forEach((el) =>
    el.onclick = () => editBudget(byId[el.dataset.cat]));
}

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

// ---------- import ----------
$("#import-form").onsubmit = async (e) => {
  e.preventDefault();
  const form = e.target;
  const files = [...form.file.files];
  const btn = $("button[type=submit]", form); btn.disabled = true;
  for (const f of files) {
    const line = document.createElement("div");
    line.className = "log-line";
    $("#import-log").prepend(line);
    const fd = new FormData();
    fd.append("file", f); fd.append("account", form.account.value); fd.append("sign", form.sign.value);
    try {
      const job = await pollJob(await api("/api/import", { method: "POST", body: fd }),
        (j) => (line.innerHTML = `<b>${esc(f.name)}</b>: ${esc(j.message || "")}`));
      if (job.status === "error") { line.innerHTML = `<b>${esc(f.name)}</b>: <span class="neg">${esc(job.message)}</span>`; continue; }
      const c = job.categorize || {};
      line.innerHTML = `<b>${esc(f.name)}</b>: found ${job.found}, added ${job.added} new` +
        (job.found > job.added ? ` (${job.found - job.added} already imported)` : "") +
        (job.flipped ? ", flipped signs (purchases were positive)" : "") +
        (job.dropped ? `, skipped ${job.dropped} rows the model returned that weren't on the page` : "") +
        `. Categorized ${c.by_rule || 0} by your rules and ${c.by_model || 0} by the local model.` +
        (c.error ? ` <span class="neg">${esc(c.error)}</span>` : "") +
        (job.months?.length ? ` Months: ${job.months.join(", ")}.` : "");
    } catch (err) {
      line.innerHTML = `<b>${esc(f.name)}</b>: <span class="neg">${esc(err.message)}</span>`;
    }
  }
  btn.disabled = false; form.file.value = "";
  await Promise.all([loadMeta(), loadStatus()]);
  loadImports();
};

async function loadImports() {
  const rows = await api("/api/imports");
  $("#imports-table tbody").innerHTML = rows.map((r) => `<tr>
    <td>${r.created_at.slice(0, 16)}</td><td>${esc(r.filename)}</td><td>${esc(r.account)}</td><td class="muted">${r.parser}</td>
    <td class="num">${r.rows_found}</td><td class="num">${r.rows_added ?? ""}</td>
    <td><button class="linkish neg" data-del="${r.id}">Remove</button></td></tr>`).join("") ||
    `<tr><td colspan="7" class="muted">Nothing imported yet.</td></tr>`;
  document.querySelectorAll("[data-del]").forEach((b) => b.onclick = async () => {
    if (!confirm("Remove this import and the transactions it added?")) return;
    await api(`/api/imports/${b.dataset.del}`, { method: "DELETE" });
    await loadMeta(); loadImports();
  });
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
