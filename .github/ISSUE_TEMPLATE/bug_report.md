---
name: Bug report
about: Report reproducible controller, chart, or provider behavior
title: "bug: "
labels: bug
assignees: ""
---

## Summary

<!-- What happened? -->

## Expected Behavior

<!-- What did you expect? -->

## Environment

- Controller image tag or commit:
- Kubernetes version:
- Provider and cluster type:
- Helm chart version or commit:
- GitOps tool, if any:

## Reproduction

```yaml
# Minimal relevant manifests or values with secrets removed
```

## Evidence

```console
kubectl get svc,endpoints,configmap,event -A
kubectl logs -n <namespace> deployment/<controller>
```

## Additional Context

<!-- Provider resources, screenshots, or links. Do not include secrets. -->
