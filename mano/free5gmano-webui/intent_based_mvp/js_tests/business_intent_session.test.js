"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const modulePath = path.resolve(
  __dirname,
  "../static/intent_based_mvp/business_intent_session.js",
);

function memoryStorage() {
  const values = new Map();
  return {
    getItem: (key) => values.has(key) ? values.get(key) : null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
  };
}

if (!fs.existsSync(modulePath)) {
  test("provides a browser session store module", () => {
    assert.ok(fs.existsSync(modulePath), "business_intent_session.js is missing");
  });
} else {
  const { createSessionStore } = require(modulePath);

  test("saves and loads the latest business intent session", () => {
    const storage = memoryStorage();
    const store = createSessionStore(storage);
    const state = {
      draft: { requirement: "Live video for 20 users", scale: "20", duration: "60" },
      preview: { structured_intent: { service_type: "live_video" } },
      intentId: "slice-intent-123",
    };

    store.save(state);

    assert.deepEqual(store.load(), state);
  });

  test("ignores corrupt session data without breaking the page", () => {
    const storage = memoryStorage();
    storage.setItem("ansa.business-intent.session.v1", "not-json");

    const store = createSessionStore(storage);

    assert.equal(store.load(), null);
    assert.equal(storage.getItem("ansa.business-intent.session.v1"), null);
  });
}
