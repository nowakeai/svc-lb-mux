# GKE LoadBalancer Setup

This guide shows how to run Service LoadBalancer Multiplexer on Google Kubernetes Engine (GKE) using GKE-managed LoadBalancer Services.

The default chart values target an external GKE passthrough Network Load Balancer in backend service mode. The normal operating model does not require the controller to create Google Cloud firewall rules, forwarding rules, or other cloud resources directly.

## Architecture On GKE

A GKE install has two Service roles:

- **Mux Service**: selectorless `type: LoadBalancer`; GKE creates the cloud load balancer for this Service.
- **Channel Service**: application-facing `type: LoadBalancer`; points at the mux through `spec.loadBalancerClass`.

Traffic flow:

```text
client
  -> GKE external passthrough Network Load Balancer
  -> mux Service port
  -> controller-managed mux Endpoints
  -> channel backend pods
```

Control flow:

```text
channel Service + channel Endpoints
  -> svc-lb-mux controller
  -> mux Service ports + mux Endpoints
  -> channel status.loadBalancer.ingress
```

The mux Service has no selector. The controller owns the mux runtime ports and Endpoints. When no channels exist, the controller keeps a placeholder `101/TCP` port so Kubernetes accepts the Service.

## Recommended Defaults

The chart defaults are GKE-oriented:

```yaml
defaultLoadBalancer:
  create: true
  name: mux
  annotations:
    cloud.google.com/l4-rbs: "enabled"
  loadBalancerClass: ""
  allocateLoadBalancerNodePorts: true
  portRange: "20000-20099"
  maxPorts: 100
  allocationConfigMapName: ""
```

`cloud.google.com/l4-rbs: "enabled"` asks GKE to create a backend service-based external passthrough Network Load Balancer.

Install with defaults:

```console
kubectl create namespace svc-mux
helm install svc-mux ./chart \
  --namespace svc-mux \
  --set image.tag=0.1.0

kubectl get svc mux -n svc-mux -w
```

The names `svc-mux` and `mux` are defaults only. Use names that match your namespace, product, or ownership model.

## Static External IP

For production, reserve a regional static external IPv4 address in the same region as the GKE cluster.

```console
PROJECT_ID=my-project
REGION=us-central1
ADDRESS_NAME=svc-mux-ip

gcloud compute addresses create $ADDRESS_NAME \
  --project $PROJECT_ID \
  --region $REGION

MUX_IP=$(gcloud compute addresses describe $ADDRESS_NAME \
  --project $PROJECT_ID \
  --region $REGION \
  --format='value(address)')
```

There are two common binding styles.

### Bind By IP Address

```yaml
defaultLoadBalancer:
  loadBalancerIP: "203.0.113.10"
```

```console
helm upgrade --install svc-mux ./chart \
  --namespace svc-mux \
  --set image.tag=0.1.0 \
  --set defaultLoadBalancer.loadBalancerIP=$MUX_IP
```

### Bind By Address Resource Name

Use the GKE address-name annotation when you want manifests to reference the reserved address resource instead of embedding the numeric IP.

```yaml
defaultLoadBalancer:
  annotations:
    cloud.google.com/l4-rbs: "enabled"
    networking.gke.io/load-balancer-ip-addresses: svc-mux-ip
  loadBalancerClass: networking.gke.io/l4-regional-external
```

```console
helm upgrade --install svc-mux ./chart \
  --namespace svc-mux \
  --set image.tag=0.1.0 \
  --set-string defaultLoadBalancer.annotations.networking\.gke\.io/load-balancer-ip-addresses=$ADDRESS_NAME \
  --set defaultLoadBalancer.loadBalancerClass=networking.gke.io/l4-regional-external
```

Set `loadBalancerClass` before creating the Service. Kubernetes treats Service load balancer class as immutable.

## Channel Services

A channel Service points at the mux through `spec.loadBalancerClass`:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: my-service
  namespace: my-namespace
spec:
  type: LoadBalancer
  loadBalancerClass: svc-mux.nowake.ai/mux.svc-mux
  allocateLoadBalancerNodePorts: false
  selector:
    app: my-app
  ports:
    - name: http
      port: 80
      targetPort: 8080
```

Channel rules:

- Use `<api-prefix>/<mux>[.<namespace>]` for `loadBalancerClass`.
- Name every port; port names are used as stable mapping identity.
- Set `allocateLoadBalancerNodePorts: false` unless a provider-specific workflow requires NodePorts.
- Each `(external port, protocol)` pair can be claimed by only one channel on the same mux.

By default, the controller uses `spec.ports[].port` as the external mux port.

To override the external port:

```yaml
metadata:
  annotations:
    svc-mux.nowake.ai/external-ports: "http:8080"
```

To allocate automatically from the mux port range:

```yaml
metadata:
  annotations:
    svc-mux.nowake.ai/external-ports: "http:auto"
```

Automatic assignments are stored in one ConfigMap per mux. Do not reuse one allocation ConfigMap across multiple muxes.

## GKE Port And Firewall Model

The controller deliberately stays inside the GKE Service LoadBalancer model:

- GKE owns forwarding rules, backend services, health checks, NEGs, and firewall rules.
- The controller does not need Google Cloud IAM permissions in the normal path.
- One GKE mux is limited to 100 Service ports.
- Larger workloads should be split across multiple mux Services.

The default GKE range matches the GKE mux limit:

```yaml
defaultLoadBalancer:
  portRange: "20000-20099"
  maxPorts: 100
```

If the controller detects a GKE-backed mux without `svc-mux.nowake.ai/max-ports`, it applies the GKE limit of 100 ports and emits `GkePortLimitApplied`. If a detected GKE mux configures a higher value, the controller caps the effective value to 100 and emits the same Warning event.

When a new channel would exceed the effective mux limit, the controller skips that channel and emits `MuxPortLimitExceeded`.

Do not manually edit GKE-managed firewall rules. If more than 100 one-port channels are needed, create additional muxes with non-overlapping ranges, for example:

```text
mux-a: 20000-20099
mux-b: 20100-20199
mux-c: 20200-20299
```

## Internal Load Balancer

For an internal GKE passthrough Network Load Balancer, use GKE's internal load balancer annotation and remove the external RBS annotation:

```yaml
defaultLoadBalancer:
  annotations:
    cloud.google.com/l4-rbs: ""
    networking.gke.io/load-balancer-type: Internal
  loadBalancerClass: ""
```

Choose internal versus external mode before production rollout because several Service load balancer parameters are immutable after creation.

## Capacity Planning

For one-port channels, the mux capacity is bounded by the smallest of:

```text
configured port range size
GKE Service LoadBalancer 100-port limit
Kubernetes Endpoints object size
Google Cloud regional quotas
```

With the default range:

```text
20099 - 20000 + 1 = 100 ports
```

So one GKE mux supports up to 100 one-port channel mappings in the current implementation.

A mux also consumes Google Cloud resources. Check regional/project quota for:

- forwarding rules
- external IPv4 addresses, if using reserved static IPs
- backend services
- health checks
- firewall rules

```console
gcloud compute project-info describe \
  --format="table(quotas.metric,quotas.limit,quotas.usage)"
```

The current controller writes a legacy `Endpoints` object for the mux. Endpoint data grows with channel count and backend replica count:

```text
endpoint_entries ~= channel_count * ready_backend_endpoints_per_channel
```

For high channel counts or large replica sets, split channels across muxes until EndpointSlice support lands.

## Validate Generated GCP Resources

Inspect Kubernetes state:

```console
kubectl get svc mux -n svc-mux -o wide
kubectl get endpoints mux -n svc-mux -o yaml
kubectl get configmap mux-port-allocations -n svc-mux -o yaml
kubectl get events -n svc-mux --sort-by=.lastTimestamp
```

Inspect Google Cloud resources:

```console
gcloud compute forwarding-rules list \
  --regions $REGION \
  --filter="IPAddress=$MUX_IP"

gcloud compute backend-services list \
  --regions $REGION

gcloud compute firewall-rules list \
  --filter="destinationRanges:$MUX_IP"
```

A validated GKE external mux should look like:

```text
reserved static IP
  -> regional forwarding rule, TCP, portRange 20000-20099
  -> regional backend service, protocol TCP
  -> GCE_VM_IP NEG containing GKE nodes
  -> GKE-managed firewall rule allowing mux Service ports
```

Public traffic should be tested against the mux external IP and every allocated channel port. The repository includes a pressure-test helper for 100 channels backed by 100 distinct pods:

```console
test-local/gke-pressure.py manifest \
  --namespace svc-mux-eip-test \
  --load-balancer-class svc-mux.nowake.ai/mux-eip.svc-mux-eip \
  | kubectl apply -f -

kubectl get configmap mux-eip-port-allocations \
  -n svc-mux-eip \
  -o jsonpath='{.data.allocations\.json}' > /tmp/mux-eip-allocations.json

test-local/gke-pressure.py probe \
  --host 203.0.113.10 \
  --allocations-json /tmp/mux-eip-allocations.json \
  --namespace svc-mux-eip-test
```

A successful probe reports:

```text
ok=100 missing=0 failed=0
```

See [gke-pressure-test-report.md](gke-pressure-test-report.md) for a sanitized validation report.

## Troubleshooting

Common checks:

- Static IP is regional and in the same region as the cluster.
- `loadBalancerClass` was set before Service creation.
- Channel ports are named.
- Channel Services use `allocateLoadBalancerNodePorts: false` unless NodePorts are intentionally required.
- GitOps ignores mux `spec.ports` for controller-managed mux Services.
- GKE-managed forwarding rule and firewall rule cover the mux port range.
- `MuxPortConflict`, `MuxPortLimitExceeded`, and `InvalidPortMapping` events explain rejected channels.

## References

- [GKE Service LoadBalancer concepts](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/service-load-balancer)
- [GKE Service LoadBalancer parameters](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/service-load-balancer-parameters)
- [GKE automatically created firewall rules](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/firewall-rules)
- [Cloud Load Balancing quotas and limits](https://cloud.google.com/load-balancing/quotas)
- [VPC firewall rules limits](https://cloud.google.com/vpc/docs/firewalls#limits)
- [Kubernetes Services: over-capacity Endpoints](https://kubernetes.io/docs/concepts/services-networking/service/#over-capacity-endpoints)
- [Kubernetes EndpointSlices](https://kubernetes.io/docs/concepts/services-networking/endpoint-slices/)
