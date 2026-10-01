const SLICE_PREFS_KEY = "ue_slice_preferences_multi"; // Changed key to reset old invalid cache

let sliceCatalog = [];
let ueIds = [];
let ueProfiles = [];
let ueStates = {};
let latestStatus = { gnb: "stopped", scenario: "stopped", starting: false };
let requestInFlight = false;
let eventSource = null;
let reconnectTimer = null;
let toastTimer = null;
let currentLogFilter = "all";
let allLogs = [];
let autoScroll = true;

const pageTitles = {
  dashboard: "UE dashboard",
  config: "Subscriber configuration",
  logs: "Live logs",
  about: "System overview"
};

const stateLabels = {
  disconnected: "Disconnected",
  attaching: "Attaching",
  random_access: "Random access",
  authenticating: "Authenticating",
  connected: "Connected",
  failed: "Failed",
  error: "Error"
};

const registrationLabels = {
  disconnected: "Disconnected",
  registering: "Registering",
  registered: "Registered"
};

const $ = (id) => document.getElementById(id);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function labelForState(state) {
  return stateLabels[state] || String(state || "unknown").replaceAll("_", " ");
}

function normalizeStatusClass(state) {
  return ["connected", "attaching", "random_access", "authenticating", "failed", "error"]
    .includes(state) ? state : "disconnected";
}

async function readJson(response) {
  try {
    return await response.json();
  } catch (_error) {
    return { success: false, error: `Backend returned HTTP ${response.status}` };
  }
}

function showToast(message, ok = true) {
  const toast = $("toast");
  clearTimeout(toastTimer);
  toast.textContent = message;
  toast.className = `toast ${ok ? "ok" : "err"}`;
  toast.style.display = "block";
  toastTimer = setTimeout(() => { toast.style.display = "none"; }, 3800);
}

function loadSlicePreferences() {
  try {
    const parsed = JSON.parse(localStorage.getItem(SLICE_PREFS_KEY) || "{}");
    return typeof parsed === "object" && parsed ? parsed : {};
  } catch (_error) {
    localStorage.removeItem(SLICE_PREFS_KEY);
    return {};
  }
}

function saveSlicePreferences(selections) {
  try {
    localStorage.setItem(SLICE_PREFS_KEY, JSON.stringify(selections));
  } catch (_error) {
    // Preferences are optional; never block a connection because storage is unavailable.
  }
}

function renderSliceCatalog() {
  const grid = $("slice-grid");
  if (!sliceCatalog.length) {
    grid.innerHTML = '<div class="empty-state">No slice configuration was returned by the backend.</div>';
    return;
  }

  grid.innerHTML = sliceCatalog.map((slice) => `
    <article class="slice-card">
      <div class="slice-card-head">
        <div>
          <h3>${escapeHtml(slice.name)}</h3>
        </div>
        <span class="slice-health ${escapeHtml(slice.status)}">${escapeHtml(slice.status)}</span>
      </div>
      <p>${escapeHtml(slice.description)}</p>
      <div class="slice-meta">
        <div><span>SST / SD</span><strong>${escapeHtml(slice.sst)} / ${escapeHtml(slice.sd)}</strong></div>
        <div><span>DNN</span><strong>${escapeHtml(slice.dnn)}</strong></div>
        <div><span>IPv4 pool</span><strong>${escapeHtml(slice.ipv4_subnet)}</strong></div>
        <div><span>Core namespace</span><strong>${escapeHtml(slice.core_namespace)}</strong></div>
      </div>
    </article>
  `).join("");
}

function selectedSliceIds(ueId) {
  return Array.from(document.querySelectorAll(`[data-slice-for-ue="${ueId}"]:checked`))
    .map((input) => input.value);
}

function desiredSlicesForUe(ueId, previous = []) {
  const preferences = loadSlicePreferences();
  const backendSelected = ueStates[ueId]?.selected_slices?.map((slice) => slice.id) || [];
  let desired = previous.length ? previous : (preferences[String(ueId)] || backendSelected);
  if (!Array.isArray(desired)) desired = desired ? [desired] : [];
  if (!desired.length && sliceCatalog.length) {
    desired = [sliceCatalog[(ueId - 1) % sliceCatalog.length].id];
  }
  return desired;
}

function renderUeControls() {
  const container = $("ue-assignment-list");
  const hadControls = Boolean(container.querySelector(".ue-assignment"));
  const openUeLogs = new Set(Array.from(container.querySelectorAll(".ue-log-details[open]"))
    .map((panel) => Number(panel.dataset.ueLogPanel)));
  const idsBefore = new Set(
    Array.from(container.querySelectorAll(".ue-assignment")).map((row) => Number(row.dataset.ueId))
  );
  const enabledBefore = new Set(enabledUeIds());
  const selectedBefore = Object.fromEntries(ueIds.map((ueId) => [ueId, selectedSliceIds(ueId)]));

  if (!ueIds.length) {
    container.innerHTML = '<div class="empty-state">No local UE profiles. Add one from the Subscriber page.</div>';
    return;
  }

  container.innerHTML = ueIds.map((ueId) => {
    const desired = desiredSlicesForUe(ueId, selectedBefore[ueId] || []);
    const sliceOptions = sliceCatalog.map((slice) => {
      const unavailable = slice.status === "unavailable";
      const checked = desired.includes(slice.id) && !unavailable;
      return `
        <label class="slice-choice ${checked ? "selected" : ""} ${unavailable ? "unavailable" : ""}">
          <input type="checkbox" value="${escapeHtml(slice.id)}" data-slice-for-ue="${ueId}"
            ${checked ? "checked" : ""} ${unavailable ? "disabled" : ""}>
          <span class="slice-choice-mark">✓</span>
          <span><strong>${escapeHtml(slice.name)}</strong><small>SST ${escapeHtml(slice.sst)} · SD ${escapeHtml(slice.sd)}</small></span>
        </label>`;
    }).join("");
    const enabled = hadControls ? (!idsBefore.has(ueId) || enabledBefore.has(ueId)) : true;
    return `
      <article class="ue-assignment" data-ue-id="${ueId}">
        <label class="ue-check"><input id="ue-enabled-${ueId}" type="checkbox" ${enabled ? "checked" : ""}><span>UE ${ueId}</span></label>
        <div id="ue-slices-${ueId}" class="slice-picker" role="group" aria-label="Slices for UE ${ueId}">${sliceOptions}</div>
        <span id="ue-state-${ueId}" class="state-chip disconnected">Disconnected</span>
        <button class="icon-action" type="button" data-disconnect-ue="${ueId}" disabled>Disconnect</button>
        <details class="ue-log-details" data-ue-log-panel="${ueId}" ${openUeLogs.has(ueId) ? "open" : ""}>
          <summary class="ue-log-toggle">
            <span class="ue-log-label"><span class="ue-log-pulse"></span>UE ${ueId} live log</span>
            <span class="ue-log-preview" id="ue-log-preview-${ueId}">Waiting for UE output…</span>
            <span class="ue-log-chevron" aria-hidden="true">⌄</span>
          </summary>
          <div class="ue-log-expanded">
            <div class="ue-log-output" id="ue-log-output-${ueId}" role="log" aria-label="UE ${ueId} live output">
              <div class="ue-log-placeholder">Waiting for UE output…</div>
            </div>
            <button class="ue-log-full-link" type="button" data-open-full-log="${ueId}">Open full logs for UE ${ueId} ↗</button>
          </div>
        </details>
      </article>`;
  }).join("");
  renderUeLogPanels();
}

function renderLogFilters() {
  const filters = $("log-filters");
  if (currentLogFilter !== "all" && !ueIds.includes(Number(currentLogFilter))) currentLogFilter = "all";
  filters.innerHTML = [
    ...ueIds.map((ueId) => `<button class="log-filter ${currentLogFilter === String(ueId) ? "active" : ""}" type="button" data-log-ue="${ueId}">UE ${ueId}</button>`),
    `<button class="log-filter ${currentLogFilter === "all" ? "active" : ""}" type="button" data-log-ue="all">All sources</button>`
  ].join("");
}

function renderUeProfiles() {
  const target = $("ue-profile-list");
  if (!target) return;
  if (!ueProfiles.length) {
    target.innerHTML = '<div class="empty-state">No local UE profiles found.</div>';
    return;
  }
  target.innerHTML = ueProfiles.map((profile) => `
    <div class="profile-row">
      <div><strong>UE ${escapeHtml(profile.ue_id)}</strong><small>Local srsUE profile</small></div>
      <span class="mono">${escapeHtml(profile.single_config)}</span>
      <span class="profile-ready ${profile.multi_ready ? "ready" : "missing"}">${profile.multi_ready ? "Multi-PDU ready" : "Multi-PDU config missing"}</span>
    </div>`).join("");
}

async function loadSlices(showFeedback = false) {
  const refreshButton = $("refresh-slices-btn");
  refreshButton.disabled = true;
  try {
    const response = await fetch("/api/slices", { cache: "no-store" });
    const data = await readJson(response);
    if (!response.ok || !Array.isArray(data.slices)) {
      throw new Error(data.error || "Unable to load slice catalog");
    }
    sliceCatalog = data.slices;
    renderSliceCatalog();
    renderUeControls();
    renderAssignments();
    renderSummary();
    if (showFeedback) showToast("Slice availability refreshed.");
  } catch (error) {
    $("slice-grid").innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
    if (showFeedback) showToast(error.message, false);
  } finally {
    refreshButton.disabled = false;
  }
}

function currentSelections() {
  const selections = {};
  for (const ueId of ueIds) {
    selections[String(ueId)] = selectedSliceIds(ueId);
  }
  return selections;
}

function enabledUeIds() {
  return ueIds.filter((ueId) => $(`ue-enabled-${ueId}`)?.checked);
}

async function persistSelections() {
  const allSelections = currentSelections();
  
  // Validate that at least one slice is selected per enabled UE
  const activeUes = enabledUeIds();
  for (let id of activeUes) {
      if (!allSelections[String(id)] || allSelections[String(id)].length === 0) {
          throw new Error(`Please select at least one slice for UE ${id}`);
      }
  }
  const selections = Object.fromEntries(activeUes.map((ueId) => [String(ueId), allSelections[String(ueId)]]));

  const response = await fetch("/api/ue-slices", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ selections })
  });
  const data = await readJson(response);
  if (!response.ok || !data.success) throw new Error(data.error || "Unable to save slice selections");
  saveSlicePreferences(allSelections);
  return data;
}

async function startLab() {
  if (requestInFlight) return;
  const ueIds = enabledUeIds();
  if (!ueIds.length) {
    showToast("Select at least one UE to start.", false);
    return;
  }
  if (!sliceCatalog.length) {
    showToast("Slice catalog is not available yet.", false);
    return;
  }

  requestInFlight = true;
  updateControls();
  try {
    await persistSelections();
    const response = await fetch("/attach", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ue_ids: ueIds })
    });
    const data = await readJson(response);
    if (!response.ok || !data.success) throw new Error(data.error || "Unable to start lab");
    latestStatus.starting = true;
    showToast(`Starting ${ueIds.length} selected UE${ueIds.length > 1 ? "s" : ""}…`);
    await refreshStatus();
  } catch (error) {
    showToast(error.message || "Unable to start lab.", false);
  } finally {
    requestInFlight = false;
    updateControls();
  }
}

async function stopLab() {
  if (requestInFlight) return;
  requestInFlight = true;
  updateControls();
  try {
    const response = await fetch("/stop-scenario", { method: "POST" });
    const data = await readJson(response);
    if (!response.ok || !data.success) throw new Error(data.error || "Unable to stop lab");
    showToast("Lab stopped safely.");
    await refreshStatus();
  } catch (error) {
    showToast(error.message || "Unable to stop lab.", false);
  } finally {
    requestInFlight = false;
    updateControls();
  }
}

async function disconnectUe(ueId) {
  if (requestInFlight) return;
  requestInFlight = true;
  updateControls();
  try {
    const response = await fetch(`/detach/${ueId}`, { method: "POST" });
    const data = await readJson(response);
    if (!response.ok || !data.success) throw new Error(data.error || `Unable to disconnect UE ${ueId}`);
    showToast(data.message || `UE ${ueId} disconnected.`);
    await refreshStatus();
  } catch (error) {
    showToast(error.message, false);
  } finally {
    requestInFlight = false;
    updateControls();
  }
}

function renderAssignments() {
  for (const ueId of ueIds) {
    const state = ueStates[ueId] || {};
    const stateName = state.state || "disconnected";
    const chip = $(`ue-state-${ueId}`);
    chip.textContent = labelForState(stateName);
    chip.className = `state-chip ${normalizeStatusClass(stateName)}`;

    const active = stateName !== "disconnected" && stateName !== "failed" && stateName !== "error";
    const checkbox = $(`ue-enabled-${ueId}`);
    const disconnectButton = document.querySelector(`[data-disconnect-ue="${ueId}"]`);
    document.querySelectorAll(`[data-slice-for-ue="${ueId}"]`).forEach((input) => {
      input.disabled = requestInFlight || active || input.closest(".slice-choice")?.classList.contains("unavailable");
    });
    checkbox.disabled = requestInFlight || active;
    disconnectButton.disabled = requestInFlight || !active;

    if (active && Array.isArray(state.selected_slices)) {
      const sliceIds = state.selected_slices.map(s => s.id);
      document.querySelectorAll(`[data-slice-for-ue="${ueId}"]`).forEach((input) => {
        input.checked = sliceIds.includes(input.value);
        input.closest(".slice-choice")?.classList.toggle("selected", input.checked);
      });
    }
  }
}

function renderSessions() {
  const body = $("sessions-body");
  const rows = [];

  for (const ueId of ueIds) {
    const state = ueStates[ueId] || {};
    const sessions = Array.isArray(state.pdu_sessions) ? state.pdu_sessions : [];
    const registration = registrationLabels[state.registration_state] || state.registration_state || "Disconnected";
    
    // Determine the slices to display (from backend state, or fallback to what is selected in the UI)
    let selectedSlices = state.selected_slices;
    if (!selectedSlices || !selectedSlices.length) {
      const uiSelectedIds = selectedSliceIds(ueId);
      selectedSlices = sliceCatalog.filter(slice => uiSelectedIds.includes(slice.id));
    }

    // No sessions established yet -> render placeholders for configured slices
    if (!sessions.length) {
      if (!selectedSlices.length) {
        // Fallback row if absolutely nothing is selected
        rows.push(`
          <tr>
            <td><strong>UE ${ueId}</strong></td>
            <td>${escapeHtml(registration)}</td>
            <td colspan="8" class="table-empty">No slices selected</td>
          </tr>`);
      } else {
        selectedSlices.forEach((selected, index) => {
          // If Multi-PDU, only show the UE name / Registration state on the first line
          const showUeContext = index === 0;
          rows.push(`
            <tr>
              <td><strong>${showUeContext ? `UE ${ueId}` : ""}</strong></td>
              <td>${showUeContext ? escapeHtml(registration) : '<span style="color:#666;">&#8627;</span>'}</td>
              <td>—</td>
              <td>${escapeHtml(selected?.name || "—")}</td>
              <td>${escapeHtml(selected?.sst ?? "—")}</td>
              <td class="mono">${escapeHtml(selected?.sd || "—")}</td>
              <td>${escapeHtml(selected?.dnn || "—")}</td>
              <td class="mono">—</td>
              <td class="mono">—</td>
              <td><span class="session-chip inactive">Not established</span></td>
            </tr>`);
        });
      }
      continue;
    }

    // Sessions are active -> Render actual PDU data mapped to slices
    sessions.forEach((session, index) => {
      const sessionState = session.state || "inactive";
      const showUeContext = index === 0;
      rows.push(`
        <tr>
          <td><strong>${showUeContext ? `UE ${ueId}` : ""}</strong></td>
          <td>${showUeContext ? escapeHtml(registration) : '<span style="color:#666;">&#8627;</span>'}</td>
          <td>${escapeHtml(session.session_id ?? "—")}</td>
          <td>${escapeHtml(session.slice_name || "—")}</td>
          <td>${escapeHtml(session.sst ?? "—")}</td>
          <td class="mono">${escapeHtml(session.sd || "—")}</td>
          <td>${escapeHtml(session.dnn || "—")}</td>
          <td class="mono">${escapeHtml(session.ip || "—")}</td>
          <td class="mono">${escapeHtml(session.interface || "—")}</td>
          <td><span class="session-chip ${escapeHtml(sessionState)}">${escapeHtml(sessionState)}</span></td>
        </tr>`);
    });
  }

  body.innerHTML = rows.join("") || '<tr><td colspan="10" class="table-empty">No UE information available</td></tr>';
}

function renderSummary() {
  const availableCount = sliceCatalog.filter((slice) => slice.status !== "unavailable").length;
  $("summary-slices").textContent = sliceCatalog.length ? availableCount : "—";
  $("summary-slice-note").textContent = sliceCatalog.length
    ? `${availableCount} of ${sliceCatalog.length} network slices ready/configured`
    : "Loading catalog";

  const registered = latestStatus.summary?.registered_ues ?? Object.values(ueStates)
    .filter((state) => state.registration_state === "registered").length;
  const sessions = latestStatus.summary?.active_pdu_sessions ?? 0;
  $("summary-ues").textContent = registered;
  $("summary-sessions").textContent = sessions;
  $("summary-ue-note").textContent = `of ${ueIds.length} configured UE${ueIds.length === 1 ? "" : "s"}`;
}

function updateSystemStatus() {
  const gnbRunning = latestStatus.gnb === "running";
  const scenarioRunning = latestStatus.scenario === "running";
  $("gnb-state").textContent = gnbRunning ? "Running" : "Stopped";
  $("scenario-state").textContent = scenarioRunning ? "Running" : "Stopped";
  $("gnb-dot").className = `status-dot ${gnbRunning ? "live" : "idle"}`;
  $("scenario-dot").className = `status-dot ${scenarioRunning ? "live" : "idle"}`;

  const anyUeActive = Object.values(ueStates).some((state) =>
    !["disconnected", "failed", "error"].includes(state.state)
  );
  const labRunning = gnbRunning || scenarioRunning || anyUeActive;
  const badge = $("global-badge");
  if (latestStatus.starting) {
    badge.textContent = "Lab starting";
    badge.className = "badge starting";
  } else if (labRunning) {
    badge.textContent = "Lab running";
    badge.className = "badge running";
  } else {
    badge.textContent = "Lab stopped";
    badge.className = "badge neutral";
  }

  $("start-btn").classList.toggle("hidden", labRunning || latestStatus.starting);
  $("stop-btn").classList.toggle("hidden", !labRunning && !latestStatus.starting);
}

function updateControls() {
  $("start-btn").disabled = requestInFlight;
  $("stop-btn").disabled = requestInFlight;
  $("add-ue-btn").disabled = requestInFlight;
  $("sync-open5gs-btn").disabled = requestInFlight;
  renderAssignments();
  updateSystemStatus();
}

function renderOpen5gsSync(data) {
  const state = data.state || "waiting";
  const badge = $("open5gs-sync-state");
  const labels = {
    ok: "Synchronized",
    syncing: "Synchronizing",
    paused: "Paused",
    error: "Sync error",
    disabled: "Disabled",
    waiting: "Waiting",
  };
  badge.textContent = labels[state] || state;
  badge.className = `badge ${state === "ok" ? "running" : state === "syncing" ? "starting" : state === "error" ? "error" : "neutral"}`;
  $("open5gs-sync-message").textContent = data.message || "No synchronization status available";
  $("open5gs-core-count").textContent = data.core_subscribers ?? "—";
  $("open5gs-local-count").textContent = data.local_profiles ?? ueProfiles.length;
  $("open5gs-last-sync").textContent = data.last_sync
    ? `Last sync ${new Date(data.last_sync).toLocaleString()}`
    : `Automatic check every ${data.interval_seconds || 10} seconds`;
}

async function loadOpen5gsSyncStatus() {
  try {
    const response = await fetch("/api/open5gs-sync", { cache: "no-store" });
    const data = await readJson(response);
    renderOpen5gsSync(data);
    if (Number.isInteger(data.local_profiles) && data.local_profiles !== ueProfiles.length) {
      await Promise.all([loadUeProfiles(), refreshStatus()]);
    }
  } catch (error) {
    renderOpen5gsSync({ state: "error", message: error.message });
  }
}

async function syncOpen5gsNow() {
  if (requestInFlight) return;
  requestInFlight = true;
  updateControls();
  try {
    const response = await fetch("/api/open5gs-sync", { method: "POST" });
    const data = await readJson(response);
    renderOpen5gsSync(data);
    if (!response.ok) throw new Error(data.message || data.error || "Open5GS sync failed");
    showToast(data.message || "Open5GS subscribers synchronized");
    await Promise.all([loadUeProfiles(), refreshStatus()]);
  } catch (error) {
    showToast(error.message, false);
  } finally {
    requestInFlight = false;
    updateControls();
  }
}

async function loadUeProfiles() {
  try {
    const response = await fetch("/api/ues", { cache: "no-store" });
    const data = await readJson(response);
    if (!response.ok || !Array.isArray(data.profiles)) throw new Error(data.error || "Unable to load UE profiles");
    ueProfiles = data.profiles;
    renderUeProfiles();
    $("ue-limit-note").textContent = `${ueProfiles.length} of ${data.limit} local UE profiles configured`;
  } catch (error) {
    $("ue-profile-list").innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
  }
}

async function refreshStatus() {
  try {
    const response = await fetch("/status", { cache: "no-store" });
    if (!response.ok) return;
    const data = await response.json();
    latestStatus = data;
    if (data.ues) {
      const nextIds = Array.isArray(data.configured_ue_ids)
        ? data.configured_ue_ids.map(Number)
        : Object.keys(data.ues).map(Number).sort((a, b) => a - b);
      const controlsChanged = nextIds.join(",") !== ueIds.join(",");
      ueIds = nextIds;
      ueStates = Object.fromEntries(ueIds.map((ueId) => [ueId, data.ues[String(ueId)] || ueStates[ueId] || {}]));
      if (controlsChanged) {
        renderUeControls();
        renderLogFilters();
      }
    }
    renderAssignments();
    renderSessions();
    renderSummary();
    updateSystemStatus();
    $("last-updated").textContent = `Updated ${new Date().toLocaleTimeString()}`;
  } catch (_error) {
    $("global-badge").textContent = "Backend offline";
    $("global-badge").className = "badge error";
  }
}

function validateProfile(profile) {
  if (!/^\d{5,15}$/.test(profile.imsi)) return "IMSI must contain 5 to 15 digits.";
  if (!/^[0-9a-fA-F]{32}$/.test(profile.k)) return "K must contain exactly 32 hexadecimal characters.";
  if (!["op", "opc"].includes(profile.operator_key_type)) return "Select OPc or OP.";
  if (!/^[0-9a-fA-F]{32}$/.test(profile.operator_key)) {
    return `${profile.operator_key_type.toUpperCase()} must contain exactly 32 hexadecimal characters.`;
  }
  if (profile.dnn !== "srsapn") return "DNN/APN must be srsapn on the current testbed.";
  if (profile.pdu_type !== "ipv4") return "Only IPv4 PDU sessions are enabled.";
  return null;
}

function updateOperatorKeyLabel() {
  const keyType = $("profile-operator-key-type").value;
  $("profile-operator-key-label").textContent = `Operator key (${keyType === "op" ? "OP" : "OPc"})`;
}

async function addUeProfile() {
  if (requestInFlight) return;
  const profile = {
    imsi: $("profile-imsi").value.trim(),
    k: $("profile-k").value.trim(),
    operator_key_type: $("profile-operator-key-type").value,
    operator_key: $("profile-operator-key").value.trim(),
    dnn: $("profile-dnn").value,
    pdu_type: $("profile-pdu-type").value,
  };
  const error = validateProfile(profile);
  if (error) {
    showToast(error, false);
    return;
  }

  requestInFlight = true;
  updateControls();
  try {
    const response = await fetch("/api/ues", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(profile)
    });
    const data = await readJson(response);
    if (!response.ok || !data.success) throw new Error(data.error || "Unable to add local UE profile");
    $("profile-imsi").value = "";
    $("profile-k").value = "";
    $("profile-operator-key").value = "";
    showToast(data.message || `UE ${data.ue_id} created.`);
    await Promise.all([refreshStatus(), loadUeProfiles()]);
  } catch (saveError) {
    showToast(saveError.message, false);
  } finally {
    requestInFlight = false;
    updateControls();
  }
}

function classForLog(line) {
  if (line.includes("PDU Session Establishment successful")) return "pdu";
  if (/\[OK\]|RRC Connected|Complete/i.test(line)) return "ok";
  if (/\[ERROR\]|Failed|Error/i.test(line)) return "error";
  if (/\[WARN\]|Warning/i.test(line)) return "warn";
  if (/\[INFO\]|\[SYS\]/i.test(line)) return "info";
  return "";
}

function ueIdFromLog(line) {
  const match = /^\[UE-(\d+)\](?:\s|$)/.exec(line);
  return match ? Number(match[1]) : null;
}

function appendUeLogLine(ueId, line) {
  const preview = $(`ue-log-preview-${ueId}`);
  const output = $(`ue-log-output-${ueId}`);
  if (!preview || !output) return;
  preview.textContent = line.replace(/^\[UE-\d+\]\s*/, "") || line;
  output.querySelector(".ue-log-placeholder")?.remove();
  const nearBottom = output.scrollHeight - output.scrollTop - output.clientHeight < 32;
  const entry = document.createElement("div");
  entry.className = `ue-log-line ${classForLog(line)}`;
  entry.textContent = line;
  output.appendChild(entry);
  while (output.children.length > 60) output.firstElementChild.remove();
  if (nearBottom) output.scrollTop = output.scrollHeight;
}

function renderUeLogPanels() {
  for (const ueId of ueIds) {
    const output = $(`ue-log-output-${ueId}`);
    const preview = $(`ue-log-preview-${ueId}`);
    if (!output || !preview) continue;
    output.innerHTML = '<div class="ue-log-placeholder">Waiting for UE output…</div>';
    preview.textContent = "Waiting for UE output…";
    allLogs.filter((line) => ueIdFromLog(line) === ueId).slice(-60)
      .forEach((line) => appendUeLogLine(ueId, line));
  }
}

function logMatchesFilter(line) {
  if (currentLogFilter === "all") return true;
  const target = `[UE-${currentLogFilter}]`;
  return line.startsWith(target) || !/^\[UE-\d+\]/.test(line);
}

function appendRenderedLog(line) {
  const element = document.createElement("div");
  element.className = `log-line ${classForLog(line)}`;
  element.textContent = line;
  $("terminal").appendChild(element);
  if (autoScroll) $("terminal").scrollTop = $("terminal").scrollHeight;
}

function renderLogs() {
  const terminal = $("terminal");
  terminal.innerHTML = "";
  const visible = allLogs.filter(logMatchesFilter);
  if (!visible.length) {
    const placeholder = document.createElement("div");
    placeholder.className = "log-line info";
    placeholder.textContent = "Waiting for matching logs…";
    terminal.appendChild(placeholder);
    return;
  }
  visible.forEach(appendRenderedLog);
}

function appendLog(line) {
  if (!line || line === "[PING]") return;
  allLogs.push(line);
  if (allLogs.length > 1000) allLogs.shift();
  const ueId = ueIdFromLog(line);
  if (ueId !== null) appendUeLogLine(ueId, line);
  if (logMatchesFilter(line)) {
    const placeholder = $("terminal").querySelector(".log-line.info:only-child");
    if (placeholder?.textContent.startsWith("Waiting")) placeholder.remove();
    appendRenderedLog(line);
  }
}

function startLogStream() {
  if (eventSource) return;
  clearTimeout(reconnectTimer);
  $("stream-state").textContent = "Connecting";
  $("stream-state").className = "stream-state";
  eventSource = new EventSource("/logs/stream");
  eventSource.onopen = () => {
    $("stream-state").textContent = "Live";
    $("stream-state").className = "stream-state live";
  };
  eventSource.onmessage = (event) => appendLog(event.data);
  eventSource.onerror = () => {
    eventSource.close();
    eventSource = null;
    $("stream-state").textContent = "Reconnecting";
    $("stream-state").className = "stream-state";
    reconnectTimer = setTimeout(startLogStream, 2000);
  };
}

function showView(viewName) {
  document.querySelectorAll(".view").forEach((view) => {
    view.classList.toggle("active", view.id === `view-${viewName}`);
  });
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === viewName);
  });
  $("page-title").textContent = pageTitles[viewName] || "5G lab";
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function bindEvents() {
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.view));
  });
  $("ue-assignment-list").addEventListener("click", (event) => {
    const fullLog = event.target.closest("[data-open-full-log]");
    if (fullLog) {
      currentLogFilter = fullLog.dataset.openFullLog;
      renderLogFilters();
      $("log-title").textContent = `Live terminal · UE ${currentLogFilter}`;
      renderLogs();
      showView("logs");
      return;
    }
    const button = event.target.closest("[data-disconnect-ue]");
    if (button) disconnectUe(Number(button.dataset.disconnectUe));
  });
  $("ue-assignment-list").addEventListener("toggle", (event) => {
    const panel = event.target.closest(".ue-log-details");
    if (!panel?.open) return;
    const output = $(`ue-log-output-${panel.dataset.ueLogPanel}`);
    if (output) requestAnimationFrame(() => { output.scrollTop = output.scrollHeight; });
  }, true);
  $("ue-assignment-list").addEventListener("change", (event) => {
    const input = event.target.closest("[data-slice-for-ue]");
    if (!input) return;
    input.closest(".slice-choice")?.classList.toggle("selected", input.checked);
    const ueId = Number(input.dataset.sliceForUe);
    const selectedIds = selectedSliceIds(ueId);
    ueStates[ueId] = ueStates[ueId] || {};
    ueStates[ueId].selected_slices = sliceCatalog.filter((slice) => selectedIds.includes(slice.id));
    saveSlicePreferences(currentSelections());
    renderSessions();
  });
  $("log-filters").addEventListener("click", (event) => {
    const button = event.target.closest(".log-filter");
    if (button) {
      currentLogFilter = button.dataset.logUe;
      document.querySelectorAll(".log-filter").forEach((item) => item.classList.toggle("active", item === button));
      $("log-title").textContent = currentLogFilter === "all"
        ? "Live terminal · All sources"
        : `Live terminal · UE ${currentLogFilter}`;
      renderLogs();
    }
  });
  $("start-btn").addEventListener("click", startLab);
  $("stop-btn").addEventListener("click", stopLab);
  $("add-ue-btn").addEventListener("click", addUeProfile);
  $("sync-open5gs-btn").addEventListener("click", syncOpen5gsNow);
  $("profile-operator-key-type").addEventListener("change", updateOperatorKeyLabel);
  $("refresh-slices-btn").addEventListener("click", () => loadSlices(true));
  $("clear-logs-btn").addEventListener("click", () => { allLogs = []; renderLogs(); renderUeLogPanels(); });
  $("terminal").addEventListener("scroll", () => {
    const terminal = $("terminal");
    autoScroll = terminal.scrollHeight - terminal.scrollTop - terminal.clientHeight < 24;
  });
  window.addEventListener("beforeunload", () => {
    clearTimeout(reconnectTimer);
    eventSource?.close();
  });
}

async function initialize() {
  bindEvents();
  renderLogs();
  startLogStream();
  await refreshStatus();
  await Promise.all([loadSlices(), loadUeProfiles(), loadOpen5gsSyncStatus()]);
  renderUeControls();
  renderLogFilters();
  renderAssignments();
  renderSessions();
  renderSummary();
  updateControls();
  setInterval(refreshStatus, 1000);
  setInterval(loadOpen5gsSyncStatus, 5000);
}

initialize();
