#!/bin/bash
k() {
    kubectl --context kind-kind "$@"
}

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo $SOURCE_DIR

k apply -f $SOURCE_DIR/manifests/
k patch svc mux --subresource=status -p '{"status": {"loadBalancer": {"ingress": [{"hostname": "managed.local"}]}}}'
