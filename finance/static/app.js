const $ = (s, el = document) => el.querySelector(s);
const money = (n) => (n < 0 ? "-" : "") + "$" + Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const api = async (path, opts = {}) => {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};
const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

let categories = [];
let months = [];

// ---------- tabs ----------
document.querySelectorAll("nav button").forEach((b) =>
  b.addEventListener("click", () => showTab(b.dataset.tab)));
function showTab(name) {
  document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll("main > section").forEach((s) => (s.hidden = s.id !== "tab-" + name));
  try { localStorage.setItem("tab", name); } catch {}
  ({ budget: loadBudget, transactions: loadTransactions, import: loadImports })[name]();
}

// ---------- shared ----------
async function loadStatus() {
  const s = await api("/api/status");
  const el = $("#model-status");
  el.textContent = s.model_ok ? `Local model: ${s.model.split(":")[0]}` : "Local model offline";
  el.className = "pill " + (s.model_ok ? "ok" : "bad");
  $("#accounts").innerHTML = s.accounts.map((a) => `<option value="${esc(a.name)}">`).join("");
  return s;
}
async function loadMeta() {
  [categories, months] = await Promise.all([api("/api/categories"), api("/api/months")]);
  const now = new Date().toISOString().slice(0, 7);
  const monthOpts = (months.includes(now) ? months : [now, ...months]);
  const cur = $("#month").value;
  $("#month").innerHTML = monthOpts.map((m) => `<option>${m}</option>`).join("");
  $("#month").value = cur && monthOpts.includes(cur) ? cur : (months[0] || now);
  const txm = $("#tx-month").value;
  $("#tx-month").innerHTML = `<option value="">All months</option>` + months.map((m) => `<option>${m}</option>`).join("");
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
  $("#t-budgeted").textContent = b.budgeted ? money(b.budgeted) : "not set";
  const note = $("#uncat-note");
  note.hidden = !b.uncategorized.count;
  note.innerHTML = `${b.uncategorized.count} uncategorized transactions (${money(b.uncategorized.amount)}) aren't counted yet. <a href="#" id="see-uncat">Review them</a>`;
  const seeUncat = $("#see-uncat");
  if (seeUncat) seeUncat.onclick = (e) => { e.preventDefault(); $("#tx-cat").value = "__none"; $("#tx-month").value = b.month; showTab("transactions"); };
  $("#budget-table tbody").innerHTML = b.categories.map((r) => {
    const pct = r.budget ? Math.min(100, (r.spent / r.budget) * 100) : 0;
    const over = r.budget && r.spent > r.budget;
    return `<tr class="${r.spent || r.budget ? "" : "dim"}">
      <td><a href="#" data-cat="${r.category_id}">${esc(r.category)}</a></td>
      <td class="num ${over ? "neg" : ""}">${money(r.spent)}</td>
      <td class="num"><button class="linkish" data-budget="${r.category_id}" data-val="${r.budget ?? ""}">${r.budget != null ? money(r.budget) : "set"}</button></td>
      <td class="bar-col">${r.budget ? `<div class="bar"><div class="${over ? "over" : ""}" style="width:${pct}%"></div></div>
        <small>${over ? money(r.spent - r.budget) + " over" : money(r.budget - r.spent) + " left"}</small>` : ""}</td>
      <td class="num muted">${r.avg3 ? money(r.avg3) : ""}</td></tr>`;
  }).join("");
  document.querySelectorAll("[data-budget]").forEach((btn) => btn.onclick = async () => {
    const v = prompt("Monthly budget for this category (blank to clear)", btn.dataset.val);
    if (v === null) return;
    const n = v.trim() === "" ? null : parseFloat(v.replace(/[$,]/g, ""));
    if (n !== null && isNaN(n)) return alert("Enter a number");
    await api(`/api/budget/${btn.dataset.budget}`, json("PUT", { monthly_amount: n }));
    loadBudget();
  });
  document.querySelectorAll("[data-cat]").forEach((a) => a.onclick = (e) => {
    e.preventDefault(); $("#tx-cat").value = a.dataset.cat; $("#tx-month").value = b.month; showTab("transactions");
  });
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

// ---------- boot ----------
(async () => {
  const s = await loadStatus();
  await loadMeta();
  let tab = s.transactions ? "budget" : "import";
  try { tab = localStorage.getItem("tab") || tab; } catch {}
  showTab(s.transactions ? tab : "import");
})();
