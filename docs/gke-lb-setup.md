# Set up GKE LoadBalancer for Service LoadBalancer Multiplexer

This guide explains how to run the default multiplexer Service on Google Kubernetes Engine (GKE). The chart defaults are tuned for an external GKE passthrough Network Load Balancer.

References:

- [GKE Service LoadBalancer concepts](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/service-load-balancer)
- [GKE Service LoadBalancer parameters](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/service-load-balancer-parameters)

## How It Works

Service LoadBalancer Multiplexer uses two kinds of Services:

- **Mux Service**: a selectorless `type: LoadBalancer` Service. GKE provisions the external L4 load balancer for this Service.
- **Channel Services**: user Services that declare `spec.loadBalancerClass: <api-prefix>/<mux>[.<namespace>]`. The controller watches these Services and mirrors their allocated NodePorts and backend endpoints onto the mux Service.

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
  selector:
    app: my-app
  ports:
    - name: http
      port: 80
      targetPort: 8080
```

The channel still needs `type: LoadBalancer` so Kubernetes allocates NodePorts. The controller uses those NodePorts as externally reachable mux ports.

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
- Firewall and health-check resources are managed by GKE for the Service load balancer. If traffic does not pass, inspect the generated forwarding rule, backend service, health check, and firewall rules in the Google Cloud project.
