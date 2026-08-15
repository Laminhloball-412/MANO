const presets = {
  video: {name: "video_streaming", throughput: 20, latency: 120, jitter: 15, loss: 1, reliability: 99, rate: 30},
  control: {name: "robot_control", throughput: 2, latency: 10, jitter: 3, loss: 0.01, reliability: 99.999, rate: 100},
  sensor: {name: "massive_iot", throughput: 0.2, latency: 250, jitter: 120, loss: 5, reliability: 95, rate: 1}
};
const byId = (id) => document.getElementById(id);

function applyPreset(name) {
  const value = presets[name];
  byId("service-name").value = value.name;
  ["throughput", "latency", "jitter", "loss", "reliability"].forEach((key) => byId(key).value = value[key]);
  byId("message-rate").value = value.rate;
}

async function loadUes() {
  const response = await fetch("/api/intent-attach/ues/");
  const body = await response.json();
  if (!body.ok) throw new Error(body.error);
  byId("ue-id").innerHTML = body.ues.map((ue) => {
    const allowed = ue.allowed_slice_types.length ? ue.allowed_slice_types.join(", ") : "no allowed slice";
    return `<option value="${ue.ue_id}">UE${ue.ue_id} · IMSI …${ue.imsi.slice(-4)} · ${allowed}</option>`;
  }).join("");
}

function renderJob(job) {
  const status = byId("job-status");
  status.textContent = job.status;
  status.className = `status-badge ${job.status.toLowerCase()}`;
  byId("job-stage").textContent = job.stage.replaceAll("_", " ");
  byId("event-list").innerHTML = (job.events || []).map((event) => `<li><b>${event.stage.replaceAll("_", " ")}</b><small>${new Date(event.at).toLocaleTimeString()}</small></li>`).join("");
  const panel = byId("decision-result");
  if (job.decision || job.error) {
    panel.classList.remove("hidden");
    if (job.error) {
      panel.innerHTML = `<h3>Execution stopped safely</h3><p class="error-text">${job.error}</p>`;
    } else {
      const slice = job.decision.selected_slice;
      const action = job.slice_result ? job.slice_result.action : "PENDING";
      const attached = job.attached_ue || {};
      panel.innerHTML = `<h3>${slice.label} · SST ${slice.sst} / SD ${slice.sd}</h3>
        <p>${job.decision.reasons.join(" · ")}</p>
        <dl><dt>Slice lifecycle</dt><dd>${action}</dd><dt>Runtime UE config</dt><dd>${attached.config || "Pending"}</dd><dt>PDU status</dt><dd>${attached.status || "Pending"}</dd><dt>Assigned IP</dt><dd>${attached.ip || "Pending"}</dd></dl>`;
    }
  }
}

async function poll(jobId) {
  const response = await fetch(`/api/intent-attach/jobs/${jobId}/`);
  const body = await response.json();
  if (!body.ok) throw new Error(body.error);
  renderJob(body.job);
  if (["QUEUED", "RUNNING"].includes(body.job.status)) setTimeout(() => poll(jobId).catch(showError), 1500);
}

function showError(error) { byId("form-error").textContent = error.message || String(error); }

document.querySelectorAll("[data-preset]").forEach((button) => button.addEventListener("click", () => applyPreset(button.dataset.preset)));
byId("intent-form").addEventListener("submit", async (event) => {
  event.preventDefault(); byId("form-error").textContent = "";
  const payload = {ue: {ue_id: Number(byId("ue-id").value)}, service: {name: byId("service-name").value}, qos_requirements: {
    min_throughput_mbps: Number(byId("throughput").value), max_latency_ms: Number(byId("latency").value),
    max_jitter_ms: Number(byId("jitter").value), max_packet_loss_percent: Number(byId("loss").value),
    min_reliability_percent: Number(byId("reliability").value), message_rate_per_second: Number(byId("message-rate").value)
  }};
  try {
    const response = await fetch("/api/intent-attach/jobs/", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
    const body = await response.json(); if (!body.ok) throw new Error(body.error); renderJob(body.job); poll(body.job.job_id).catch(showError);
  } catch (error) { showError(error); }
});

applyPreset("video"); loadUes().catch(showError);
