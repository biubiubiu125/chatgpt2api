from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class StorageBackend(ABC):
    """抽象存储后端基类"""

    @abstractmethod
    def load_accounts(self) -> list[dict[str, Any]]:
        """加载所有账号数据"""
        pass

    @abstractmethod
    def save_accounts(self, accounts: list[dict[str, Any]]) -> None:
        """保存所有账号数据"""
        pass

    def save_accounts_merged(
        self,
        accounts: list[dict[str, Any]],
        previous_accounts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        self.save_accounts(accounts)
        return accounts

    @abstractmethod
    def load_auth_keys(self) -> list[dict[str, Any]]:
        """加载所有鉴权密钥数据"""
        pass

    @abstractmethod
    def save_auth_keys(self, auth_keys: list[dict[str, Any]]) -> None:
        """保存所有鉴权密钥数据"""
        pass

    def save_auth_keys_merged(
        self,
        auth_keys: list[dict[str, Any]],
        previous_auth_keys: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        self.save_auth_keys(auth_keys)
        return auth_keys

    def reset_after_fork(self) -> None:
        """Re-isolate process-owned resources after a fork.

        File-based backends share nothing that a fork breaks, so the default is a
        no-op. Backends holding sockets or connection pools must override this.
        """
        return None

    @abstractmethod
    def health_check(self) -> dict[str, Any]:
        """健康检查，返回存储后端状态"""
        pass

    @abstractmethod
    def get_backend_info(self) -> dict[str, Any]:
        """获取存储后端信息"""
        pass
