# Documentation

Use this page as the documentation map for Service LoadBalancer Multiplexer.
If you are new to the project, start with the quick path and then choose the
provider guide that matches your cluster.

## Start Here

- [Getting started](getting-started.md): install the controller, create a mux,
  expose a first channel Service, and verify the result.
- [Channel Service manual](channel-services.md): understand channel ports,
  `targetPort`, mux public ports, `external-ports`, and automatic allocation.
- [Troubleshooting](troubleshooting.md): diagnose missing mux ports, pending
  ingress, failed backends, GitOps drift, and port conflicts.

## Tutorials By Case

- [Tutorials and common cases](tutorials.md): copyable examples for HTTP,
  P2P/TCP workloads, automatic port allocation, migration from normal
  LoadBalancer Services, multiple muxes, and GitOps-managed installs.

## Provider Guides

- [GKE LoadBalancer setup](gke-lb-setup.md): recommended GKE install path,
  static IP binding, GKE-native firewall model, capacity planning, and
  validation commands.
- [AWS NLB setup](aws-nlb-setup.md): AWS Network Load Balancer setup notes and
  values examples for EKS.

## Operations And Internals

- [GitOps compatibility](gitops.md): Argo CD and Flux ignore rules for mux
  runtime fields.
- [Controller design and features](controller.md): detailed controller
  behavior, implementation notes, write ownership, events, and limitations.
- [GKE pressure test report](gke-pressure-test-report.md): sanitized
  100-channel, 100-backend validation report.

## Project

- [Roadmap](../ROADMAP.md)
- [Contributing](../CONTRIBUTING.md)
- [Security](../SECURITY.md)
- [Support](../SUPPORT.md)
