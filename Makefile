.PHONY: help requirements upgrade-chart update lint template test-requirements python-compile docker-build

CHART_DIR ?= chart
IMAGE ?= ghcr.io/nowakeai/svc-lb-mux

help:
	@echo "Available targets:"
	@echo "  requirements      - Export Python dependencies to scripts/requirements.txt"
	@echo "  upgrade-chart     - Bump patch version in chart/Chart.yaml"
	@echo "  update            - Update dependencies and chart version"
	@echo "  lint              - Lint Helm chart"
	@echo "  template          - Render Helm chart"
	@echo "  test-requirements - Verify requirements.txt can be installed"
	@echo "  python-compile    - Compile Python sources"
	@echo "  docker-build      - Build controller image locally"

requirements:
	@echo "Exporting requirements.txt..."
	@uv export --format requirements-txt --no-editable > scripts/requirements.txt
	@echo "Generated scripts/requirements.txt"

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

update: requirements upgrade-chart
	@echo "Update complete"

lint:
	@helm lint $(CHART_DIR)

template:
	@helm template svc-mux $(CHART_DIR)

test-requirements:
	@tmpdir=$$(mktemp -d); \
	python -m venv $$tmpdir; \
	$$tmpdir/bin/python -m pip install --dry-run -r scripts/requirements.txt; \
	rm -rf $$tmpdir

python-compile:
	@python -m py_compile scripts/main.py scripts/webserver.py scripts/events.py scripts/utils.py scripts/alert.py

docker-build:
	@docker build -t $(IMAGE):local .
