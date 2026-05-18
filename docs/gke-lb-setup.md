# Set up GKE LoadBalancer for Service LoadBalancer Multiplexer

This guide explains how to run the default multiplexer Service on Google Kubernetes Engine (GKE). The chart defaults are tuned for an external GKE passthrough Network Load Balancer.

References:

- [GKE Service LoadBalancer concepts](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/service-load-balancer)
- [GKE Service LoadBalancer parameters](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/service-load-balancer-parameters)

## How It Works

Service LoadBalancer Multiplexer uses two kinds of Services:

- **Mux Service**: a selectorless `type: LoadBalancer` Service. GKE provisions the external L4 load balancer for this Service.
- **Channel Services**: user Services that declare `spec.loadBalancerClass: <api-prefix>/<mux>[.<namespace>]`. The controller watches these Services and mirrors their declared external ports and backend endpoints onto the mux Service.

On GKE, a `type: LoadBalancer` Service creates Google Cloud load balancing resources. The chart defaults use the `cloud.google.com/l4-rbs: "enabled"` annotation, which asks GKE to create a backend service-based external passthrough Network Load Balancer.

The traffic path is:

1. A client connects to the mux load balancer IP on a channel port.
2. GKE forwards the TCP/UDP flow to cluster nodes for the mux Service.
3. Kubernetes Service routing sends the flow to the endpoint set maintained by the controller.
4. The channel Service receives the mux load balancer ingress status, so DNS automation can point users at the shared mux address.

The mux Service has no selector. The controller manages its ports and Endpoints object directly. When there are no channels, the controller keeps a placeholder `101/TCP` port so the Service remains valid.

## Default Chart Configuration

The default `chart/values.yaml` mux configuration is GKE-oriented. The Service name `mux` and namespace `svc-mux` are chart defaults, not required names:

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
  portRange: "20000-20099"
  maxPorts: 100
  allocationConfigMapName: ""
```

Install with defaults:

```console
kubectl create namespace svc-mux
helm install svc-mux ./chart \
  --namespace svc-mux \
  --set image.tag=latest
```

Check the mux Service:

```console
kubectl get svc mux -n svc-mux -w
```

When provisioning completes, `EXTERNAL-IP` shows the GKE load balancer IP.

## Bind A Static External IP

For production, reserve a regional static external IPv4 address and bind the mux Service to it. The static address must be in the same region as the GKE cluster.

Set environment variables:

```console
PROJECT_ID=my-project
REGION=us-central1
ADDRESS_NAME=svc-mux-ip
```

Reserve the address:

```console
gcloud compute addresses create $ADDRESS_NAME \
  --project $PROJECT_ID \
  --region $REGION
```

Get the reserved IP:

```console
MUX_IP=$(gcloud compute addresses describe $ADDRESS_NAME \
  --project $PROJECT_ID \
  --region $REGION \
  --format='value(address)')
echo $MUX_IP
```

### Option 1: Bind By IP Address

Use `defaultLoadBalancer.loadBalancerIP`:

```yaml
defaultLoadBalancer:
  loadBalancerIP: "203.0.113.10"
```

Install or upgrade:

```console
helm upgrade --install svc-mux ./chart \
  --namespace svc-mux \
  --set image.tag=latest \
  --set defaultLoadBalancer.loadBalancerIP=$MUX_IP
```

### Option 2: Bind By Address Resource Name

On supported GKE versions, use the GKE annotation that names the reserved address resource. For external passthrough Network Load Balancers, current GKE documentation requires the GKE load balancer class when using this annotation:

```yaml
defaultLoadBalancer:
  annotations:
    cloud.google.com/l4-rbs: "enabled"
    networking.gke.io/load-balancer-ip-addresses: svc-mux-ip
  loadBalancerClass: networking.gke.io/l4-regional-external
```

Install or upgrade:

```console
helm upgrade --install svc-mux ./chart \
  --namespace svc-mux \
  --set image.tag=latest \
  --set-string defaultLoadBalancer.annotations.networking\.gke\.io/load-balancer-ip-addresses=$ADDRESS_NAME \
  --set defaultLoadBalancer.loadBalancerClass=networking.gke.io/l4-regional-external
```

Use the resource-name annotation when you want Kubernetes manifests to reference the reserved address by name instead of embedding the numeric IP. Use `loadBalancerIP` when you want the most direct, provider-neutral Service field. Set the load balancer class before creating the Service; Service load balancer class is immutable after creation.

## GKE-Native Firewall Model

GKE automatically creates and reconciles the ingress allow firewall rule for a `type: LoadBalancer` Service. Service LoadBalancer Multiplexer should stay inside that native model: the controller does not need Google Cloud IAM permissions and does not create, update, or delete VPC firewall rules.

The practical rule for GKE is to keep each mux Service within GKE's documented Service LoadBalancer port model:

- Use a compact, contiguous mux port range.
- Keep one mux at or below 100 Service ports.
- Split larger workloads across multiple mux Services instead of asking one GKE Service to expose more than 100 ports.

The chart default follows this model:

~~~yaml
defaultLoadBalancer:
  portRange: "20000-20099"
  maxPorts: 100
~~~

With these settings, GKE can continue to own the forwarding rule and firewall rule. Adding a channel consumes one mux Service port inside the configured range; when the mux already has 100 ports, the controller emits a `MuxPortLimitExceeded` event and skips additional channels instead of pushing the Service into unsupported GKE behavior.

Do not manually edit the GKE-managed firewall rule. If you need more than 100 one-port channels, create additional mux Services with non-overlapping ranges, for example `20000-20099`, `20100-20199`, and `20200-20299`.

## Network Tier

GKE supports selecting the Google Cloud network tier for an external passthrough Network Load Balancer. Premium tier is the Google Cloud default. To be explicit:

```yaml
defaultLoadBalancer:
  annotations:
    cloud.google.com/network-tier: Premium
```

Use `Standard` only when your cluster, static address, and load balancing requirements are compatible with Standard tier.

## Internal Load Balancer

The chart defaults are for an external mux. For an internal GKE passthrough Network Load Balancer, use GKE's internal load balancer annotation and remove the external RBS annotation:

```yaml
defaultLoadBalancer:
  annotations:
    cloud.google.com/l4-rbs: ""
    networking.gke.io/load-balancer-type: Internal
  loadBalancerClass: ""
```

Internal and external Services use different Google Cloud load balancing resources. Choose the mode before production rollout because several Service load balancer parameters are immutable after creation.

## Channel Services

After the mux is ready, channel Services use the configured API prefix and the actual mux Service reference. With the chart defaults, the mux Service is `mux` in namespace `svc-mux`:

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

The channel still uses `type: LoadBalancer` so Kubernetes accepts the custom `loadBalancerClass`, but the channel does not need NodePorts. By default, the controller uses each channel `spec.ports[].port` as the mux external port. Set `allocateLoadBalancerNodePorts: false` on channel Services to avoid unnecessary NodePort allocation.

To expose a different mux port without changing the channel Service port, add the configured API prefix annotation:

```yaml
metadata:
  annotations:
    svc-mux.nowake.ai/external-ports: "http:8080"
```

Each `(external port, protocol)` pair can be used by only one channel on the same mux. For automatic assignment, configure `defaultLoadBalancer.portRange` on the mux and request `auto` on the channel:

```yaml
defaultLoadBalancer:
  portRange: "20000-20099"
  maxPorts: 100
  # Optional per-mux override. Defaults to <mux-name>-port-allocations.
  allocationConfigMapName: ""
```

```yaml
metadata:
  annotations:
    svc-mux.nowake.ai/external-ports: "http:auto"
```

Automatic assignments are stored in one ConfigMap per mux so the mapping remains stable across controller restarts and GitOps re-application without coupling unrelated muxes to the same object size or update stream. Do not reuse the same allocation ConfigMap name for multiple muxes.

## Capacity Planning And GCP Limits

The capacity of one mux on GKE is the smallest limit across the controller port allocation range, the GKE Service load balancer frontend, Kubernetes endpoint state, and Google Cloud project quotas.

For one-port channel Services, the controller-side port pool limit is:

~~~text
max_channels_by_port_pool = floor(port_range_size / ports_per_channel)
port_range_size = end_port - start_port + 1
~~~

With the default GKE range `20000-20099`:

~~~text
20099 - 20000 + 1 = 100 ports
~~~

So the default GKE pool can assign up to **100 one-port channel mappings**, which matches the chart default `maxPorts: 100` guard. Larger ranges can be used on providers that support them, but on GKE a single mux should stay at or below 100 Service ports.

Current GKE-specific planning should use this conservative bound for a single mux:

~~~text
single_mux_channels <= min(
  configured_port_range_size / ports_per_channel,
  GKE managed Service port/frontend behavior,
  Kubernetes endpoint object capacity,
  available Google Cloud quotas
)
~~~

### Practical GKE Bound

GKE documents Service load balancer port behavior around small discrete port sets and larger port sets. For backend service-based external passthrough Network Load Balancers, the Google Cloud forwarding rule can represent up to five discrete ports or one contiguous port range. GKE Service load balancer parameters also document the `all ports` behavior for more than five and up to 100 unique Service ports.

Because the current controller writes one mux `spec.ports[]` entry for each exposed channel port, **100 one-port channels per mux** is the GKE-native public traffic target. The chart enforces this with `maxPorts: 100` by default.

For more than roughly 100 one-port channels, prefer one of these approaches:

- Split channels across multiple mux Services, each with a compact contiguous range such as `20000-20099`, `20100-20199`, and `20200-20299`.
- Keep ranges contiguous. Sparse port choices can cause the forwarding rule, firewall rule, and Service ports to diverge in surprising ways.
- Validate the generated forwarding rule, backend service, health check, and firewall rule before declaring the mux ready for production traffic.
- Track EndpointSlice support in the controller roadmap before using one mux for very large channel counts.

### Google Cloud Resource Quotas

A GKE external passthrough Network Load Balancer consumes Google Cloud resources. Exact quota names and defaults can vary by project, region, and load balancer mode, so check the project quotas instead of relying on static numbers in this document.

For capacity planning, one mux usually consumes approximately:

| Resource | Planning impact |
| --- | --- |
| Regional forwarding rule | Usually one per mux load balancer. This often becomes the first project-level mux-count quota to check. |
| Regional external IPv4 address | One per mux when using a reserved static IP. |
| Backend service | One per backend service-based external passthrough Network Load Balancer. |
| Health check | GKE creates health-check resources for the load balancer path. |
| Firewall rule | GKE creates firewall rules for Service load balancer traffic and health checks. |
| Service ports | One mux Service port per exposed channel port in the current controller implementation. |

Check current quota usage:

~~~console
gcloud compute project-info describe \
  --format="table(quotas.metric,quotas.limit,quotas.usage)"
~~~

Filter the output for forwarding rules, addresses, backend services, health checks, and firewall rules. The number of mux Services that can be created in one project is approximately:

~~~text
max_muxes_by_project_quota = min(
  remaining_regional_forwarding_rules,
  remaining_regional_external_ipv4_addresses_if_static,
  remaining_backend_services,
  remaining_health_checks,
  remaining_firewall_rules
)
~~~

### Endpoint State Size

The current implementation manages a legacy `Endpoints` object for the mux. Its data grows with both channel count and backend pod count:

~~~text
endpoint_entries ~= channel_count * ready_backend_endpoints_per_channel
~~~

For example, 100 channels pointing at three ready pods each produces roughly 300 endpoint address entries in the mux Endpoints object. Kubernetes documents legacy Endpoints over-capacity behavior at 1000 endpoints, and large Endpoints objects are also harder for operators to inspect and for controllers to update safely. For high channel counts or large backend replica sets, split channels across muxes until the controller supports EndpointSlice natively.

### Validation Commands

After deploying a mux, inspect the generated resources:

~~~console
kubectl get svc mux -n svc-mux -o wide
kubectl get endpoints mux -n svc-mux -o yaml
kubectl get configmap <mux-name>-port-allocations -n svc-mux -o yaml
~~~

Inspect Google Cloud load balancer resources:

~~~console
gcloud compute forwarding-rules list \
  --regions $REGION \
  --filter="IPAddress=34.83.176.141"

gcloud compute firewall-rules list \
  --filter="network:default AND allowed.tcp:*"
~~~

Confirm that the GKE-managed forwarding rule and firewall rule cover the mux port range. Public traffic should be tested against the mux external IP and every allocated channel port before considering the capacity test successful.

### References

- [GKE Service LoadBalancer concepts](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/service-load-balancer)
- [GKE Service LoadBalancer parameters](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/service-load-balancer-parameters)
- [Cloud Load Balancing quotas and limits](https://cloud.google.com/load-balancing/quotas)
- [VPC firewall rules limits](https://cloud.google.com/vpc/docs/firewalls#limits)
- [GKE automatically created firewall rules](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/firewall-rules)
- [Kubernetes Services: over-capacity Endpoints](https://kubernetes.io/docs/concepts/services-networking/service/#over-capacity-endpoints)
- [Kubernetes EndpointSlices](https://kubernetes.io/docs/concepts/services-networking/endpoint-slices/)

## Troubleshooting

Check the mux Service and events:

```console
kubectl describe svc mux -n svc-mux
kubectl get events -n svc-mux --sort-by=.lastTimestamp
```

Check controller logs:

```console
kubectl logs -n svc-mux -l app.kubernetes.io/name=svc-mux -f
```

Common issues:

- The static IP must be regional and in the same region as the GKE cluster.
- Some load balancer annotations and Service fields are effectively immutable. Recreate the Service if changing load balancer mode.
- Channel Service ports must be named. The controller rejects unnamed ports because it hashes `namespace/name/portName` into stable mux port names.
- Channel Services should normally set `allocateLoadBalancerNodePorts: false`; the mux owns the provider load balancer ports.
- If two channels on the same mux request the same external port and protocol, the controller emits a `MuxPortConflict` event and skips the conflicting channel mapping.
- Firewall and health-check resources are managed by GKE for the Service load balancer. If traffic does not pass, inspect the generated forwarding rule, backend service, health check, and firewall rules in the Google Cloud project.
