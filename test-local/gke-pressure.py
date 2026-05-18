#!/usr/bin/env python3
"""Generate and validate GKE mux pressure-test resources."""

import argparse
import json
import sys
import urllib.request

DEFAULT_IMAGE = "hashicorp/http-echo:1.0"
DEFAULT_RESPONSE_PREFIX = "svc-lb-mux-channel"


def yaml_doc(doc: str):
    print("---")
    print(doc.strip())


def command_manifest(args):
    yaml_doc(
        f"""
apiVersion: v1
kind: Namespace
metadata:
  name: {args.namespace}
"""
    )
    for index in range(1, args.count + 1):
        name = f"{args.name_prefix}-{index:03d}"
        response = f"{args.response_prefix}-{index:03d}"
        yaml_doc(
            f"""
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {name}
  namespace: {args.namespace}
  labels:
    app.kubernetes.io/name: svc-lb-mux-pressure
    app.kubernetes.io/component: backend
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: svc-lb-mux-pressure
      app.kubernetes.io/instance: {name}
  template:
    metadata:
      labels:
        app.kubernetes.io/name: svc-lb-mux-pressure
        app.kubernetes.io/instance: {name}
    spec:
      containers:
        - name: http
          image: {args.image}
          args:
            - -listen=:8080
            - -text={response}
          ports:
            - name: http
              containerPort: 8080
"""
        )
        yaml_doc(
            f"""
apiVersion: v1
kind: Service
metadata:
  name: {name}
  namespace: {args.namespace}
  annotations:
    {args.api_prefix}/external-ports: http:auto
spec:
  type: LoadBalancer
  loadBalancerClass: {args.load_balancer_class}
  allocateLoadBalancerNodePorts: false
  selector:
    app.kubernetes.io/name: svc-lb-mux-pressure
    app.kubernetes.io/instance: {name}
  ports:
    - name: http
      port: {args.service_port}
      targetPort: http
      protocol: TCP
"""
        )


def load_allocations(path):
    with open(path, encoding="utf-8") as file:
        state = json.load(file)
    return state.get("allocations", [])


def command_probe(args):
    allocations = load_allocations(args.allocations_json)
    expected = {
        f"{args.name_prefix}-{index:03d}": f"{args.response_prefix}-{index:03d}"
        for index in range(1, args.count + 1)
    }
    ports_by_service = {
        item["service"]: int(item["port"])
        for item in allocations
        if item.get("namespace") == args.namespace and item.get("service") in expected
    }

    missing = sorted(set(expected) - set(ports_by_service))
    failures = []
    successes = []

    for service, body in sorted(expected.items()):
        port = ports_by_service.get(service)
        if port is None:
            continue
        url = f"http://{args.host}:{port}/"
        try:
            with urllib.request.urlopen(url, timeout=args.timeout) as response:
                actual = response.read().decode().strip()
        except Exception as error:  # noqa: BLE001 - report every failed probe.
            failures.append((service, port, f"request failed: {error}"))
            continue
        if actual != body:
            failures.append((service, port, f"expected {body!r}, got {actual!r}"))
        else:
            successes.append((service, port))

    print(f"ok={len(successes)} missing={len(missing)} failed={len(failures)}")
    if missing:
        print("missing services:", ", ".join(missing[:20]))
    for service, port, message in failures[:20]:
        print(f"{service}:{port} {message}")
    if missing or failures:
        return 1
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    manifest = subparsers.add_parser("manifest", help="print pressure-test YAML")
    manifest.add_argument("--namespace", default="svc-mux-eip-test")
    manifest.add_argument("--name-prefix", default="channel")
    manifest.add_argument("--count", type=int, default=100)
    manifest.add_argument("--image", default=DEFAULT_IMAGE)
    manifest.add_argument("--api-prefix", default="svc-mux.nowake.ai")
    manifest.add_argument("--load-balancer-class", required=True)
    manifest.add_argument("--service-port", type=int, default=8080)
    manifest.add_argument("--response-prefix", default=DEFAULT_RESPONSE_PREFIX)
    manifest.set_defaults(func=command_manifest)

    probe = subparsers.add_parser("probe", help="probe mux ports using allocation JSON")
    probe.add_argument("--host", required=True)
    probe.add_argument("--allocations-json", required=True)
    probe.add_argument("--namespace", default="svc-mux-eip-test")
    probe.add_argument("--name-prefix", default="channel")
    probe.add_argument("--count", type=int, default=100)
    probe.add_argument("--response-prefix", default=DEFAULT_RESPONSE_PREFIX)
    probe.add_argument("--timeout", type=float, default=3.0)
    probe.set_defaults(func=command_probe)
    return parser


def main():
    args = build_parser().parse_args()
    result = args.func(args)
    return int(result or 0)


if __name__ == "__main__":
    sys.exit(main())
