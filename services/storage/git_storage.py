from __future__ import annotations

import tempfile
from pathlib import PurePosixPath, PureWindowsPath
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from git import Repo
from git.exc import GitCommandError
from git.remote import PushInfo

from services.file_lock import file_lock
from services.json_file import read_json_file, write_json_file
from services.storage.base import StorageBackend
from services.storage.merge import merge_item_lists


class GitStorageConflict(RuntimeError):
    pass


MERGED_SAVE_ATTEMPTS = 3


class GitStorageBackend(StorageBackend):
    """Git 私有仓库存储后端"""

    def __init__(
        self,
        repo_url: str,
        token: str,
        branch: str = "main",
        file_path: str = "accounts.json",
        auth_keys_file_path: str = "auth_keys.json",
        local_cache_dir: Path | None = None,
    ):
        self.repo_url = repo_url
        self.token = token
        self.branch = branch
        self.file_path = file_path
        self.auth_keys_file_path = auth_keys_file_path
        self._validate_configured_file_path(self.file_path)
        self._validate_configured_file_path(self.auth_keys_file_path)
        
        # 本地缓存目录
        if local_cache_dir is None:
            local_cache_dir = Path(tempfile.gettempdir()) / "chatgpt2api_git_cache"
        self.local_cache_dir = local_cache_dir
        self.local_cache_dir.mkdir(parents=True, exist_ok=True)
        self._lock_path = self.local_cache_dir / "storage.lock"
        
        # 构建带认证的 Git URL
        self.auth_repo_url = self._build_auth_url(repo_url, token)

    @staticmethod
    def _validate_configured_file_path(file_path: str) -> tuple[str, ...]:
        normalized = str(file_path or "").strip().replace("\\", "/")
        path = PurePosixPath(normalized)
        if (
            not normalized
            or path.is_absolute()
            or PureWindowsPath(normalized).is_absolute()
            or not path.parts
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError("Git storage file paths must be relative and stay inside the repository")
        return path.parts

    @staticmethod
    def _build_auth_url(repo_url: str, token: str) -> str:
        """构建带认证的 Git URL"""
        if not token:
            return repo_url

        if repo_url.startswith("git@"):
            host, separator, path = repo_url[4:].partition(":")
            if not separator:
                return repo_url
            repo_url = f"https://{host}/{path}"

        if not repo_url.startswith(("http://", "https://")):
            return repo_url

        parsed = urlsplit(repo_url)
        hostport = parsed.netloc.rsplit("@", 1)[-1]
        auth = quote(str(token), safe="")
        return urlunsplit((
            parsed.scheme,
            f"{auth}@{hostport}",
            parsed.path,
            parsed.query,
            parsed.fragment,
        ))

    def _redact_error(self, error: object) -> str:
        message = str(error or "")
        if self.auth_repo_url:
            message = message.replace(self.auth_repo_url, self._mask_token(self.auth_repo_url))
        if self.token:
            message = message.replace(self.token, "****")
        return message

    def _clone_or_pull(self) -> Repo:
        """克隆或拉取仓库"""
        repo_path = self.local_cache_dir / "repo"
        
        if repo_path.exists() and (repo_path / ".git").exists():
            # 仓库已存在，拉取最新代码
            try:
                repo = Repo(repo_path)
                origin = repo.remote("origin")
                origin.pull(self.branch)
                return repo
            except GitCommandError as exc:
                raise GitStorageConflict("failed to synchronize Git storage") from exc
                # 拉取失败，删除重新克隆
        
        # 克隆仓库
        repo = Repo.clone_from(
            self.auth_repo_url,
            repo_path,
            branch=self.branch,
        )
        return repo

    def load_accounts(self) -> list[dict[str, Any]]:
        """从 Git 仓库加载账号数据"""
        try:
            return self._load_json_file(self.file_path)
        except Exception as e:
            print(f"[git-storage] load failed: {self._redact_error(e)}")
            raise

    def save_accounts(self, accounts: list[dict[str, Any]]) -> None:
        """保存账号数据到 Git 仓库"""
        with file_lock(self._lock_path):
            try:
                self._save_json_file(self.file_path, accounts, "Update accounts data")
            except Exception as e:
                print(f"[git-storage] save failed: {self._redact_error(e)}")
                raise e

    def save_accounts_merged(
        self,
        accounts: list[dict[str, Any]],
        previous_accounts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        with file_lock(self._lock_path):
            try:
                return self._save_merged_list(
                    self.file_path,
                    accounts,
                    previous_accounts,
                    identity_key="access_token",
                    message="Update accounts data",
                )
            except Exception as e:
                print(f"[git-storage] save failed: {self._redact_error(e)}")
                raise e

    def load_auth_keys(self) -> list[dict[str, Any]]:
        """从 Git 仓库加载鉴权密钥数据"""
        try:
            data = self._load_json_value(self.auth_keys_file_path)
            if isinstance(data, dict):
                data = data.get("items")
            return data if isinstance(data, list) else []
        except Exception as e:
            print(f"[git-storage] load failed: {self._redact_error(e)}")
            raise

    def save_auth_keys(self, auth_keys: list[dict[str, Any]]) -> None:
        """保存鉴权密钥数据到 Git 仓库"""
        with file_lock(self._lock_path):
            try:
                self._save_json_file(self.auth_keys_file_path, {"items": auth_keys}, "Update auth keys data")
            except Exception as e:
                print(f"[git-storage] save failed: {self._redact_error(e)}")
                raise e

    def save_auth_keys_merged(
        self,
        auth_keys: list[dict[str, Any]],
        previous_auth_keys: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        with file_lock(self._lock_path):
            try:
                return self._save_merged_list(
                    self.auth_keys_file_path,
                    auth_keys,
                    previous_auth_keys,
                    identity_key="id",
                    message="Update auth keys data",
                    wrapped=True,
                )
            except Exception as e:
                print(f"[git-storage] save failed: {self._redact_error(e)}")
                raise e

    def _load_json_file(self, file_path: str) -> list[dict[str, Any]]:
        data = self._load_json_value(file_path)
        return data if isinstance(data, list) else []

    def _load_json_value(self, file_path: str) -> Any:
        repo = self._clone_or_pull()
        file_full_path = self._repo_file_path(repo, file_path)
        if not file_full_path.exists():
            return None
        return read_json_file(file_full_path, name=file_path, default_factory=lambda: None)

    def _repo_file_path(self, repo: Repo, file_path: str) -> Path:
        parts = self._validate_configured_file_path(file_path)
        root = Path(repo.working_dir).resolve()
        target = root.joinpath(*parts).resolve(strict=False)
        if not target.is_relative_to(root):
            raise ValueError("Git storage file path escapes the repository")
        return target

    def _save_merged_list(
        self,
        file_path: str,
        items: list[dict[str, Any]],
        previous_items: list[dict[str, Any]],
        *,
        identity_key: str,
        message: str,
        wrapped: bool = False,
    ) -> list[dict[str, Any]]:
        last_error: GitStorageConflict | None = None
        for _attempt in range(MERGED_SAVE_ATTEMPTS):
            current_value = self._load_json_value(file_path)
            current_items = (
                current_value.get("items")
                if wrapped and isinstance(current_value, dict)
                else current_value
            )
            merged = merge_item_lists(
                current_items if isinstance(current_items, list) else [],
                items,
                previous_items,
                identity_key=identity_key,
            )
            try:
                self._save_json_file(file_path, {"items": merged} if wrapped else merged, message)
                return merged
            except GitStorageConflict as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise GitStorageConflict("unable to save merged Git storage data")

    def _save_json_file(self, file_path: str, items: Any, message: str) -> None:
        repo = self._clone_or_pull()
        self._validate_configured_file_path(file_path)
        file_full_path = self._repo_file_path(repo, file_path)
        write_json_file(file_full_path, items, backup=False)
        repo.index.add([file_path])
        if repo.is_dirty():
            repo.index.commit(message)
            results = repo.remote("origin").push(self.branch)
            rejected = [
                result
                for result in results
                if result.flags & (PushInfo.ERROR | PushInfo.REJECTED | PushInfo.REMOTE_REJECTED)
            ]
            if rejected:
                repo.remote("origin").fetch(self.branch)
                repo.git.reset("--hard", f"origin/{self.branch}")
                raise GitStorageConflict("remote rejected concurrent Git storage update")

    def health_check(self) -> dict[str, Any]:
        """健康检查"""
        try:
            repo = self._clone_or_pull()
            return {
                "status": "healthy",
                "backend": "git",
                "repo_url": self._mask_token(self.repo_url),
                "branch": self.branch,
                "file_path": self.file_path,
                "auth_keys_file_path": self.auth_keys_file_path,
                "last_commit": repo.head.commit.hexsha[:8],
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "backend": "git",
                "error": self._redact_error(e),
            }

    def get_backend_info(self) -> dict[str, Any]:
        """获取存储后端信息"""
        return {
            "type": "git",
            "description": "Git 私有仓库存储",
            "repo_url": self._mask_token(self.repo_url),
            "branch": self.branch,
            "file_path": self.file_path,
            "auth_keys_file_path": self.auth_keys_file_path,
        }

    @staticmethod
    def _mask_token(url: str) -> str:
        """隐藏 URL 中的 token"""
        if "@" in url and "://" in url:
            protocol, rest = url.split("://", 1)
            if "@" in rest:
                _, host = rest.split("@", 1)
                return f"{protocol}://****@{host}"
        return url
