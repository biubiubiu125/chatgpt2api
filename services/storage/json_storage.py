from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from services.file_lock import file_lock
from services.json_file import read_json_file, write_json_file
from services.storage.base import StorageBackend
from services.storage.merge import merge_item_lists


class JSONStorageBackend(StorageBackend):
    """本地 JSON 文件存储后端"""

    def __init__(self, file_path: Path, auth_keys_path: Path | None = None):
        self.file_path = file_path
        self.auth_keys_path = auth_keys_path or file_path.with_name("auth_keys.json")
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.auth_keys_path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _load_json_list(file_path: Path) -> list[dict[str, Any]]:
        data = read_json_file(
            file_path,
            name=file_path.name,
            default_factory=list,
            expected_types=list,
        )
        return data if isinstance(data, list) else []

    @staticmethod
    def _save_json_list(file_path: Path, items: list[dict[str, Any]]) -> None:
        write_json_file(file_path, items)

    def load_accounts(self) -> list[dict[str, Any]]:
        """从 JSON 文件加载账号数据"""
        return self._load_json_list(self.file_path)

    def save_accounts(self, accounts: list[dict[str, Any]]) -> None:
        """保存账号数据到 JSON 文件"""
        with file_lock(self.file_path.with_name(f"{self.file_path.name}.lock")):
            self._save_json_list(self.file_path, accounts)

    def save_accounts_merged(
        self,
        accounts: list[dict[str, Any]],
        previous_accounts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        with file_lock(self.file_path.with_name(f"{self.file_path.name}.lock")):
            current = self._load_json_list(self.file_path)
            merged = merge_item_lists(
                current,
                accounts,
                previous_accounts,
                identity_key="access_token",
            )
            self._save_json_list(self.file_path, merged)
            return merged

    def load_auth_keys(self) -> list[dict[str, Any]]:
        """从 JSON 文件加载鉴权密钥数据"""
        data = read_json_file(
            self.auth_keys_path,
            name="auth_keys.json",
            default_factory=list,
            expected_types=(dict, list),
        )
        if isinstance(data, dict):
            data = data.get("items")
        return data if isinstance(data, list) else []

    def save_auth_keys(self, auth_keys: list[dict[str, Any]]) -> None:
        """保存鉴权密钥数据到 JSON 文件"""
        with file_lock(self.auth_keys_path.with_name(f"{self.auth_keys_path.name}.lock")):
            write_json_file(self.auth_keys_path, {"items": auth_keys})

    def save_auth_keys_merged(
        self,
        auth_keys: list[dict[str, Any]],
        previous_auth_keys: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        with file_lock(self.auth_keys_path.with_name(f"{self.auth_keys_path.name}.lock")):
            current = self.load_auth_keys()
            merged = merge_item_lists(
                current,
                auth_keys,
                previous_auth_keys,
                identity_key="id",
            )
            write_json_file(self.auth_keys_path, {"items": merged})
            return merged

    def health_check(self) -> dict[str, Any]:
        """健康检查"""
        try:
            # 检查文件是否可读写
            if self.file_path.exists():
                self.file_path.read_text(encoding="utf-8")
            if self.auth_keys_path.exists():
                self.auth_keys_path.read_text(encoding="utf-8")
            for directory in {self.file_path.parent, self.auth_keys_path.parent}:
                directory.mkdir(parents=True, exist_ok=True)
                probe = directory / f".storage-health-{os.getpid()}.tmp"
                try:
                    probe.write_text("ok", encoding="utf-8")
                finally:
                    probe.unlink(missing_ok=True)
            return {
                "status": "healthy",
                "backend": "json",
                "file_exists": self.file_path.exists(),
                "file_path": str(self.file_path),
                "auth_keys_file_exists": self.auth_keys_path.exists(),
                "auth_keys_file_path": str(self.auth_keys_path),
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "backend": "json",
                "error": str(e),
            }

    def get_backend_info(self) -> dict[str, Any]:
        """获取存储后端信息"""
        return {
            "type": "json",
            "description": "本地 JSON 文件存储",
            "file_path": str(self.file_path),
            "file_exists": self.file_path.exists(),
            "auth_keys_file_path": str(self.auth_keys_path),
            "auth_keys_file_exists": self.auth_keys_path.exists(),
        }
