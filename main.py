from __future__ import annotations

import os

from scripts.env_loader import load_env_file_into_environment

load_env_file_into_environment(os.getenv("CHATGPT2API_ENV_FILE"))

from api import create_app
from scripts.run_uvicorn import run

app = create_app()

if __name__ == "__main__":
    run()
