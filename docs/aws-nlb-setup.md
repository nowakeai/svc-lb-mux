# Set Up AWS NLB for Service LoadBalancer Multiplexer

This guide explains how to run the mux Service behind an AWS Network Load Balancer (NLB) on EKS. It assumes the AWS Load Balancer Controller is already installed in the cluster.

References:

- [AWS Load Balancer Controller installation](https://kubernetes-sigs.github.io/aws-load-balancer-controller/latest/deploy/installation/)
- [AWS Load Balancer Controller NLB guide](https://kubernetes-sigs.github.io/aws-load-balancer-controller/latest/guide/service/nlb/)
- [AWS Load Balancer Controller Service annotations](https://kubernetes-sigs.github.io/aws-load-balancer-controller/latest/guide/service/annotations/)

## How It Works

Service LoadBalancer Multiplexer uses a selectorless mux Service and channel Services. On EKS, the mux Service is the only Service that should provision an AWS NLB. Channel Services still use `type: LoadBalancer`, but their `spec.loadBalancerClass` points to the mux controller, for example `svc-mux.nowake.ai/mux.svc-mux`, so they are reconciled by Service LoadBalancer Multiplexer instead of AWS Load Balancer Controller.

The traffic path is:

1. A client connects to the NLB DNS name or static Elastic IP on a channel port.
2. AWS NLB forwards the flow to Kubernetes backends for the mux Service.
3. The mux Service ports and Endpoints are maintained by the controller from channel Services.
4. The controller syncs the mux load balancer status back to each channel Service.

## Prerequisites

- EKS cluster with AWS Load Balancer Controller installed.
- Subnets tagged so the controller can discover public or private subnets for the NLB.
- Amazon VPC CNI with routable pod IPs if you use NLB `ip` target type.
- One Elastic IP allocation per public subnet if you want stable public IPs.

## Recommended NLB Values

For EKS with `ip` targets, configure the chart-created mux Service like this. The Service name `mux` is only the chart default; use a semantic name or a project namespace if that fits your deployment better:

```yaml
defaultLoadBalancer:
  create: true
  name: mux
  annotations:
    # Disable the GKE default annotation from chart/values.yaml.
    cloud.google.com/l4-rbs: ""
    service.beta.kubernetes.io/aws-load-balancer-scheme: internet-facing
    service.beta.kubernetes.io/aws-load-balancer-nlb-target-type: ip
  loadBalancerClass: service.k8s.aws/nlb
  allocateLoadBalancerNodePorts: false
```

Save those settings in an AWS-specific values file, for example `aws-values.yaml`, and install or upgrade:

```console
helm upgrade --install svc-mux oci://ghcr.io/nowakeai/charts/svc-lb-mux \
  --version 0.1.0 \
  --namespace svc-mux \
  --create-namespace \
  --values aws-values.yaml
```

The chart skips annotations with empty values, so `cloud.google.com/l4-rbs: ""` removes the GKE default annotation from the rendered mux Service.

## Bind Static Elastic IPs

For an internet-facing NLB, allocate one Elastic IP per subnet/AZ that the NLB will use. Then set `service.beta.kubernetes.io/aws-load-balancer-eip-allocations` to the comma-separated allocation IDs. The number of EIP allocations must match the number of NLB subnets.

```yaml
defaultLoadBalancer:
  annotations:
    cloud.google.com/l4-rbs: ""
    service.beta.kubernetes.io/aws-load-balancer-scheme: internet-facing
    service.beta.kubernetes.io/aws-load-balancer-nlb-target-type: ip
    service.beta.kubernetes.io/aws-load-balancer-subnets: subnet-aaa,subnet-bbb
    service.beta.kubernetes.io/aws-load-balancer-eip-allocations: eipalloc-aaa,eipalloc-bbb
  loadBalancerClass: service.k8s.aws/nlb
  allocateLoadBalancerNodePorts: false
```

Using explicit subnets keeps the EIP-to-AZ mapping deterministic. If you rely on subnet auto-discovery, still make sure the allocation count matches the discovered subnet count.

## Instance Target Mode

If your cluster cannot use pod IP targets, use NLB `instance` target type and keep NodePort allocation enabled:

```yaml
defaultLoadBalancer:
  annotations:
    cloud.google.com/l4-rbs: ""
    service.beta.kubernetes.io/aws-load-balancer-scheme: internet-facing
    service.beta.kubernetes.io/aws-load-balancer-nlb-target-type: instance
  loadBalancerClass: service.k8s.aws/nlb
  allocateLoadBalancerNodePorts: true
```

Instance mode routes NLB traffic to node ports first, then kube-proxy forwards traffic to pods. IP mode routes directly to pod IPs and is usually the better fit when VPC CNI is available.

## Channel Services

Channel Services should use the mux controller load balancer class, not `service.k8s.aws/nlb`. Replace `mux.svc-mux` with the mux Service name and namespace you chose:

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
    - name: p2p
      port: 30303
      targetPort: 30303
```

By default, the controller uses each channel `spec.ports[].port` as the mux external port and mirrors the channel endpoints onto the mux Service. Channel Services should normally set `allocateLoadBalancerNodePorts: false`; the mux Service is the only Service that needs provider-facing load balancer plumbing.

If the desired public mux port is already in `spec.ports[].port`, no `external-ports` annotation is needed. For the full port model, see [Channel Service manual](channel-services.md).

To expose a different mux port without changing the channel Service port, add the configured API prefix annotation:

```yaml
metadata:
  annotations:
    svc-mux.nowake.ai/external-ports: "p2p:30304"
```

Each `(external port, protocol)` pair can be used by only one channel on the same mux. For automatic assignment, configure `defaultLoadBalancer.portRange` on the mux and request `auto` on the channel:

```yaml
defaultLoadBalancer:
  portRange: "20000-20099"
  # Optional per-mux override. Defaults to <mux-name>-port-allocations.
  allocationConfigMapName: ""
```

```yaml
metadata:
  annotations:
    svc-mux.nowake.ai/external-ports: "p2p:auto"
```

Automatic assignments and static port claims are stored in one state ConfigMap per mux so mappings remain stable across controller restarts and GitOps re-application without coupling unrelated muxes to the same object size or update stream. Do not reuse the same state ConfigMap name for multiple muxes. Conflicts are reported with a `MuxPortConflict` event.

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

- The mux Service must use `loadBalancerClass: service.k8s.aws/nlb`; channel Services must use the Service LoadBalancer Multiplexer class.
- For `ip` target type, pod IPs must be routable in the VPC through Amazon VPC CNI.
- For `instance` target type on the mux Service, `allocateLoadBalancerNodePorts` must stay enabled or NodePorts must be allocated manually. Channel Services can still keep `allocateLoadBalancerNodePorts: false` because their NodePorts are not used for mux port selection.
- For static public IPs, the NLB must be internet-facing and the EIP allocation count must match the selected subnets.
- AWS Load Balancer Controller manages frontend and backend security groups in current versions. If targets are unhealthy, inspect the target group health reason, NLB security groups, backend security group rules, and subnet tags before manually adding broad security group rules.
