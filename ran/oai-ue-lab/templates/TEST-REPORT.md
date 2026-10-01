# OCUDU gNB + Duranta OAI UE lab report

Test date: 2026-08-15 (Asia/Saigon)

## Result

- OCUDU gNB source: `f85247d736e4de55206e34be0ce105faf0549a28`
  (`26.04.0`), built as `build-oai/apps/gnb_split_8/gnb`.
- Duranta OpenAirInterface UE: tag `2026.w25`, commit
  `31eb466a7d598a7f0a8850b0a7405dca16290b9a`, built with ZMQ and AVX-512
  disabled.
- One PDU session: PASS, SST1/DNN `srsapn`, TUN `10.45.1.3/24`.
- Two PDU sessions: PASS, SST1 `10.45.1.6/24` and SST2 `10.45.2.3/24` in
  the final verification run.
- User plane: PASS. Both UPF gateways answered 3/3 packets with 0% loss.
- All Kubernetes workloads remained healthy after the test, and no gNB, UE,
  or srsUE process was left running.

## Configuration scope

The successful run uses the OCUDU tutorial's isolated ZMQ/TDD profile:
band n78, 20 MHz, SCS 30 kHz, 23.04 Msps and DL NR-ARFCN 632628. It does not
replace the lab's original FDD band-3 configuration.

The original `gnb1.yaml` and `uetest.conf` hashes match the pre-test snapshot:

- `gnb1.yaml`: `b8fa3f6ac6952ddb1810f4ed01374a1e7c43f96b2ebed3f6c801940faf06e9d2`
- `uetest.conf`: `a79d7f3bdd42e4702bb09984bb214617fb774b5b222d5cc598b2401fe0d9c3dc`

The original FDD band-3 profile reached RRC Connected but OCUDU could not
decode the post-Msg3 PUSCH carrying RRCSetupComplete. That profile therefore
did not reach the AMF and is retained only as an interoperability diagnostic.

## Multi-PDU subscriber patch

Duranta `2026.w25` does not put every configured PDU-session S-NSSAI into the
initial Requested NSSAI. With only SST1 marked as a default subscription, the
AMF returned SST1 as the sole Allowed NSSAI and the UE suppressed its SST2
request. `enable-sst2-default.js` changes only SST2 `default_indicator` from
`false` to `true`. The original complete subscriber document is backed up on
the lab as a private mode-0600 artifact. `reset-sst2-default.js` restores the
original flag.

No authentication material is included in this downloadable bundle.
