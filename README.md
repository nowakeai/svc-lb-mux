# Service LoadBalancer Multiplexer

Service LoadBalancer Multiplexer is a Kubernetes controller that lets multiple `LoadBalancer` Services share one Layer 4 LoadBalancer.

The repository is split into application code and deployment packaging:

- `scripts/`: controller and debug UI source code
- `chart/`: Helm chart
- `Dockerfile`: controller image build
- `.github/workflows/ci.yml`: validation and GHCR image publishing

The Kubernetes API prefix is intentionally kept as `lb4-multiplexer.altlayer.io` for compatibility with existing Services.

## Concepts

- **Multiplexer**: a selectorless `LoadBalancer` Service that owns the shared external load balancer.
- **Channel**: a `LoadBalancer` Service that points at a multiplexer through `spec.loadBalancerClass`.

## Install

Builds from this repo publish the controller image to `ghcr.io/nowakeai/svc-lb-mux`. Install the chart from the repository root:

```console
kubectl create namespace lb4
helm install service-loadbalancer-multiplexer ./chart \
  --namespace lb4 \
  --set image.tag=latest
```

By default the chart creates a multiplexer Service named `mux` in the release namespace.

## Default LoadBalancer

Customize the default multiplexer through `chart/values.yaml`:

```yaml
defaultLoadBalancer:
  create: true
  name: mux
  labels: {}
  annotations:
    lb4-multiplexer.altlayer.io/multiplexer: "true"
    service.beta.kubernetes.io/aws-load-balancer-type: external
    service.beta.kubernetes.io/aws-load-balancer-scheme: internet-facing
    service.beta.kubernetes.io/aws-load-balancer-nlb-target-type: ip
  loadBalancerClass: service.k8s.aws/nlb
```

For AWS, install the AWS Load Balancer Controller and use NLB `ip` target mode. See [aws-nlb-setup.md](aws-nlb-setup.md).

If a multiplexer has no channels, the controller keeps a placeholder `101/TCP` port.

## Create A Channel Service

A channel Service must:

1. Set `spec.type` to `LoadBalancer`.
2. Set `spec.loadBalancerClass` to `lb4-multiplexer.altlayer.io/<mux>[.<namespace>]`.
3. Name every port.

Example:

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
  loadBalancerClass: lb4-multiplexer.altlayer.io/mux.lb4
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
kubectl get secret -n lb4 service-loadbalancer-multiplexer-debug-token -o jsonpath='{.data.token}' | base64 -d
```

Port-forward locally:

```console
kubectl port-forward -n lb4 deployment/service-loadbalancer-multiplexer 8080:8080
```

Then open <http://localhost:8080> and use any username with the token as the password.

For external access, enable `debugWeb.ingress` and use TLS.

## Uninstall

```console
helm uninstall --namespace lb4 service-loadbalancer-multiplexer
```

The chart keeps the default LoadBalancer Service via `helm.sh/resource-policy: keep`. To delete managed Services later, remove the finalizer `lb4-multiplexer.altlayer.io/finalizer` first.

## Development

Common commands:

```console
make lint
make template
make python-compile
make docker-build
```

Dependency metadata lives in `pyproject.toml`; the runtime image installs pinned dependencies from `scripts/requirements.txt`. Regenerate requirements with:

```console
make requirements
```
