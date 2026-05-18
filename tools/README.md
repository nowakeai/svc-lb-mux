# Service LoadBalancer Multiplexer Debug Tool

`mux-debug` is a helper CLI for inspecting mux Services, channel Services, endpoints, and P2P connectivity in Kubernetes clusters.

## Install Dependencies

```console
python -m venv .venv
. .venv/bin/activate
pip install -r tools/requirements-advanced.txt
```

## Naming Defaults

The tool follows the project naming defaults:

- Default mux namespace: `svc-mux`
- Default API prefix: `svc-mux.nowake.ai`
- Default mux Service name: `mux`
- Additional mux Services should use names such as `mux2`, `mux3`, or another `muxN` form.

Override the API prefix for a non-default deployment:

```console
API_PREFIX=example.internal ./tools/mux-debug list
```

## Commands

List mux Services:

```console
./tools/mux-debug list
```

Show the routing graph for the default mux:

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
- Confirm each channel Service uses `spec.loadBalancerClass: svc-mux.nowake.ai/mux.svc-mux` or the prefix configured for your deployment.
- Confirm every channel Service port has a name.
- Confirm the mux Service has the expected external IP or hostname.
- Confirm Endpoints exist for the channel Service and include ready pod addresses.
