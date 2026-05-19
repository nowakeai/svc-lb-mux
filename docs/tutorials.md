# Tutorials And Common Cases

These examples are intentionally small. Use them as starting points, then apply
the provider-specific guidance from [GKE](gke-lb-setup.md) or
[AWS](aws-nlb-setup.md) before production rollout.

## Case 1: Expose One HTTP Service

Use this when one backend should be reachable on a known public port.

```yaml
apiVersion: v1
kind: Service
metadata:
  name: web
  namespace: app
spec:
  type: LoadBalancer
  loadBalancerClass: svc-mux.nowake.ai/mux.svc-mux
  allocateLoadBalancerNodePorts: false
  selector:
    app: web
  ports:
    - name: http
      protocol: TCP
      port: 80
      targetPort: http
```

Expected mapping:

```text
http:80->80
```

## Case 2: Keep A Backend Port But Use A Different Public Port

Use this when pods listen on one port but clients must connect to another
public port.

```yaml
apiVersion: v1
kind: Service
metadata:
  name: web-alt
  namespace: app
spec:
  type: LoadBalancer
  loadBalancerClass: svc-mux.nowake.ai/mux.svc-mux
  allocateLoadBalancerNodePorts: false
  selector:
    app: web-alt
  ports:
    - name: http
      protocol: TCP
      port: 30080
      targetPort: http
```

No `external-ports` annotation is needed because `port: 30080` is already the
desired mux public port.

Use the annotation only if the channel Service port must remain different:

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

Expected mapping:

```text
http:8080->30080
```

## Case 3: Allocate Public Ports Automatically

Use this when users do not care which public port each channel receives, but
they need stable assignments after allocation.

Configure the mux range:

```yaml
defaultLoadBalancer:
  portRange: "20000-20099"
  maxPorts: 100
```

Request automatic allocation:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: worker-api
  namespace: app
  annotations:
    svc-mux.nowake.ai/external-ports: "http:auto"
spec:
  type: LoadBalancer
  loadBalancerClass: svc-mux.nowake.ai/mux.svc-mux
  allocateLoadBalancerNodePorts: false
  selector:
    app: worker-api
  ports:
    - name: http
      port: 8080
      targetPort: http
```

Check the assigned port:

```console
kubectl get svc worker-api -n app \
  -o go-template='{{index .metadata.annotations "svc-mux.nowake.ai/ports"}}{{"\n"}}'
```

## Case 4: Migrate Existing LoadBalancer Services

Use this when a workload already has a normal provider-backed `LoadBalancer`
Service and you want to test the mux path before cutting DNS.

1. Keep the existing Service in place.
2. Create a second channel Service with a distinct name and the same selector.
3. Set `spec.loadBalancerClass` to the mux class.
4. Set `spec.ports[].port` to the public port you want on the mux.
5. Set `targetPort` to the same backend port used by the old Service.
6. Verify the mux IP or hostname and TCP connectivity.
7. Move DNS only after the mux path is validated.

Example channel:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: app-mux
  namespace: app
spec:
  type: LoadBalancer
  loadBalancerClass: svc-mux.nowake.ai/mux.svc-mux
  allocateLoadBalancerNodePorts: false
  selector:
    app: app
  ports:
    - name: public
      protocol: TCP
      port: 20301
      targetPort: app-public
```

The channel port `20301` is meaningful: it is the mux public port. The
`targetPort` points at the backend pod port.

## Case 5: Expose P2P Or Other Raw TCP Workloads

Use one channel per backend component when different components expose
different target ports. Do not combine unrelated components under one selector
just to share a Service.

```yaml
apiVersion: v1
kind: Service
metadata:
  name: node-p2p
  namespace: chain
spec:
  type: LoadBalancer
  loadBalancerClass: svc-mux.nowake.ai/mux.svc-mux
  allocateLoadBalancerNodePorts: false
  selector:
    app: chain-node
    component: node
  ports:
    - name: p2ptcp
      protocol: TCP
      port: 20301
      targetPort: p2ptcp
---
apiVersion: v1
kind: Service
metadata:
  name: execution-p2p
  namespace: chain
spec:
  type: LoadBalancer
  loadBalancerClass: svc-mux.nowake.ai/mux.svc-mux
  allocateLoadBalancerNodePorts: false
  selector:
    app: chain-node
    component: execution
  ports:
    - name: p2p
      protocol: TCP
      port: 10301
      targetPort: p2p
```

Expected mappings:

```text
p2ptcp:20301->20301
p2p:10301->10301
```

## Case 6: Split Capacity Across Multiple Muxes

Use multiple muxes when provider limits, blast radius, ownership, or port ranges
should be separated.

Example channel references:

```yaml
spec:
  loadBalancerClass: svc-mux.nowake.ai/payments.platform
```

```yaml
spec:
  loadBalancerClass: svc-mux.nowake.ai/rollup-p2p.platform
```

Keep automatic allocation ranges separate:

```text
payments: 20000-20099
rollup-p2p: 20100-20199
```

On GKE, one mux is limited to 100 Service ports. Split high-port workloads
before reaching that limit.

## Case 7: Manage Muxes With GitOps

Use GitOps for desired provider settings and channel specs, but ignore
controller-owned runtime fields.

Git should own:

- mux Service name, namespace, labels, provider annotations, `type`, static IP,
  and load balancer class.
- channel Service selectors, `loadBalancerClass`, ports, and user annotations.

Git should not own:

- mux `spec.ports`.
- generated mux Endpoints.
- controller-owned annotations such as `svc-mux.nowake.ai/ports`,
  `svc-mux.nowake.ai/channels`, `svc-mux.nowake.ai/topology`, and
  `svc-mux.nowake.ai/summary`.
- mux state ConfigMap contents, including automatic and static port claims.

Use [GitOps compatibility](gitops.md) for Argo CD and Flux examples.
