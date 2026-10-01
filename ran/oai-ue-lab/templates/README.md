# OAI UE compatibility test templates

These files are isolated test inputs for the existing lab. They do not replace
`gnb1.yaml`, any srsUE configuration, or Kubernetes manifests. The optional,
reversible subscriber patch is documented below.

Radio parameters matching the existing gNB:

- PLMN: `46099`, TAC: `1`
- band: `3`, NR-ARFCN: `368500` (`1842.5 MHz` downlink)
- FDD uplink offset: `-95 MHz`; SSB start: subcarrier `162`
- bandwidth: `10 MHz`, SCS: `15 kHz`, `52` PRBs
- sample rate: `15.36 Msps` for OAI long-PRACH support
- test-copy PUSCH cap: `max_ue_mcs: 10` to keep initial SRB1 uplink on QPSK
- OAI TX: bind `tcp://0.0.0.0:2001`
- OAI RX: connect `tcp://127.0.0.1:2000`

The templates deliberately contain `__IMSI__`, `__KEY__`, and `__OPC__`
placeholders. For a live run, render a private mode-0600 configuration from an
already registered subscriber; never commit that rendered file.

Expected UE command (from the OAI build directory):

```sh
sudo ./nr-uesoftmodem -O /path/to/private.conf \
  -r 52 --numerology 0 --band 3 -C 1842500000 --CO -95000000 \
  --ssb 162 \
  --device.name oai_zmqdevif \
  --zmq.[0].tx_channels tcp://0.0.0.0:2001 \
  --zmq.[0].rx_channels tcp://127.0.0.1:2000
```

Restart both gNB and OAI UE for every attempt: the OAI ZMQ backend does not
reconnect its channels after either endpoint is restarted.

Use the locally built official OCUDU source tree with:

```sh
GNB_MODE=ocudu-source ./run-oai-ue-test.sh one /path/to/private-one.conf
GNB_MODE=ocudu-source ./run-oai-ue-test.sh two /path/to/private-two.conf
```

For the isolated OAI CI-compatible TDD profile, add
`GNB_PROFILE=band78-ci`. The default `GNB_PROFILE=band3` preserves the lab
radio parameters for interoperability diagnosis.

Commands used for the successful tutorial-aligned runs:

```sh
GNB_MODE=ocudu-source GNB_PROFILE=band78-ci \
  ./run-oai-ue-test.sh one /home/ubuntu/ran/oai-ue-lab/private-one.conf
GNB_MODE=ocudu-source GNB_PROFILE=band78-ci \
  ./run-oai-ue-test.sh two /home/ubuntu/ran/oai-ue-lab/private-two.conf
```

The default `OAI_FLAVOR=duranta` uses the tutorial-aligned Duranta checkout
at `/home/ubuntu/ran/openairinterface5g-duranta` (tag `2026.w25`). Set
`OAI_FLAVOR=upstream` only for comparison with the separate upstream build.

Duranta `2026.w25` does not include all configured PDU-session S-NSSAIs in
its initial Requested NSSAI. For the lab subscriber, `enable-sst2-default.js`
makes SST2 part of the AMF Allowed NSSAI so the UE can request both sessions.
Use `reset-sst2-default.js` to restore the original subscriber flag.

OCUDU source is kept separately at `/home/ubuntu/ran/ocudu-oai` and the
runner uses `/home/ubuntu/ran/ocudu-oai/build-oai/apps/gnb_split_8/gnb`.
This leaves the existing srsRAN checkout and its original configuration
untouched.
