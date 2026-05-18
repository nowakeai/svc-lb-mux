.PHONY: build clean install test

# Build the binary
build:
	go build -o rlpx-verify main.go

# Build with optimizations for smaller binary
build-release:
	CGO_ENABLED=0 go build -ldflags="-s -w" -o rlpx-verify main.go

# Clean build artifacts
clean:
	rm -f rlpx-verify

# Install to parent tools directory
install: build
	cp rlpx-verify ..

# Test with a sample enode (will fail without real node)
test: build
	./rlpx-verify -enode "enode://abcd@localhost:30303" -timeout 1 || true

# Run go mod tidy
tidy:
	go mod tidy

# Download dependencies
deps:
	go mod download
