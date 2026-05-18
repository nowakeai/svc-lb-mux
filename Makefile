.PHONY: help requirements update-deps check-lock upgrade-chart update lint template python-compile test docker-build

CHART_DIR ?= chart
IMAGE ?= ghcr.io/nowakeai/svc-lb-mux

help:
	@echo "Available targets:"
	@echo "  requirements      - Export a compatibility requirements.txt from uv.lock"
	@echo "  upgrade-chart     - Bump patch version in chart/Chart.yaml"
	@echo "  update-deps       - Upgrade Python dependencies in uv.lock"
	@echo "  lint              - Lint Helm chart and Python code"
	@echo "  template          - Render Helm chart"
	@echo "  check-lock        - Verify uv.lock is up to date"
	@echo "  python-compile    - Compile Python sources"
	@echo "  test              - Run unit tests"
	@echo "  docker-build      - Build controller image locally"

requirements:
	@echo "Exporting compatibility requirements.txt..."
	@uv export --format requirements-txt --no-editable --locked > requirements.txt
	@echo "Generated ignored requirements.txt from uv.lock"

update-deps:
	@uv lock --upgrade

check-lock:
	@uv lock --check

upgrade-chart:
	@echo "Bumping chart version..."
	@current=$$(grep '^version:' $(CHART_DIR)/Chart.yaml | awk '{print $$2}'); \
	major=$$(echo $$current | cut -d. -f1); \
	minor=$$(echo $$current | cut -d. -f2); \
	patch=$$(echo $$current | cut -d. -f3); \
	new_patch=$$((patch + 1)); \
	new_version="$$major.$$minor.$$new_patch"; \
	sed -i "s/^version: .*/version: $$new_version/" $(CHART_DIR)/Chart.yaml; \
	echo "Chart version: $$current -> $$new_version"

update: update-deps upgrade-chart
	@echo "Update complete"

lint:
	@helm lint $(CHART_DIR)
	@uv run ruff check

template:
	@helm template svc-mux $(CHART_DIR)

python-compile:
	@uv run python -m compileall -q src tests

test:
	@PYTHONPATH=src uv run python -m unittest discover -s tests -v

docker-build:
	@docker build -t $(IMAGE):local .
