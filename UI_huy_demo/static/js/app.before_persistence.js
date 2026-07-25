let evtSource = null;
let reconnectTimer = null;
let toastTimer = null;
let autoScroll = true;
let streamWanted = false;
let requestInFlight = false;

const terminal = document.getElementById("terminal");
const badge = document.getElementById("badge");
const dot = document.getElementById("dot");
const statState = document.getElementById("stat-state");
const statIp = document.getElementById("stat-ip");
const attachButton = document.getElementById("attach-btn");
const detachButton = document.getElementById("detach-btn");

const TRANSITION_STATES = [
  "attaching",
  "random_access",
  "authenticating"
];

const STEPS = {
  disconnected: [],
  attaching: ["step-attach"],
  random_access: ["step-attach", "step-auth"],
  authenticating: ["step-attach", "step-auth"],
  connected: [
    "step-attach",
    "step-auth",
    "step-pdu",
    "step-done"
  ],
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
  return STATE_LABELS[state] ||
    state.replaceAll("_", " ");
}

function updateSteps(state) {
  const stepIds = [
    "step-attach",
    "step-auth",
    "step-pdu",
    "step-done"
  ];

  stepIds.forEach((id) => {
    const element = document.getElementById(id);
    element.classList.remove("active", "done");
  });

  const activeSteps = STEPS[state] || [];

  activeSteps.forEach((id, index) => {
    const element = document.getElementById(id);
    const isCurrentStep =
      index === activeSteps.length - 1 &&
      state !== "connected";

    element.classList.add(isCurrentStep ? "active" : "done");
  });
}

function updateActionButtons(state) {
  const isConnected = state === "connected";
  const isTransitioning = TRANSITION_STATES.includes(state);

  attachButton.style.display = isConnected
    ? "none"
    : "inline-flex";

  detachButton.style.display = isConnected
    ? "inline-flex"
    : "none";

  attachButton.disabled = requestInFlight || isTransitioning;
  detachButton.disabled = requestInFlight;

  if (isTransitioning) {
    attachButton.innerHTML =
      "<span>◌</span> Connecting...";
  } else if (state === "failed" || state === "error") {
    attachButton.innerHTML =
      "<span>↻</span> Retry attach";
  } else {
    attachButton.innerHTML =
      "<span>▶</span> Attach UE";
  }
}

function updateStatus(data) {
  const state = data.state || "disconnected";
  const ip = data.ip || "—";
  const label = stateLabel(state);

  statState.textContent = label.toUpperCase();
  statState.className = "stat-value";
  statIp.textContent = ip;

  badge.textContent = label.toUpperCase();
  badge.className = "badge " + state;

  updateSteps(state);
  updateActionButtons(state);

  if (state === "connected") {
    statState.classList.add("green");
    dot.className = "dot live";

    if (!streamWanted) {
      streamWanted = true;
      startSSE();
    }
  } else if (TRANSITION_STATES.includes(state)) {
    dot.className = "dot live";
  } else {
    dot.className = "dot idle";

    if (state === "disconnected") {
      streamWanted = false;
      stopSSE();
    }
  }
}

function classForLine(line) {
  if (line.includes("PDU Session Establishment successful")) {
    return "pdu";
  }

  if (
    line.includes("[OK]") ||
    line.includes("RRC Connected") ||
    line.includes("Complete")
  ) {
    return "ok";
  }

  if (line.includes("[INFO]")) {
    return "info";
  }

  if (
    line.includes("[WARN]") ||
    line.includes("Warning")
  ) {
    return "warn";
  }

  if (
    line.includes("[ERROR]") ||
    line.includes("Failed") ||
    line.includes("Error")
  ) {
    return "error";
  }

  return "";
}

function appendLog(line) {
  if (!line || line === "[PING]") {
    return;
  }

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

function clearLogs() {
  terminal.innerHTML = "";

  const placeholder = document.createElement("div");
  placeholder.className = "log-line info";
  placeholder.textContent = "Waiting for UE logs...";
  terminal.appendChild(placeholder);
}

terminal.addEventListener("scroll", () => {
  const distanceFromBottom =
    terminal.scrollHeight -
    terminal.scrollTop -
    terminal.clientHeight;

  autoScroll = distanceFromBottom < 24;
});

function stopSSE() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }

  if (evtSource) {
    evtSource.close();
    evtSource = null;
  }
}

function startSSE() {
  if (!streamWanted || evtSource) {
    return;
  }

  evtSource = new EventSource("/logs/stream");

  evtSource.onmessage = (event) => {
    appendLog(event.data);
  };

  evtSource.onerror = () => {
    if (evtSource) {
      evtSource.close();
      evtSource = null;
    }

    if (streamWanted) {
      reconnectTimer = setTimeout(startSSE, 2000);
    }
  };
}

function showToast(message, ok) {
  const toast = document.getElementById("toast");

  if (toastTimer) {
    clearTimeout(toastTimer);
  }

  toast.textContent = message;
  toast.className = "toast " + (ok ? "ok" : "err");
  toast.style.display = "block";

  toastTimer = setTimeout(() => {
    toast.style.display = "none";
  }, 3500);
}

function getValue(id) {
  return document.getElementById(id).value.trim();
}

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

  if (requiredValues.some((value) => !value)) {
    return "Please complete every configuration field.";
  }

  if (!/^\d{5,15}$/.test(config.imsi)) {
    return "IMSI must contain 5 to 15 digits.";
  }

  if (!/^[0-9a-fA-F]{32}$/.test(config.k)) {
    return "K must contain exactly 32 hexadecimal characters.";
  }

  if (!/^[0-9a-fA-F]{32}$/.test(config.opc)) {
    return "OPc must contain exactly 32 hexadecimal characters.";
  }

  if (!/^\d{14,16}$/.test(config.imei)) {
    return "IMEI must contain 14 to 16 digits.";
  }

  if (!/^[a-zA-Z0-9.-]+$/.test(config.apn)) {
    return "APN contains invalid characters.";
  }

  const sstNumber = Number(config.sst);

  if (
    !Number.isInteger(sstNumber) ||
    sstNumber < 0 ||
    sstNumber > 255
  ) {
    return "SST must be an integer from 0 to 255.";
  }

  if (!/^[0-9a-fA-F]{6}$/.test(config.sd)) {
    return "SD must contain exactly 6 hexadecimal characters.";
  }

  return null;
}

async function readJson(response) {
  try {
    return await response.json();
  } catch (error) {
    return {
      success: false,
      error: "Backend returned an invalid response."
    };
  }
}

async function refreshStatus() {
  try {
    const response = await fetch("/status", {
      cache: "no-store"
    });

    if (!response.ok) {
      return;
    }

    const data = await response.json();
    updateStatus(data);
  } catch (error) {
    // A temporary polling failure must not freeze the interface.
  }
}

async function saveConfig() {
  if (requestInFlight) {
    return;
  }

  const config = collectConfig();
  const validationError = validateConfig(config);

  if (validationError) {
    showToast(validationError, false);
    return;
  }

  requestInFlight = true;

  try {
    const response = await fetch("/save-config", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(config)
    });

    const data = await readJson(response);

    showToast(
      data.success
        ? "Configuration saved to uetest.conf."
        : data.error || "Unable to save configuration.",
      Boolean(data.success)
    );
  } catch (error) {
    showToast("Unable to contact the backend.", false);
  } finally {
    requestInFlight = false;
  }
}

async function attachUE() {
  if (requestInFlight) {
    return;
  }

  requestInFlight = true;
  streamWanted = true;
  clearLogs();
  startSSE();
  updateStatus({
    state: "attaching",
    ip: null
  });

  try {
    const response = await fetch("/attach", {
      method: "POST"
    });

    const data = await readJson(response);

    if (!data.success) {
      streamWanted = false;
      stopSSE();

      updateStatus({
        state: "error",
        ip: null
      });

      showToast(
        data.error || "Unable to attach UE.",
        false
      );

      return;
    }

    showToast("Attach request sent.", true);
  } catch (error) {
    streamWanted = false;
    stopSSE();

    updateStatus({
      state: "error",
      ip: null
    });

    showToast("Unable to contact the backend.", false);
  } finally {
    requestInFlight = false;
    refreshStatus();
  }
}

async function detachUE() {
  if (requestInFlight) {
    return;
  }

  requestInFlight = true;
  updateActionButtons("connected");

  try {
    const response = await fetch("/detach", {
      method: "POST"
    });

    const data = await readJson(response);

    if (!data.success) {
      showToast(
        data.error || "Unable to detach UE.",
        false
      );

      return;
    }

    streamWanted = false;
    stopSSE();

    updateStatus({
      state: "disconnected",
      ip: null
    });

    showToast("UE detached.", true);
  } catch (error) {
    showToast("Unable to contact the backend.", false);
  } finally {
    requestInFlight = false;
    refreshStatus();
  }
}

function showScreen(screenName) {
  document.querySelectorAll(".screen").forEach((screen) => {
    screen.classList.toggle(
      "active",
      screen.id === "screen-" + screenName
    );
  });

  document.querySelectorAll(".nav-item").forEach((button) => {
    button.classList.toggle(
      "active",
      button.dataset.target === screenName
    );
  });

  window.scrollTo({
    top: 0,
    behavior: "smooth"
  });
}

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => {
    showScreen(button.dataset.target);
  });
});

window.addEventListener("beforeunload", () => {
  streamWanted = false;
  stopSSE();
});

updateStatus({
  state: "disconnected",
  ip: null
});

refreshStatus();
setInterval(refreshStatus, 1000);
