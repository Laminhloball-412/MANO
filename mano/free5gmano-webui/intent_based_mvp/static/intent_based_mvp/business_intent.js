(function () {
  "use strict";

  const form = document.getElementById("business-form");
  if (!form) return;

  const requirement = document.getElementById("business-requirement");
  const exampleSelect = document.getElementById("business-example");
  const searchInput = document.getElementById("business-service-search");
  const scale = document.getElementById("business-scale");
  const duration = document.getElementById("business-duration");
  const previewButton = document.getElementById("business-preview");
  const deployButton = document.getElementById("business-deploy");
  const errorBox = document.getElementById("business-error");
  const messageBox = document.getElementById("business-message");
  const stageBadge = document.getElementById("business-stage");
  const sessionStore = window.BusinessIntentSession
    ? window.BusinessIntentSession.createSessionStore(window.localStorage)
    : null;
  let approvedPreview = null;
  let currentIntentId = null;
  let catalogServices = {};

  function setBusy(isBusy, label) {
    previewButton.disabled = isBusy;
    previewButton.textContent = label || "Analyze";
    if (isBusy) deployButton.disabled = true;
  }

  function showError(message) {
    errorBox.textContent = message;
    errorBox.hidden = false;
    errorBox.focus();
  }

  function clearNotices() {
    errorBox.hidden = true;
    messageBox.hidden = true;
  }

  function inputPayload(executionMode) {
    const payload = {
      schema_version: "2.0",
      business_requirement: requirement.value.trim(),
      execution_mode: executionMode || "dry_run",
    };
    if (scale.value !== "") payload.scale = Number(scale.value);
    if (duration.value !== "") payload.duration_minutes = Number(duration.value);
    return payload;
  }

  function currentDraft() {
    return {
      requirement: requirement.value,
      scale: scale.value,
      duration: duration.value,
      exampleValue: exampleSelect.value,
      searchValue: searchInput.value,
    };
  }

  function persistSession() {
    if (!sessionStore) return;
    sessionStore.save({
      draft: currentDraft(),
      preview: approvedPreview,
      intentId: currentIntentId,
    });
  }

  async function requestJson(url, options) {
    const response = await fetch(url, options);
    let body;
    try {
      body = await response.json();
    } catch (_error) {
      throw new Error("The server returned invalid data.");
    }
    if (!response.ok || !body.ok) {
      throw new Error(body.error || "The request failed.");
    }
    return body;
  }

  function valueText(value) {
    if (value === null || value === undefined || value === "") return "Not available";
    if (typeof value === "boolean") return value ? "Verified" : "Not verified";
    if (Array.isArray(value)) return value.join(", ");
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
  }

  function renderDefinitionList(element, entries) {
    element.replaceChildren();
    entries.forEach(function (entry) {
      const term = document.createElement("dt");
      const detail = document.createElement("dd");
      term.textContent = entry[0];
      detail.textContent = valueText(entry[1]);
      element.append(term, detail);
    });
  }

  function reveal(contentId, emptyId) {
    document.getElementById(contentId).hidden = false;
    document.getElementById(emptyId).hidden = true;
  }

  function renderPreview(data) {
    const intent = data.structured_intent;
    const trace = data.trace;
    if (scale.value === "") scale.value = intent.scale;
    if (duration.value === "") duration.value = intent.duration_minutes;
    renderDefinitionList(document.getElementById("understanding-list"), [
      ["Service", intent.service_type],
      ["Quality", intent.quality],
      ["Scale", intent.scale],
      ["Duration", intent.duration_minutes + " minutes"],
      ["Translation confidence", trace.confidence],
      ["Defaults applied", trace.defaults_applied],
    ]);
    const phraseList = document.getElementById("matched-phrases");
    phraseList.replaceChildren();
    (trace.matched_phrases || []).forEach(function (phrase) {
      const item = document.createElement("span");
      item.textContent = phrase;
      phraseList.appendChild(item);
    });
    reveal("understanding-content", "understanding-empty");

    const sla = data.technical_sla;
    const policy = data.deployment_policy;
    const defaultQos = policy.default_qos || {};
    const serviceQos = policy.service_qos || {};
    renderDefinitionList(document.getElementById("technical-list"), [
      ["Slice family", sla.preferred_template],
      ["Target throughput", sla.target_throughput_mbps + " Mbps"],
      ["Maximum latency", sla.max_latency_ms + " ms"],
      ["Minimum reliability", sla.min_reliability_percent + "%"],
      ["DNN", policy.dnn],
      ["Default session QoS", "5QI " + valueText(defaultQos.five_qi) + " / ARP " + valueText(defaultQos.arp_priority)],
      ["Service QoS", valueText(serviceQos.profile || intent.service_type) + " / 5QI " + valueText(serviceQos.five_qi) + " / ARP " + valueText(serviceQos.arp_priority) + " / " + valueText(serviceQos.resource_type)],
      ["3GPP 5QI Priority Level", serviceQos.five_qi_priority_level],
      ["Packet Delay Budget", serviceQos.packet_delay_budget_ms ? serviceQos.packet_delay_budget_ms + " ms" : null],
      ["Packet Error Rate", serviceQos.packet_error_rate],
      ["ARP source", serviceQos.arp_source],
      ["5QI source", serviceQos.five_qi_source],
      ["QoS evidence", "Core DECLARED_NOT_ENFORCED / Radio NOT_VERIFIED"],
    ]);
    reveal("technical-list", "technical-empty");
  }

  function renderEvents(job) {
    const list = document.getElementById("business-events");
    list.replaceChildren();
    (job.events || []).forEach(function (event) {
      const item = document.createElement("li");
      item.textContent = event.stage || event.event || "Update";
      list.appendChild(item);
    });
    if (!list.children.length) {
      const item = document.createElement("li");
      item.className = "empty-state";
      item.textContent = "The job has no events yet.";
      list.appendChild(item);
    }
    stageBadge.textContent = job.stage || job.status || "Processing";
    stageBadge.classList.toggle("is-ready", job.stage === "READY_FOR_UE");
    stageBadge.classList.toggle("is-error", job.status === "FAILED");
  }

  function renderCompletedJob(job) {
    const sliceResult = job.slice_result || {};
    const allocated = sliceResult.slice || {};
    const configuration = job.configuration || {};
    const core = job.core_registration || allocated.core_registration || {};
    const sst = configuration.sst || allocated.sst;
    const sd = configuration.sd || allocated.sd;
    const dnn = configuration.dnn || "srsapn";
    const defaultQos = configuration.default_qos || {};
    const serviceQos = configuration.service_qos || {};
    const qosEvidence = configuration.qos_evidence || {};
    renderDefinitionList(document.getElementById("core-list"), [
      ["AMF synchronized", core.verified],
      ["NSSF synchronized", core.verified],
      ["NRF registered", (core.nrf_registration || {}).status],
      ["NSSF endpoint", core.nssf_endpoint],
    ]);
    reveal("core-list", "core-empty");

    renderDefinitionList(document.getElementById("ready-list"), [
      ["Namespace", allocated.namespace || configuration.namespace],
      ["NSSI ID", allocated.nssi_id],
      ["S-NSSAI", "SST " + valueText(sst) + " / SD " + valueText(sd)],
      ["DNN", dnn],
      ["Default session QoS", "5QI " + valueText(defaultQos.five_qi) + " / ARP " + valueText(defaultQos.arp_priority)],
      ["Service QoS", valueText(serviceQos.profile) + " / 5QI " + valueText(serviceQos.five_qi) + " / ARP " + valueText(serviceQos.arp_priority)],
      ["Core QoS policy", qosEvidence.core_policy || "DECLARED_NOT_ENFORCED"],
      ["Radio QoS", qosEvidence.radio || "NOT_VERIFIED"],
      ["PDU Session type", "IPv4v6"],
      ["gNB configuration", "Allow S-NSSAI SST " + valueText(sst) + " / SD " + valueText(sd)],
      ["UE configuration", "Request a PDU Session using this S-NSSAI and DNN " + dnn],
      ["Core state", job.stage],
    ]);
    reveal("ready-list", "ready-empty");
  }

  async function pollJob(intentId) {
    for (let attempt = 0; attempt < 90; attempt += 1) {
      const body = await requestJson("api/intents/" + encodeURIComponent(intentId) + "/");
      renderEvents(body.job);
      if (body.job.status === "FAILED") {
        throw new Error(body.job.error || "Slice provisioning failed.");
      }
      if (body.job.status === "COMPLETED") {
        renderCompletedJob(body.job);
        return body.job;
      }
      await new Promise(function (resolve) { window.setTimeout(resolve, 2000); });
    }
    throw new Error("The job timed out. Check its status again later.");
  }

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    clearNotices();
    approvedPreview = null;
    setBusy(true, "Analyzing...");
    try {
      const body = await requestJson("api/translate/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(inputPayload("dry_run")),
      });
      approvedPreview = body.data;
      currentIntentId = null;
      renderPreview(body.data);
      deployButton.disabled = false;
      messageBox.textContent = "Analysis complete. Review the SLA before provisioning.";
      messageBox.hidden = false;
      persistSession();
    } catch (error) {
      showError(error.message);
    } finally {
      setBusy(false);
      deployButton.disabled = !approvedPreview;
    }
  });

  deployButton.addEventListener("click", async function () {
    if (!approvedPreview) return;
    if (!window.confirm("Provision a slice using the displayed technical translation?")) return;
    clearNotices();
    deployButton.disabled = true;
    deployButton.textContent = "Submitting request...";
    try {
      const body = await requestJson("api/intents/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(inputPayload("execute")),
      });
      currentIntentId = body.job.intent_id;
      persistSession();
      renderEvents(body.job);
      const job = await pollJob(body.job.intent_id);
      messageBox.textContent = "The slice is ready for a UE using the displayed S-NSSAI.";
      messageBox.hidden = false;
      renderCompletedJob(job);
      persistSession();
    } catch (error) {
      showError(error.message);
      stageBadge.classList.add("is-error");
    } finally {
      deployButton.textContent = "Provision slice";
      deployButton.disabled = !approvedPreview || Boolean(currentIntentId);
    }
  });

  [requirement, scale, duration].forEach(function (field) {
    field.addEventListener("input", function () {
      approvedPreview = null;
      currentIntentId = null;
      deployButton.disabled = true;
      persistSession();
    });
  });

  exampleSelect.addEventListener("change", function () {
    const option = exampleSelect.options[exampleSelect.selectedIndex];
    if (!option || !option.value) return;
    requirement.value = option.value;
    approvedPreview = null;
    currentIntentId = null;
    deployButton.disabled = true;
    persistSession();
    requirement.focus();
  });

  searchInput.addEventListener("input", function () {
    renderCatalogExamples(searchInput.value);
    persistSession();
  });

  function renderCatalogExamples(query) {
    const services = window.BusinessIntentCatalog.filterServices(catalogServices, query);
    exampleSelect.replaceChildren();
    const prompt = document.createElement("option");
    prompt.value = "";
    prompt.textContent = services.length ? "Choose a service" : "No services found";
    exampleSelect.appendChild(prompt);
    services.forEach(function (service) {
      const option = document.createElement("option");
      option.value = window.BusinessIntentCatalog.requestExample(service);
      option.textContent = service.label;
      exampleSelect.appendChild(option);
    });
    exampleSelect.disabled = !services.length;
  }

  async function loadCatalogExamples() {
    const body = await requestJson("api/catalog/");
    catalogServices = body.data.services || {};
    searchInput.disabled = false;
    renderCatalogExamples("");
  }

  async function restoreSession() {
    if (!sessionStore) return;
    const saved = sessionStore.load();
    if (!saved) return;

    const draft = saved.draft || {};
    requirement.value = draft.requirement || "";
    scale.value = draft.scale || "";
    duration.value = draft.duration || "";
    searchInput.value = draft.searchValue || "";
    renderCatalogExamples(searchInput.value);
    if (draft.exampleValue && Array.from(exampleSelect.options).some(function (option) {
      return option.value === draft.exampleValue;
    })) {
      exampleSelect.value = draft.exampleValue;
    }

    if (saved.preview) {
      approvedPreview = saved.preview;
      renderPreview(saved.preview);
      deployButton.disabled = false;
    }

    if (!saved.intentId) return;
    currentIntentId = saved.intentId;
    deployButton.disabled = true;
    try {
      const body = await requestJson("api/intents/" + encodeURIComponent(currentIntentId) + "/");
      renderEvents(body.job);
      if (body.job.status === "COMPLETED") {
        renderCompletedJob(body.job);
        messageBox.textContent = "Previous session restored.";
        messageBox.hidden = false;
        return;
      }
      if (body.job.status === "FAILED") {
        messageBox.textContent = "Previous provisioning job failed.";
        messageBox.hidden = false;
        return;
      }
      messageBox.textContent = "Previous session restored. Provisioning is still running.";
      messageBox.hidden = false;
      const job = await pollJob(currentIntentId);
      renderCompletedJob(job);
      messageBox.textContent = "The restored slice is ready for UE access.";
    } catch (_error) {
      currentIntentId = null;
      persistSession();
      messageBox.textContent = "The saved job is no longer available. The request was restored.";
      messageBox.hidden = false;
      deployButton.disabled = !approvedPreview;
    }
  }

  async function initializePage() {
    try {
      await loadCatalogExamples();
    } catch (_error) {
      searchInput.disabled = true;
      exampleSelect.replaceChildren();
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "Unable to load services";
      exampleSelect.appendChild(option);
      exampleSelect.disabled = true;
    }
    await restoreSession();
  }

  initializePage();
})();
