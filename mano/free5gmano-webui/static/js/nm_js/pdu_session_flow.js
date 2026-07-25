let selectedSupi = "";
let pduRefreshTimer = null;

function escapePdu(value) {
  const node = document.createElement("div");
  node.textContent = value === null || value === undefined ? "—" : String(value);
  return node.innerHTML;
}

function teid(value) {
  if (value === null || value === undefined) return "—";
  return "0x" + Number(value).toString(16).toUpperCase() + " (" + value + ")";
}

function sessionBadge(session) {
  if (session.online) return '<span class="badge badge-success">LIVE</span>';
  if (session.state === "inactive") return '<span class="badge badge-secondary">INACTIVE</span>';
  return '<span class="badge badge-warning">' + escapePdu(session.state.toUpperCase()) + '</span>';
}

function selectPduSession(supi) {
  selectedSupi = supi;
  loadPduFlow(true);
}

function renderSessionList(sessions) {
  const body = document.getElementById("pdu-session-list");
  document.getElementById("pdu-session-count").textContent = sessions.length;
  if (!sessions.length) {
    body.innerHTML = '<tr><td colspan="3" class="text-center text-muted py-4">No PDU sessions reported</td></tr>';
    return;
  }
  body.innerHTML = sessions.map(function (session) {
    const selected = session.supi === selectedSupi ? " is-selected" : "";
    return '<tr class="pdu-session-row' + selected + '" data-supi="' +
      escapePdu(session.supi) + '"><td><div class="font-weight-bold">' +
      escapePdu(session.ue_name) + '</div><div class="small text-muted">' +
      escapePdu(session.ipv4) + '</div></td><td>SST ' + escapePdu(session.sst) +
      '<div class="small text-muted">SD ' + escapePdu(session.sd) + '</div></td><td>' +
      sessionBadge(session) + '</td></tr>';
  }).join("");
  body.querySelectorAll(".pdu-session-row").forEach(function (row) {
    row.addEventListener("click", function () {
      selectPduSession(row.getAttribute("data-supi"));
    });
  });
}

function put(id, value) {
  document.getElementById(id).textContent =
    value === null || value === undefined || value === "" ? "—" : value;
}

function renderSelected(session) {
  if (!session) {
    put("pdu-selected-name", "No UE session available");
    put("pdu-selected-supi", "Start a UE to create a PDU session");
    put("pdu-selected-state", "NO SESSION");
    ["kpi-ip", "kpi-psi", "kpi-dnn", "kpi-snssai", "kpi-qos",
      "kpi-ul-teid", "kpi-dl-teid", "kpi-pdr", "kpi-gnb", "kpi-upf"
    ].forEach(function (id) { put(id, "—"); });
    return;
  }
  selectedSupi = session.supi;
  put("pdu-selected-name", session.ue_name);
  put("pdu-selected-supi", session.supi + " · " + session.namespace);
  document.getElementById("pdu-selected-state").innerHTML =
    '<span class="pdu-dot ' + (session.online ? "pdu-dot-live" : "pdu-dot-idle") +
    '"></span>' + (session.online ? "LIVE USER PLANE" : "IDLE / OFFLINE");
  put("kpi-ip", session.ipv4);
  put("kpi-psi", session.psi);
  put("kpi-dnn", session.dnn);
  put("kpi-snssai", "SST " + session.sst + " / SD " + session.sd);
  const qos = (session.qos_flows || []).map(function (flow) {
    return flow.qfi + " / " + flow["5qi"];
  }).join(", ");
  put("kpi-qos", qos || "—");
  // Uplink packets target the UPF TEID; downlink targets the gNB TEID.
  put("kpi-ul-teid", teid(session.upf_teid));
  put("kpi-dl-teid", teid(session.gnb_teid));
  put("kpi-pdr", session.pdr_id);
  put("kpi-gnb", "gNB " + session.gnb_addr);
  put("kpi-upf", "UPF " + session.upf_addr);
}

function renderTimeline(events) {
  document.getElementById("pdu-timeline").innerHTML = events.map(function (event) {
    return '<div class="pdu-plane">' + escapePdu(event.plane) + '</div>' +
      '<div class="pdu-event ' + escapePdu(event.status) + '">' +
      '<div class="small text-muted">' + escapePdu(event.source) +
      ' <span class="pdu-arrow mx-2">→</span> ' + escapePdu(event.target) + '</div>' +
      '<div class="font-weight-bold text-gray-800">' + escapePdu(event.message) + '</div>' +
      '<div class="small text-muted">' + escapePdu(event.detail) + '</div></div>';
  }).join("");
}

async function loadPduFlow(manual) {
  const alert = document.getElementById("pdu-alert");
  if (manual) {
    alert.className = "alert alert-info";
    alert.textContent = "Refreshing Open5GS session context...";
    alert.style.display = "block";
  }
  try {
    const query = selectedSupi ? "?supi=" + encodeURIComponent(selectedSupi) : "";
    const response = await axios.get("/api/pdu-session-flow/status/" + query);
    const data = response.data;
    if (!data.ok) throw new Error(data.error || "PDU flow API failed");
    if (!selectedSupi && data.selected) selectedSupi = data.selected.supi;
    renderSessionList(data.sessions || []);
    renderSelected(data.selected);
    renderTimeline(data.timeline || []);
    document.getElementById("capture-note").textContent = data.capture.message;
    put("pdu-last-update", "Updated " + new Date().toLocaleTimeString());
    if (data.errors && data.errors.length) {
      alert.className = "alert alert-warning";
      alert.textContent = "Some telemetry sources are unavailable: " + data.errors.join("; ");
      alert.style.display = "block";
    } else if (data.selected && !data.selected.online) {
      alert.className = "alert alert-secondary";
      alert.textContent = data.selected.ue_name +
        " has a retained PDU context but is currently idle/offline.";
      alert.style.display = "block";
    } else {
      alert.style.display = "none";
    }
  } catch (error) {
    alert.className = "alert alert-danger";
    alert.textContent = error.response && error.response.data ?
      (error.response.data.error || "Unable to load PDU flow") : error.message;
    alert.style.display = "block";
  }
  clearTimeout(pduRefreshTimer);
  pduRefreshTimer = setTimeout(function () { loadPduFlow(false); }, 5000);
}

document.addEventListener("DOMContentLoaded", function () {
  loadPduFlow(false);
});
