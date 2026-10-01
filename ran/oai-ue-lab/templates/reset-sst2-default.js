// Restores the subscriber's original SST2 default_indicator value.
const result = db.subscribers.updateOne(
  { imsi: "460990000000001" },
  { $set: { "slice.$[slice2].default_indicator": false } },
  { arrayFilters: [{ "slice2.sst": 2, "slice2.sd": "000001" }] }
);
printjson({ matchedCount: result.matchedCount, modifiedCount: result.modifiedCount });
