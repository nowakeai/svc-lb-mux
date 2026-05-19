# Service LoadBalancer Multiplexer Debug Tool

`mux-debug` is an experimental helper CLI for inspecting mux Services, channel Services, and endpoints in Kubernetes clusters. Its generic graph and TCP checks are useful for any svc-lb-mux deployment. Its deeper P2P diagnostics are focused on OP Stack node traffic and are maintained as a best-effort operator aid, not as part of the controller runtime.

## Status

This tool is WIP. The generic mux inspection commands should remain useful, but P2P-specific diagnostics are intentionally OP Stack-oriented and optional. Treat provider- or protocol-specific checks as experimental.

## Install Dependencies

```console
python -m venv .venv
. .venv/bin/activate
pip install -r tools/requirements-advanced.txt
```

## Naming Defaults And Conventions

The tool follows the chart defaults when a command needs a mux namespace or API prefix:

- Default mux namespace: `svc-mux`
- Default API prefix: `svc-mux.nowake.ai`
- Default mux Service name in examples: `mux`

These are not naming requirements. For real deployments, prefer one mux per namespace or project. Use a semantic mux name when it helps operators, or reuse `mux` across namespaces and pass the namespace explicitly.

Override the API prefix for a non-default deployment:

```console
API_PREFIX=example.internal ./tools/mux-debug list
```

## OP Stack P2P Diagnostics

The P2P commands are primarily for OP Stack bootnode and archive-node checks:

- `op-node` pods are detected as libp2p peers and can be checked with TCP plus libp2p handshake validation.
- `geth`, `reth`, and `erigon` pods are detected as devp2p peers. The tool attempts to read `admin_nodeInfo` or an enode and can run RLPx validation when the optional helper is available.
- Non-OP Stack workloads should treat P2P output as best-effort only. The generic mux graph, endpoint, and TCP checks are the supported cross-workload diagnostics.

## Commands

List mux Services:

```console
./tools/mux-debug list
```

Show the routing graph for the chart default mux:

```console
./tools/mux-debug graph mux -n svc-mux
```

Test all routes for a mux:

```console
./tools/mux-debug test mux mux -n svc-mux
```

Test a specific channel Service:

```console
./tools/mux-debug test channel my-service -n my-namespace
```

Test a pod selected by a channel Service:

```console
./tools/mux-debug test pod my-pod-0 -n my-namespace
```

Get P2P peer information from a pod:

```console
./tools/mux-debug peer-info my-pod-0 -n my-namespace
```

## Resource Argument Format

Most commands accept either separate namespace flags or `namespace/name` resource references:

```console
./tools/mux-debug graph svc-mux/mux
./tools/mux-debug test pod my-namespace/my-pod-0
```

## What The Tool Reads

The tool is read-only. It uses Kubernetes Services and Endpoints to derive:

- mux external IP or hostname
- channel Services attached through `spec.loadBalancerClass`
- mux port mappings stored in controller annotations
- target pod IPs and readiness state
- optional external DNS annotations

## Troubleshooting Checklist

- Confirm the mux Service has the configured API prefix annotation, for example `svc-mux.nowake.ai/multiplexer: "true"`.
- Confirm each channel Service uses `spec.loadBalancerClass: <api-prefix>/<mux>[.<namespace>]`, for example `svc-mux.nowake.ai/mux.svc-mux` with the chart defaults.
- Confirm every channel Service port has a name.
- Confirm the mux Service has the expected external IP or hostname.
- Confirm Endpoints exist for the channel Service and include ready pod addresses.
