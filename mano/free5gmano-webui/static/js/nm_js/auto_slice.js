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

  function setBusy(busy) {
    document.querySelectorAll(".auto-create").forEach(function (button) {
      button.disabled = busy;
    });
  }

  function renderStatus(data) {
    (data.profiles || []).forEach(function (profile) {
      var element = document.getElementById("sd-" + profile.slice_type);
      if (element) element.textContent = profile.next_sd;
      var namespaceElement = document.getElementById("ns-" + profile.slice_type);
      if (namespaceElement) namespaceElement.textContent = profile.next_namespace;
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
        ? "Namespace mồ côi " + orphans.join(", ") + " sẽ được tự dọn khi slot đó được dùng lại."
        : untracked
        ? "Status loaded. Có " + untracked + " NSSI cũ không do Auto Slice quản lý; hệ thống vẫn dùng NRM của chúng khi chọn SD."
        : "Ready. SD có thể trùng giữa các SST khác nhau."
    );
  }

  window.loadAutoSliceStatus = function () {
    axios.get("/api/auto-slice/status/")
      .then(function (response) { renderStatus(response.data.data); })
      .catch(function (error) {
        showAlert("danger", (error.response && error.response.data.error) || error.message);
      });
  };

  window.createAutoSlice = function (sliceType) {
    if (!window.confirm("Create và allocate một slice " + sliceType.toUpperCase() + "?")) return;
    setBusy(true);
    showAlert("info", "Đang render, upload và allocate " + sliceType.toUpperCase() + ". Vui lòng chờ...");
    axios.post("/api/auto-slice/create/", {slice_type: sliceType})
      .then(function (response) {
        var result = response.data.data;
        showAlert("success", "Đã tạo " + result.namespace + " · SST " + result.sst + " · SD " + result.sd);
        window.loadAutoSliceStatus();
      })
      .catch(function (error) {
        showAlert("danger", (error.response && error.response.data.error) || error.message);
      })
      .finally(function () { setBusy(false); });
  };

  window.deleteAutoSlice = function (target, namespace) {
    if (!window.confirm("Delete " + namespace + " và cleanup các template do Auto Slice tạo?")) return;
    setBusy(true);
    showAlert("warning", "Đang delete " + namespace + "...");
    axios.post("/api/auto-slice/delete/", {target: target})
      .then(function () {
        showAlert("success", "Đã delete " + namespace);
        window.loadAutoSliceStatus();
      })
      .catch(function (error) {
        showAlert("danger", (error.response && error.response.data.error) || error.message);
      })
      .finally(function () { setBusy(false); });
  };

  document.addEventListener("DOMContentLoaded", window.loadAutoSliceStatus);
}());
