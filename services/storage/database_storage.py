from __future__ import annotations

import json
import threading
from typing import Any

from sqlalchemy import Column, String, Text, create_engine, Integer, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, sessionmaker

from services.storage.base import StorageBackend
from services.storage.merge import item_identity, merge_item_lists
from services.database_url import APP_DATABASE_ROLE, ensure_database_role_marker, is_postgres_url

Base = declarative_base()


class AccountModel(Base):
    """账号数据模型"""
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    access_token = Column(String(2048), unique=True, nullable=False, index=True)
    data = Column(Text, nullable=False)  # JSON 格式存储完整账号数据


class AuthKeyModel(Base):
    """鉴权密钥数据模型"""
    __tablename__ = "auth_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key_id = Column(String(255), unique=True, nullable=False, index=True)
    data = Column(Text, nullable=False)


class DatabaseStorageBackend(StorageBackend):
    """数据库存储后端（支持 SQLite、PostgreSQL、MySQL 等）"""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self.engine = create_engine(
            database_url,
            pool_pre_ping=True,  # 自动检测连接是否有效
            pool_recycle=3600,   # 1小时回收连接
        )
        self.database_role: dict[str, str] = {}
        if is_postgres_url(database_url):
            with self.engine.begin() as connection:
                self.database_role = ensure_database_role_marker(
                    connection,
                    APP_DATABASE_ROLE,
                    create_if_missing=True,
                )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self._write_lock = threading.RLock()

    def load_accounts(self) -> list[dict[str, Any]]:
        """从数据库加载账号数据"""
        return self._load_rows(AccountModel, identity_key="access_token", stored_key="access_token")

    def save_accounts(self, accounts: list[dict[str, Any]]) -> None:
        """保存账号数据到数据库"""
        self._save_rows(AccountModel, accounts, "access_token")

    def save_accounts_merged(
        self,
        accounts: list[dict[str, Any]],
        previous_accounts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Merge account list updates without deleting concurrent writes."""
        return self._save_rows_merged(AccountModel, accounts, previous_accounts, "access_token")

    def load_auth_keys(self) -> list[dict[str, Any]]:
        """从数据库加载鉴权密钥数据"""
        return self._load_rows(AuthKeyModel, identity_key="id", stored_key="key_id")

    def save_auth_keys(self, auth_keys: list[dict[str, Any]]) -> None:
        """保存鉴权密钥数据到数据库"""
        self._save_rows(AuthKeyModel, auth_keys, "id", "key_id")

    def save_auth_keys_merged(
        self,
        auth_keys: list[dict[str, Any]],
        previous_auth_keys: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Merge auth-key list updates without resurrecting deleted keys."""
        return self._save_rows_merged(AuthKeyModel, auth_keys, previous_auth_keys, "id", "key_id")

    def _load_rows(
        self,
        model: type[AccountModel] | type[AuthKeyModel],
        *,
        identity_key: str,
        stored_key: str,
    ) -> list[dict[str, Any]]:
        session = self.Session()
        try:
            items = []
            for row in session.query(model).all():
                items.append(self._decode_row_data(row, identity_key=identity_key, stored_key=stored_key))
            return items
        finally:
            session.close()

    @staticmethod
    def _decode_row_data(
        row: AccountModel | AuthKeyModel,
        *,
        identity_key: str,
        stored_key: str,
    ) -> dict[str, Any]:
        try:
            item_data = json.loads(str(row.data or ""))
        except json.JSONDecodeError as exc:
            raise ValueError(f"database storage row {row.id} contains invalid JSON") from exc
        if not isinstance(item_data, dict):
            raise ValueError(f"database storage row {row.id} must contain a JSON object")
        item_identity = str(item_data.get(identity_key) or "").strip()
        stored_identity = str(getattr(row, stored_key) or "").strip()
        if not item_identity or item_identity != stored_identity:
            raise ValueError(f"database storage row {row.id} has inconsistent identity")
        return item_data

    @staticmethod
    def _validate_items(items: list[dict[str, Any]], identity_key: str) -> list[dict[str, Any]]:
        if not isinstance(items, list):
            raise ValueError("database storage payload must be a list")
        validated: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("database storage items must be JSON objects")
            identity = str(item.get(identity_key) or "").strip()
            if not identity:
                raise ValueError(f"database storage item is missing {identity_key}")
            if identity in seen:
                raise ValueError(f"database storage contains duplicate {identity_key}")
            seen.add(identity)
            validated.append(item)
        return validated

    def _save_rows(
        self,
        model: type[AccountModel] | type[AuthKeyModel],
        items: list[dict[str, Any]],
        source_key: str,
        target_key: str | None = None,
    ) -> None:
        target_column = target_key or source_key
        validated_items = self._validate_items(items, source_key)
        with self._write_lock:
            session = self.Session()
            try:
                session.query(model).delete()
                for item in validated_items:
                    key_value = str(item.get(source_key) or "").strip()
                    session.add(
                        model(
                            **{target_column: key_value},
                            data=json.dumps(item, ensure_ascii=False),
                        )
                    )
                session.commit()
            except Exception as e:
                session.rollback()
                raise e
            finally:
                session.close()

    def _load_rows_from_session(
        self,
        session,
        model: type[AccountModel] | type[AuthKeyModel],
    ) -> list[dict[str, Any]]:
        rows = session.query(model).with_for_update().all()
        identity_key = "access_token" if model is AccountModel else "id"
        stored_key = "access_token" if model is AccountModel else "key_id"
        items = []
        for row in rows:
            items.append(self._decode_row_data(row, identity_key=identity_key, stored_key=stored_key))
        return items

    def _save_rows_merged(
        self,
        model: type[AccountModel] | type[AuthKeyModel],
        items: list[dict[str, Any]],
        previous_items: list[dict[str, Any]],
        source_key: str,
        target_key: str | None = None,
    ) -> list[dict[str, Any]]:
        target_column = target_key or source_key
        self._validate_items(items, source_key)
        self._validate_items(previous_items, source_key)
        with self._write_lock:
            for attempt in range(2):
                session = self.Session()
                retry = False
                try:
                    current_items = self._load_rows_from_session(session, model)
                    merged = merge_item_lists(
                        current_items,
                        items,
                        previous_items,
                        identity_key=source_key,
                    )
                    merged_by_id = {
                        item_identity(item, source_key): item
                        for item in merged
                        if isinstance(item, dict) and item_identity(item, source_key)
                    }
                    rows = session.query(model).with_for_update().all()
                    rows_by_id = {
                        str(getattr(row, target_column) or "").strip(): row
                        for row in rows
                        if str(getattr(row, target_column) or "").strip()
                    }
                    for key_value, row in rows_by_id.items():
                        if key_value not in merged_by_id:
                            session.delete(row)
                            continue
                        row.data = json.dumps(merged_by_id[key_value], ensure_ascii=False)
                    for key_value, item in merged_by_id.items():
                        if key_value in rows_by_id:
                            continue
                        session.add(
                            model(
                                **{target_column: key_value},
                                data=json.dumps(item, ensure_ascii=False),
                            )
                        )
                    session.commit()
                    return list(merged_by_id.values())
                except IntegrityError:
                    session.rollback()
                    if attempt == 0:
                        retry = True
                    else:
                        raise
                except Exception as e:
                    session.rollback()
                    raise e
                finally:
                    session.close()
                if retry:
                    continue
        raise RuntimeError("database merge did not complete")

    def health_check(self) -> dict[str, Any]:
        """健康检查"""
        try:
            session = self.Session()
            try:
                # 尝试执行简单查询
                session.execute(text("SELECT 1"))
                count = session.query(AccountModel).count()
                auth_key_count = session.query(AuthKeyModel).count()
                return {
                    "status": "healthy",
                    "backend": "database",
                    "database_url": self._mask_password(self.database_url),
                    "database_role": dict(self.database_role),
                    "account_count": count,
                    "auth_key_count": auth_key_count,
                }
            finally:
                session.close()
        except Exception as e:
            return {
                "status": "unhealthy",
                "backend": "database",
                "error": str(e),
            }

    def get_backend_info(self) -> dict[str, Any]:
        """获取存储后端信息"""
        db_type = "unknown"
        if "sqlite" in self.database_url:
            db_type = "sqlite"
        elif "postgresql" in self.database_url or "postgres" in self.database_url:
            db_type = "postgresql"
        elif "mysql" in self.database_url:
            db_type = "mysql"

        return {
            "type": "database",
            "db_type": db_type,
            "description": f"数据库存储 ({db_type})",
            "database_url": self._mask_password(self.database_url),
            "database_role": dict(self.database_role),
        }

    @staticmethod
    def _mask_password(url: str) -> str:
        """隐藏数据库连接字符串中的密码"""
        if "://" not in url:
            return url
        try:
            protocol, rest = url.split("://", 1)
            if "@" in rest:
                credentials, host = rest.split("@", 1)
                if ":" in credentials:
                    username, _ = credentials.split(":", 1)
                    return f"{protocol}://{username}:****@{host}"
            return url
        except Exception:
            return url
