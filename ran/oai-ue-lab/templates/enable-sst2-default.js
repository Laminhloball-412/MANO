// Lab-only, reversible subscriber patch for Duranta OAI UE 2026.w25.
// It does not alter authentication material or session/DNN definitions.
const result = db.subscribers.updateOne(
  { imsi: "460990000000001" },
  { $set: { "slice.$[slice2].default_indicator": true } },
  { arrayFilters: [{ "slice2.sst": 2, "slice2.sd": "000001" }] }
);
printjson({ matchedCount: result.matchedCount, modifiedCount: result.modifiedCount });
