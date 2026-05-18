"""Kubernetes client wrapper using kubectl"""

import json
import logging
import pickle
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class KubeClient:
    """Kubernetes client wrapper using kubectl"""

    # Cache settings
    CACHE_DIR = Path("/tmp/mux-debug-cache")
    CACHE_TTL = 60  # seconds

    def __init__(self, context: Optional[str] = None):
        if context:
            self.context = context
        else:
            # Get current context if not specified
            self.context = self._get_current_context()
        self._setup_cache()

    def _setup_cache(self):
        """Setup cache directory"""
        self.CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def _get_cache_path(self, key: str) -> Path:
        """Get cache file path for a given key"""
        # Include context in cache key to avoid conflicts
        cache_key = f"{self.context}_{key}"
        return self.CACHE_DIR / f"{cache_key}.cache"

    def _get_cached_data(self, key: str) -> Optional[any]:
        """Get cached data if valid"""
        cache_path = self._get_cache_path(key)
        if not cache_path.exists():
            return None

        try:
            stat = cache_path.stat()
            age = time.time() - stat.st_mtime
            if age > self.CACHE_TTL:
                logger.debug(f"Cache expired for {key} (age: {age:.1f}s)")
                return None

            with open(cache_path, "rb") as f:
                data = pickle.load(f)
                logger.debug(f"Cache hit for {key} (age: {age:.1f}s)")
                return data
        except Exception as e:
            logger.debug(f"Failed to read cache for {key}: {e}")
            return None

    def _set_cached_data(self, key: str, data: any):
        """Set cached data"""
        cache_path = self._get_cache_path(key)
        try:
            with open(cache_path, "wb") as f:
                pickle.dump(data, f)
            logger.debug(f"Cached data for {key}")
        except Exception as e:
            logger.debug(f"Failed to write cache for {key}: {e}")

    def _get_current_context(self) -> str:
        """Get current kubectl context"""
        result = subprocess.run(
            ["kubectl", "config", "current-context"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            logger.warning("Failed to get current context, using 'default'")
            return "default"

    def _run_kubectl(
        self, args: List[str], capture_output=True
    ) -> subprocess.CompletedProcess:
        """Run kubectl command"""
        cmd = ["kubectl", "--context", self.context] + args
        logger.debug(f"Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=capture_output, text=True)
        if result.returncode != 0 and capture_output:
            logger.debug(f"kubectl stderr: {result.stderr}")
        return result

    def get_resource(
        self, resource_type: str, name: str, namespace: str
    ) -> Optional[Dict]:
        """Get a Kubernetes resource"""
        result = self._run_kubectl(
            ["get", resource_type, name, "-n", namespace, "-o", "json"]
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
        return None

    def list_resources(
        self,
        resource_type: str,
        namespace: Optional[str] = None,
        selector: Optional[str] = None,
        use_cache: bool = True,
    ) -> List[Dict]:
        """List Kubernetes resources with optional caching"""
        # Build cache key
        cache_key_parts = [f"list_{resource_type}"]
        if namespace:
            cache_key_parts.append(f"ns_{namespace}")
        else:
            cache_key_parts.append("all_namespaces")
        if selector:
            cache_key_parts.append(f"sel_{selector}")
        cache_key = "_".join(cache_key_parts)

        # Try cache first
        if use_cache:
            cached = self._get_cached_data(cache_key)
            if cached is not None:
                return cached

        # Fetch from kubectl
        args = ["get", resource_type, "-o", "json"]
        if namespace:
            args.extend(["-n", namespace])
        else:
            args.append("--all-namespaces")
        if selector:
            args.extend(["-l", selector])

        result = self._run_kubectl(args)
        if result.returncode == 0:
            items = json.loads(result.stdout).get("items", [])
            # Cache the result
            if use_cache:
                self._set_cached_data(cache_key, items)
            return items
        return []

    def exec_pod(
        self, pod_name: str, namespace: str, command: List[str]
    ) -> subprocess.CompletedProcess:
        """Execute command in a pod"""
        args = ["exec", pod_name, "-n", namespace, "--"] + command
        return self._run_kubectl(args)

    def get_pod_logs(self, pod_name: str, namespace: str, tail: int = 100) -> str:
        """Get pod logs"""
        result = self._run_kubectl(
            ["logs", pod_name, "-n", namespace, "--tail", str(tail)]
        )
        return result.stdout if result.returncode == 0 else ""
