# Troubleshooting

Start with the resource that is closest to the symptom, then work backward from
the channel Service to the mux Service and generated Endpoints.

## Quick Checks

List mux and channel Services:

```console
kubectl get svc -A | grep LoadBalancer
```

Inspect one channel:

```console
kubectl describe svc <channel> -n <namespace>
```

Inspect the mux:

```console
kubectl describe svc <mux> -n <mux-namespace>
kubectl get endpoints <mux> -n <mux-namespace>
kubectl get events -A --sort-by=.lastTimestamp
```

Read controller logs:

```console
kubectl logs -n svc-mux -l app.kubernetes.io/name=svc-mux --tail=200
```

## Channel Does Not Attach To A Mux

Check `spec.loadBalancerClass`.

Default format:

```text
svc-mux.nowake.ai/<mux-name>.<mux-namespace>
```

Example:

```yaml
spec:
  loadBalancerClass: svc-mux.nowake.ai/mux.svc-mux
```

Common causes:

- Wrong API prefix.
- Wrong mux name or namespace.
- The mux Service does not exist.
- The channel Service is missing `type: LoadBalancer`.

## Mux Port Is Missing

Check the channel port definitions:

```console
kubectl get svc <channel> -n <namespace> -o yaml
```

Every channel port needs a name:

```yaml
ports:
  - name: http
    port: 8080
    targetPort: http
```

If `external-ports` is present, every referenced name must exist in
`spec.ports`:

```yaml
metadata:
  annotations:
    svc-mux.nowake.ai/external-ports: "http:30080"
```

If the mux public port should be the same as the channel Service port, remove
the annotation and set `spec.ports[].port` to the desired public port.

## Port Conflict

Only one channel on a mux can claim a given `(port, protocol)` pair.

Symptoms:

- `MuxPortConflict` event.
- One channel does not appear in the mux port list.

Behavior:

- If a port claim exists in the per-mux state ConfigMap, that owner is preserved across controller restarts.
- If the state ConfigMap is missing and GitOps temporarily reverts mux `spec.ports` to a placeholder, channel `svc-mux.nowake.ai/ports` annotations can preserve ownership as a fallback.
- A newly added conflicting channel is skipped, even if its namespace/name sorts earlier.
- If neither channel has a live mux port yet, namespace/name order is the deterministic tie-breaker.

Fix:

- Choose a different `spec.ports[].port`.
- Or choose a different `external-ports` override.
- Or move one channel to another mux.

## Automatic Allocation Fails

Symptoms:

- `InvalidPortMapping` event mentioning automatic allocation.
- No available port in the configured range.

Check the mux range and state ConfigMap:

```console
kubectl get svc <mux> -n <mux-namespace> -o yaml
kubectl get configmap <mux-name>-port-allocations -n <mux-namespace> -o yaml
```

With the default API prefix, the relevant annotations are:

```text
svc-mux.nowake.ai/port-range
svc-mux.nowake.ai/allocation-configmap-name
```

Fix:

- Configure a `portRange` on the mux.
- Increase the range.
- Delete unused channels so stale automatic assignments can be pruned.
- Use one state ConfigMap per mux.

## Mux Has Ports But Traffic Fails

Check generated mux Endpoints:

```console
kubectl get endpoints <mux> -n <mux-namespace> -o yaml
```

The endpoint ports should match the backend pod ports, not necessarily the mux
public ports. For example:

```text
mux Service port: 20301
endpoint port:    9003
```

Common causes:

- Channel selector matches no pods.
- Pods are not ready, so Endpoints have no ready addresses.
- `targetPort` does not match a container port name or number.
- Provider firewall, security group, or health check behavior blocks traffic.

## Channel Ingress Is Empty

The controller copies mux `status.loadBalancer.ingress` to channel Services.
If channel status is empty, check the mux status first:

```console
kubectl get svc <mux> -n <mux-namespace> \
  -o jsonpath='{.status.loadBalancer.ingress}{"\n"}'
```

If the mux has no ingress, the provider load balancer is still pending or
failed. Use the provider guide:

- [GKE LoadBalancer setup](gke-lb-setup.md)
- [AWS NLB setup](aws-nlb-setup.md)

## GitOps Keeps Reverting Mux Ports

Symptoms:

- The mux `spec.ports` flips between a placeholder and channel-derived ports.
- Provider load balancer updates repeatedly.
- Argo CD or Flux reports drift on mux `spec.ports`.

Fix:

- Ignore `/spec/ports` for mux Services.
- Ignore controller-owned mux annotations if your GitOps tool reports drift.
- Do not manage generated mux Endpoints from Git.

See [GitOps compatibility](gitops.md).

## Useful Annotations

With the default API prefix:

| Resource | Annotation | Meaning |
| --- | --- | --- |
| Channel | `svc-mux.nowake.ai/ports` | Controller-written `channelPort->muxPort` mapping. |
| Channel | `svc-mux.nowake.ai/external-ports` | User-written mux public port override or `name:auto`. |
| Mux | `svc-mux.nowake.ai/summary` | One-line channel, port, pod, and ingress summary. |
| Mux | `svc-mux.nowake.ai/topology` | Human-readable mux to channel topology. |
| Mux | `svc-mux.nowake.ai/channels` | Controller-written channel reference list. |

Do not put controller-written annotations in Git.
