(function (root, factory) {
  "use strict";

  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.BusinessIntentSession = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const DEFAULT_KEY = "ansa.business-intent.session.v1";

  function createSessionStore(storage, key) {
    const storageKey = key || DEFAULT_KEY;

    function clear() {
      try {
        storage.removeItem(storageKey);
      } catch (_error) {
        return false;
      }
      return true;
    }

    return {
      load: function () {
        let raw;
        try {
          raw = storage.getItem(storageKey);
          if (!raw) return null;
          const document = JSON.parse(raw);
          if (document.version !== 1 || !document.state || typeof document.state !== "object") {
            throw new Error("Unsupported session data");
          }
          return document.state;
        } catch (_error) {
          clear();
          return null;
        }
      },
      save: function (state) {
        try {
          storage.setItem(storageKey, JSON.stringify({ version: 1, state: state }));
          return true;
        } catch (_error) {
          return false;
        }
      },
      clear: clear,
    };
  }

  return {
    createSessionStore: createSessionStore,
  };
});
