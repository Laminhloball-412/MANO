// Restore the lab subscriber's original session policy.
var imsi = "460990000000001";
var subscriber = db.subscribers.findOne({ imsi: imsi });
if (!subscriber) {
  throw new Error("subscriber not found: " + imsi);
}

subscriber.slice.forEach(function (slice) {
  if (slice.sd !== "000001") return;
  slice.session.forEach(function (session) {
    if (session.name !== "srsapn") return;
    session.ambr = {
      downlink: { value: NumberInt(1), unit: NumberInt(3) },
      uplink: { value: NumberInt(1), unit: NumberInt(3) }
    };
    session.qos = {
      index: NumberInt(9),
      arp: {
        priority_level: NumberInt(8),
        pre_emption_capability: NumberInt(1),
        pre_emption_vulnerability: NumberInt(1)
      }
    };
  });
});

var result = db.subscribers.replaceOne({ _id: subscriber._id }, subscriber);
if (result.matchedCount !== 1 && result.nMatched !== 1) {
  throw new Error("subscriber reset did not match exactly one record");
}
print("restored original QoS profile for IMSI " + imsi);
