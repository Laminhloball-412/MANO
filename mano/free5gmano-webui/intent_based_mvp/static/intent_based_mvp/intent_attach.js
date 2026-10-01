const byId = (id) => document.getElementById(id);
let catalog = null;

function escapeHtml(value) {
  return String(value ?? "—").replace(/[&<>'"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char]));
}

function formatNumber(value) {
  if (value === null || value === undefined || value === "") return "—";
  return Number.isFinite(Number(value)) ? Number(value).toLocaleString(undefined, {maximumFractionDigits: 3}) : escapeHtml(value);
}

function showError(message) {
  const box = byId("intent-error");
  box.textContent = message || "";
  box.hidden = !message;
}

async function requestJson(url, options) {
  const response = await fetch(url, options);
  const body = await response.json();
  if (!response.ok || body.ok === false) throw new Error(body.error || `Request failed (${response.status})`);
  return body;
}

function populateCatalog(document) {
  catalog = document.data;
  const service = byId("intent-service");
  service.innerHTML = Object.entries(catalog.services).map(([value, item]) => `<option value="${escapeHtml(value)}">${escapeHtml(item.label)}</option>`).join("");
  const priority = byId("intent-priority");
  priority.innerHTML = Object.keys(catalog.priorities).map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("");
  priority.value = "high";
  updateServiceOptions();
}

function updateServiceOptions() {
  if (!catalog) return;
  const definition = catalog.services[byId("intent-service").value];
  byId("intent-quality").innerHTML = Object.keys(definition.qualities).map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("");
  byId("intent-quality").value = "high" in definition.qualities ? "high" : Object.keys(definition.qualities)[0];
  byId("intent-scale-label").textContent = definition.scale_label;
  byId("intent-scale").min = definition.scale.minimum;
  byId("intent-scale").max = definition.scale.maximum;
}

function serviceIntent() {
  return {
    schema_version: "2.0",
    service_type: byId("intent-service").value,
    quality: byId("intent-quality").value,
    scale: Number(byId("intent-scale").value),
    priority: byId("intent-priority").value,
    duration_minutes: Number(byId("intent-duration").value),
    execution_mode: byId("intent-execution").value,
  };
}

function renderSla(job) {
  const translation = job.translation || {};
  const sla = translation.technical_sla;
  if (!sla) return;
  const metrics = [
    ["Minimum throughput", sla.min_throughput_mbps, "Mbps"],
    ["Target throughput", sla.target_throughput_mbps, "Mbps"],
    ["Peak throughput", sla.peak_throughput_mbps, "Mbps"],
    ["Maximum latency", sla.max_latency_ms, "ms"],
    ["Maximum jitter", sla.max_jitter_ms, "ms"],
    ["Maximum loss", sla.max_packet_loss_percent, "%"],
    ["Minimum reliability", sla.min_reliability_percent, "%"],
    ["Expected sessions", sla.expected_sessions, ""],
  ];
  byId("sla-grid").innerHTML = metrics.map(([label, value, unit]) => `<article class="metric-cell"><span>${escapeHtml(label)}</span><strong>${formatNumber(value)} <small>${escapeHtml(unit)}</small></strong></article>`).join("");
  const trace = translation.translation_trace || {};
  const formula = trace.formula || {};
  byId("translation-trace").textContent = `Catalog ${trace.catalog_version || "—"} · ${formatNumber(formula.throughput_per_unit_mbps)} Mbps/unit × ${formatNumber(formula.scale)} units · guaranteed ratio ${formatNumber(formula.guaranteed_ratio)}`;
  byId("sla-empty").hidden = true;
  byId("sla-content").hidden = false;
}

function renderCandidates(job) {
  const decision = job.decision;
  if (!decision) return;
  const selected = decision.selected_candidate ? decision.selected_candidate.candidate_id : "";
  byId("candidate-body").innerHTML = (decision.candidates || []).map((candidate) => {
    const selectedClass = candidate.candidate_id === selected ? " is-selected" : "";
    const eligibility = candidate.eligible ? "Eligible" : "Rejected";
    const reason = candidate.eligible ? "All hard constraints passed" : (candidate.failed_checks || []).join(", ");
    return `<tr class="${selectedClass}"><td><strong>${escapeHtml(candidate.candidate_id)}</strong><small>${escapeHtml(candidate.template_family)}</small></td><td>${escapeHtml(candidate.action)}</td><td><span class="status-chip ${candidate.eligible ? "ok" : "bad"}">${eligibility}</span></td><td>${formatNumber(candidate.suitability_percent)}%</td><td>${escapeHtml(candidate.evidence_status)}</td><td>${escapeHtml(reason)}</td></tr>`;
  }).join("");
}

function renderLifecycle(job) {
  byId("intent-stage").textContent = (job.stage || "idle").replaceAll("_", " ");
  byId("intent-stage").className = `stage-badge ${job.status === "FAILED" ? "is-failed" : job.status === "COMPLETED" ? "is-complete" : ""}`;
  const events = job.events || [];
  byId("lifecycle-list").innerHTML = events.length ? events.map((event) => `<li><span class="lifecycle-marker"></span><div><strong>${escapeHtml(event.stage.replaceAll("_", " "))}</strong><small>${event.at ? new Date(event.at).toLocaleTimeString() : ""}</small></div></li>`).join("") : '<li class="intent-empty">No lifecycle events.</li>';
}

function renderConfiguration(job) {
  const configuration = job.configuration || (job.plan || {}).deployment_configuration;
  if (!configuration) return;
  const ordered = ["namespace","template_family","sst","sd","dnn","five_qi","arp_priority","session_ambr_mbps","downlink_limit_mbps","min_throughput_mbps","target_throughput_mbps","peak_throughput_mbps","max_latency_ms","max_jitter_ms","max_packet_loss_percent","min_reliability_percent","expected_sessions","evidence_status"];
  byId("configuration-content").innerHTML = ordered.filter((key) => key in configuration).map((key) => `<div class="configuration-row"><span>${escapeHtml(key.replaceAll("_", " "))}</span><strong>${escapeHtml(configuration[key])}</strong></div>`).join("");
  byId("configuration-empty").hidden = true;
  byId("configuration-content").hidden = false;
}

function renderJob(job) {
  renderSla(job);
  renderCandidates(job);
  renderLifecycle(job);
  renderConfiguration(job);
  if (job.error) showError(job.error);
}

async function pollIntent(intentId) {
  const body = await requestJson(`/Intent_Attach/api/intents/${encodeURIComponent(intentId)}/`);
  renderJob(body.job);
  if (["QUEUED", "RUNNING"].includes(body.job.status)) {
    window.setTimeout(() => pollIntent(intentId).catch((error) => showError(error.message)), 1200);
  } else {
    byId("intent-submit").disabled = false;
    byId("intent-submit").querySelector("span").textContent = "Evaluate intent";
    loadInventory().catch((error) => showError(error.message));
  }
}

async function loadInventory() {
  const body = await requestJson("/Intent_Attach/api/instances/");
  const instances = body.instances || [];
  byId("inventory-body").innerHTML = instances.length ? instances.map((item) => `<tr><td><strong>${escapeHtml(item.namespace)}</strong></td><td>${escapeHtml(item.template_family)}</td><td>SST ${escapeHtml(item.sst)} · SD ${escapeHtml(item.sd)}</td><td>${formatNumber(item.residual_throughput_mbps)} Mbps</td><td>${escapeHtml(item.evidence_status)}</td><td><span class="status-chip ${item.healthy ? "ok" : "bad"}">${item.healthy ? "Operational" : "Unavailable"}</span></td></tr>`).join("") : '<tr><td colspan="6" class="intent-empty-cell">No managed slice instances are active.</td></tr>';
}

async function submitIntent(event) {
  event.preventDefault();
  showError("");
  const payload = serviceIntent();
  if (payload.execution_mode === "execute" && !window.confirm("Execute this plan and allow Free5GMANO to reuse or allocate a slice?")) return;
  const button = byId("intent-submit");
  button.disabled = true;
  button.querySelector("span").textContent = "Evaluating…";
  try {
    const body = await requestJson("/Intent_Attach/api/intents/", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
    renderJob(body.job);
    await pollIntent(body.job.intent_id);
  } catch (error) {
    button.disabled = false;
    button.querySelector("span").textContent = "Evaluate intent";
    showError(error.message);
  }
}

byId("intent-service").addEventListener("change", updateServiceOptions);
byId("service-intent-form").addEventListener("submit", submitIntent);
byId("inventory-refresh").addEventListener("click", () => loadInventory().catch((error) => showError(error.message)));
requestJson("/Intent_Attach/api/catalog/").then(populateCatalog).catch((error) => showError(error.message));
loadInventory().catch((error) => {
  byId("inventory-body").innerHTML = `<tr><td colspan="6" class="intent-empty-cell">${escapeHtml(error.message)}</td></tr>`;
});
