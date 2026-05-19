import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from models import MuxEp, MuxPort
from port_allocations import AUTO_PORT
from reconcile import (
    build_generated_endpoints_metadata,
    build_mux_ports,
    channel_refs,
    collect_auto_allocation_keys,
    collect_existing_port_owners,
    collect_static_port_claims,
    count_ready_channel_pods,
    effective_mux_max_ports,
    find_mux_port_conflicts,
    get_current_endpoints_set,
    get_old_endpoints_set,
    gke_max_ports_warning,
    is_gke_load_balancer_service,
    parse_external_ports_annotation,
    port_hash,
    process_channel_ports,
    requested_max_ports,
    would_exceed_mux_port_limit,
)


class ReconcileHelperTest(unittest.TestCase):
    def test_port_hash_is_stable_and_short(self):
        first = port_hash("app", "api", "http")
        second = port_hash("app", "api", "http")

        self.assertEqual(first, second)
        self.assertEqual(len(first), 7)

    def test_build_mux_ports_uses_placeholder_when_empty(self):
        self.assertEqual(
            build_mux_ports(set()),
            [{"name": "placeholder", "port": 101, "protocol": "TCP"}],
        )

    def test_build_mux_ports_filters_placeholder_when_real_ports_exist(self):
        ports = {
            MuxPort("placeholder", 101, "TCP"),
            MuxPort("abc1234", 30080, "TCP"),
        }

        self.assertEqual(
            build_mux_ports(ports),
            [{"name": "abc1234", "port": 30080, "protocol": "TCP"}],
        )



    def test_requested_max_ports_parses_annotation(self):
        mux = FakeMux(annotations={"svc-mux.nowake.ai/max-ports": "100"})

        self.assertEqual(requested_max_ports(mux), 100)

    def test_requested_max_ports_rejects_invalid_values(self):
        mux = FakeMux(annotations={"svc-mux.nowake.ai/max-ports": "0"})

        with self.assertRaisesRegex(ValueError, "expected >= 1"):
            requested_max_ports(mux)

    def test_gke_mux_is_detected_from_load_balancer_class(self):
        mux = FakeMux(spec={"loadBalancerClass": "networking.gke.io/l4-regional-external"})

        self.assertTrue(is_gke_load_balancer_service(mux))

    def test_gke_mux_is_detected_from_provider_annotations(self):
        mux = FakeMux(annotations={"cloud.google.com/l4-rbs": "enabled"})

        self.assertTrue(is_gke_load_balancer_service(mux))

    def test_effective_mux_max_ports_applies_gke_default(self):
        mux = FakeMux(spec={"loadBalancerClass": "networking.gke.io/l4-regional-external"})

        self.assertEqual(effective_mux_max_ports(mux), 100)
        self.assertIn("applying the GKE", gke_max_ports_warning(mux))

    def test_effective_mux_max_ports_caps_gke_overrides(self):
        mux = FakeMux(
            annotations={"svc-mux.nowake.ai/max-ports": "200"},
            spec={"loadBalancerClass": "networking.gke.io/l4-regional-external"},
        )

        self.assertEqual(effective_mux_max_ports(mux), 100)
        self.assertIn("using the GKE", gke_max_ports_warning(mux))

    def test_effective_mux_max_ports_keeps_lower_gke_override(self):
        mux = FakeMux(
            annotations={"svc-mux.nowake.ai/max-ports": "50"},
            spec={"loadBalancerClass": "networking.gke.io/l4-regional-external"},
        )

        self.assertEqual(effective_mux_max_ports(mux), 50)
        self.assertIsNone(gke_max_ports_warning(mux))

    def test_would_exceed_mux_port_limit_counts_channel_ports(self):
        current = {MuxPort("a", 20000, "TCP")}
        channel = channel_service(
            ports=[
                {"name": "http", "port": 80, "protocol": "TCP"},
                {"name": "grpc", "port": 81, "protocol": "TCP"},
            ]
        )

        self.assertTrue(would_exceed_mux_port_limit(current, channel, 2))
        self.assertFalse(would_exceed_mux_port_limit(current, channel, 3))

    def test_find_mux_port_conflicts_does_not_commit_conflicting_channel(self):
        channel = channel_service(
            name="api",
            ports=[
                {"name": "http", "port": 80, "protocol": "TCP"},
                {"name": "admin", "port": 80, "protocol": "TCP"},
            ],
        )
        owners = {}
        mux_ports = {
            MuxPort(port_hash("app", "api", "http"), 80, "TCP"),
            MuxPort(port_hash("app", "api", "admin"), 80, "TCP"),
        }

        conflicts = find_mux_port_conflicts(owners, channel, mux_ports)

        self.assertEqual(len(conflicts), 1)
        self.assertEqual(owners, {})

    def test_parse_external_ports_annotation(self):
        self.assertEqual(
            parse_external_ports_annotation("http:443, grpc:8443"),
            {"http": 443, "grpc": 8443},
        )

    def test_parse_external_ports_annotation_supports_auto(self):
        self.assertEqual(
            parse_external_ports_annotation("http:auto"), {"http": AUTO_PORT}
        )

    def test_parse_external_ports_annotation_rejects_bad_port(self):
        with self.assertRaises(ValueError):
            parse_external_ports_annotation("http:not-a-port")

    def test_process_channel_ports_defaults_to_service_port(self):
        channel = channel_service(
            ports=[
                {
                    "name": "http",
                    "port": 80,
                    "targetPort": 8080,
                    "protocol": "TCP",
                }
            ]
        )

        ports, annotation, service = process_channel_ports(
            channel, None, service_factory=FakeService
        )

        self.assertEqual(service.namespace, "app")
        self.assertEqual(ports, {MuxPort(port_hash("app", "api", "http"), 80, "TCP")})
        self.assertEqual(annotation, "http:80->80")

    def test_process_channel_ports_uses_explicit_external_port(self):
        channel = channel_service(
            annotations={"svc-mux.nowake.ai/external-ports": "http:443"},
            ports=[
                {
                    "name": "http",
                    "port": 80,
                    "targetPort": 8080,
                    "protocol": "TCP",
                }
            ],
        )

        ports, annotation, _ = process_channel_ports(
            channel, None, service_factory=FakeService
        )

        self.assertEqual(ports, {MuxPort(port_hash("app", "api", "http"), 443, "TCP")})
        self.assertEqual(annotation, "http:80->443")

    def test_process_channel_ports_allows_same_number_for_tcp_and_udp(self):
        channel = channel_service(
            ports=[
                {"name": "dns-tcp", "port": 53, "protocol": "TCP"},
                {"name": "dns-udp", "port": 53, "protocol": "UDP"},
            ]
        )

        ports, annotation, _ = process_channel_ports(
            channel, None, service_factory=FakeService
        )

        self.assertEqual(
            ports,
            {
                MuxPort(port_hash("app", "api", "dns-tcp"), 53, "TCP"),
                MuxPort(port_hash("app", "api", "dns-udp"), 53, "UDP"),
            },
        )
        self.assertEqual(annotation, "dns-tcp:53->53, dns-udp:53->53")

    def test_port_name_change_changes_stable_mux_port_name(self):
        before, _, _ = process_channel_ports(
            channel_service(ports=[{"name": "http", "port": 80, "protocol": "TCP"}]),
            None,
            service_factory=FakeService,
        )
        after, _, _ = process_channel_ports(
            channel_service(ports=[{"name": "web", "port": 80, "protocol": "TCP"}]),
            None,
            service_factory=FakeService,
        )

        self.assertNotEqual(
            {port.name for port in before}, {port.name for port in after}
        )

    def test_service_rename_changes_stable_mux_port_name(self):
        before, _, _ = process_channel_ports(
            channel_service(name="api"),
            None,
            service_factory=FakeService,
        )
        after, _, _ = process_channel_ports(
            channel_service(name="renamed-api"),
            None,
            service_factory=FakeService,
        )

        self.assertNotEqual(
            {port.name for port in before}, {port.name for port in after}
        )

    def test_process_channel_ports_allocates_auto_external_port(self):
        channel = channel_service(
            annotations={"svc-mux.nowake.ai/external-ports": "http:auto"},
            ports=[{"name": "http", "port": 80, "protocol": "TCP"}],
        )
        allocator = FakeAllocator(30000)

        ports, annotation, _ = process_channel_ports(
            channel, None, service_factory=FakeService, allocator=allocator
        )

        self.assertEqual(ports, {MuxPort(port_hash("app", "api", "http"), 30000, "TCP")})
        self.assertEqual(annotation, "http:80->30000")

    def test_process_channel_ports_requires_allocator_for_auto_external_port(self):
        channel = channel_service(
            annotations={"svc-mux.nowake.ai/external-ports": "http:auto"},
            ports=[{"name": "http", "port": 80, "protocol": "TCP"}],
        )

        with self.assertRaises(ValueError):
            process_channel_ports(channel, None, service_factory=FakeService)

    def test_collect_port_claims_split_static_and_auto_ports(self):
        channels = [
            channel_service(name="api"),
            channel_service(
                name="admin",
                annotations={"svc-mux.nowake.ai/external-ports": "http:auto"},
            ),
        ]

        self.assertEqual(collect_static_port_claims(channels), {(80, "TCP")})
        self.assertEqual(len(collect_auto_allocation_keys(channels)), 1)

    def test_process_channel_ports_rejects_unknown_explicit_port_name(self):
        channel = channel_service(
            annotations={"svc-mux.nowake.ai/external-ports": "grpc:8443"},
            ports=[{"name": "http", "port": 80, "protocol": "TCP"}],
        )

        with self.assertRaises(ValueError):
            process_channel_ports(channel, None, service_factory=FakeService)

    def test_process_channel_ports_rejects_unnamed_port(self):
        channel = channel_service(ports=[{"port": 80, "protocol": "TCP"}])

        with self.assertRaisesRegex(ValueError, "ports must be named"):
            process_channel_ports(channel, None, service_factory=FakeService)

    def test_port_claim_collectors_skip_invalid_channels(self):
        valid_auto = channel_service(
            name="auto",
            annotations={"svc-mux.nowake.ai/external-ports": "http:auto"},
        )
        valid_static = channel_service(name="static")
        invalid_annotation = channel_service(
            name="bad-port",
            annotations={"svc-mux.nowake.ai/external-ports": "http:70000"},
        )
        invalid_name = channel_service(
            name="bad-name",
            annotations={"svc-mux.nowake.ai/external-ports": "grpc:8443"},
        )
        unnamed = channel_service(name="unnamed", ports=[{"port": 80}])

        channels = [valid_auto, valid_static, invalid_annotation, invalid_name, unnamed]

        self.assertEqual(collect_static_port_claims(channels), {(80, "TCP")})
        self.assertEqual(len(collect_auto_allocation_keys(channels)), 1)

    def test_existing_mux_port_owner_is_preserved_across_reconcile(self):
        existing = channel_service(name="z-existing")
        newcomer = channel_service(name="a-new")
        existing_mux_name = port_hash("app", "z-existing", "http")
        existing_ports = {MuxPort(existing_mux_name, 80, "TCP")}
        owners = collect_existing_port_owners([newcomer, existing], existing_ports)

        self.assertEqual(owners, {(80, "TCP"): f"app/z-existing:{existing_mux_name}"})
        newcomer_ports = {MuxPort(port_hash("app", "a-new", "http"), 80, "TCP")}
        conflicts = find_mux_port_conflicts(owners, newcomer, newcomer_ports)

        self.assertEqual(len(conflicts), 1)
        self.assertIn("app/z-existing", conflicts[0])

    def test_deleted_existing_mux_port_owner_is_not_preserved(self):
        existing_mux_name = port_hash("app", "deleted", "http")
        existing_ports = {MuxPort(existing_mux_name, 80, "TCP")}

        self.assertEqual(collect_existing_port_owners([], existing_ports), {})

    def test_channel_port_annotation_preserves_owner_when_mux_has_placeholder(self):
        existing = channel_service(
            name="z-existing",
            annotations={"svc-mux.nowake.ai/ports": "http:80->80"},
        )
        newcomer = channel_service(name="a-new")
        owners = collect_existing_port_owners(
            [newcomer, existing],
            {MuxPort("placeholder", 101, "TCP")},
        )
        existing_mux_name = port_hash("app", "z-existing", "http")

        self.assertEqual(owners, {(80, "TCP"): f"app/z-existing:{existing_mux_name}"})
        conflicts = find_mux_port_conflicts(
            owners,
            newcomer,
            {MuxPort(port_hash("app", "a-new", "http"), 80, "TCP")},
        )

        self.assertEqual(len(conflicts), 1)
        self.assertIn("app/z-existing", conflicts[0])

    def test_ambiguous_channel_port_annotations_do_not_seed_owner(self):
        first = channel_service(
            name="first",
            annotations={"svc-mux.nowake.ai/ports": "http:80->80"},
        )
        second = channel_service(
            name="second",
            annotations={"svc-mux.nowake.ai/ports": "http:80->80"},
        )

        self.assertEqual(
            collect_existing_port_owners([first, second], {MuxPort("placeholder", 101, "TCP")}),
            {},
        )

    def test_find_mux_port_conflicts_detects_duplicate_port_protocol(self):
        first = channel_service(name="api")
        second = channel_service(name="admin")
        owners = {}
        first_ports = {MuxPort(port_hash("app", "api", "http"), 80, "TCP")}
        second_ports = {MuxPort(port_hash("app", "admin", "http"), 80, "TCP")}

        self.assertEqual(find_mux_port_conflicts(owners, first, first_ports), [])
        conflicts = find_mux_port_conflicts(owners, second, second_ports)

        self.assertEqual(len(conflicts), 1)
        self.assertIn("80/TCP", conflicts[0])

    def test_find_mux_port_conflicts_allows_same_port_on_different_protocols(self):
        first = channel_service(name="dns-tcp")
        second = channel_service(name="dns-udp")
        owners = {}
        first_ports = {MuxPort(port_hash("app", "dns-tcp", "dns"), 53, "TCP")}
        second_ports = {MuxPort(port_hash("app", "dns-udp", "dns"), 53, "UDP")}

        self.assertEqual(find_mux_port_conflicts(owners, first, first_ports), [])
        self.assertEqual(find_mux_port_conflicts(owners, second, second_ports), [])
        self.assertEqual(set(owners), {(53, "TCP"), (53, "UDP")})

    def test_endpoint_sets_include_ready_and_not_ready(self):
        endpoint = {
            "subsets": [
                {
                    "addresses": [{"ip": "10.0.0.1"}],
                    "notReadyAddresses": [{"ip": "10.0.0.2"}],
                    "ports": [{"port": 30080, "protocol": "TCP"}],
                }
            ]
        }

        ready, not_ready = get_old_endpoints_set(endpoint)

        self.assertEqual(ready, {MuxEp("10.0.0.1", 30080, "TCP")})
        self.assertEqual(not_ready, {MuxEp("10.0.0.2", 30080, "TCP")})
        self.assertEqual(get_current_endpoints_set(endpoint), (ready, not_ready))

    def test_channel_refs_are_stable_sorted_namespaced_names(self):
        channels = [
            channel_service(name="b"),
            channel_service(name="a", namespace="other"),
        ]

        self.assertEqual(channel_refs(channels), ["app/b", "other/a"])

    def test_build_generated_endpoints_metadata_marks_controller_ownership(self):
        labels, annotations = build_generated_endpoints_metadata(
            {"existing": "label"},
            ("svc-mux", "mux"),
            [channel_service(name="api")],
        )

        self.assertEqual(labels["existing"], "label")
        self.assertEqual(labels["app.kubernetes.io/managed-by"], "svc-lb-mux")
        self.assertEqual(labels["app.kubernetes.io/component"], "mux-endpoints")
        self.assertEqual(annotations["svc-mux.nowake.ai/managed"], "true")
        self.assertEqual(annotations["svc-mux.nowake.ai/mux"], "svc-mux/mux")
        self.assertEqual(annotations["svc-mux.nowake.ai/channels"], '["app/api"]')

    def test_count_ready_channel_pods(self):
        class Memo:
            endpoints = {
                ("app", "api"): {
                    "subsets": [
                        {"addresses": [{"ip": "10.0.0.1"}, {"ip": "10.0.0.2"}]},
                        {"notReadyAddresses": [{"ip": "10.0.0.3"}]},
                    ]
                }
            }

        channels = [{"metadata": {"namespace": "app", "name": "api"}}]

        self.assertEqual(count_ready_channel_pods(channels, Memo()), 2)


if __name__ == "__main__":
    unittest.main()


def channel_service(name="api", namespace="app", annotations=None, ports=None):
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "namespace": namespace,
            "name": name,
            "annotations": annotations or {},
        },
        "spec": {
            "type": "LoadBalancer",
            "ports": ports
            or [
                {
                    "name": "http",
                    "port": 80,
                    "targetPort": 8080,
                    "protocol": "TCP",
                }
            ],
        },
    }



class FakeMux:
    def __init__(self, annotations=None, spec=None):
        self.annotations = annotations or {}
        self.spec = spec or {}

class FakeService:
    def __init__(self, body):
        self.namespace = body["metadata"]["namespace"]
        self.name = body["metadata"]["name"]


class FakeAllocator:
    def __init__(self, port):
        self.port = port

    def allocate(self, channel, port):
        return self.port
