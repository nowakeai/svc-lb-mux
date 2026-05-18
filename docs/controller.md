# Controller Design And Features

This document describes what the Service LoadBalancer Multiplexer controller does and how the main behavior is implemented. It is intended for operators, contributors, and reviewers who need to understand the controller without reading the full source first.

## Runtime Model

The controller is a Kopf-based Kubernetes operator. It watches Kubernetes `Service` and `Endpoints` resources and reconciles one selectorless mux Service from many channel Services.

Main modules:

| Module | Responsibility |
| --- | --- |
| `src/main.py` | Controller entrypoint used by the container. |
| `src/controller.py` | Kopf handlers, indexes, mux daemon loop, Kubernetes writes, event emission. |
| `src/reconcile.py` | Pure reconciliation helpers for ports, channel validation, Endpoints metadata, GKE limits, and status patches. |
| `src/port_allocations.py` | Stable automatic external port allocation backed by one ConfigMap per mux. |
| `src/refs.py` | `loadBalancerClass` mux reference parsing and validation. |
| `src/annotations.py` | Human-readable mux and channel annotation formatting. |
| `src/events.py` | Kubernetes Event creation and debug UI event recording. |
| `src/webserver.py`, `src/debug_*` | FastAPI debug UI and read-only runtime state. |

At startup the controller:

- configures Kopf finalizer and worker settings;
- creates in-memory indexes and endpoint caches;
- starts the debug webserver thread when enabled;
- watches all namespaces by default through the chart command line.

The Helm chart runs the controller with `kopf run --standalone --all-namespaces /app/main.py`. Importing `src/controller.py` registers the Kopf handlers. `src/main.py` also contains a direct `kopf.run()` entrypoint for local execution, but the chart uses the explicit `kopf run` command.

## API Prefix

The controller reads `API_PREFIX`, defaulting to `svc-mux.nowake.ai`. This prefix is used for:

- mux and channel annotations;
- controller finalizer;
- channel `spec.loadBalancerClass` references.

The helper `annotation_key()` builds prefixed annotation keys, and `get_mux_from_lb_class()` validates channel references in the form:

```text
<api-prefix>/<mux>[.<namespace>]
```

If the namespace is omitted, the controller uses `DEFAULT_MUX_NAMESPACE`, which defaults to the controller namespace.

## Runtime Configuration

Important environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `API_PREFIX` | `svc-mux.nowake.ai` | Prefix for annotations, finalizer, and channel `loadBalancerClass`. |
| `NAMESPACE` | `default` | Controller namespace, injected from the Pod namespace by the chart. |
| `DEFAULT_MUX_NAMESPACE` | `NAMESPACE` | Namespace used when `loadBalancerClass` omits the mux namespace. |
| `DEBUG_WEB_ENABLED` | `true` | Starts or disables the FastAPI debug UI. |
| `DEBUG_WEB_PORT` | `8080` | Debug UI listen port. |
| `DEBUG_WEB_ACTIONS_ENABLED` | `false` | Enables active debug actions such as `/api/test-tcp`. |
| `DEBUG_WEB_AUTH_TOKEN` / `AUTH_TOKEN` | empty | Enables HTTP Basic auth for the debug UI when set. |
| `DRYRUN_MODE` | `false` | Computes desired state without writing Kubernetes objects. |
| `SVC_LB_MUX_DEBUG` | empty | Raises controller logging to DEBUG when true-like. |

The code also defines `<api-prefix>/disabled`, but current reconciliation does not implement disable behavior for muxes or channels. Treat it as reserved, not a supported feature.

## Mux Service Detection

A mux Service is a Kubernetes `Service` that:

- has `<api-prefix>/multiplexer: "true"`;
- has `spec.type: LoadBalancer`;
- has no `spec.selector`.

Implementation details:

- `multiplexer_services` indexes annotated Services.
- Invalid mux Services emit events:
  - `NotLoadBalancer` when the Service is not `type: LoadBalancer`;
  - `NotSupported` when the Service has a selector.
- For every valid mux, the controller starts a Kopf daemon loop and creates a per-mux queue.

The mux Service owns the provider-facing load balancer. The controller owns its runtime `spec.ports`, generated Endpoints, and controller annotations.

## Channel Service Detection

A channel Service is a Kubernetes `Service` that:

- has `spec.type: LoadBalancer`;
- has `spec.loadBalancerClass` starting with `<api-prefix>/`.

Implementation details:

- `channel_services` indexes channels by namespace.
- `mux_channels` parses `loadBalancerClass` and indexes each channel under its target mux.
- Invalid `loadBalancerClass` values emit `InvalidLoadBalancerClass`.
- Channel create, update, and resume handlers validate that every Service port is named, then enqueue the target mux for reconciliation.
- Channel deletion removes cached endpoints and triggers mux reconciliation so deleted channel ports and endpoints disappear from the mux.

Channel Services keep normal selectors and application-facing ports. They do not need cloud-provider load balancers of their own.

## Reconciliation Loop

Each mux has one daemon loop. The loop runs when a queued channel event arrives or after `DAEMON_QUEUE_TIMEOUT` seconds so periodic drift is corrected.

Each iteration:

1. Refreshes the mux Service from the API server.
2. Reads mux `status.loadBalancer` for ingress propagation.
3. Gets the current channel set from the Kopf index.
4. Builds or loads the mux port allocator when a mux port range is configured.
5. Processes channels in deterministic namespace/name order.
6. Resolves desired mux ports for each channel.
7. Rejects invalid or conflicting channel mappings.
8. Aggregates channel Endpoints into mux Endpoints.
9. Saves allocation ConfigMap changes.
10. Patches mux annotations, mux `spec.ports`, mux Endpoints, and channel metadata/status only when they changed.

The deterministic channel order is important because automatic port allocation should be stable and repeatable when multiple channels are reconciled at once.

## Port Mapping Modes

The controller supports three external mux port modes.

### Default Port Mapping

If a channel has no `<api-prefix>/external-ports` annotation, each channel `spec.ports[].port` becomes the mux external port.

Example:

```yaml
spec:
  ports:
    - name: http
      port: 80
      targetPort: 8080
```

The mux receives an external `80/TCP` port for that channel.

Implementation details:

- `resolve_channel_external_port()` falls back to `spec.ports[].port`.
- Port numbers are validated as integers in `1-65535`.
- The mux Service port name is a 7-character SHA-256 prefix of `namespace/service/portName`.

### Explicit Custom Port Mapping

A channel can request a different external mux port without changing its internal Service port:

```yaml
metadata:
  annotations:
    svc-mux.nowake.ai/external-ports: "http:8080,grpc:9090"
```

Implementation details:

- `parse_external_ports_annotation()` parses comma-separated `portName:externalPort` pairs.
- Every referenced port name must exist in `spec.ports`.
- Unknown names, malformed values, and out-of-range ports emit `InvalidPortMapping`.
- The channel gets a `<api-prefix>/ports` annotation such as `http:80->8080` for readability.

### Automatic Port Allocation

A channel can request automatic allocation:

```yaml
metadata:
  annotations:
    svc-mux.nowake.ai/external-ports: "http:auto"
```

The mux must have a port range:

```yaml
metadata:
  annotations:
    svc-mux.nowake.ai/port-range: "20000-20099"
```

Implementation details:

- `requested_port_range()` parses one or more ranges such as `20000-20099,21000-21010`.
- `PortAllocator` reuses existing assignments when still valid.
- New assignments use the first available `(port, protocol)` in configured range order.
- Static claims are reserved before auto allocation so auto ports do not collide with explicit ports.
- Deleted or inactive auto assignments are pruned during reconciliation.
- Exhaustion emits `InvalidPortMapping` with a no-available-port message.

## Stable Allocation ConfigMap

Automatic assignments are stored in one ConfigMap per mux. The default name is:

```text
<mux-name>-port-allocations
```

It can be overridden with:

```yaml
metadata:
  annotations:
    svc-mux.nowake.ai/allocation-configmap: custom-name
```

ConfigMap data uses `allocations.json`:

```json
{
  "schemaVersion": 1,
  "mux": {"namespace": "svc-mux", "name": "mux"},
  "allocations": [
    {
      "namespace": "app",
      "service": "api",
      "portName": "http",
      "protocol": "TCP",
      "port": 20000,
      "source": "auto"
    }
  ]
}
```

Implementation details:

- The ConfigMap is labeled `app.kubernetes.io/name=svc-lb-mux` and `app.kubernetes.io/component=port-allocation`.
- It is annotated with `<api-prefix>/mux: <namespace>/<name>`.
- The store validates both metadata ownership and embedded state ownership.
- Reusing one ConfigMap for multiple muxes is rejected with `PortAllocationStoreInvalid`.
- Invalid JSON is rejected with `PortAllocationStoreInvalid`.

This design avoids one global allocation object, reduces ConfigMap size pressure, and makes operations per-mux.

## Port Conflict Handling

Within one mux, each `(external port, protocol)` pair can be used by only one channel.

Implementation details:

- `find_mux_port_conflicts()` tracks owners during a reconciliation pass.
- Conflicting channels emit `MuxPortConflict` and are skipped.
- The same numeric port can be used for different protocols, for example `53/TCP` and `53/UDP`.
- Duplicate port claims inside the same channel are also treated as conflicts.

## GKE Port Limit Handling

GKE LoadBalancer Services support up to 100 unique Service ports. The controller applies this model automatically for detected GKE muxes.

A mux is considered GKE-backed when:

- `spec.loadBalancerClass` starts with `networking.gke.io/`; or
- common GKE Service annotations are present, such as `cloud.google.com/l4-rbs`.

Implementation details:

- `effective_mux_max_ports()` returns the configured max or the GKE cap.
- Missing `max-ports` on a detected GKE mux is treated as 100 and emits `GkePortLimitApplied`.
- Values greater than 100 are capped to 100 and emit `GkePortLimitApplied`.
- Invalid `max-ports` values emit `InvalidMaxPorts` and prevent channel processing for that mux iteration.
- When a channel would exceed the effective limit, it emits `MuxPortLimitExceeded` and is skipped.

The Helm chart sets the default GKE values:

```yaml
defaultLoadBalancer:
  portRange: "20000-20099"
  maxPorts: 100
```

## Endpoint Aggregation

The controller aggregates channel Endpoints into the mux Endpoints object.

Implementation details:

- Channel Endpoints create, update, and resume events update the in-memory endpoint cache and trigger mux reconciliation.
- `collect_channel_endpoints()` reads each channel Endpoints subset.
- Ready addresses are copied into mux subset `addresses`.
- Not-ready addresses are copied into mux subset `notReadyAddresses`.
- Empty endpoint subsets are skipped.
- Endpoint port names are rewritten to the stable mux port hash for `namespace/service/portName`.
- Port number and protocol come from the channel Endpoints port entry.

This is an aggregation of Kubernetes Endpoints data, not direct cloud load balancer backend programming. Cloud providers still observe the mux Service and its Kubernetes backend state through their normal Service controller integrations.

The generated mux Endpoints receives labels and annotations:

```yaml
metadata:
  labels:
    app.kubernetes.io/managed-by: svc-lb-mux
    app.kubernetes.io/component: mux-endpoints
  annotations:
    svc-mux.nowake.ai/managed: "true"
    svc-mux.nowake.ai/mux: svc-mux/mux
    svc-mux.nowake.ai/channels: '["app/api"]'
```

The current implementation uses legacy `Endpoints`. EndpointSlice support is planned but not implemented yet.

## Mux Service Port Reconciliation

After all channels are processed, the controller builds mux `spec.ports` from the resolved mux ports.

Implementation details:

- Each mux port entry contains `name`, `port`, and `protocol`.
- The name is the stable 7-character hash of channel namespace, Service name, and port name.
- If no real channel ports exist, the controller writes a placeholder `101/TCP` port.
- The placeholder is removed automatically when real ports exist.
- The mux Service is patched only when the computed port set differs from the current set.
- Port changes emit `MuxPortsChanged`.

Because mux `spec.ports` is controller-owned runtime state, GitOps tools must ignore this field for mux Services.

## Channel Metadata And Status Updates

For every accepted channel, the controller patches:

- `<api-prefix>/ports` annotation with readable channel-to-mux mappings;
- `status.loadBalancer` with the mux Service `status.loadBalancer`.

Example annotation:

```text
http:8080->20000, grpc:9090->20001
```

Implementation details:

- `update_channel_service_metadata()` patches metadata only when the annotation changed.
- It patches the `status` subresource only when channel load balancer status differs from mux status.
- This lets DNS controllers and users see the shared mux ingress on each channel Service.

## Mux Readability Annotations

The controller writes three mux annotations for operator readability:

| Annotation | Purpose |
| --- | --- |
| `<api-prefix>/channels` | JSON list of channel namespace/name references. |
| `<api-prefix>/topology` | Multi-line human-readable channel, DNS, port, and backend summary. |
| `<api-prefix>/summary` | One-line summary of channels, ports, ready pods, and mux DNS/IP. |

Implementation details:

- `format_topology_annotation()` includes mux DNS/IP, channel DNS hint, port mappings, and ready backend pod count.
- `format_summary_annotation()` produces compact text such as `100 channel(s) | 100 port(s) | 100 pod(s) | DNS: 203.0.113.10`.
- Annotation changes emit `MuxAnnotationsUpdated`.

## Events

The controller emits Kubernetes Events for both accepted changes and rejected inputs.

Common normal events:

| Reason | Meaning |
| --- | --- |
| `MuxAnnotationsUpdated` | Mux readability annotations changed. |
| `MuxPortsChanged` | Mux `spec.ports` changed. |
| `MuxEndpointsCreated` | Mux Endpoints object was created. |
| `MuxEndpointsChanged` | Mux Endpoints changed. |

Common warning/error events:

| Reason | Meaning |
| --- | --- |
| `NotLoadBalancer` | Annotated mux is not `type: LoadBalancer`. |
| `NotSupported` | Annotated mux has a selector. |
| `InvalidLoadBalancerClass` | Channel `loadBalancerClass` does not match expected format. |
| `InvalidPort` | Channel Service port is unnamed. |
| `InvalidPortMapping` | Channel external port annotation or port value is invalid. |
| `InvalidPortRange` | Mux port range annotation is invalid. |
| `InvalidMaxPorts` | Mux max port annotation is invalid. |
| `PortAllocationStoreInvalid` | Allocation ConfigMap is malformed or owned by another mux. |
| `MuxPortConflict` | Two mappings want the same `(external port, protocol)`. |
| `MuxPortLimitExceeded` | A channel would exceed the mux port limit. |
| `GkePortLimitApplied` | GKE mux was capped or defaulted to the 100-port limit. |

`src/events.py` also records events into the debug UI state. Event creation is cached to avoid excessive duplicate events.

`src/alert.py` contains a Slack webhook helper, but it is not currently wired into the controller reconciliation path. Kubernetes Events are the active notification surface today.

## Debug Web UI

The controller can start a FastAPI debug webserver on `DEBUG_WEB_PORT`, default `8080`.

Implemented routes and behavior:

| Route | Purpose |
| --- | --- |
| `/` | Serves the embedded HTML debug UI. |
| `/healthz` | Health endpoint, unauthenticated. |
| `/api/state` | Runtime state snapshot. |
| `/api/topology` | Mux/channel topology view. |
| `/api/config` | Debug UI capability flags. |
| `/api/test-tcp` | Optional active TCP probe when debug actions are enabled. |

Security behavior:

- read-only mode by default;
- HTTP Basic auth is enabled when a token is configured;
- active TCP probes are disabled unless explicitly enabled;
- `/healthz` bypasses auth for Kubernetes probes;
- baseline security headers and request logging middleware are applied.

The debug UI state is updated from the controller reconciliation loop and event helper. Product-specific diagnostics should be added behind future plugin boundaries rather than hard-coded into core routes.

## Dry-Run Mode

When `DRYRUN_MODE` is enabled, the controller computes desired state but does not patch Kubernetes objects. It logs intended writes instead.

Dry-run affects:

- channel annotation and status patches;
- allocation ConfigMap writes;
- mux annotation patches;
- mux Service port patches;
- mux Endpoints create/patch operations;
- event emission paths that are guarded by production-mode checks.

## Finalizers And Deletion

Kopf uses the configured `<api-prefix>/finalizer` for managed handlers.

Deletion behavior:

- Channel deletion removes cached channel endpoints and triggers mux reconciliation.
- Mux deletion removes its endpoint cache and queue and removes debug UI state.
- The chart keeps the default mux Service with `helm.sh/resource-policy: keep`, so uninstall order matters.

If the controller is uninstalled before deleting mux/channel Services, finalizers can remain and block namespace deletion. This is tracked in the roadmap as uninstall guidance/automation work.

## GitOps Ownership Boundaries

The controller intentionally writes some Kubernetes fields:

| Resource | Controller-owned fields |
| --- | --- |
| Mux Service | `spec.ports`, `<api-prefix>/channels`, `<api-prefix>/topology`, `<api-prefix>/summary` |
| Mux Endpoints | entire generated Endpoints object and ownership metadata |
| Channel Service | `<api-prefix>/ports`, `status.loadBalancer` |
| Allocation ConfigMap | `allocations.json` and mux ownership metadata |
| Events | Kubernetes Events for changes and validation failures |

GitOps should own mux identity, provider annotations, labels, Service type, load balancer class, static IP settings, and channel desired specs. GitOps should ignore mux `spec.ports` and generated controller annotations; see [gitops.md](gitops.md).

## RBAC Requirements

The chart grants the controller access to the resources it watches and writes:

| Resource | Verbs | Why |
| --- | --- | --- |
| `services` | get, list, watch, create, update, patch, delete | Watch mux/channel Services and patch mux runtime spec/annotations. |
| `services/status` | get, update, patch | Copy mux load balancer status to channel Services. |
| `endpoints` | get, list, watch, create, update, patch, delete | Watch channel Endpoints and create/patch mux Endpoints. |
| `endpoints/status` | get, update, patch | Present in chart RBAC for compatibility. |
| `events` | create, patch | Record reconciliation changes and validation failures. |
| `configmaps` | get, list, watch, create, update, patch | Store per-mux automatic port allocations. |
| `endpointslices` | get, list, watch, create, update, patch, delete | Pre-granted for planned EndpointSlice support; current controller still uses Endpoints. |
| `customresourcedefinitions` | get, list, watch | Reserved chart permission for controller ecosystem compatibility. |

## Current Limitations

- EndpointSlice programming is not implemented yet; the controller writes legacy `Endpoints`.
- The controller does not create or manage cloud provider resources directly.
- GKE-specific behavior is limited to detection and port-limit enforcement; GKE still owns cloud resources.
- Automatic allocation has no delayed release or reuse grace period yet.
- The debug UI is still being modularized and should remain read-only by default.
