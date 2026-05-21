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

## nowake.ai Docs Site Source Contract

Some documents in this repository are rendered by the nowake.ai website docs
site through its explicit source manifest. Keep those source documents suitable
for both this repository and the website renderer:

- Keep the project repository as the source of truth; do not add website-only
  Starlight frontmatter here.
- Start each public source document with exactly one top-level `#` heading. The
  website sync step removes that heading and injects site frontmatter from its
  manifest.
- Use relative links for other repository docs. The website sync step rewrites
  manifest-listed links to `/docs/...` and leaves other repository links as
  GitHub source links.
- Keep public docs task-oriented: problem, prerequisites, commands or config,
  expected output, common failure signals, current limits, and next links.
- Keep provider limits and GitOps/runtime-field caveats close to the commands
  they affect.

## Project

- [Roadmap](../ROADMAP.md)
- [Contributing](../CONTRIBUTING.md)
- [Security](../SECURITY.md)
- [Support](../SUPPORT.md)
