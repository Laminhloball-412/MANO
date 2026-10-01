"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const modulePath = path.resolve(
  __dirname,
  "../static/intent_based_mvp/business_intent_catalog.js",
);

let catalog = {};
try {
  catalog = require(modulePath);
} catch (_error) {
  catalog = {};
}

const services = {
  robot_control: {
    label: "Robot control",
    business_phrases: ["remote robot control"],
    scale_label: "robots",
  },
  live_uplink: {
    label: "Live uplink streaming",
    business_phrases: ["live uplink streaming"],
    scale_label: "uplink streams",
  },
  voice: {
    label: "Conversational voice",
    business_phrases: ["conversational voice"],
    scale_label: "calls",
  },
};

test("returns every service in label order when search is empty", () => {
  assert.equal(typeof catalog.filterServices, "function");
  assert.deepEqual(
    catalog.filterServices(services, "").map((service) => service.label),
    ["Conversational voice", "Live uplink streaming", "Robot control"],
  );
});

test("filters suggestions by service name without changing their request example", () => {
  assert.equal(typeof catalog.filterServices, "function");
  const result = catalog.filterServices(services, "UPLINK");

  assert.equal(result.length, 1);
  assert.equal(result[0].label, "Live uplink streaming");
  assert.equal(
    catalog.requestExample(result[0]),
    "Create a network slice for live uplink streaming with 10 uplink streams for 60 minutes.",
  );
});

test("returns no suggestions when no service name matches", () => {
  assert.equal(typeof catalog.filterServices, "function");
  assert.deepEqual(catalog.filterServices(services, "satellite"), []);
});
