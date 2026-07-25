  let evtSource = null;
  let autoScroll = true;

  const terminal  = document.getElementById("terminal");
  const badge     = document.getElementById("badge");
  const dot       = document.getElementById("dot");
  const statState = document.getElementById("stat-state");
  const statIp    = document.getElementById("stat-ip");

  const STEPS = {
    attaching:      ["step-attach"],
    random_access:  ["step-attach", "step-auth"],
    authenticating: ["step-attach", "step-auth"],
    connected:      ["step-attach", "step-auth", "step-pdu", "step-done"],
    failed:         [],
    disconnected:   []
  };

  function updateSteps(state) {
    ["step-attach","step-auth","step-pdu","step-done"].forEach(id => {
      const el = document.getElementById(id);
      el.classList.remove("active","done");
    });
    const active = STEPS[state] || [];
    active.forEach((id, i) => {
      const el = document.getElementById(id);
      if (i === active.length - 1 && state !== "connected") {
        el.classList.add("active");
      } else {
        el.classList.add("done");
      }
    });
  }

  function updateStatus(data) {
    const state = data.state || "disconnected";
    const ip    = data.ip || "—";

    statState.textContent = state.replace(/_/g," ").toUpperCase();
    statState.className   = "stat-value";
    statIp.textContent    = ip;

    badge.textContent  = state.toUpperCase().replace(/_/g," ");
    badge.className    = "badge " + state;

    updateSteps(state);

    if (state === "connected") {
      statState.classList.add("green");
      dot.className = "dot live";
    } else if (state === "disconnected" || state === "failed") {
      dot.className = "dot idle";
    } else {
      dot.className = "dot live";
    }
  }

  function classForLine(line) {
    if (line.includes("PDU Session Establishment successful")) return "pdu";
    if (line.includes("[OK]") || line.includes("RRC Connected") || line.includes("Complete")) return "ok";
    if (line.includes("[INFO]")) return "info";
    if (line.includes("[WARN]") || line.includes("Warning")) return "warn";
    if (line.includes("[ERROR]") || line.includes("Failed") || line.includes("Error")) return "error";
    return "";
  }

  function appendLog(line) {
    if (line === "[PING]") return;
    const div = document.createElement("div");
    div.className = "log-line " + classForLine(line);
    div.textContent = line;
    terminal.appendChild(div);
    if (autoScroll) terminal.scrollTop = terminal.scrollHeight;
  }

  function clearLogs() {
    terminal.innerHTML = "";
  }

  terminal.addEventListener("scroll", () => {
    const atBottom = terminal.scrollHeight - terminal.scrollTop - terminal.clientHeight < 20;
    autoScroll = atBottom;
  });

  // Poll status every second
  setInterval(async () => {
    try {
      const r = await fetch("/status");
      const d = await r.json();
      updateStatus(d);
    } catch(e) {}
  }, 1000);

  function startSSE() {
    if (evtSource) evtSource.close();
    evtSource = new EventSource("/logs/stream");
    evtSource.onmessage = e => appendLog(e.data);
    evtSource.onerror   = () => { evtSource.close(); evtSource = null; };
  }

  function showToast(msg, ok) {
    const t = document.getElementById("toast");
    t.textContent  = msg;
    t.className    = "toast " + (ok ? "ok" : "err");
    t.style.display = "block";
    setTimeout(() => t.style.display = "none", 4000);
  }

  async function saveConfig() {
    const body = {
      k:    document.getElementById("k").value.trim(),
      opc:  document.getElementById("opc").value.trim(),
      imei: document.getElementById("imei").value.trim(),
      apn:  document.getElementById("apn").value.trim(),
      imsi: document.getElementById("imsi").value.trim(),
      sst:  document.getElementById("sst").value.trim(),
      sd:   document.getElementById("sd").value.trim()
    };

    if (!body.k || !body.opc || !body.imei || !body.apn) {
      showToast("All fields are required", false);
      return;
    }

    try {
      const r = await fetch("/save-config", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body)
      });
      const d = await r.json();
      showToast(d.success ? "✓ Config saved to ue_rf.conf" : "✗ " + d.error, d.success);
    } catch(e) {
      showToast("✗ Request failed: " + e, false);
    }
  }

  async function attachUE() {
    clearLogs();
    startSSE();

    try {
      const r = await fetch("/attach", { method: "POST" });
      const d = await r.json();
      if (!d.success) {
        showToast("✗ " + d.error, false);
      }
    } catch(e) {
      showToast("✗ Request failed: " + e, false);
    }
  }

  async function detachUE() {
    try {
      const r = await fetch("/detach", { method: "POST" });
      const d = await r.json();
      showToast(d.success ? "✓ UE detached" : "✗ " + d.error, d.success);
    } catch(e) {}
  }
