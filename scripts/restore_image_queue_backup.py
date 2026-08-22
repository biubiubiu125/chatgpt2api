from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("CHATGPT2API_AUTH_KEY", "offline-image-queue-restore")

from services.backup_service import BackupService
from services.image_queue.database import ImageQueueDatabase
from services.image_queue.repository import ImageQueueRepository
from services.image_queue.settings import ImageQueueSettings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Restore a chatgpt2api image queue backup into an empty PostgreSQL database.",
    )
    parser.add_argument("archive", type=Path)
    parser.add_argument("--passphrase", default="")
    parser.add_argument("--artifact-root", type=Path, default=None)
    args = parser.parse_args()

    settings = ImageQueueSettings.from_env()
    database = ImageQueueDatabase(settings)
    database.start()
    try:
        repository = ImageQueueRepository(database)
        service = BackupService()
        service.set_image_queue_restore_provider(repository.restore_logical_backup)
        result = service.restore_archive_file(
            args.archive,
            artifact_root=args.artifact_root or settings.artifact_root,
            passphrase=args.passphrase,
        )
    finally:
        database.dispose()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
