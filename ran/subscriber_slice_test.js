#!/usr/bin/env node

const fs = require("fs");
const mongoose = require("mongoose");
const Subscriber = require("/open5gs/webui/server/models/subscriber");

const action = process.argv[2];
const imsi = process.argv[3] || "460990000000002";
const backupPath = process.argv[4] || "/tmp/ue2-slices-backup.json";

function stripMongoIds(value) {
  if (Array.isArray(value)) {
    value.forEach(stripMongoIds);
    return;
  }
  if (value && typeof value === "object") {
    delete value._id;
    Object.values(value).forEach(stripMongoIds);
  }
}

async function main() {
  if (!["show", "add-slice1", "restore"].includes(action)) {
    throw new Error("usage: subscriber_slice_test.js show|add-slice1|restore IMSI [backup.json]");
  }

  await mongoose.connect(process.env.DB_URI, {
    useNewUrlParser: true,
    useUnifiedTopology: true,
  });
  const subscriber = await Subscriber.findOne({ imsi });
  if (!subscriber) {
    throw new Error(`subscriber ${imsi} not found`);
  }

  if (action === "show") {
    console.log(JSON.stringify({ imsi, slice: subscriber.slice }, null, 2));
    return;
  }

  if (action === "add-slice1") {
    fs.writeFileSync(backupPath, JSON.stringify(subscriber.slice, null, 2));
    const alreadyAllowed = subscriber.slice.some(
      (slice) => Number(slice.sst) === 1 && String(slice.sd).padStart(6, "0") === "000001"
    );
    if (!alreadyAllowed) {
      if (!subscriber.slice.length) {
        throw new Error(`subscriber ${imsi} has no source session profile to clone`);
      }
      const cloned = subscriber.slice[0].toObject
        ? subscriber.slice[0].toObject()
        : JSON.parse(JSON.stringify(subscriber.slice[0]));
      stripMongoIds(cloned);
      cloned.sst = 1;
      cloned.sd = "000001";
      cloned.default_indicator = false;
      subscriber.slice.push(cloned);
      await subscriber.save();
    }
    console.log(JSON.stringify({
      imsi,
      changed: !alreadyAllowed,
      backup: backupPath,
      slice: subscriber.slice,
    }, null, 2));
    return;
  }

  const originalSlices = JSON.parse(fs.readFileSync(backupPath, "utf8"));
  subscriber.slice = originalSlices;
  await subscriber.save();
  console.log(JSON.stringify({ imsi, restored: true, slice: subscriber.slice }, null, 2));
}

main()
  .catch((error) => {
    console.error(error.stack || error.message);
    process.exitCode = 1;
  })
  .finally(async () => {
    await mongoose.disconnect();
  });
