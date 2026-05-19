# Getting Started

This guide takes a generic path through a first Service LoadBalancer
Multiplexer install. For provider-specific production settings, read this page
first and then continue with the [GKE](gke-lb-setup.md) or
[AWS](aws-nlb-setup.md) guide.

## What You Will Create

- One controller Deployment installed by Helm.
- One selectorless mux `LoadBalancer` Service.
- One channel `LoadBalancer` Service that points at the mux.
- One set of controller-generated mux Endpoints.

Traffic will flow like this:

```text
client
  -> provider load balancer for the mux Service
  -> mux Service port
  -> generated mux Endpoints
  -> channel backend pods
```

## Prerequisites

- A Kubernetes cluster that supports `type: LoadBalancer` Services.
- Helm 3.
- `kubectl` configured for the target cluster.
- Permission to create Services, Endpoints, ConfigMaps, RBAC, and Deployments.

If you use GitOps, read [GitOps compatibility](gitops.md) before committing mux
Services. GitOps must ignore mux runtime `spec.ports`.

## Install The Controller

Install the chart with the default mux enabled:

```console
helm install svc-mux oci://ghcr.io/nowakeai/charts/svc-lb-mux \
  --version 0.1.1 \
  --namespace svc-mux \
  --create-namespace
```

The chart creates a mux Service named `mux` in namespace `svc-mux`. These names
are defaults, not requirements.

Watch the controller and mux Service:

```console
kubectl get deploy -n svc-mux
kubectl get svc mux -n svc-mux -w
```

The mux may show a provider IP or hostname only after the cloud provider
finishes provisioning the load balancer.

## Create A Test Backend

Create a small echo workload:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: echo
  namespace: default
spec:
  replicas: 2
  selector:
    matchLabels:
      app: echo
  template:
    metadata:
      labels:
        app: echo
    spec:
      containers:
        - name: echo
          image: hashicorp/http-echo:1.0
          args:
            - -text=hello from svc-lb-mux
          ports:
            - name: http
              containerPort: 5678
---
apiVersion: v1
kind: Service
metadata:
  name: echo
  namespace: default
spec:
  type: LoadBalancer
  loadBalancerClass: svc-mux.nowake.ai/mux.svc-mux
  allocateLoadBalancerNodePorts: false
  selector:
    app: echo
  ports:
    - name: http
      port: 8080
      targetPort: http
```

Apply it:

```console
kubectl apply -f echo-channel.yaml
```

The channel Service keeps normal Kubernetes Service semantics. The mux
controller reads the channel and exposes `8080/TCP` on the mux Service.

## Verify The Mapping

Check the channel Service:

```console
kubectl get svc echo -n default
kubectl describe svc echo -n default
```

The controller copies the mux ingress status to the channel Service and writes
a readable mapping annotation:

```text
svc-mux.nowake.ai/ports: http:8080->8080
```

Check the mux:

```console
kubectl get svc mux -n svc-mux
kubectl get endpoints mux -n svc-mux
```

The mux Service should include port `8080/TCP`. The mux Endpoints should point
at the backend pod IPs and resolved backend port.

If the mux has an external IP, test it:

```console
MUX_IP=$(kubectl get svc mux -n svc-mux -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
curl "http://$MUX_IP:8080"
```

For providers that return a hostname instead of an IP:

```console
MUX_HOST=$(kubectl get svc mux -n svc-mux -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')
curl "http://$MUX_HOST:8080"
```

## Next Steps

- Read [Channel Service manual](channel-services.md) before exposing real
  workloads.
- Read [Tutorials and common cases](tutorials.md) for copyable patterns.
- Read [GKE LoadBalancer setup](gke-lb-setup.md) or
  [AWS NLB setup](aws-nlb-setup.md) for provider-specific production values.
- Read [GitOps compatibility](gitops.md) before managing mux Services from
  Argo CD, Flux, or another reconciler.
