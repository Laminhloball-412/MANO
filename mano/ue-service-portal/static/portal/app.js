(function () {
  "use strict";

  const byId = function (id) { return document.getElementById(id); };
  const toast = byId("toast");

  function showToast(message, isError) {
    if (!toast) return;
    toast.textContent = message;
    toast.className = "toast show" + (isError ? " error" : "");
    window.setTimeout(function () { toast.className = "toast"; }, 4200);
  }

  async function api(url, options) {
    const response = await fetch(url, options || {});
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || "Request failed");
    return data;
  }

  const modal = byId("add-ue-modal");
  const openButton = byId("open-add-ue");
  const closeButtons = [byId("close-add-ue"), byId("cancel-add-ue")].filter(Boolean);
  if (modal && openButton) {
    openButton.addEventListener("click", function () { modal.hidden = false; });
    closeButtons.forEach(function (button) { button.addEventListener("click", function () { modal.hidden = true; }); });
    modal.addEventListener("mousedown", function (event) { if (event.target === modal) modal.hidden = true; });
  }

  const allowedInputs = Array.prototype.slice.call(document.querySelectorAll('input[name="allowed"]'));
  const requested = byId("draft-requested");
  function refreshRequestedOptions() {
    if (!requested) return;
    const selected = requested.value;
    const checked = allowedInputs.filter(function (input) { return input.checked; });
    if (!checked.length && allowedInputs.length) { allowedInputs[0].checked = true; checked.push(allowedInputs[0]); }
    requested.innerHTML = "";
    checked.forEach(function (input) {
      const option = document.createElement("option");
      option.value = input.value;
      option.textContent = input.value.toUpperCase() + " · SST " + ({ embb: 1, urllc: 2, mmtc: 3 }[input.value]);
      option.selected = input.value === selected;
      requested.appendChild(option);
    });
    allowedInputs.forEach(function (input) { input.closest(".slice-option").classList.toggle("selected", input.checked); });
  }
  allowedInputs.forEach(function (input) { input.addEventListener("change", refreshRequestedOptions); });

  const addForm = byId("add-ue-form");
  if (addForm) {
    addForm.addEventListener("submit", async function (event) {
      event.preventDefault();
      const allowed = allowedInputs.filter(function (input) { return input.checked; }).map(function (input) { return input.value; });
      const submit = addForm.querySelector('button[type="submit"]');
      submit.disabled = true;
      submit.textContent = "Saving…";
      try {
        const data = await api("/api/ue-drafts/", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ dnn: byId("draft-dnn").value, allowed_slices: allowed, requested_slice: requested.value }),
        });
        showToast(data.message, false);
        window.setTimeout(function () { window.location.reload(); }, 550);
      } catch (error) {
        showToast(error.message, true);
        submit.disabled = false;
        submit.textContent = "Save UI draft";
      }
    });
  }

  document.querySelectorAll(".delete-ue-button:not([disabled])").forEach(function (button) {
    button.addEventListener("click", async function () {
      if (!window.confirm("Delete this portal UE draft? Subscriber and lab config will remain unchanged.")) return;
      try {
        const data = await api("/api/ue-drafts/" + button.dataset.ueId + "/delete/", { method: "POST" });
        showToast(data.message, false);
        window.setTimeout(function () { window.location.reload(); }, 500);
      } catch (error) { showToast(error.message, true); }
    });
  });

  const connectButton = byId("connect-radio");
  const disconnectButton = byId("disconnect-radio");
  const countSelect = byId("radio-ue-count");
  let radioBusy = false;

  function updateRadioDisplay(data) {
    const state = byId("radio-state");
    const detail = byId("radio-detail");
    const light = byId("radio-light");
    if (state) state.textContent = data.status;
    if (detail) detail.textContent = data.status === "DISCONNECTED" ? "No gNB or UE process is managed by the portal" : (data.mode + " · " + (data.ues || []).length + " UE process(es)");
    if (light) light.className = "radio-light state-" + data.status.toLowerCase();
    (data.ues || []).forEach(function (ue) {
      const id = String(ue.id);
      const badge = byId("ue-status-" + id);
      if (badge) { badge.textContent = ue.status + (ue.ip ? " · " + ue.ip : ""); badge.className = "status status-" + ue.status.toLowerCase(); }
    });
    if (connectButton) connectButton.disabled = radioBusy || data.active_processes > 0;
    if (disconnectButton) disconnectButton.disabled = radioBusy || data.active_processes === 0;
    const labState = byId("lab-runtime-state");
    if (labState) labState.textContent = data.status;
    return data;
  }

  async function pollRadio() {
    try { const response = await api("/api/radio/status/"); updateRadioDisplay(response.data); }
    catch (error) { const state = byId("radio-state"); if (state) state.textContent = "UNAVAILABLE"; }
  }

  if (connectButton) {
    connectButton.addEventListener("click", async function () {
      radioBusy = true; connectButton.disabled = true; connectButton.textContent = "Starting radio…";
      try {
        const response = await api("/api/radio/connect/", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ count: Number(countSelect.value) }) });
        updateRadioDisplay(response.data); showToast("gNB and UE startup has begun. Status will update automatically.", false);
      } catch (error) { showToast(error.message, true); }
      finally { radioBusy = false; connectButton.textContent = "Connect UE(s)"; pollRadio(); }
    });
  }
  if (disconnectButton) {
    disconnectButton.addEventListener("click", async function () {
      radioBusy = true; disconnectButton.disabled = true; disconnectButton.textContent = "Disconnecting…";
      try { const response = await api("/api/radio/disconnect/", { method: "POST" }); updateRadioDisplay(response.data); showToast("Portal-managed radio processes stopped.", false); }
      catch (error) { showToast(error.message, true); }
      finally { radioBusy = false; disconnectButton.textContent = "Disconnect"; pollRadio(); }
    });
  }
  if (connectButton || byId("lab-runtime-state")) { pollRadio(); window.setInterval(pollRadio, 2500); }

  const targetUe = byId("target-ue");
  const duration = byId("workload-duration");
  document.querySelectorAll(".workload-start").forEach(function (button) {
    button.addEventListener("click", async function () {
      button.disabled = true; const original = button.textContent; button.textContent = "Starting real traffic…";
      try {
        await api("/api/workloads/start/", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ue: targetUe.value, workload: button.dataset.workload, duration: Number(duration.value) }),
        });
        window.location.href = "/measurements/?ue=" + encodeURIComponent(targetUe.value);
      } catch (error) { showToast(error.message, true); button.disabled = false; button.textContent = original; }
    });
  });
  const stopWorkload = byId("stop-workload");
  if (stopWorkload) {
    stopWorkload.addEventListener("click", async function () {
      try { await api("/api/workloads/stop/", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ue: targetUe.value }) }); showToast("Workload stopped.", false); }
      catch (error) { showToast(error.message, true); }
    });
  }

  const measurementUe = byId("measurement-ue");
  function formatRate(value) {
    if (value == null) return "—";
    if (value >= 1000000) return (value / 1000000).toFixed(2) + " Mbps";
    if (value >= 1000) return (value / 1000).toFixed(1) + " Kbps";
    return value.toFixed(0) + " bps";
  }
  function fixed(value, suffix, digits) { return value == null ? "—" : Number(value).toFixed(digits == null ? 2 : digits) + suffix; }
  function setText(id, value) { const element = byId(id); if (element) element.textContent = value; }

  async function pollTelemetry() {
    if (!measurementUe) return;
    try {
      const data = await api("/api/telemetry/?ue=" + encodeURIComponent(measurementUe.value));
      const v = data.values;
      setText("metric-throughput", formatRate(v.throughput_bps));
      setText("metric-rtt", fixed(v.rtt_ms, " ms"));
      setText("metric-jitter", fixed(v.jitter_ms, " ms"));
      setText("metric-loss", v.packet_loss_ratio == null ? "—" : (v.packet_loss_ratio * 100).toFixed(2) + "%");
      setText("metric-packet-size", fixed(v.packet_size_bytes, " B", 0));
      setText("metric-message-rate", fixed(v.message_rate, " msg/s"));
      setText("live-workload", (data.workload || "Idle") + " · " + data.workload_status);
      setText("live-state", data.prometheus_ready ? "LIVE" : "Waiting for scrape");
      const dot = byId("live-dot"); if (dot) dot.className = data.prometheus_ready ? "online" : "";
      const vector = byId("live-feature-vector");
      if (vector) vector.textContent = JSON.stringify({ ue_id: data.ue, source: data.source, workload: data.workload, throughput_bps: v.throughput_bps, rtt_ms: v.rtt_ms, jitter_ms: v.jitter_ms, loss_ratio: v.packet_loss_ratio, message_rate: v.message_rate, updated_at: data.updated_at }, null, 2);
    } catch (error) { setText("live-state", "Prometheus unavailable"); }
  }
  if (measurementUe) { measurementUe.addEventListener("change", pollTelemetry); pollTelemetry(); window.setInterval(pollTelemetry, 2000); }
})();
