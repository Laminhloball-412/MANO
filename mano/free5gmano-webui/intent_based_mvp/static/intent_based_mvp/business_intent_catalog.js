(function (root, factory) {
  "use strict";

  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.BusinessIntentCatalog = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function filterServices(services, query) {
    const normalizedQuery = String(query || "").trim().toLocaleLowerCase();
    return Object.keys(services || {}).map(function (serviceType) {
      return services[serviceType];
    }).filter(function (service) {
      return !normalizedQuery || String(service.label || "").toLocaleLowerCase().includes(normalizedQuery);
    }).sort(function (left, right) {
      return left.label.localeCompare(right.label);
    });
  }

  function requestExample(service) {
    const phrase = (service.business_phrases || [service.label])[0];
    const unit = String(service.scale_label || "concurrent users").toLowerCase();
    return "Create a network slice for " + phrase + " with 10 " + unit + " for 60 minutes.";
  }

  return {
    filterServices: filterServices,
    requestExample: requestExample,
  };
});
