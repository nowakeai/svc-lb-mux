# GitOps Compatibility

Service LoadBalancer Multiplexer is designed to work with GitOps tools by keeping a clear boundary between user-authored desired state and controller-owned runtime state.

The key rule is simple: GitOps may create mux and channel Services, but the controller owns the runtime fields that are derived from active channels.

## Ownership Model

| Resource | GitOps should own | Controller should own |
| --- | --- | --- |
| Controller chart | Deployment, RBAC, ServiceAccount, debug Service, chart values | Nothing, except normal Kubernetes-generated status |
| Chart-created default mux Service | Name, namespace, provider annotations, labels, `type`, `loadBalancerClass`, static IP settings | `spec.ports`, mux runtime annotations |
| Additional mux Service | Name, namespace, provider annotations, labels, `type`, `loadBalancerClass`, static IP settings | `spec.ports`, mux runtime annotations |
| Channel Service | Selector, app-facing `spec.ports`, `loadBalancerClass`, user annotations | `status.loadBalancer`, `<api-prefix>/ports` annotation |
| Mux Endpoints | Do not manage from Git | Whole generated Endpoints object |
| Per-mux state ConfigMap | Do not manage from Git, unless repairing state manually | `allocations.json`, `portClaims`, and metadata |

Controller-owned annotations use the configured API prefix. With the default prefix, those include:

- `svc-mux.nowake.ai/ports` on channel Services.
- `svc-mux.nowake.ai/channels`, `svc-mux.nowake.ai/topology`, and `svc-mux.nowake.ai/summary` on mux Services.
- `svc-mux.nowake.ai/managed`, `svc-mux.nowake.ai/mux`, and `svc-mux.nowake.ai/channels` on generated Endpoints.

Do not put controller-owned annotation keys in Git. If a GitOps tool reports drift or rewrites them anyway, configure explicit ignore rules for those keys.

## Why Mux Ports Must Be Ignored

A Kubernetes Service must have at least one port, so a mux Service manifest usually contains a placeholder such as `101/TCP`. That placeholder only makes the Service valid before any channels are attached. It is not the desired runtime port list.

Once channels attach to the mux, the controller rewrites mux `spec.ports` from the active channel mappings. If GitOps keeps applying the placeholder from Git, the resource will churn:

1. GitOps applies placeholder `spec.ports`.
2. The controller replaces it with channel-derived ports.
3. GitOps detects drift and applies the placeholder again.
4. The controller fixes it again.

This can cause noisy sync status, repeated Service updates, and provider load balancer reconciliation churn. Always ignore `/spec/ports` for every mux Service that is managed by GitOps.

## Mux Service Template

Use this template when creating an additional mux from Git. Replace the name, namespace, API prefix, provider annotations, and load balancer settings for your environment.

```yaml
apiVersion: v1
kind: Service
metadata:
  name: mux
  namespace: my-project
  annotations:
    svc-mux.nowake.ai/multiplexer: "true"
    # Provider-specific settings belong in Git.
    # cloud.google.com/l4-rbs: "enabled"
spec:
  type: LoadBalancer
  allocateLoadBalancerNodePorts: true
  # loadBalancerClass, loadBalancerIP, and provider annotations are optional
  # and depend on the cloud provider.
  ports:
    # Placeholder only. GitOps must ignore /spec/ports for this mux.
    - name: placeholder
      protocol: TCP
      port: 101
```

The controller keeps provider settings intact and replaces only the runtime ports derived from channels.

## Generated Runtime Resources

The mux Endpoints object is controller-owned derived state. Do not put it in Git.

```yaml
metadata:
  labels:
    app.kubernetes.io/managed-by: svc-lb-mux
    app.kubernetes.io/component: mux-endpoints
  annotations:
    svc-mux.nowake.ai/managed: "true"
    svc-mux.nowake.ai/mux: svc-mux/mux
    svc-mux.nowake.ai/channels: '["app/api"]'
```

Each mux gets its own controller-owned state ConfigMap. It stores stable static claims, explicit external-port claims, and `external-ports: "name:auto"` assignments in `allocations.json`, plus mux owner metadata. Do not overwrite it from Git unless you are intentionally repairing mux state, and do not point multiple muxes at the same state ConfigMap.

## Annotation Drift

Annotations are not automatically safe just because they are annotations.

Argo CD documents controller or webhook mutations as a common reason for resources to become `OutOfSync`, and supports `ignoreDifferences` for those fields. If automated sync should also respect ignored fields during apply, add `RespectIgnoreDifferences=true`.

Flux Helm Controller supports drift detection ignore rules with JSON Pointer paths. Flux Kustomize Controller controls apply behavior through Server-Side Apply policies; its default behavior is to reconcile managed resources toward the desired manifests, while `Merge`, `IfNotPresent`, and `Ignore` change that behavior.

Practical rule for this project:

- Do not include controller-owned annotation keys in Git.
- Ignore controller-owned annotation keys if your GitOps tool reports drift on them.
- Never rely on GitOps preserving a controller-owned field that is also declared in Git.
- Keep provider annotations in Git; those are user-owned desired state.

## Argo CD

### Controller Chart With Default Mux

Use this when the same Argo CD Application installs the controller chart and the chart creates the default mux Service.

If `defaultLoadBalancer.create=false`, remove the mux-specific ignore entry because the chart does not create a mux Service.

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: svc-lb-mux
  namespace: argocd
spec:
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - RespectIgnoreDifferences=true
  ignoreDifferences:
    # Chart-created default mux Service.
    - group: ""
      kind: Service
      name: mux
      namespace: svc-mux
      jsonPointers:
        - /spec/ports
        - /metadata/annotations/svc-mux.nowake.ai~1channels
        - /metadata/annotations/svc-mux.nowake.ai~1topology
        - /metadata/annotations/svc-mux.nowake.ai~1summary
    # Optional: channel Service status drift, if this Application also owns channels.
    - group: ""
      kind: Service
      jsonPointers:
        - /status
```

JSON Pointer paths must escape `/` as `~1`, so `svc-mux.nowake.ai/channels` becomes `svc-mux.nowake.ai~1channels`.

### Additional Mux Services

Use this in the Argo CD Application that owns an additional mux Service manifest. Add one entry per mux, or keep a narrow naming convention and generate these entries from your ApplicationSet.

```yaml
spec:
  syncPolicy:
    syncOptions:
      - RespectIgnoreDifferences=true
  ignoreDifferences:
    - group: ""
      kind: Service
      name: mux
      namespace: my-project
      jsonPointers:
        - /spec/ports
        - /metadata/annotations/svc-mux.nowake.ai~1channels
        - /metadata/annotations/svc-mux.nowake.ai~1topology
        - /metadata/annotations/svc-mux.nowake.ai~1summary
```

Keep `/spec/ports` scoped to mux Services. Do not ignore `/spec/ports` for every Service in the cluster.

### System-Level Argo CD Customization

If many Applications create mux Services, you may put annotation ignores in `argocd-cm`. Keep this conservative. A system-level `/spec/ports` ignore for all Services is usually too broad.

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: argocd-cm
  namespace: argocd
data:
  resource.customizations.ignoreDifferences.Service: |
    jsonPointers:
      - /status
      - /metadata/annotations/svc-mux.nowake.ai~1ports
      - /metadata/annotations/svc-mux.nowake.ai~1channels
      - /metadata/annotations/svc-mux.nowake.ai~1topology
      - /metadata/annotations/svc-mux.nowake.ai~1summary
```

Prefer Application-level ignore rules for mux `/spec/ports`, because only mux Services should have controller-owned ports.

## Flux

### HelmRelease With Default Mux

Use this when Flux Helm Controller installs this chart and `defaultLoadBalancer.create=true`.

```yaml
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: svc-mux
  namespace: svc-mux
spec:
  driftDetection:
    mode: enabled
    ignore:
      # Chart-created default mux Service.
      - target:
          kind: Service
          name: mux
          namespace: svc-mux
        paths:
          - /spec/ports
          - /metadata/annotations/svc-mux.nowake.ai~1channels
          - /metadata/annotations/svc-mux.nowake.ai~1topology
          - /metadata/annotations/svc-mux.nowake.ai~1summary
      # Optional: channel Service status drift, if the release owns channels.
      - target:
          kind: Service
        paths:
          - /status
```

If `defaultLoadBalancer.create=false`, remove the mux-specific ignore entry.

### Additional Mux Services Managed By Flux Helm

If you package additional mux Services in a HelmRelease, add one ignore target per mux Service:

```yaml
spec:
  driftDetection:
    mode: enabled
    ignore:
      - target:
          kind: Service
          name: mux
          namespace: my-project
        paths:
          - /spec/ports
          - /metadata/annotations/svc-mux.nowake.ai~1channels
          - /metadata/annotations/svc-mux.nowake.ai~1topology
          - /metadata/annotations/svc-mux.nowake.ai~1summary
```

### Additional Mux Services Managed By Flux Kustomization

Flux Kustomization does not provide the same per-resource JSON Pointer drift ignore interface as Flux HelmRelease. For a mux Service rendered as plain YAML, choose one of these patterns:

- Prefer Flux HelmRelease if you need precise field-level drift ignore for `/spec/ports`.
- Use `kustomize.toolkit.fluxcd.io/ssa: IfNotPresent` if the mux Service should be created by Flux once and then left to the controller/provider after it exists.
- Use `kustomize.toolkit.fluxcd.io/ssa: Merge` only for additive non-overlapping fields. It is not a fix for mux `spec.ports`, because `spec.ports` is declared in Git as a placeholder and is also updated by the controller.
- Do not use the default `Override` behavior for mux Services unless you have another mechanism preventing `/spec/ports` churn.

Copyable `IfNotPresent` mux template:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: mux
  namespace: my-project
  annotations:
    svc-mux.nowake.ai/multiplexer: "true"
    kustomize.toolkit.fluxcd.io/ssa: IfNotPresent
    # Provider-specific settings belong in Git before first apply.
    # cloud.google.com/l4-rbs: "enabled"
spec:
  type: LoadBalancer
  allocateLoadBalancerNodePorts: true
  ports:
    - name: placeholder
      protocol: TCP
      port: 101
```

Use `IfNotPresent` deliberately: Flux will create the Service if it is missing, but later changes to that Service manifest in Git will not be applied automatically. If you need ongoing Git-managed provider settings and field-level ignore for `/spec/ports`, use HelmRelease drift ignore instead.

## Operational Checks

Inspect controller-owned state with:

```console
kubectl describe svc mux -n svc-mux
kubectl get endpoints mux -n svc-mux -o yaml
kubectl get configmap <mux-name>-port-allocations -n svc-mux -o yaml
kubectl get events -n svc-mux --sort-by=.lastTimestamp
```

If you see repeated GitOps/controller churn, check whether GitOps is applying mux `spec.ports`, generated mux Endpoints, state ConfigMaps, or controller-owned annotations.

## References

- Argo CD diff customization: <https://argo-cd.readthedocs.io/en/stable/user-guide/diffing/>
- Argo CD `RespectIgnoreDifferences`: <https://argo-cd.readthedocs.io/en/stable/user-guide/sync-options/#respect-ignore-differences-configs>
- Flux HelmRelease drift detection: <https://fluxcd.io/flux/components/helm/helmreleases/#drift-detection>
- Flux Kustomization apply policies: <https://fluxcd.io/flux/components/kustomize/kustomizations/#controlling-the-apply-behavior-of-resources>
