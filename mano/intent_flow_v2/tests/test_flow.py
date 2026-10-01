import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from intent_flow import (  # noqa: E402
    Orchestrator,
    classify_intent,
    nrm_overrides_from_configuration,
)
from renderer import (  # noqa: E402
    render_nrm_package,
    render_packages,
    validate_source_packages,
)
from webui_overlay.ui_route.intent_payload import build_runtime_intent  # noqa: E402


class ClassificationTests(unittest.TestCase):
    def test_explicit_type_wins(self):
        kind, reasons = classify_intent(
            {"slice_type": "eMBB", "latency_ms": 1}
        )
        self.assertEqual("embb", kind)
        self.assertEqual(["explicit slice_type"], reasons)

    def test_low_latency_is_urllc(self):
        kind, reasons = classify_intent({"latency_ms": 5})
        self.assertEqual("urllc", kind)
        self.assertTrue(reasons)

    def test_video_is_embb(self):
        kind, _ = classify_intent({"service": "4K video streaming"})
        self.assertEqual("embb", kind)

    def test_mmtc_is_supported(self):
        kind, _ = classify_intent({"slice_type": "mMTC"})
        self.assertEqual("mmtc", kind)


class SdAllocatorTests(unittest.TestCase):
    def test_first_free_is_scoped_to_sst(self):
        orchestrator = Orchestrator.__new__(Orchestrator)
        orchestrator.active_snssai_pairs = lambda: {
            (1, "000001"),
            (1, "000003"),
            (2, "000001"),
        }
        state = {
            "slices": {
                "slice-9": {
                    "sst": 1,
                    "sd": "000004",
                    "status": "READY",
                }
            }
        }
        self.assertEqual(2, orchestrator.first_free_sd(1, state))
        self.assertEqual(2, orchestrator.first_free_sd(2, state))
        self.assertEqual(1, orchestrator.first_free_sd(3, state))

    def test_explicit_slot_is_used_for_stable_pdu_mapping(self):
        orchestrator = Orchestrator.__new__(Orchestrator)
        orchestrator.max_slots = 10
        orchestrator.occupied_slots = lambda _state: {1: "slice-1"}
        self.assertEqual(
            3,
            orchestrator.choose_requested_slot({"slot": 3}, {"slices": {}}),
        )

    def test_explicit_slot_refuses_to_overwrite_active_slice(self):
        orchestrator = Orchestrator.__new__(Orchestrator)
        orchestrator.max_slots = 10
        orchestrator.occupied_slots = lambda _state: {2: "slice-2"}
        with self.assertRaisesRegex(RuntimeError, "already occupied"):
            orchestrator.choose_requested_slot({"slot": 2}, {"slices": {}})


class RuntimeSlaPlanTests(unittest.TestCase):
    def orchestrator(self):
        orchestrator = Orchestrator.__new__(Orchestrator)
        orchestrator.prefix = "slice-"
        orchestrator.config = {
            "profiles": {
                "embb": {
                    "source_vnf_template_id": "vnf-source",
                    "source_nsd_template_id": "nsd-source",
                    "nrm_template_id": "nrm-source",
                    "sst": 1,
                    "ipv4_subnet": "10.45.{slot}.0/24",
                    "ipv4_gateway": "10.45.{slot}.1",
                }
            }
        }
        orchestrator.choose_requested_slot = lambda _intent, _state: 7
        orchestrator.first_free_sd = lambda _sst, _state: 7
        return orchestrator

    def test_plan_carries_policy_bounded_deployment_configuration(self):
        intent = {
            "intent_id": "intent-video-high",
            "slice_type": "embb",
            "technical_sla": {
                "min_throughput_mbps": 20.0,
                "target_throughput_mbps": 80.0,
                "peak_throughput_mbps": 80.0,
                "max_latency_ms": 100.0,
                "max_jitter_ms": 30.0,
                "max_packet_loss_percent": 1.0,
                "min_reliability_percent": 99.9,
                "expected_sessions": 20,
            },
            "deployment_policy": {
                "template_family": "embb",
                "dnn": "srsapn",
                "five_qi": 8,
                "arp_priority": 4,
                "session_ambr_mbps": 80.0,
                "downlink_limit_mbps": 80.0,
                "evidence_status": "DECLARED",
            },
        }

        plan, _ = self.orchestrator().make_plan(intent, {"slices": {}})

        self.assertEqual(20.0, plan.deployment_configuration["min_throughput_mbps"])
        self.assertEqual(80.0, plan.deployment_configuration["peak_throughput_mbps"])
        self.assertEqual(8, plan.deployment_configuration["five_qi"])
        self.assertEqual("DECLARED", plan.deployment_configuration["evidence_status"])

    def test_nrm_overrides_are_derived_from_normalized_configuration(self):
        overrides = nrm_overrides_from_configuration(
            {
                "target_throughput_mbps": 80.0,
                "peak_throughput_mbps": 80.0,
                "max_latency_ms": 100.0,
                "max_jitter_ms": 30.0,
                "max_packet_loss_percent": 1.0,
                "min_reliability_percent": 99.9,
            }
        )

        self.assertEqual("80 Mbps", overrides["expDataRateDL"])
        self.assertEqual("100 ms", overrides["latency"])
        self.assertEqual("99.9%", overrides["reliability"])

    def test_web_adapter_preserves_translated_sla_and_policy(self):
        payload = {
            "slice_type": "embb",
            "sst": 1,
            "technical_sla": {"peak_throughput_mbps": 80.0},
            "deployment_policy": {
                "template_family": "embb",
                "five_qi": 8,
            },
            "service_intent": {"service_type": "live_video"},
        }

        intent = build_runtime_intent(payload, intent_id="slice-intent-abc")

        self.assertEqual("slice-intent-abc", intent["intent_id"])
        self.assertEqual(payload["technical_sla"], intent["technical_sla"])
        self.assertEqual(payload["deployment_policy"], intent["deployment_policy"])
        self.assertEqual(payload["service_intent"], intent["service_intent"])


class DeploymentStrategyTests(unittest.TestCase):
    @patch("intent_flow.run")
    def test_smf_and_upf_are_patched_to_recreate(self, mocked_run):
        deployments = {
            "items": [
                {
                    "metadata": {"name": "generated-smf"},
                    "spec": {"strategy": {"type": "RollingUpdate"}},
                },
                {
                    "metadata": {"name": "generated-upf"},
                    "spec": {"strategy": {"type": "RollingUpdate"}},
                },
                {
                    "metadata": {"name": "generated-pcf"},
                    "spec": {"strategy": {"type": "RollingUpdate"}},
                },
            ]
        }

        def response(command, **_kwargs):
            if command[:3] == ["kubectl", "get", "deployments"]:
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(deployments),
                    stderr="",
                )
            if command[:3] == ["kubectl", "patch", "deployment"]:
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if command[:3] == ["kubectl", "get", "deployment"]:
                return SimpleNamespace(
                    returncode=0,
                    stdout="Recreate",
                    stderr="",
                )
            raise AssertionError(command)

        mocked_run.side_effect = response
        orchestrator = Orchestrator.__new__(Orchestrator)
        patched = orchestrator.ensure_pfcp_recreate_strategy("slice-4")

        self.assertEqual(["generated-smf", "generated-upf"], patched)
        patch_calls = [
            call.args[0]
            for call in mocked_run.call_args_list
            if call.args[0][:3] == ["kubectl", "patch", "deployment"]
        ]
        self.assertEqual(2, len(patch_calls))
        self.assertTrue(
            all('"type": "Recreate"' in command[-1] for command in patch_calls)
        )


class RendererTests(unittest.TestCase):
    def write_zip(self, path: Path, members: dict[str, str]) -> None:
        with zipfile.ZipFile(path, "w") as archive:
            for name, content in members.items():
                archive.writestr(name, content)

    def test_source_validation_requires_exact_vnf_nsd_id_match(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vnf_zip = root / "vnf.zip"
            nsd_zip = root / "nsd.zip"
            self.write_zip(vnf_zip, {
                "smf/Definitions/smf.yaml": "descriptor_id: source-smf\n",
                "upf/Definitions/upf.yaml": "descriptor_id: source-upf\n",
            })
            self.write_zip(nsd_zip, {
                "Definitions/ns.yaml": (
                    "descriptor_id: source-nsd\n"
                    "- vnfd_id: source-smf\n- vnfd_id: different-upf\n"
                )
            })
            with self.assertRaisesRegex(ValueError, "unknown VNF IDs"):
                validate_source_packages(nsd_zip=nsd_zip, vnf_zip=vnf_zip)

    def test_source_validation_accepts_matching_vnf_nsd_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vnf_zip = root / "vnf.zip"
            nsd_zip = root / "nsd.zip"
            self.write_zip(vnf_zip, {
                "smf/Definitions/smf.yaml": "descriptor_id: source-smf\n",
                "upf/Definitions/upf.yaml": "descriptor_id: source-upf\n",
            })
            self.write_zip(nsd_zip, {
                "Definitions/ns.yaml": (
                    "descriptor_id: source-nsd\n"
                    "- vnfd_id: source-smf\n- vnfd_id: source-upf\n"
                )
            })
            result = validate_source_packages(nsd_zip=nsd_zip, vnf_zip=vnf_zip)
            self.assertEqual("MATCHED", result["status"])
            self.assertEqual(result["vnf_descriptor_ids"], result["nsd_vnfd_ids"])

    def test_render_rewrites_identity_namespace_and_slice_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vnf_zip = root / "vnf.zip"
            nsd_zip = root / "nsd.zip"
            self.write_zip(
                vnf_zip,
                {
                    "smf/Definitions/smf.yaml": """
descriptor_id: old-smf
namespace: slice-1
""",
                    "smf/Files/Artifacts/smfcfg.conf": """
smf:
  sbi:
    server:
      - address: 0.0.0.0
        advertise: smf-svc.slice-1.svc.cluster.local
  pfcp:
    server:
      - address: 0.0.0.0
- subnet: 10.45.1.0/24
  gateway: 10.45.1.1
- subnet: 2001:db8:1::/48
  gateway: 2001:db8:1::1
- sst: 1
  sd: "000001"
""",
                    "upf/Definitions/upf.yaml": """
descriptor_id: old-upf
namespace: slice-1
attributes:
  name_of_service: upf-svc
  is_export_service: True
""",
                    "upf/Files/Artifacts/upfcfg.conf": """
upf:
  pfcp:
    server:
      - address: 0.0.0.0
  gtpu:
    server:
      - address: 0.0.0.0
  session:
    - subnet: 10.45.0.0/16
      gateway: 10.45.0.1
command: ip addr add 10.45.0.1/16 dev ogstun
""",
                },
            )
            self.write_zip(
                nsd_zip,
                {
                    "Definitions/ns.yaml": """
descriptor_id: old-nsd
- vnfd_id: old-smf
- vnfd_id: old-upf
"""
                },
            )
            manifest = render_packages(
                nsd_zip=nsd_zip,
                vnf_zip=vnf_zip,
                namespace="slice-3",
                slot=3,
                slice_type="urllc",
                sst=2,
                sd="000002",
                ipv4_subnet="10.45.3.0/24",
                ipv4_gateway="10.45.3.1",
                ipv6_subnet="2001:db8:3::/48",
                ipv6_gateway="2001:db8:3::1",
                output_root=root / "out",
            )
            with zipfile.ZipFile(manifest["outputs"]["vnf_zip"]) as archive:
                config = archive.read(
                    "smf/Files/Artifacts/smfcfg.conf"
                ).decode()
                definition = archive.read(
                    "smf/Definitions/smf.yaml"
                ).decode()
            self.assertIn("namespace: slice-3", definition)
            self.assertNotIn("old-smf", definition)
            self.assertTrue(
                manifest["descriptors"]["vnf"][0]["new_id"].endswith("smf")
            )
            self.assertEqual(
                "smf", manifest["descriptors"]["vnf"][0]["component"]
            )
            self.assertIn("smf-svc.slice-3.svc.cluster.local", config)
            self.assertEqual(
                1,
                config.count("smf-svc.slice-3.svc.cluster.local"),
            )
            self.assertIn("  pfcp:\n    server:\n      - dev: eth0", config)
            self.assertIn("10.45.3.0/24", config)
            self.assertIn("10.45.3.1", config)
            self.assertIn("2001:db8:3::/48", config)
            self.assertIn("2001:db8:3::1", config)
            self.assertIn("sst: 2", config)
            self.assertIn('sd: "000002"', config)
            with zipfile.ZipFile(manifest["outputs"]["vnf_zip"]) as archive:
                upf_config = archive.read(
                    "upf/Files/Artifacts/upfcfg.conf"
                ).decode()
            self.assertIn("  gtpu:\n    server:\n      - dev: eth0", upf_config)
            self.assertIn("subnet: 10.45.3.0/24", upf_config)
            self.assertIn("gateway: 10.45.3.1", upf_config)
            self.assertIn("10.45.3.1/24 dev ogstun", upf_config)
            self.assertNotIn("10.45.0.0/16", upf_config)
            json.dumps(manifest)

    def test_render_makes_upf_service_headless(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vnf_zip = root / "vnf.zip"
            nsd_zip = root / "nsd.zip"
            self.write_zip(
                vnf_zip,
                {
                    "upf/Definitions/upf.yaml": """
descriptor_id: old-upf
namespace: slice-1
attributes:
  name_of_service: upf-svc
  is_export_service: True
""",
                },
            )
            self.write_zip(
                nsd_zip,
                {
                    "Definitions/ns.yaml": """
descriptor_id: old-nsd
- vnfd_id: old-upf
"""
                },
            )
            manifest = render_packages(
                nsd_zip=nsd_zip,
                vnf_zip=vnf_zip,
                namespace="slice-2",
                slot=2,
                slice_type="urllc",
                sst=2,
                sd="000001",
                ipv4_subnet="10.45.2.0/24",
                ipv4_gateway="10.45.2.1",
                ipv6_subnet=None,
                ipv6_gateway=None,
                output_root=root / "out",
            )
            with zipfile.ZipFile(manifest["outputs"]["vnf_zip"]) as archive:
                definition = archive.read(
                    "upf/Definitions/upf.yaml"
                ).decode()
            self.assertIn("headless_service: True", definition)
            self.assertIn("publish_not_ready_addresses: True", definition)

    def test_render_nrm_sets_snssai_and_performance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nrm_zip = root / "nrm.zip"
            self.write_zip(
                nrm_zip,
                {
                    "sliceNrm.json": json.dumps({
                        "definitions": {
                            "SnssaiList": {
                                "sst": {
                                    "value": "1",
                                    "type": "eMBB",
                                    "characteristics": "old",
                                },
                                "sd": "000001",
                            },
                            "PrefReq": {"properties": {"userDensity": "old"}},
                        }
                    })
                },
            )
            result = render_nrm_package(
                nrm_zip=nrm_zip,
                run_dir=root / "rendered",
                namespace="slice-4",
                sst=3,
                sd="000001",
                service_type="mMTC",
                characteristics="Massive IoT",
                performance_overrides={"userDensity": "1 000 000/km2"},
            )
            with zipfile.ZipFile(result["output"]) as archive:
                document = json.loads(archive.read("sliceNrm.json"))
            snssai = document["definitions"]["SnssaiList"]
            self.assertEqual("3", snssai["sst"]["value"])
            self.assertEqual("mMTC", snssai["sst"]["type"])
            self.assertEqual("000001", snssai["sd"])
            self.assertEqual(
                "1 000 000/km2",
                document["definitions"]["PrefReq"]["properties"]["userDensity"],
            )


if __name__ == "__main__":
    unittest.main()
