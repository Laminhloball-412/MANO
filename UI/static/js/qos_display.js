function formatLiveQos(session = {}) {
  return {
    fiveQi: session.received_5qi ?? "—",
    arp: session.received_arp ?? "—",
  };
}

if (typeof globalThis !== "undefined") {
  globalThis.formatLiveQos = formatLiveQos;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { formatLiveQos };
}
