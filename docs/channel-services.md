# Channel Service Manual

A channel Service is a normal Kubernetes `Service` with `type: LoadBalancer`,
but its `spec.loadBalancerClass` points at a mux instead of a cloud-provider
load balancer controller.

This page explains how channel ports map to mux ports and backend ports.

## Required Shape

```yaml
apiVersion: v1
kind: Service
metadata:
  name: app-http
  namespace: app
spec:
  type: LoadBalancer
  loadBalancerClass: svc-mux.nowake.ai/mux.svc-mux
  allocateLoadBalancerNodePorts: false
  selector:
    app: app-http
  ports:
    - name: http
      protocol: TCP
      port: 8080
      targetPort: http
```

Rules:

- `spec.loadBalancerClass` is `<api-prefix>/<mux>[.<namespace>]`.
- Every channel port must have a stable `name`.
- The port name is the identity used by annotations and generated mux port
  names.
- `allocateLoadBalancerNodePorts: false` is the normal channel setting. The mux
  Service owns provider-facing load balancer plumbing.
- Each `(mux public port, protocol)` pair can be claimed by only one channel on
  a mux.

## Port Meaning

For this Service port:

```yaml
ports:
  - name: http
    protocol: TCP
    port: 8080
    targetPort: http
```

The meanings are:

| Field | Meaning |
| --- | --- |
| `name` | Stable channel port identity. Required. |
| `port` | Channel Service port. By default, also the mux public port. |
| `targetPort` | Backend pod port or named container port. |
| `protocol` | Mux and backend protocol. Defaults to `TCP` if omitted. |

Default mapping:

```text
channel spec.ports[].port == mux public port
channel spec.ports[].targetPort == backend pod port
```

Example:

```yaml
ports:
  - name: p2p
    port: 30303
    targetPort: p2p
```

The mux exposes `30303/TCP`, and the generated mux Endpoints route to the pod
port resolved from `targetPort: p2p`.

## Override The Mux Public Port

Use `<api-prefix>/external-ports` only when the mux public port should differ
from the channel Service port.

```yaml
metadata:
  annotations:
    svc-mux.nowake.ai/external-ports: "http:30080"
spec:
  ports:
    - name: http
      port: 8080
      targetPort: http
```

Mapping:

```text
channel Service port: 8080
mux public port:      30080
backend targetPort:   http
```

The controller writes a readable annotation:

```text
svc-mux.nowake.ai/ports: http:8080->30080
```

Use explicit overrides for compatibility with an existing public port, a
provider port plan, or a migration where internal Service ports should remain
unchanged.

## Automatic Port Allocation

Use `name:auto` when you want the controller to allocate a mux public port from
the mux range.

Mux annotation or chart value:

```yaml
defaultLoadBalancer:
  portRange: "20000-20099"
```

Channel annotation:

```yaml
metadata:
  annotations:
    svc-mux.nowake.ai/external-ports: "http:auto"
spec:
  ports:
    - name: http
      port: 8080
      targetPort: http
```

The assignment is stored in the per-mux state ConfigMap, so the selected port remains
stable across controller restarts and repeated GitOps applies. Static and explicit
port claims are stored there as well, which prevents newly added channels from
stealing an existing port after a restart.

Do not reuse one state ConfigMap for multiple muxes.

## Aggregate External DNS Hostnames

Use `<api-prefix>/external-dns-hostname` on channel Services when multiple
channels should contribute hostnames to the mux Service. The controller
aggregates those values onto the mux as
`external-dns.alpha.kubernetes.io/hostname`, de-duplicates hostnames, and keeps
the order stable by channel namespace and name.

```yaml
metadata:
  annotations:
    svc-mux.nowake.ai/external-dns-hostname: "api.example.com,www.example.com"
```

Do not use `external-dns.alpha.kubernetes.io/hostname` on channel Services for
mux aggregation. The controller ignores that annotation on channels so
external-dns does not see duplicate ownership on several channel Services.

If channel Services set
`external-dns.alpha.kubernetes.io/cloudflare-proxied`, the controller aggregates
it onto the mux with AND semantics. Any channel value of `"false"` makes the
mux annotation `"false"`; otherwise all explicit `"true"` values produce
`"true"`.

If the mux Service already has
`external-dns.alpha.kubernetes.io/hostname` or
`external-dns.alpha.kubernetes.io/cloudflare-proxied` without controller
ownership metadata, the controller treats that annotation as user- or
GitOps-owned, leaves it unchanged, and emits warning events for channel
annotations that cannot be aggregated.

## Multiple Ports

A channel can expose multiple named ports:

```yaml
metadata:
  annotations:
    svc-mux.nowake.ai/external-ports: "http:30080,grpc:auto"
spec:
  ports:
    - name: http
      port: 8080
      targetPort: http
    - name: grpc
      port: 9090
      targetPort: grpc
```

Every name referenced in `external-ports` must exist in `spec.ports`.
Unreferenced ports use their own `spec.ports[].port` as the mux public port.

## Common Mistakes

- Missing port names. The controller rejects unnamed channel ports.
- Treating `targetPort` as the mux public port. It is the backend pod port.
- Keeping `external-ports` after changing `spec.ports[].port` to the desired
  public port. The annotation is an override, so it should be absent unless an
  override is intentional.
- Reusing the same public port and protocol across two channels on one mux.
- Managing controller-owned `svc-mux.nowake.ai/ports` in Git. This annotation is
  generated by the controller.
