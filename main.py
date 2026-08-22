from __future__ import annotations

from api import create_app
from scripts.run_uvicorn import run

app = create_app()

if __name__ == "__main__":
    run()
