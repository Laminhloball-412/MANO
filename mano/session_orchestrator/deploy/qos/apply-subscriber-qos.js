// Lab-only Open5GS subscriber patch for the three fixed S-NSSAIs.
// Run against the open5gs database. A mongodump backup is required first.
var imsi = "460990000000001";
var profiles = {
  "1": { fiveQi: 8, downlinkMbps: 100, uplinkMbps: 100, arp: 8, capability: 1, vulnerability: 2 },
  "2": { fiveQi: 7, downlinkMbps: 5, uplinkMbps: 5, arp: 2, capability: 2, vulnerability: 1 },
  "3": { fiveQi: 9, downlinkMbps: 1, uplinkMbps: 1, arp: 12, capability: 1, vulnerability: 2 }
};

var subscriber = db.subscribers.findOne({ imsi: imsi });
if (!subscriber) {
  throw new Error("subscriber not found: " + imsi);
}

subscriber.slice.forEach(function (slice) {
  var profile = profiles[String(slice.sst)];
  if (!profile || slice.sd !== "000001") return;
  slice.session.forEach(function (session) {
    if (session.name !== "srsapn") return;
    session.ambr = {
      downlink: { value: NumberInt(profile.downlinkMbps), unit: NumberInt(2) },
      uplink: { value: NumberInt(profile.uplinkMbps), unit: NumberInt(2) }
    };
    session.qos = {
      index: NumberInt(profile.fiveQi),
      arp: {
        priority_level: NumberInt(profile.arp),
        pre_emption_capability: NumberInt(profile.capability),
        pre_emption_vulnerability: NumberInt(profile.vulnerability)
      }
    };
  });
});

var result = db.subscribers.replaceOne({ _id: subscriber._id }, subscriber);
if (result.matchedCount !== 1 && result.nMatched !== 1) {
  throw new Error("subscriber update did not match exactly one record");
}
print("applied three-slice QoS profile to IMSI " + imsi);
