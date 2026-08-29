from __future__ import annotations

import os

os.environ.setdefault("CHATGPT2API_AUTH_KEY", "test-auth-key")
# Keep the test process isolated from the production PostgreSQL default; tests
# that cover the default explicitly remove this override.
os.environ.setdefault("STORAGE_BACKEND", "json")
