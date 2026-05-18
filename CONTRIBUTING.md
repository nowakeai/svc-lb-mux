# Contributing

Thanks for improving Service LoadBalancer Multiplexer. Keep changes small, reviewable, and aligned with Kubernetes controller and Helm chart conventions.

## Development Setup

Prerequisites:

- Python 3.13
- uv 0.10.x
- Helm 3
- Docker or another OCI image builder

Common validation commands:

```console
make check-lock
make lint
make template
make python-compile
make test
make docker-build
```

## Dependency Policy

Runtime dependencies are declared in `pyproject.toml` and locked in `uv.lock`. The lockfile is the source of truth for installs.

When changing dependencies:

```console
make update-deps
make check-lock
```

Do not commit generated `requirements.txt`. The `make requirements` target exists only for compatibility with external tools that cannot read `uv.lock`.

## Source Layout

- `src/`: controller and debug UI runtime code copied into the image.
- `chart/`: Helm chart.
- `docs/`: provider-specific setup guides.
- `tools/`: optional debugging utilities.

## Pull Request Checklist

Before opening a PR, verify:

- Python sources compile.
- Unit tests pass.
- Helm chart lints and renders.
- Docker image builds locally when runtime dependencies or source layout changes.
- Documentation examples match the chart defaults.
