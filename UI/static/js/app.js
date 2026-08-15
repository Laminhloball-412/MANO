let evtSource = null;
let reconnectTimer = null;
let toastTimer = null;
let autoScroll = true;
let streamWanted = false;
let requestInFlight = false;

// ─── STATE QUẢN LÝ MULTI-UE ───────────────────────────
let currentUeId = 1; // Tab đang mở mặc định là 1
let ueStates = {
  1: { state: "disconnected", ip: null },
  2: { state: "disconnected", ip: null },
  3: { state: "disconnected", ip: null }
};
let ueLogs = { 1: [], 2: [], 3: [] }; // 3 hòm thư chứa log riêng biệt
// ──────────────────────────────────────────────────────

const CONFIG_STORAGE_KEY = "ue_manager_config_v1";

const terminal = document.getElementById("terminal");
const logTitle = document.getElementById("log-title");
const badge = document.getElementById("badge");
const dot = document.getElementById("dot");
const statState = document.getElementById("stat-state");
const statIp = document.getElementById("stat-ip");
const attachButton = document.getElementById("attach-btn");
const detachButton = document.getElementById("detach-btn");

const TRANSITION_STATES = ["attaching", "random_access", "authenticating"];

const STEPS = {
  disconnected: [],
  attaching: ["step-attach"],
  random_access: ["step-attach", "step-auth"],
  authenticating: ["step-attach", "step-auth"],
  connected: ["step-attach", "step-auth", "step-pdu", "step-done"],
  failed: [],
  error: []
};

const STATE_LABELS = {
  disconnected: "Disconnected",
  attaching: "Attaching",
  random_access: "Random access",
  authenticating: "Authenticating",
  connected: "Connected",
  failed: "Failed",
  error: "Error"
};

function stateLabel(state) {
  return STATE_LABELS[state] || state.replaceAll("_", " ");
}

// ─── HÀM CHUYỂN ĐỔI TAB UE ────────────────────────────
function switchHomeUE(id) {
  currentUeId = id;
  syncTabs();
  renderUEState();
}

function switchLogUE(id) {
  currentUeId = id;
  syncTabs();
  renderUELogs();
}

function syncTabs() {
  // Đồng bộ màu nút bấm ở cả 2 màn hình Home và Logs
  [1, 2, 3].forEach(i => {
    document.getElementById(`home-tab-${i}`).classList.toggle('active', i === currentUeId);
    document.getElementById(`log-tab-${i}`).classList.toggle('active', i === currentUeId);
  });
  // Đổi tiêu đề terminal
  if (logTitle) {
    logTitle.textContent = `Live terminal (UE ${currentUeId})`;
  }
}

// ─── HÀM RENDER DỮ LIỆU LÊN MÀN HÌNH ──────────────────
function renderUEState() {
  const data = ueStates[currentUeId];
  const state = data.state || "disconnected";
  const ip = data.ip || "—";
  const label = stateLabel(state);

  statState.textContent = label.toUpperCase();
  statState.className = "stat-value";
  if (state === "connected") statState.classList.add("green");
  
  statIp.textContent = ip;
  badge.textContent = `UE ${currentUeId}: ${label.toUpperCase()}`;
  badge.className = "badge " + state;

  updateSteps(state);
  updateGlobalStatus();
}

function updateSteps(state) {
  const stepIds = ["step-attach", "step-auth", "step-pdu", "step-done"];
  stepIds.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.classList.remove("active", "done");
  });

  const activeSteps = STEPS[state] || [];
  activeSteps.forEach((id, index) => {
    const el = document.getElementById(id);
    if (el) {
      const isCurrentStep = index === activeSteps.length - 1 && state !== "connected";
      el.classList.add(isCurrentStep ? "active" : "done");
    }
  });
}

function updateGlobalStatus() {
  // Nút tổng sẽ thay đổi dựa vào việc có BẤT KỲ UE nào đang chạy hay không
  const isAnyRunning = Object.values(ueStates).some(ue => ue.state !== 'disconnected');
  
  attachButton.style.display = isAnyRunning ? "none" : "inline-flex";
  detachButton.style.display = isAnyRunning ? "inline-flex" : "none";
  
  attachButton.disabled = requestInFlight;
  detachButton.disabled = requestInFlight;

  dot.className = isAnyRunning ? "dot live" : "dot idle";

  if (isAnyRunning && !streamWanted) {
    streamWanted = true;
    startSSE();
  } else if (!isAnyRunning && streamWanted) {
    streamWanted = false;
    stopSSE();
  }
}

// ─── XỬ LÝ LOG ĐA LUỒNG ───────────────────────────────
function classForLine(line) {
  if (line.includes("PDU Session Establishment successful")) return "pdu";
  if (line.includes("[OK]") || line.includes("RRC Connected") || line.includes("Complete")) return "ok";
  if (line.includes("[INFO]")) return "info";
  if (line.includes("[WARN]") || line.includes("Warning")) return "warn";
  if (line.includes("[ERROR]") || line.includes("Failed") || line.includes("Error")) return "error";
  return "";
}

function appendLog(line) {
  if (!line || line === "[PING]") return;

  let targetUe = null;
  // Phân loại log dựa theo nhãn từ backend
  if (line.startsWith("[UE-1]")) targetUe = 1;
  else if (line.startsWith("[UE-2]")) targetUe = 2;
  else if (line.startsWith("[UE-3]")) targetUe = 3;

  if (targetUe === null) {
    // Nếu là log hệ thống (gNB, SCENARIO), nhét vào cả 3 hòm thư để máy nào cũng đọc được
    [1, 2, 3].forEach(i => {
      ueLogs[i].push(line);
      if (ueLogs[i].length > 500) ueLogs[i].shift();
    });
    renderSingleLine(line); // Cập nhật luôn màn hình
  } else {
    // Log riêng của UE nào thì nhét vào túi UE đó
    ueLogs[targetUe].push(line);
    if (ueLogs[targetUe].length > 500) ueLogs[targetUe].shift();
    
    // Nếu màn hình đang mở đúng Tab của UE đó thì mới in ra
    if (targetUe === currentUeId) {
      renderSingleLine(line);
    }
  }
}

function renderSingleLine(line) {
  const div = document.createElement("div");
  div.className = "log-line " + classForLine(line);
  div.textContent = line;
  terminal.appendChild(div);

  while (terminal.childElementCount > 500) {
    terminal.firstElementChild.remove();
  }
  if (autoScroll) {
    terminal.scrollTop = terminal.scrollHeight;
  }
}

function renderUELogs() {
  terminal.innerHTML = "";
  const logs = ueLogs[currentUeId];
  
  if (logs.length === 0) {
    const placeholder = document.createElement("div");
    placeholder.className = "log-line info";
    placeholder.textContent = `Waiting for UE ${currentUeId} logs...`;
    terminal.appendChild(placeholder);
  } else {
    logs.forEach(line => renderSingleLine(line));
    if (autoScroll) terminal.scrollTop = terminal.scrollHeight;
  }
}

function clearLogs() {
  ueLogs[1] = []; ueLogs[2] = []; ueLogs[3] = [];
  renderUELogs();
}

terminal.addEventListener("scroll", () => {
  const dist = terminal.scrollHeight - terminal.scrollTop - terminal.clientHeight;
  autoScroll = dist < 24;
});

// ─── KẾT NỐI API & BACKEND ────────────────────────────
function stopSSE() {
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  if (evtSource) { evtSource.close(); evtSource = null; }
}

function startSSE() {
  if (!streamWanted || evtSource) return;
  evtSource = new EventSource("/logs/stream");
  evtSource.onmessage = (event) => appendLog(event.data);
  evtSource.onerror = () => {
    if (evtSource) { evtSource.close(); evtSource = null; }
    if (streamWanted) reconnectTimer = setTimeout(startSSE, 2000);
  };
}

async function refreshStatus() {
  try {
    const response = await fetch("/status", { cache: "no-store" });
    if (!response.ok) return;
    const data = await response.json();
    
    // Đổ data từ backend vào biến bộ nhớ
    if (data.ues) {
      ueStates[1] = data.ues["1"] || ueStates[1];
      ueStates[2] = data.ues["2"] || ueStates[2];
      ueStates[3] = data.ues["3"] || ueStates[3];
    }
    renderUEState(); // Render lại màn hình hiện tại
  } catch (error) {}
}

async function attachUE() {
  if (requestInFlight) return;
  requestInFlight = true;
  
  clearLogs();
  [1, 2, 3].forEach(i => ueStates[i] = { state: "attaching", ip: null });
  renderUEState();

  streamWanted = true;
  startSSE();

  try {
    // Lấy config hiện tại và báo backend khởi động 3 UE
    const config = collectConfig();
    config.num_ues = 3; 

    const response = await fetch("/attach", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config)
    });

    const data = await readJson(response);
    if (!data.success) throw new Error(data.error);
    showToast("Starting Multi-UE Scenario...", true);
  } catch (error) {
    streamWanted = false; stopSSE();
    [1, 2, 3].forEach(i => ueStates[i] = { state: "error", ip: null });
    showToast(error.message || "Unable to contact backend.", false);
  } finally {
    requestInFlight = false;
    refreshStatus();
  }
}

async function detachUE() {
  if (requestInFlight) return;
  requestInFlight = true;

  try {
    const response = await fetch("/stop-scenario", { method: "POST" });
    const data = await readJson(response);
    if (!data.success) throw new Error(data.error);

    streamWanted = false; stopSSE();
    [1, 2, 3].forEach(i => ueStates[i] = { state: "disconnected", ip: null });
    showToast("All UEs detached.", true);
  } catch (error) {
    showToast("Unable to contact backend.", false);
  } finally {
    requestInFlight = false;
    refreshStatus();
  }
}

// ─── LƯU CẤU HÌNH (CONFIG) ────────────────────────────
function getValue(id) { return document.getElementById(id).value.trim(); }

function collectConfig() {
  return {
    imsi: getValue("imsi"),
    k: getValue("k"),
    opc: getValue("opc"),
    imei: getValue("imei"),
    apn: getValue("apn"),
    sst: getValue("sst"),
    sd: getValue("sd")
  };
}

function validateConfig(config) {
  const requiredValues = Object.values(config);
  if (requiredValues.some(value => !value)) return "Please complete every configuration field.";
  if (!/^\d{5,15}$/.test(config.imsi)) return "IMSI must contain 5 to 15 digits.";
  if (!/^[0-9a-fA-F]{32}$/.test(config.k)) return "K must contain exactly 32 hexadecimal characters.";
  if (!/^[0-9a-fA-F]{32}$/.test(config.opc)) return "OPc must contain exactly 32 hexadecimal characters.";
  if (!/^\d{14,16}$/.test(config.imei)) return "IMEI must contain 14 to 16 digits.";
  if (!/^[a-zA-Z0-9.-]+$/.test(config.apn)) return "APN contains invalid characters.";
  const sstNumber = Number(config.sst);
  if (!Number.isInteger(sstNumber) || sstNumber < 0 || sstNumber > 255) return "SST must be an integer from 0 to 255.";
  if (!/^[0-9a-fA-F]{6}$/.test(config.sd)) return "SD must contain exactly 6 hexadecimal characters.";
  return null;
}

function saveConfigToBrowser(config) {
  try { localStorage.setItem(CONFIG_STORAGE_KEY, JSON.stringify(config)); return true; } 
  catch (error) { return false; }
}

function loadConfigFromBrowser() {
  try {
    const savedText = localStorage.getItem(CONFIG_STORAGE_KEY);
    if (!savedText) return;
    const savedConfig = JSON.parse(savedText);
    ["imsi", "k", "opc", "imei", "apn", "sst", "sd"].forEach(id => {
      const input = document.getElementById(id);
      if (input && typeof savedConfig[id] === "string") input.value = savedConfig[id];
    });
  } catch (error) { localStorage.removeItem(CONFIG_STORAGE_KEY); }
}

async function saveConfig() {
  if (requestInFlight) return;
  const config = collectConfig();
  const validationError = validateConfig(config);
  if (validationError) { showToast(validationError, false); return; }

  requestInFlight = true;
  config.num_ues = 3; // Lệnh backend sửa file conf cho cả 3 máy

  try {
    const response = await fetch("/save-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config)
    });
    const data = await readJson(response);
    if (data.success) saveConfigToBrowser(config);
    showToast(data.success ? "Configuration saved." : data.error, Boolean(data.success));
  } catch (error) {
    showToast("Unable to contact the backend.", false);
  } finally {
    requestInFlight = false;
  }
}

// ─── UTILS VÀ KHỞI TẠO ────────────────────────────────
function showToast(message, ok) {
  const toast = document.getElementById("toast");
  if (toastTimer) clearTimeout(toastTimer);
  toast.textContent = message;
  toast.className = "toast " + (ok ? "ok" : "err");
  toast.style.display = "block";
  toastTimer = setTimeout(() => toast.style.display = "none", 3500);
}

async function readJson(response) {
  try { return await response.json(); } 
  catch (error) { return { success: false, error: "Backend error." }; }
}

function showScreen(screenName) {
  document.querySelectorAll(".screen").forEach(screen => {
    screen.classList.toggle("active", screen.id === "screen-" + screenName);
  });
  document.querySelectorAll(".nav-item").forEach(button => {
    button.classList.toggle("active", button.dataset.target === screenName);
  });
  window.scrollTo({ top: 0, behavior: "smooth" });
}

document.querySelectorAll(".nav-item").forEach(button => {
  button.addEventListener("click", () => showScreen(button.dataset.target));
});

window.addEventListener("beforeunload", () => { streamWanted = false; stopSSE(); });

// Kích hoạt khi trang vừa tải
loadConfigFromBrowser();
syncTabs();
renderUEState();
renderUELogs();
refreshStatus();
setInterval(refreshStatus, 1000);
