#!/bin/bash
k() {
    kubectl --context kind-kind "$@"
}

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo $SOURCE_DIR

k create namespace svc-mux --dry-run=client -o yaml | k apply -f -
k apply -f $SOURCE_DIR/manifests/
k patch svc -n svc-mux mux --subresource=status -p '{"status": {"loadBalancer": {"ingress": [{"hostname": "managed.local"}]}}}'
