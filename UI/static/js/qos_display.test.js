const assert = require("node:assert/strict");

let formatLiveQos;
try {
  ({ formatLiveQos } = require("./qos_display.js"));
} catch (_error) {
  formatLiveQos = undefined;
}

assert.equal(typeof formatLiveQos, "function", "formatLiveQos must be implemented");

assert.deepEqual(
  formatLiveQos({ received_5qi: 9, received_arp: 8 }),
  { fiveQi: 9, arp: 8 },
  "the UI must display the values received by the gNB",
);

assert.deepEqual(
  formatLiveQos({ expected_5qi: 9, expected_arp: 8 }),
  { fiveQi: "—", arp: "—" },
  "expected Open5GS values must not be shown as live evidence",
);

console.log("qos_display.test.js: PASS");
