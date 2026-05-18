# AGENTS.md

This file defines repository-specific constraints for AI agents and human contributors working on Service LoadBalancer Multiplexer.

## Project Boundaries

- Keep this project focused on Service LoadBalancer Multiplexer, abbreviated as `svc-lb-mux` or `lb-mux`.
- Do not introduce provider-specific behavior into generic controller code unless it is behind explicit configuration.
- Keep cloud-provider guidance in `docs/`; keep deployable Helm resources in `chart/`; keep runtime application code in `src/`.
- Do not mention legacy organization or product names in code, chart values, or documentation.
- Prefer Kubernetes-native resources and conventions over custom operational workflows.

## Source Layout

- `src/` contains the controller, API/debug server, and runtime web assets copied into the image.
- `chart/` contains the Helm chart only.
- `docs/` contains user-facing guides and architecture notes.
- `tools/` contains optional local/debug utilities that are not required for the controller runtime.
- New production Python modules belong under `src/`; do not recreate a `scripts/` runtime layout.

## Modularity Rules

- Keep files under 800 lines. If a file reaches that size, split it by responsibility before adding more behavior.
- Existing files over 800 lines are technical debt. Avoid expanding them except for small fixes, and prefer extracting cohesive modules first.
- Keep functions small enough to review in one screen when practical. Split orchestration, Kubernetes API access, reconciliation logic, and formatting into separate helpers.
- Avoid broad utility modules that collect unrelated behavior. A helper module should have a clear domain.
- Do not hide controller behavior behind clever abstractions. Prefer simple, explicit reconciliation steps with clear names.

## Backend Rules

- Use FastAPI for the debug/API webserver.
- Keep web route handlers thin. Put business logic, Kubernetes reads, topology building, and plugin behavior in separate modules.
- Keep authentication, request parsing, response formatting, and long-running network checks separated.
- Do not block the event loop with slow Kubernetes calls, TCP probes, or plugin execution. Use async APIs or move blocking work off the request path.
- Keep public API responses stable and documented when they are useful for operators or automation.

## Frontend Rules

- Treat the debug UI as a real product surface, not a throwaway HTML page.
- Keep frontend code modular. When UI behavior grows, split markup, state, API access, and rendering logic into separate files or components.
- Do not let a single frontend file grow beyond 800 lines.
- Keep provider-specific or product-specific debug views behind plugin-style feature boundaries.
- Avoid hardcoded environment assumptions in the UI. Read capabilities and enabled plugins from backend APIs.
- Keep the UI useful for operations: clear status, inspectable resources, readable mappings, and actionable errors.

## Helm And Kubernetes Rules

- Chart defaults should work for a generic install and currently prefer GKE-compatible defaults.
- Resource names such as `svc-mux` and `mux` are defaults and recommendations, not requirements.
- Preserve user-owned fields and support GitOps workflows. Avoid controller updates that fight declarative tools.
- Prefer annotations and labels with the configured `api.prefix`; do not hardcode the default prefix in controller logic.
- Any generated or controller-owned resource state must have clear ownership boundaries.
- When adding Service behavior, account for cloud-provider differences and document provider-specific requirements.

## Dependency Rules

- `uv.lock` is the source of truth for Python installs.
- Declare runtime dependencies in `pyproject.toml` and update `uv.lock` with `uv lock --upgrade` when needed.
- Do not commit generated `requirements.txt`; it is ignored and exists only for compatibility exports.
- Avoid new runtime dependencies unless they materially reduce complexity or provide a well-maintained standard implementation.

## Documentation Rules

- Update `README.md` when defaults, install behavior, concepts, or user-facing workflows change.
- Put detailed provider setup in `docs/`.
- Keep examples aligned with `chart/values.yaml`.
- Describe mux/channel terminology consistently:
  - A mux is the shared selectorless `LoadBalancer` Service.
  - A channel is an application-facing `LoadBalancer` Service that points at a mux.
- Avoid implying that recommended names are mandatory.

## Validation

Run the smallest relevant validation for the change. For broad changes, run:

```console
make check-lock
make lint
make template
make python-compile
make docker-build
```

For webserver changes, also run a route-level smoke test and verify auth behavior.

For chart changes, verify both `helm lint chart` and `helm template svc-mux chart --namespace svc-mux`.
