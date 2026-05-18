# Roadmap

This roadmap describes the near-term direction for Service LoadBalancer Multiplexer. It is not a commitment to specific release dates.

## Current Focus

- Harden GKE and EKS provider behavior while staying inside native Kubernetes and cloud-provider LoadBalancer semantics.
- Keep GitOps workflows predictable by clearly separating user-owned fields from controller-owned runtime fields.
- Improve operator readability for mux ports, channel mappings, generated Endpoints, allocation ConfigMaps, and events.
- Keep the controller modular, testable, and small enough for external contributors to review.

## Planned Work

- EndpointSlice support for larger and more readable backend state.
- Integration tests against local Kubernetes clusters.
- Provider-focused validation scripts for GKE and EKS.
- Debug UI rewrite with modular backend routes, safer auth, and plugin boundaries.
- Optional diagnostic plugins, including generic TCP checks and domain-specific networking checks.
- Upgrade and migration notes for early adopters.

## Design Principles

- Prefer Kubernetes-native resources over custom cloud automation.
- Do not require cloud IAM permissions unless a provider-specific feature truly needs them.
- Keep defaults conservative and easy to reason about.
- Treat mux/channel naming as recommended vocabulary, not a naming mandate.
- Keep nowake.ai open-source infrastructure projects practical, auditable, and operator-friendly.
