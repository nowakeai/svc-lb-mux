# Service LoadBalancer Multiplexer

[![CI](https://github.com/nowakeai/svc-lb-mux/actions/workflows/ci.yml/badge.svg)](https://github.com/nowakeai/svc-lb-mux/actions/workflows/ci.yml)
[![Image](https://img.shields.io/badge/GHCR-ghcr.io%2Fnowakeai%2Fsvc--lb--mux-blue)](https://github.com/nowakeai/svc-lb-mux/pkgs/container/svc-lb-mux)

Service LoadBalancer Multiplexer is a Kubernetes controller that lets multiple `LoadBalancer` Services share one Layer 4 load balancer.

## Motivation

Kubernetes makes external TCP/UDP exposure convenient with `type: LoadBalancer`, but each Service usually asks the cloud provider to create a separate load balancer. That becomes painful for workloads with many externally reachable ports or many small services: cloud load balancer quotas become a scaling limit, provisioning is slower, and every extra load balancer adds recurring cost.

This project was built for cases where many Services can safely share one provider-managed Layer 4 load balancer. A selectorless mux Service owns the cloud load balancer, while channel Services keep the familiar Kubernetes Service workflow. The controller mirrors channel ports and endpoints onto the mux, then syncs the mux ingress status back to the channels. The result is fewer cloud load balancers, lower cost, and less pressure on provider load balancer limits without forcing application teams to give up Service objects.

The repository is split into application code and deployment packaging:

- `src/`: controller and debug UI source code
- `chart/`: Helm chart, installed by default as release `svc-mux` in namespace `svc-mux`
- `Dockerfile`: controller image build
- `.github/workflows/ci.yml`: validation and GHCR image publishing
- `docs/`: provider setup guides
- `uv.lock`: locked Python runtime dependency graph

The Kubernetes API prefix is configurable through `api.prefix`. New installs default to `svc-mux.nowake.ai`.

## Concepts

- **Multiplexer**: a selectorless `LoadBalancer` Service that owns the shared external load balancer.
- **Channel**: a `LoadBalancer` Service that points at a multiplexer through `spec.loadBalancerClass`.

## Install

Builds from this repo publish the controller image to `ghcr.io/nowakeai/svc-lb-mux`. Install the chart from the repository root:

```console
kubectl create namespace svc-mux
helm install svc-mux ./chart \
  --namespace svc-mux \
  --set image.tag=latest
```

The chart defaults create one multiplexer Service named `mux` in namespace `svc-mux`. These are only defaults. In production, choose names that match your ownership model: commonly one mux per namespace or project, either with a semantic name such as `payments` or `rollup-p2p`, or simply `mux` in each project namespace and let the namespace disambiguate it.

## API Prefix

New resources use `svc-mux.nowake.ai` by default:

```yaml
api:
  prefix: svc-mux.nowake.ai
```

Set `api.prefix` in your values file when a deployment needs a different API prefix. The controller reads and writes annotations, finalizers, and `loadBalancerClass` values using that single configured prefix.

## Default LoadBalancer

Customize the default multiplexer through `chart/values.yaml`:

```yaml
defaultLoadBalancer:
  create: true
  name: mux
  labels: {}
  annotations:
    cloud.google.com/l4-rbs: "enabled"
  loadBalancerIP: ""
  loadBalancerClass: ""
  allocateLoadBalancerNodePorts: true
```

The default chart values target GKE. See [docs/gke-lb-setup.md](docs/gke-lb-setup.md) for GKE architecture, static IP binding, and provider-specific options. For EKS/NLB deployments, see [docs/aws-nlb-setup.md](docs/aws-nlb-setup.md).

If a multiplexer has no channels, the controller keeps a placeholder `101/TCP` port.

## Create A Channel Service

A channel Service must:

1. Set `spec.type` to `LoadBalancer`.
2. Set `spec.loadBalancerClass` to `<api-prefix>/<mux>[.<namespace>]`. The default prefix is `svc-mux.nowake.ai`; `<mux>` and `<namespace>` are the actual multiplexer Service name and namespace you choose.
3. Name every port.

Example using the chart defaults:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: my-service
  namespace: my-namespace
  annotations:
    external-dns.alpha.kubernetes.io/hostname: my-hostname.com
spec:
  type: LoadBalancer
  loadBalancerClass: svc-mux.nowake.ai/mux.svc-mux
  selector:
    app: my-app
  ports:
    - name: http
      port: 80
      targetPort: 80
    - name: https
      port: 443
      targetPort: 443
```

Kubernetes allocates `nodePort`s for the channel. The controller mirrors those ports and endpoints onto the multiplexer and syncs the multiplexer `status.loadBalancer.ingress` back to the channel Service.

## Debug Web UI

The debug UI is enabled by default on port `8080`, and authentication is enabled by default. If you do not provide a token, Helm generates one in a Secret.

Retrieve the generated token:

```console
kubectl get secret -n svc-mux svc-mux-debug-token -o jsonpath='{.data.token}' | base64 -d
```

Port-forward locally:

```console
kubectl port-forward -n svc-mux deployment/svc-mux 8080:8080
```

Then open <http://localhost:8080> and use any username with the token as the password.

For external access, enable `debugWeb.ingress` and use TLS.

## Uninstall

```console
helm uninstall --namespace svc-mux svc-mux
```

The chart keeps the default LoadBalancer Service via `helm.sh/resource-policy: keep`. To delete managed Services later, remove the configured API finalizer, for example `svc-mux.nowake.ai/finalizer`.

## Development

Common commands:

```console
make check-lock
make lint
make template
make python-compile
make docker-build
```

Dependency metadata lives in `pyproject.toml`; exact runtime dependencies are locked in `uv.lock`. The container installs dependencies with `uv sync --locked`, so `uv.lock` is the source of truth.

Use these commands to maintain dependencies:

```console
make update-deps     # upgrade uv.lock
make check-lock      # verify pyproject.toml and uv.lock are consistent
make requirements    # generate ignored requirements.txt for compatibility only
```
