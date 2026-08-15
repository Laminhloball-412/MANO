(function () {
  "use strict";

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"}[char];
    });
  }

  function showAlert(kind, message) {
    var alert = document.getElementById("auto-slice-alert");
    alert.className = "alert alert-" + kind;
    alert.textContent = message;
  }

  function errorMessage(error) {
    return (error.response && error.response.data && error.response.data.error) || error.message;
  }

  function setBusy(busy) {
    document.querySelectorAll(".auto-create").forEach(function (button) {
      button.disabled = busy;
    });
  }

  function renderStatus(data) {
    (data.profiles || []).forEach(function (profile) {
      var sd = document.getElementById("sd-" + profile.slice_type);
      var namespace = document.getElementById("ns-" + profile.slice_type);
      if (sd) sd.textContent = profile.next_sd;
      if (namespace) namespace.textContent = profile.next_namespace;
    });

    var rows = [];
    var tracked = data.tracked_slices || {};
    Object.keys(tracked).sort().forEach(function (namespace) {
      var slice = tracked[namespace] || {};
      if (slice.status === "DELETED" || slice.status === "ROLLED_BACK") return;
      var target = slice.nssi_id || namespace;
      rows.push(
        "<tr><td>" + escapeHtml(namespace) + "</td>" +
        "<td>" + escapeHtml(slice.slice_type) + "</td>" +
        "<td>" + escapeHtml(slice.sst) + "</td>" +
        "<td>" + escapeHtml(slice.sd) + "</td>" +
        "<td>" + escapeHtml(slice.status) + "</td>" +
        "<td class='small'>" + escapeHtml(slice.nssi_id || "-") + "</td>" +
        "<td><button class='btn btn-sm btn-outline-danger' onclick=\"deleteAutoSlice('" +
        escapeHtml(target) + "','" + escapeHtml(namespace) + "')\">" +
        "<i class='fas fa-trash'></i> Delete</button></td></tr>"
      );
    });
    (data.untracked_api_instances || []).forEach(function (nssi) {
      rows.push(
        "<tr class='table-warning'><td><span class='text-muted'>Auto-detect</span></td>" +
        "<td>-</td><td>-</td><td>-</td><td>LEGACY</td>" +
        "<td class='small'>" + escapeHtml(nssi) + "</td>" +
        "<td><button class='btn btn-sm btn-outline-danger' onclick=\"deleteAutoSlice('" +
        escapeHtml(nssi) + "','legacy NSSI')\">" +
        "<i class='fas fa-tools'></i> Repair Delete</button></td></tr>"
      );
    });
    document.getElementById("auto-slice-table").innerHTML = rows.length
      ? rows.join("")
      : "<tr><td colspan='7' class='text-center text-muted'>No tracked Auto Slice</td></tr>";

    var untracked = (data.untracked_api_instances || []).length;
    var orphans = data.orphan_namespaces || [];
    showAlert(
      (untracked || orphans.length) ? "warning" : "success",
      orphans.length
        ? "Orphan namespace(s): " + orphans.join(", ") + ". They will be cleaned when the slot is reused."
        : untracked
        ? "Status loaded. " + untracked + " legacy NSSI instance(s) are not tracked by Auto Slice."
        : "Ready. Local base descriptors are available on the MANO host."
    );
  }

  window.loadAutoSliceStatus = function () {
    axios.get("/api/auto-slice/status/")
      .then(function (response) { renderStatus(response.data.data); })
      .catch(function (error) { showAlert("danger", errorMessage(error)); });
  };

  window.createAutoSlice = function (sliceType) {
    if (!window.confirm("Create and allocate one " + sliceType.toUpperCase() + " slice?")) return;
    setBusy(true);
    showAlert("info", "Rendering local descriptors, uploading, and allocating " + sliceType.toUpperCase() + "...");
    axios.post("/api/auto-slice/create/", {slice_type: sliceType})
      .then(function (response) {
        var result = response.data.data;
        showAlert("success", "Created " + result.namespace + " · SST " + result.sst + " · SD " + result.sd);
        window.loadAutoSliceStatus();
      })
      .catch(function (error) { showAlert("danger", errorMessage(error)); })
      .finally(function () { setBusy(false); });
  };

  window.deleteAutoSlice = function (target, namespace) {
    if (!window.confirm("Delete " + namespace + " and its generated templates? Local base descriptors will be kept.")) return;
    setBusy(true);
    showAlert("warning", "Deleting " + namespace + "...");
    axios.post("/api/auto-slice/delete/", {target: target})
      .then(function () { showAlert("success", "Deleted " + namespace); window.loadAutoSliceStatus(); })
      .catch(function (error) { showAlert("danger", errorMessage(error)); })
      .finally(function () { setBusy(false); });
  };

  document.addEventListener("DOMContentLoaded", window.loadAutoSliceStatus);
}());
