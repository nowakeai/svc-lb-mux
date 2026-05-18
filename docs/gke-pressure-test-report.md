# GKE Pressure Test Report

Date: 2026-05-18 UTC

This report summarizes a live GKE validation of Service LoadBalancer Multiplexer with one mux Service, 100 channel Services, and 100 distinct backend pods. Identifiers are partially redacted so the report is useful without exposing full project details.

## Objective

Validate that a single GKE-backed mux can:

- bind a reserved static external IPv4 address;
- stay inside the GKE-native 100 Service port limit;
- rely on GKE-managed forwarding and firewall resources;
- expose 100 channel Services through one cloud load balancer;
- route each external port to the correct backend pod, not merely accept TCP connections;
- reject additional channels after the mux reaches the configured GKE port limit.

## Environment

| Item | Value |
| --- | --- |
| Kubernetes provider | Google Kubernetes Engine |
| Region | `us-west1` |
| Project | redacted |
| Cluster | redacted |
| Controller image | `ghcr.io/nowakeai/svc-lb-mux:v0.2.4-alpha.5` |
| Helm release | `svc-mux-eip` |
| Controller namespace | `svc-mux-eip` |
| Test namespace | `svc-mux-eip-test` |
| Mux Service | `mux-eip` |
| API prefix | `svc-mux.nowake.ai` |
| Static IP resource | redacted, status `IN_USE` |
| External IP | redacted, same reserved IPv4 used throughout the test |

## Mux Configuration

```yaml
defaultLoadBalancer:
  name: mux-eip
  annotations:
    cloud.google.com/l4-rbs: "enabled"
    networking.gke.io/load-balancer-ip-addresses: <redacted-address-resource>
  loadBalancerClass: networking.gke.io/l4-regional-external
  allocateLoadBalancerNodePorts: true
  portRange: "20000-20099"
  maxPorts: 100
  allocationConfigMapName: mux-eip-port-allocations
```

The channel Services used:

```yaml
spec:
  type: LoadBalancer
  loadBalancerClass: svc-mux.nowake.ai/mux-eip.svc-mux-eip
  allocateLoadBalancerNodePorts: false
  ports:
    - name: http
      port: 8080
      targetPort: http
metadata:
  annotations:
    svc-mux.nowake.ai/external-ports: http:auto
```

## Generated GCP Load Balancer Shape

GKE created a backend service-based external passthrough Network Load Balancer.

```text
reserved static IPv4
  -> regional forwarding rule, TCP, portRange 20000-20099
  -> regional backend service, protocol TCP
  -> zonal GCE_VM_IP network endpoint group
  -> GKE nodes
  -> Kubernetes Service routing
  -> controller-managed mux Endpoints
  -> channel backend pods
```

Observed resource properties, with names redacted where appropriate:

| Resource | Observed configuration |
| --- | --- |
| Forwarding rule | regional, `EXTERNAL`, `TCP`, `PREMIUM` network tier, `portRange: 20000-20099` |
| Backend service | regional, `EXTERNAL`, `TCP`, balancing mode `CONNECTION`, locality policy `MAGLEV`, timeout `30s` |
| Health check | regional shared GKE L4 health check, HTTP `:10256/healthz` |
| NEG | zonal `GCE_VM_IP`, size `9` during the test |
| Firewall rule | GKE-managed ingress rule, destination is the reserved mux IP, source `0.0.0.0/0`, allowed TCP ports `20000-20099` |
| Static IP | regional external IPv4, status `IN_USE`, attached to the forwarding rule |

No user-managed firewall rule was required after switching to the GKE-native model.

## Test Workload

The pressure workload was generated with `test-local/gke-pressure.py`.

It created:

- 100 Deployments;
- 100 backend pods;
- 100 channel Services;
- one unique response body per backend, using the pattern `svc-lb-mux-channel-<index>`.

The mux allocation ConfigMap contained 100 persisted assignments. The mux Service exposed 100 TCP ports in the configured range.

## Validation Commands

Representative commands:

```console
kubectl rollout status deployment/svc-mux-eip -n svc-mux-eip --timeout=180s
kubectl wait --for=condition=Available deployment \
  -l app.kubernetes.io/name=svc-lb-mux-pressure \
  -n svc-mux-eip-test \
  --timeout=300s

kubectl get svc mux-eip -n svc-mux-eip \
  -o jsonpath='{.metadata.annotations.svc-mux\.nowake\.ai/summary}'

kubectl get configmap mux-eip-port-allocations \
  -n svc-mux-eip \
  -o jsonpath='{.data.allocations\.json}' > /tmp/mux-eip-allocations.json

test-local/gke-pressure.py probe \
  --host <redacted-static-ip> \
  --allocations-json /tmp/mux-eip-allocations.json \
  --namespace svc-mux-eip-test \
  --timeout 5
```

## Results

| Check | Result |
| --- | --- |
| Controller rollout | Passed |
| 100 backend Deployments available | Passed |
| Allocation ConfigMap entries | 100 |
| Mux Service ports | 100 |
| GKE forwarding rule port range | `20000-20099` |
| GKE-managed firewall coverage | TCP `20000-20099` |
| Public HTTP content probe | `ok=100 missing=0 failed=0` |
| Additional channel above limit | rejected with `MuxPortLimitExceeded` |
| Missing `max-ports` on detected GKE mux | Warning event `GkePortLimitApplied` observed |

The content probe validates that every allocated external port routed to the expected backend response body. This is stronger than a TCP-only probe because it verifies the channel-to-port-to-backend mapping.

## Notes And Limits

- The GKE-native target for one mux is 100 one-port channel mappings because GKE LoadBalancer Services support up to 100 unique Service ports.
- Larger deployments should shard channels across multiple mux Services with non-overlapping port ranges.
- The current controller still writes a legacy `Endpoints` object for the mux. EndpointSlice support is on the roadmap for better scale and readability.
- The test namespace was intentionally left in place after validation for follow-up inspection.

## Conclusion

The tested GKE configuration successfully reused one GKE-managed external passthrough Network Load Balancer for 100 channel Services and 100 distinct backend pods. Static IP binding, GKE-managed firewall reconciliation, stable per-mux port allocation, public routing, and GKE port-limit enforcement all behaved as expected.
