from __future__ import annotations

import copy
import hashlib
import hmac
import io
import json
import os
import random
import shutil
import subprocess
import tarfile
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from pathlib import PurePosixPath
from urllib.parse import quote, urlencode

from curl_cffi import Curl, CurlInfo, CurlOpt, requests

from services.config import BASE_DIR, CONFIG_FILE, DATA_DIR, config, load_backup_state, save_backup_state
from services.image_storage_service import IMAGE_INDEX_FILE
from services.image_tags_service import TAGS_FILE


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso_now() -> str:
    return _utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean(value: object) -> str:
    return str(value or "").strip()


def _sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _native_filesystem_path(path: Path) -> str:
    resolved = str(path.resolve())
    if os.name != "nt" or resolved.startswith("\\\\?\\"):
        return resolved
    if len(resolved) < 240:
        return resolved
    if resolved.startswith("\\\\"):
        return "\\\\?\\UNC\\" + resolved.lstrip("\\")
    return "\\\\?\\" + resolved


def _path_exists(path: Path) -> bool:
    return os.path.exists(_native_filesystem_path(path))


def _path_is_file(path: Path) -> bool:
    return os.path.isfile(_native_filesystem_path(path))


def _read_path_bytes(path: Path) -> bytes:
    with open(_native_filesystem_path(path), "rb") as source:
        return source.read()


def _unlink_path(path: Path) -> None:
    os.unlink(_native_filesystem_path(path))


def _is_backup_object(key: object) -> bool:
    name = _clean(key).rsplit("/", 1)[-1]
    return name.startswith("backup-") and (name.endswith(".tar.gz") or name.endswith(".tar.gz.enc"))


def _validated_backup_object_key(settings: dict[str, object], key: object) -> str:
    candidate = _clean(key).replace("\\", "/").lstrip("/")
    if not candidate:
        raise BackupError("backup object key cannot be empty")
    relative = PurePosixPath(candidate)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise BackupError("backup key is invalid")
    prefix = _clean(settings.get("prefix")).strip("/")
    required_prefix = f"{prefix}/" if prefix else ""
    if required_prefix and not candidate.startswith(required_prefix):
        raise BackupError("backup key is outside the configured backup prefix")
    name = candidate[len(required_prefix):] if required_prefix else candidate
    if "/" in name or not _is_backup_object(name):
        raise BackupError("backup key is not a chatgpt2api backup object")
    return candidate


def _hmac_sha256(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _openssl_encrypt(data: bytes, passphrase: str) -> bytes:
    env = dict(os.environ)
    env["CHATGPT2API_BACKUP_PASSPHRASE"] = passphrase
    try:
        result = subprocess.run(
            [
                "openssl",
                "enc",
                "-aes-256-cbc",
                "-pbkdf2",
                "-salt",
                "-md",
                "sha256",
                "-pass",
                "env:CHATGPT2API_BACKUP_PASSPHRASE",
            ],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            env=env,
        )
    except FileNotFoundError as exc:
        raise BackupError("当前环境缺少 openssl，无法执行加密备份") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise BackupError(f"加密备份失败：{detail or 'openssl 执行失败'}") from exc
    return result.stdout


def _openssl_encrypt_file(source: Path, destination: Path, passphrase: str) -> None:
    env = dict(os.environ)
    env["CHATGPT2API_BACKUP_PASSPHRASE"] = passphrase
    try:
        with source.open("rb") as input_file, destination.open("wb") as output_file:
            subprocess.run(
                [
                    "openssl",
                    "enc",
                    "-aes-256-cbc",
                    "-pbkdf2",
                    "-salt",
                    "-md",
                    "sha256",
                    "-pass",
                    "env:CHATGPT2API_BACKUP_PASSPHRASE",
                ],
                stdin=input_file,
                stdout=output_file,
                stderr=subprocess.PIPE,
                check=True,
                env=env,
            )
    except FileNotFoundError as exc:
        raise BackupError("openssl is required for encrypted backups") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise BackupError(f"backup encryption failed: {detail or 'openssl failed'}") from exc


def _openssl_decrypt(data: bytes, passphrase: str) -> bytes:
    env = dict(os.environ)
    env["CHATGPT2API_BACKUP_PASSPHRASE"] = passphrase
    try:
        result = subprocess.run(
            [
                "openssl",
                "enc",
                "-d",
                "-aes-256-cbc",
                "-pbkdf2",
                "-md",
                "sha256",
                "-pass",
                "env:CHATGPT2API_BACKUP_PASSPHRASE",
            ],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            env=env,
        )
    except FileNotFoundError as exc:
        raise BackupError("当前环境缺少 openssl，无法解密备份内容") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise BackupError(f"解密备份失败：{detail or 'openssl 执行失败'}") from exc
    return result.stdout


def _openssl_decrypt_file(source: Path, destination: Path, passphrase: str) -> None:
    env = dict(os.environ)
    env["CHATGPT2API_BACKUP_PASSPHRASE"] = passphrase
    try:
        with source.open("rb") as input_file, destination.open("wb") as output_file:
            subprocess.run(
                [
                    "openssl",
                    "enc",
                    "-d",
                    "-aes-256-cbc",
                    "-pbkdf2",
                    "-md",
                    "sha256",
                    "-pass",
                    "env:CHATGPT2API_BACKUP_PASSPHRASE",
                ],
                stdin=input_file,
                stdout=output_file,
                stderr=subprocess.PIPE,
                check=True,
                env=env,
            )
    except FileNotFoundError as exc:
        raise BackupError("openssl is required for encrypted backup downloads") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise BackupError(f"backup decryption failed: {detail or 'openssl failed'}") from exc


def _guess_content_type(name: str) -> str:
    if name.endswith(".json"):
        return "application/json"
    if name.endswith(".jsonl"):
        return "application/x-ndjson"
    if name.endswith(".tar.gz"):
        return "application/gzip"
    if name.endswith(".gz"):
        return "application/gzip"
    return "application/octet-stream"


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


def _count_items(value: object) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return len(value)
    return 0


class BackupError(RuntimeError):
    pass


class CloudflareR2Client:
    def __init__(self, settings: dict[str, object]) -> None:
        self.account_id = _clean(settings.get("account_id"))
        self.access_key_id = _clean(settings.get("access_key_id"))
        self.secret_access_key = _clean(settings.get("secret_access_key"))
        self.bucket = _clean(settings.get("bucket"))
        self.prefix = _clean(settings.get("prefix")) or "backups"
        self.session = requests.Session(impersonate="chrome146", verify=True)

    def validate(self) -> None:
        missing = []
        if not self.account_id:
            missing.append("Account ID")
        if not self.access_key_id:
            missing.append("Access Key ID")
        if not self.secret_access_key:
            missing.append("Secret Access Key")
        if not self.bucket:
            missing.append("Bucket")
        if missing:
            raise BackupError(f"R2 配置不完整：缺少 {'、'.join(missing)}")

    @property
    def endpoint(self) -> str:
        return f"https://{self.account_id}.r2.cloudflarestorage.com"

    def _aws_v4_headers(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: bytes = b"",
        body_hash: str = "",
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[str, dict[str, str]]:
        now = _utc_now()
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        encoded_query = urlencode(sorted((query or {}).items()))
        payload_hash = body_hash or _sha256_hex(body)
        host = f"{self.account_id}.r2.cloudflarestorage.com"
        headers = {
            "host": host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        if extra_headers:
            for key, value in extra_headers.items():
                headers[key.lower()] = value.strip()
        sorted_items = sorted((key.lower(), " ".join(str(value).strip().split())) for key, value in headers.items())
        canonical_headers = "".join(f"{key}:{value}\n" for key, value in sorted_items)
        signed_headers = ";".join(key for key, _ in sorted_items)
        canonical_request = "\n".join([
            method.upper(),
            path,
            encoded_query,
            canonical_headers,
            signed_headers,
            payload_hash,
        ])
        credential_scope = f"{date_stamp}/auto/s3/aws4_request"
        string_to_sign = "\n".join([
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            _sha256_hex(canonical_request.encode("utf-8")),
        ])
        k_date = _hmac_sha256(("AWS4" + self.secret_access_key).encode("utf-8"), date_stamp)
        k_region = hmac.new(k_date, b"auto", hashlib.sha256).digest()
        k_service = hmac.new(k_region, b"s3", hashlib.sha256).digest()
        k_signing = hmac.new(k_service, b"aws4_request", hashlib.sha256).digest()
        signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        authorization = (
            "AWS4-HMAC-SHA256 "
            f"Credential={self.access_key_id}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, "
            f"Signature={signature}"
        )
        request_headers = {key: value for key, value in headers.items()}
        request_headers["authorization"] = authorization
        return encoded_query, request_headers

    def _request(
        self,
        method: str,
        key: str = "",
        *,
        query: dict[str, str] | None = None,
        body: bytes = b"",
        extra_headers: dict[str, str] | None = None,
        timeout: float = 60.0,
    ):
        object_path = f"/{self.bucket}"
        if key:
            object_path += f"/{quote(key.lstrip('/'), safe='/')}"
        encoded_query, headers = self._aws_v4_headers(method, object_path, query=query, body=body, extra_headers=extra_headers)
        url = f"{self.endpoint}{object_path}"
        if encoded_query:
            url += f"?{encoded_query}"
        response = self.session.request(method.upper(), url, headers=headers, data=body, timeout=timeout)
        return response

    def test_connection(self) -> dict[str, object]:
        self.validate()
        response = self._request("GET", query={"list-type": "2", "max-keys": "1"}, timeout=30.0)
        if response.status_code >= 400:
            raise BackupError(f"连接 R2 失败：HTTP {response.status_code}")
        return {"ok": True, "status": int(response.status_code)}

    def upload_bytes(self, key: str, payload: bytes, *, content_type: str, metadata: dict[str, str] | None = None) -> dict[str, object]:
        headers = {"content-type": content_type}
        if metadata:
            for item_key, item_value in metadata.items():
                headers[f"x-amz-meta-{item_key}"] = str(item_value)
        response = self._request("PUT", key, body=payload, extra_headers=headers)
        if response.status_code >= 400:
            raise BackupError(f"上传备份失败：HTTP {response.status_code}")
        return {"key": key, "etag": str(response.headers.get("etag") or "").strip('"')}

    def upload_file(
        self,
        key: str,
        source: Path,
        *,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, object]:
        source = Path(source)
        digest = hashlib.sha256()
        size = 0
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
        object_path = f"/{self.bucket}/{quote(key.lstrip('/'), safe='/')}"
        extra_headers = {"content-type": content_type, "content-length": str(size)}
        if metadata:
            for item_key, item_value in metadata.items():
                extra_headers[f"x-amz-meta-{item_key}"] = str(item_value)
        _query, headers = self._aws_v4_headers(
            "PUT",
            object_path,
            body_hash=digest.hexdigest(),
            extra_headers=extra_headers,
        )
        response_body = io.BytesIO()
        response_headers = io.BytesIO()
        curl = Curl()
        status_code = 0
        try:
            with source.open("rb") as stream:
                curl.setopt(CurlOpt.URL, f"{self.endpoint}{object_path}".encode("utf-8"))
                curl.setopt(CurlOpt.UPLOAD, 1)
                curl.setopt(CurlOpt.READFUNCTION, stream.read)
                curl.setopt(CurlOpt.INFILESIZE_LARGE, size)
                curl.setopt(CurlOpt.HTTPHEADER, [
                    f"{name}: {value}".encode("utf-8")
                    for name, value in headers.items()
                ])
                curl.setopt(CurlOpt.WRITEDATA, response_body)
                curl.setopt(CurlOpt.HEADERDATA, response_headers)
                curl.setopt(CurlOpt.TIMEOUT_MS, 300_000)
                curl.perform()
                status_code = int(curl.getinfo(CurlInfo.RESPONSE_CODE))
        except Exception as exc:
            raise BackupError(f"backup upload failed: {exc}") from exc
        finally:
            curl.close()
        if status_code >= 400:
            detail = response_body.getvalue().decode("utf-8", errors="replace")[:500]
            raise BackupError(f"backup upload failed: HTTP {status_code}: {detail}")
        etag = ""
        for line in response_headers.getvalue().decode("iso-8859-1", errors="replace").splitlines():
            if line.lower().startswith("etag:"):
                etag = line.split(":", 1)[1].strip().strip('"')
        return {"key": key, "etag": etag}

    def delete_object(self, key: str) -> None:
        response = self._request("DELETE", key, timeout=30.0)
        if response.status_code >= 400 and response.status_code != 404:
            raise BackupError(f"删除备份失败：HTTP {response.status_code}")

    def download_bytes(self, key: str) -> bytes:
        response = self._request("GET", key, timeout=60.0)
        if response.status_code >= 400:
            raise BackupError(f"读取备份失败：HTTP {response.status_code}")
        return bytes(response.content or b"")

    def download_file(self, key: str, destination: Path) -> dict[str, object]:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        object_path = f"/{self.bucket}/{quote(key.lstrip('/'), safe='/')}"
        _query, headers = self._aws_v4_headers("GET", object_path)
        response_headers = io.BytesIO()
        curl = Curl()
        status_code = 0
        try:
            with destination.open("wb") as output:
                curl.setopt(CurlOpt.URL, f"{self.endpoint}{object_path}".encode("utf-8"))
                curl.setopt(CurlOpt.HTTPHEADER, [
                    f"{name}: {value}".encode("utf-8")
                    for name, value in headers.items()
                ])
                curl.setopt(CurlOpt.WRITEDATA, output)
                curl.setopt(CurlOpt.HEADERDATA, response_headers)
                curl.setopt(CurlOpt.TIMEOUT_MS, 300_000)
                curl.perform()
                status_code = int(curl.getinfo(CurlInfo.RESPONSE_CODE))
        except Exception as exc:
            raise BackupError(f"backup download failed: {exc}") from exc
        finally:
            curl.close()
        if status_code >= 400:
            detail = ""
            try:
                detail = destination.read_text(encoding="utf-8", errors="replace")[:500]
            except Exception:
                detail = ""
            raise BackupError(f"backup download failed: HTTP {status_code}{': ' + detail if detail else ''}")
        return {"key": key, "size": destination.stat().st_size}

    def list_objects(self) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        continuation = ""
        while True:
            query = {"list-type": "2", "prefix": f"{self.prefix.rstrip('/')}/", "max-keys": "1000"}
            if continuation:
                query["continuation-token"] = continuation
            response = self._request("GET", query=query, timeout=30.0)
            if response.status_code >= 400:
                raise BackupError(f"获取备份列表失败：HTTP {response.status_code}")
            text = response.text
            for block in text.split("<Contents>")[1:]:
                key = _clean(block.split("<Key>", 1)[1].split("</Key>", 1)[0]) if "<Key>" in block else ""
                if not key:
                    continue
                size_text = _clean(block.split("<Size>", 1)[1].split("</Size>", 1)[0]) if "<Size>" in block else "0"
                updated = _clean(block.split("<LastModified>", 1)[1].split("</LastModified>", 1)[0]) if "<LastModified>" in block else ""
                items.append({
                    "key": key,
                    "size": int(size_text or 0),
                    "updated_at": updated,
                })
            truncated = "<IsTruncated>true</IsTruncated>" in text
            if not truncated:
                break
            if "<NextContinuationToken>" not in text:
                break
            continuation = _clean(text.split("<NextContinuationToken>", 1)[1].split("</NextContinuationToken>", 1)[0])
            if not continuation:
                break
        items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return items

    def close(self) -> None:
        self.session.close()


class BackupService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._running = False
        self._image_queue_provider = None
        self._image_queue_restore_provider = None
        self._image_queue_artifact_root: Path | None = None

    def set_image_queue_provider(self, provider) -> None:
        self._image_queue_provider = provider

    def set_image_queue_restore_provider(self, provider) -> None:
        self._image_queue_restore_provider = provider

    def set_image_queue_artifact_root(self, root: Path | None) -> None:
        self._image_queue_artifact_root = Path(root).resolve() if root is not None else None

    def _artifact_root(self) -> Path:
        return (self._image_queue_artifact_root or config.images_dir).resolve()

    def restore_archive_payload(
        self,
        payload: bytes,
        *,
        artifact_root: Path | None = None,
    ) -> dict[str, object]:
        root = (artifact_root or self._artifact_root()).resolve()
        os.makedirs(_native_filesystem_path(root), exist_ok=True)
        staging_root = Path(tempfile.mkdtemp(
            prefix=".image-queue-restore-",
            dir=_native_filesystem_path(root),
        )).resolve()
        staged_targets: list[tuple[Path, Path]] = []
        promoted_targets: list[Path] = []
        try:
            with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
                queue_member = archive.getmember("data/image-queue.json")
                if not queue_member.isfile() or queue_member.size > 256 * 1024 * 1024:
                    raise BackupError("image queue backup payload is invalid")
                extracted_queue = archive.extractfile(queue_member)
                if extracted_queue is None:
                    raise BackupError("image queue backup payload is missing")
                queue_payload = json.loads(extracted_queue.read().decode("utf-8"))
                if not isinstance(queue_payload, dict):
                    raise BackupError("image queue backup payload is invalid")
                expected = {
                    str(item.get("relative_path") or ""): str(item.get("sha256") or "")
                    for item in queue_payload.get("artifacts") or []
                    if isinstance(item, dict)
                    and str(item.get("relative_path") or "")
                    and (
                        item.get("backup_file_included") is True
                        or item.get("backup_required") is True
                        or (
                            "backup_file_included" not in item
                            and str(item.get("storage_backend") or "local") == "local"
                        )
                    )
                }
                restored_paths: set[str] = set()
                restored_images = 0
                for member in archive.getmembers():
                    if not member.isfile() or not member.name.startswith("data/images/"):
                        continue
                    relative_text = member.name.removeprefix("data/images/")
                    relative = PurePosixPath(relative_text)
                    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
                        raise BackupError("backup contains an unsafe image path")
                    if member.size < 0 or member.size > 50 * 1024 * 1024:
                        raise BackupError("backup image exceeds 50MB limit")
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        raise BackupError("backup image could not be read")
                    image_data = extracted.read()
                    if len(image_data) != member.size:
                        raise BackupError("backup image is truncated")
                    expected_hash = expected.get(relative.as_posix())
                    if expected_hash and _sha256_hex(image_data) != expected_hash:
                        raise BackupError("backup image hash does not match queue metadata")
                    target = root.joinpath(*relative.parts).resolve()
                    if not target.is_relative_to(root):
                        raise BackupError("backup contains an unsafe image path")
                    if _path_exists(target):
                        if not _path_is_file(target) or _sha256_hex(_read_path_bytes(target)) != _sha256_hex(image_data):
                            raise BackupError(f"restore target already exists with different content: {relative.as_posix()}")
                    else:
                        staged = (staging_root / f"{len(staged_targets)}-{_sha256_hex(image_data)}.restore").resolve()
                        descriptor, temporary_name = tempfile.mkstemp(
                            prefix=".restore-",
                            suffix=".tmp",
                            dir=staging_root,
                        )
                        os.close(descriptor)
                        temporary = Path(temporary_name)
                        try:
                            with open(_native_filesystem_path(temporary), "wb") as output:
                                output.write(image_data)
                                output.flush()
                                os.fsync(output.fileno())
                            os.replace(
                                _native_filesystem_path(temporary),
                                _native_filesystem_path(staged),
                            )
                        finally:
                            if _path_exists(temporary):
                                _unlink_path(temporary)
                        staged_targets.append((staged, target))
                    restored_paths.add(relative.as_posix())
        except (KeyError, json.JSONDecodeError, UnicodeDecodeError, tarfile.TarError) as exc:
            shutil.rmtree(staging_root, ignore_errors=True)
            raise BackupError("backup does not contain a valid image queue export") from exc
        except Exception:
            shutil.rmtree(staging_root, ignore_errors=True)
            raise
        try:
            missing = sorted(set(expected) - restored_paths)
            if missing:
                raise BackupError(f"backup is missing {len(missing)} image queue artifact files")
            if not callable(self._image_queue_restore_provider):
                raise BackupError("image queue restore provider is unavailable")
            for staged, target in staged_targets:
                os.makedirs(_native_filesystem_path(target.parent), exist_ok=True)
                if _path_exists(target):
                    staged_hash = _sha256_hex(_read_path_bytes(staged))
                    if not _path_is_file(target) or _sha256_hex(_read_path_bytes(target)) != staged_hash:
                        raise BackupError(f"restore target already exists with different content: {target.relative_to(root).as_posix()}")
                    continue
                os.replace(
                    _native_filesystem_path(staged),
                    _native_filesystem_path(target),
                )
                promoted_targets.append(target)
                restored_images += 1
            queue_summary = self._image_queue_restore_provider(queue_payload)
            return {
                "restored_images": restored_images,
                "image_queue": dict(queue_summary or {}),
            }
        except Exception as exc:
            for target in reversed(promoted_targets):
                try:
                    if target.is_relative_to(root) and _path_is_file(target):
                        _unlink_path(target)
                except OSError:
                    pass
            if isinstance(exc, BackupError):
                raise
            raise BackupError(f"image queue database restore failed: {exc}") from exc
        finally:
            shutil.rmtree(staging_root, ignore_errors=True)

    def restore_archive_file(
        self,
        path: Path,
        *,
        artifact_root: Path | None = None,
        passphrase: str = "",
    ) -> dict[str, object]:
        source = path.resolve()
        if not source.is_file():
            raise BackupError(f"backup file does not exist: {source}")
        payload = source.read_bytes()
        if source.name.endswith(".enc"):
            secret = _clean(passphrase) or _clean(os.environ.get("CHATGPT2API_BACKUP_PASSPHRASE"))
            secret = secret or _clean(config.get_backup_settings().get("passphrase"))
            if not secret:
                raise BackupError("encrypted backup requires a passphrase")
            payload = _openssl_decrypt(payload, secret)
        return self.restore_archive_payload(payload, artifact_root=artifact_root)

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run, daemon=True, name="r2-backup-scheduler")
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._stop_event.set()
            thread = self._thread
            self._thread = None
        if thread and thread.is_alive():
            thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.run_scheduled_backup_if_needed()
            except Exception:
                pass
            self._stop_event.wait(30)

    def run_scheduled_backup_if_needed(self) -> None:
        settings = config.get_backup_settings()
        if not settings.get("enabled"):
            return
        state = self.get_status()
        if state.get("running"):
            return
        interval_minutes = int(settings.get("interval_minutes") or 360)
        last_finished_raw = _clean(state.get("last_finished_at"))
        if last_finished_raw:
            try:
                last_finished = datetime.fromisoformat(last_finished_raw.replace("Z", "+00:00"))
                elapsed = (_utc_now() - last_finished.astimezone(UTC)).total_seconds()
                if elapsed < interval_minutes * 60:
                    return
            except Exception:
                pass
        self.run_backup(trigger="schedule")

    def get_status(self) -> dict[str, object]:
        return {
            **load_backup_state(),
            "running": self._running,
        }

    def is_configured(self) -> bool:
        settings = config.get_backup_settings()
        return all([
            _clean(settings.get("account_id")),
            _clean(settings.get("access_key_id")),
            _clean(settings.get("secret_access_key")),
            _clean(settings.get("bucket")),
        ])

    def get_settings(self) -> dict[str, object]:
        settings = dict(config.get_backup_settings())
        settings["secret_access_key"] = "********" if _clean(settings.get("secret_access_key")) else ""
        settings["passphrase"] = "********" if _clean(settings.get("passphrase")) else ""
        return settings

    def update_settings(self, payload: dict[str, object]) -> dict[str, object]:
        current = config.get_backup_settings()
        merged = dict(current)
        merged.update(dict(payload or {}))
        if "include" in payload and isinstance(payload.get("include"), dict):
            include = dict(current.get("include") or {})
            include.update(payload.get("include") or {})
            merged["include"] = include
        if payload.get("secret_access_key") == "********":
            merged["secret_access_key"] = current.get("secret_access_key")
        if payload.get("passphrase") == "********":
            merged["passphrase"] = current.get("passphrase")
        updated = config.update({"backup": merged})
        return dict(updated.get("backup") or {})

    def test_connection(self) -> dict[str, object]:
        client = CloudflareR2Client(config.get_backup_settings())
        try:
            return client.test_connection()
        finally:
            client.close()

    def list_backups(self) -> list[dict[str, object]]:
        if not self.is_configured():
            return []
        settings = config.get_backup_settings()
        client = CloudflareR2Client(settings)
        try:
            items = client.list_objects()
        finally:
            client.close()
        parsed: list[dict[str, object]] = []
        for item in items:
            key = _clean(item.get("key"))
            try:
                key = _validated_backup_object_key(settings, key)
            except BackupError:
                continue
            name = key.rsplit("/", 1)[-1]
            encrypted = name.endswith(".enc")
            parsed.append({
                "key": key,
                "name": name,
                "size": int(item.get("size") or 0),
                "updated_at": item.get("updated_at"),
                "encrypted": encrypted,
            })
        return parsed

    def delete_backup(self, key: str) -> None:
        settings = config.get_backup_settings()
        candidate = _validated_backup_object_key(settings, key)
        if not candidate:
            raise BackupError("备份对象 key 不能为空")
        client = CloudflareR2Client(settings)
        try:
            client.delete_object(candidate)
        finally:
            client.close()

    def _download_backup_materialized_compat(self, key: str) -> dict[str, object]:
        settings = config.get_backup_settings()
        candidate = _validated_backup_object_key(settings, key)
        if not candidate:
            raise BackupError("备份对象 key 不能为空")
        client = CloudflareR2Client(settings)
        try:
            payload = client.download_bytes(candidate)
        finally:
            client.close()
        name = candidate.rsplit("/", 1)[-1] or "backup.bin"
        if candidate.endswith(".enc"):
            passphrase = _clean(config.get_backup_settings().get("passphrase"))
            if not passphrase:
                raise BackupError("当前未配置加密口令，无法下载并解密已加密备份")
            payload = _openssl_decrypt(payload, passphrase)
            if name.endswith(".enc"):
                name = name[:-4] or "backup.tar.gz"
        return {
            "key": candidate,
            "name": name,
            "content_type": _guess_content_type(name),
            "payload": payload,
            "size": len(payload),
        }

    def _get_backup_detail_materialized_compat(self, key: str) -> dict[str, object]:
        settings = config.get_backup_settings()
        candidate = _validated_backup_object_key(settings, key)
        if not candidate:
            raise BackupError("备份对象 key 不能为空")
        client = CloudflareR2Client(settings)
        try:
            payload = client.download_bytes(candidate)
        finally:
            client.close()
        detail = self._decode_backup_payload(candidate, payload)
        detail["key"] = candidate
        detail["name"] = candidate.rsplit("/", 1)[-1]
        detail["encrypted"] = candidate.endswith(".enc")
        return detail

    def download_backup(self, key: str) -> dict[str, object]:
        item = self.prepare_backup_download(key)
        try:
            payload = Path(item["path"]).read_bytes()
        finally:
            cleanup = item.get("cleanup")
            if callable(cleanup):
                cleanup()
        return {
            "key": item["key"],
            "name": item["name"],
            "content_type": item["content_type"],
            "payload": payload,
            "size": len(payload),
        }

    def prepare_backup_download(self, key: str) -> dict[str, object]:
        settings = config.get_backup_settings()
        candidate = _validated_backup_object_key(settings, key)
        if not candidate:
            raise BackupError("backup object key cannot be empty")
        temp_dir = Path(tempfile.mkdtemp(prefix="chatgpt2api-backup-download-"))
        downloaded_path = temp_dir / (candidate.rsplit("/", 1)[-1] or "backup.bin")
        client = CloudflareR2Client(settings)
        try:
            client.download_file(candidate, downloaded_path)
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise
        finally:
            client.close()

        name = downloaded_path.name
        payload_path = downloaded_path
        if candidate.endswith(".enc"):
            passphrase = _clean(config.get_backup_settings().get("passphrase"))
            if not passphrase:
                shutil.rmtree(temp_dir, ignore_errors=True)
                raise BackupError("backup passphrase is required to decrypt this backup")
            name = name[:-4] or "backup.tar.gz"
            payload_path = temp_dir / name
            try:
                _openssl_decrypt_file(downloaded_path, payload_path, passphrase)
            except Exception:
                shutil.rmtree(temp_dir, ignore_errors=True)
                raise
        return {
            "key": candidate,
            "name": name,
            "content_type": _guess_content_type(name),
            "path": payload_path,
            "size": payload_path.stat().st_size,
            "cleanup": lambda: shutil.rmtree(temp_dir, ignore_errors=True),
        }

    def get_backup_detail(self, key: str) -> dict[str, object]:
        settings = config.get_backup_settings()
        candidate = _validated_backup_object_key(settings, key)
        if not candidate:
            raise BackupError("backup object key cannot be empty")
        temp_dir = Path(tempfile.mkdtemp(prefix="chatgpt2api-backup-detail-"))
        payload_path = temp_dir / (candidate.rsplit("/", 1)[-1] or "backup.bin")
        client = CloudflareR2Client(settings)
        try:
            client.download_file(candidate, payload_path)
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise
        finally:
            client.close()
        try:
            if candidate.endswith(".enc"):
                passphrase = _clean(config.get_backup_settings().get("passphrase"))
                if not passphrase:
                    raise BackupError("backup passphrase is required to inspect this encrypted backup")
                decoded_path = temp_dir / payload_path.name.removesuffix(".enc")
                _openssl_decrypt_file(payload_path, decoded_path, passphrase)
                payload_path = decoded_path
            detail = self._decode_archive_detail_file(payload_path)
            detail["key"] = candidate
            detail["name"] = candidate.rsplit("/", 1)[-1]
            detail["encrypted"] = candidate.endswith(".enc")
            return detail
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def run_backup(self, *, trigger: str = "manual") -> dict[str, object]:
        with self._lock:
            current = self.get_status()
            if self._running:
                raise BackupError("当前已有备份任务正在执行")
            started_at = _iso_now()
            self._running = True
            save_backup_state({
                "last_started_at": started_at,
                "last_finished_at": current.get("last_finished_at"),
                "last_status": "idle",
                "last_error": None,
                "last_object_key": current.get("last_object_key"),
            })
        try:
            result = self._run_backup_once(trigger=trigger)
            save_backup_state({
                "last_started_at": started_at,
                "last_finished_at": _iso_now(),
                "last_status": "success",
                "last_error": None,
                "last_object_key": result["key"],
            })
            return result
        except Exception as exc:
            save_backup_state({
                "last_started_at": started_at,
                "last_finished_at": _iso_now(),
                "last_status": "error",
                "last_error": str(exc) or exc.__class__.__name__,
                "last_object_key": current.get("last_object_key"),
            })
            raise
        finally:
            self._running = False

    def _run_backup_once(self, *, trigger: str) -> dict[str, object]:
        settings = config.get_backup_settings()
        client = CloudflareR2Client(settings)
        client.validate()
        encrypted = bool(settings.get("encrypt"))
        suffix = ".tar.gz.enc" if encrypted else ".tar.gz"
        timestamp = _utc_now().strftime("%Y%m%dT%H%M%SZ")
        random_tag = f"{random.randint(0, 0xFFFF):04x}"
        object_key = f"{client.prefix.rstrip('/')}/backup-{timestamp}-{random_tag}{suffix}"
        metadata = {
            "created-at": _iso_now(),
            "encrypted": "true" if encrypted else "false",
            "trigger": trigger,
        }
        try:
            with tempfile.TemporaryDirectory(prefix="chatgpt2api-backup-") as temp_dir:
                raw_path = Path(temp_dir) / "backup.tar.gz"
                self._build_backup_archive_file(settings, trigger=trigger, destination=raw_path)
                upload_path = raw_path
                if encrypted:
                    passphrase = _clean(settings.get("passphrase"))
                    if not passphrase:
                        raise BackupError("已启用备份加密，但未设置加密口令")
                    upload_path = Path(temp_dir) / "backup.tar.gz.enc"
                    _openssl_encrypt_file(raw_path, upload_path, passphrase)
                result = client.upload_file(
                    object_key,
                    upload_path,
                    content_type="application/octet-stream",
                    metadata=metadata,
                )
                payload_size = upload_path.stat().st_size
                self._apply_rotation(client, int(settings.get("rotation_keep") or 0))
                return {
                    "key": result["key"],
                    "size": payload_size,
                    "encrypted": encrypted,
                }
        finally:
            client.close()

    def _decode_backup_payload(self, key: str, payload: bytes) -> dict[str, object]:
        decoded = payload
        if key.endswith(".enc"):
            passphrase = _clean(config.get_backup_settings().get("passphrase"))
            if not passphrase:
                raise BackupError("当前未配置加密口令，无法查看已加密备份")
            decoded = _openssl_decrypt(decoded, passphrase)
        return self._decode_archive_detail(decoded)

    def _apply_rotation(self, client: CloudflareR2Client, keep: int) -> None:
        if keep <= 0:
            return
        items = [item for item in client.list_objects() if _is_backup_object(item.get("key"))]
        if len(items) <= keep:
            return
        for item in items[keep:]:
            key = _clean(item.get("key"))
            if key:
                client.delete_object(key)

    def _decode_archive_detail(self, payload: bytes) -> dict[str, object]:
        files: list[dict[str, object]] = []
        snapshots: list[dict[str, object]] = []
        metadata: dict[str, object] = {}
        try:
            with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
                members = [member for member in archive.getmembers() if member.isfile()]
                for member in members:
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        continue
                    raw = extracted.read()
                    name = member.name
                    if name == "backup-metadata.json":
                        try:
                            parsed = json.loads(raw.decode("utf-8"))
                            if isinstance(parsed, dict):
                                metadata = parsed
                        except Exception:
                            metadata = {}
                        continue
                    if name.startswith("snapshots/") and name.endswith(".json"):
                        count = 0
                        try:
                            parsed_snapshot = json.loads(raw.decode("utf-8"))
                            count = _count_items(parsed_snapshot)
                        except Exception:
                            count = 0
                        snapshots.append({
                            "name": name.removeprefix("snapshots/").removesuffix(".json"),
                            "count": count,
                        })
                        continue
                    files.append({
                        "name": name,
                        "exists": True,
                        "content_type": _guess_content_type(name),
                        "size": len(raw),
                        "sha256": _sha256_hex(raw),
                    })
        except tarfile.TarError as exc:
            raise BackupError("解析备份压缩包失败，备份可能已损坏") from exc
        files.sort(key=lambda item: str(item.get("name") or ""))
        snapshots.sort(key=lambda item: str(item.get("name") or ""))
        return {
            "created_at": metadata.get("created_at"),
            "trigger": metadata.get("trigger"),
            "app_version": metadata.get("app_version"),
            "storage_backend": metadata.get("storage_backend"),
            "files": files,
            "snapshots": snapshots,
        }

    def _decode_archive_detail_file(self, source: Path) -> dict[str, object]:
        files: list[dict[str, object]] = []
        snapshots: list[dict[str, object]] = []
        metadata: dict[str, object] = {}
        try:
            with tarfile.open(source, mode="r:gz") as archive:
                members = [member for member in archive.getmembers() if member.isfile()]
                for member in members:
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        continue
                    name = member.name
                    if name == "backup-metadata.json":
                        try:
                            parsed = json.loads(extracted.read().decode("utf-8"))
                            if isinstance(parsed, dict):
                                metadata = parsed
                        except Exception:
                            metadata = {}
                        continue
                    if name.startswith("snapshots/") and name.endswith(".json"):
                        count = 0
                        try:
                            parsed_snapshot = json.loads(extracted.read().decode("utf-8"))
                            count = _count_items(parsed_snapshot)
                        except Exception:
                            count = 0
                        snapshots.append({
                            "name": name.removeprefix("snapshots/").removesuffix(".json"),
                            "count": count,
                        })
                        continue
                    digest = hashlib.sha256()
                    size = 0
                    for chunk in iter(lambda: extracted.read(1024 * 1024), b""):
                        digest.update(chunk)
                        size += len(chunk)
                    files.append({
                        "name": name,
                        "exists": True,
                        "content_type": _guess_content_type(name),
                        "size": size,
                        "sha256": digest.hexdigest(),
                    })
        except tarfile.TarError as exc:
            raise BackupError("failed to parse backup archive; the backup may be damaged") from exc
        files.sort(key=lambda item: str(item.get("name") or ""))
        snapshots.sort(key=lambda item: str(item.get("name") or ""))
        return {
            "created_at": metadata.get("created_at"),
            "trigger": metadata.get("trigger"),
            "app_version": metadata.get("app_version"),
            "storage_backend": metadata.get("storage_backend"),
            "files": files,
            "snapshots": snapshots,
        }

    def _build_backup_archive(self, settings: dict[str, object], *, trigger: str) -> bytes:
        with tempfile.TemporaryDirectory(prefix="chatgpt2api-backup-compat-") as temp_dir:
            destination = Path(temp_dir) / "backup.tar.gz"
            self._build_backup_archive_file(
                settings,
                trigger=trigger,
                destination=destination,
            )
            return destination.read_bytes()

    def _build_backup_archive_file(
        self,
        settings: dict[str, object],
        *,
        trigger: str,
        destination: Path,
    ) -> None:
        include = settings.get("include") if isinstance(settings.get("include"), dict) else {}
        metadata = {
            "version": 2,
            "created_at": _iso_now(),
            "trigger": trigger,
            "app_version": config.app_version,
            "storage_backend": config.get_storage_backend().get_backend_info(),
        }
        logical_export = None
        if include.get("image_tasks"):
            if not callable(self._image_queue_provider):
                raise BackupError("image queue backup provider is unavailable")
            provider_owner = getattr(self._image_queue_provider, "__self__", None)
            streaming_provider = getattr(provider_owner, "write_logical_backup", None)
            if not callable(streaming_provider):
                logical_export = self._image_queue_provider()
                if not isinstance(logical_export, dict):
                    raise BackupError("image queue backup provider returned invalid payload")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(destination, mode="w:gz") as archive:
            self._add_bytes_to_archive(archive, "backup-metadata.json", _json_bytes(metadata))
            if include.get("config"):
                self._add_file_to_archive(archive, CONFIG_FILE, "config.json")
            if include.get("register"):
                self._add_file_to_archive(archive, DATA_DIR / "register.json", "data/register.json")
            if include.get("cpa"):
                self._add_file_to_archive(archive, DATA_DIR / "cpa_config.json", "data/cpa_config.json")
            if include.get("sub2api"):
                self._add_file_to_archive(archive, DATA_DIR / "sub2api_config.json", "data/sub2api_config.json")
            if include.get("logs"):
                self._add_file_to_archive(archive, DATA_DIR / "logs.jsonl", "data/logs.jsonl")
            if include.get("dashboard_metrics"):
                self._add_file_to_archive(archive, DATA_DIR / "dashboard_metrics.json", "data/dashboard_metrics.json")
            image_files: dict[str, tuple[str, int]] = {}
            if include.get("images"):
                self._add_file_to_archive(archive, TAGS_FILE, "data/image_tags.json")
                image_files = self._add_directory_snapshot_to_archive(
                    archive,
                    self._artifact_root(),
                    "data/images",
                )
            if include.get("image_tasks"):
                if callable(streaming_provider):
                    self._add_streaming_queue_export(
                        archive,
                        streaming_provider,
                        image_files=image_files,
                    )
                else:
                    logical_export = self._mark_queue_artifact_files(
                        logical_export,
                        image_files=image_files,
                    )
                    self._add_bytes_to_archive(archive, "data/image-queue.json", _json_bytes(logical_export))
                self._add_file_to_archive(archive, IMAGE_INDEX_FILE, "data/image_index.json")
            if include.get("accounts_snapshot"):
                self._add_bytes_to_archive(
                    archive,
                    "snapshots/accounts.json",
                    _json_bytes(config.get_storage_backend().load_accounts()),
                )
            if include.get("auth_keys_snapshot"):
                self._add_bytes_to_archive(
                    archive,
                    "snapshots/auth_keys.json",
                    _json_bytes(config.get_storage_backend().load_auth_keys()),
                )

    def _add_streaming_queue_export(
        self,
        archive: tarfile.TarFile,
        provider,
        *,
        image_files: dict[str, tuple[str, int]],
    ) -> None:
        with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b") as spool:
            output = io.TextIOWrapper(spool, encoding="utf-8")
            provider(
                output,
                artifact_transform=lambda item: self._mark_streamed_artifact(
                    item,
                    image_files=image_files,
                ),
            )
            output.flush()
            output.detach()
            size = spool.seek(0, os.SEEK_END)
            spool.seek(0)
            info = tarfile.TarInfo(name="data/image-queue.json")
            info.size = size
            info.mtime = int(_utc_now().timestamp())
            archive.addfile(info, spool)

    @staticmethod
    def _mark_streamed_artifact(
        item: dict[str, object],
        *,
        image_files: dict[str, tuple[str, int]],
    ) -> dict[str, object]:
        exported = dict(item)
        relative = PurePosixPath(str(exported.get("relative_path") or ""))
        relative_name = (
            relative.as_posix()
            if relative.parts and not relative.is_absolute() and ".." not in relative.parts
            else ""
        )
        archived = image_files.get(relative_name) if relative_name else None
        if archived is not None:
            expected_sha256 = str(exported.get("sha256") or "").strip().lower()
            if expected_sha256 and archived[0] != expected_sha256:
                raise ValueError(
                    f"image queue artifact checksum changed during backup: {relative_name}"
                )
            expected_size = exported.get("byte_size")
            if expected_size not in (None, "") and archived[1] != int(expected_size):
                raise ValueError(
                    f"image queue artifact size changed during backup: {relative_name}"
                )
        exported["backup_file_included"] = archived is not None
        if archived is None and bool(exported.get("backup_required")):
            raise BackupError(
                f"active image queue artifact missing from backup: {relative_name or '<invalid>'}"
            )
        return exported

    def _mark_queue_artifact_files(
        self,
        logical_export: object,
        *,
        image_files: dict[str, tuple[str, int]],
    ) -> dict[str, object]:
        exported = copy.deepcopy(logical_export) if isinstance(logical_export, dict) else {}
        artifacts = exported.get("artifacts")
        if not isinstance(artifacts, list):
            return exported
        tasks = {
            str(item.get("id") or ""): item
            for item in exported.get("tasks", [])
            if isinstance(item, dict)
        }
        for item in artifacts:
            if not isinstance(item, dict):
                continue
            relative = PurePosixPath(str(item.get("relative_path") or ""))
            included = False
            if relative.parts and not relative.is_absolute() and ".." not in relative.parts:
                relative_name = relative.as_posix()
                archived = image_files.get(relative_name)
                included = archived is not None
                expected_sha256 = str(item.get("sha256") or "").strip().lower()
                if archived is not None and expected_sha256 and archived[0] != expected_sha256:
                    raise ValueError(
                        f"image queue artifact checksum changed during backup: {relative_name}"
                    )
                expected_size = item.get("byte_size")
                if archived is not None and expected_size not in (None, "") and archived[1] != int(expected_size):
                    raise ValueError(
                        f"image queue artifact size changed during backup: {relative_name}"
                    )
            item["backup_file_included"] = included
            if not included:
                task = tasks.get(str(item.get("task_id") or ""))
                task_status = str((task or {}).get("status") or "")
                delivery_status = str((task or {}).get("delivery_status") or "")
                required = bool(item.get("backup_required")) or task_status in {
                    "queued", "running", "saving", "retrying"
                }
                required = required or (
                    task_status in {"success", "failed", "canceled"}
                    and delivery_status == "pending"
                )
                if required:
                    relative_name = relative.as_posix() if relative.parts else "<invalid>"
                    raise BackupError(
                        f"active image queue artifact missing from backup: {relative_name}"
                    )
        return exported

    def _add_bytes_to_archive(self, archive: tarfile.TarFile, name: str, payload: bytes) -> None:
        info = tarfile.TarInfo(name=name)
        info.size = len(payload)
        info.mtime = int(_utc_now().timestamp())
        archive.addfile(info, io.BytesIO(payload))

    def _add_file_to_archive(self, archive: tarfile.TarFile, source: Path, arcname: str) -> None:
        if not source.exists() or not source.is_file():
            return
        archive.add(source, arcname=arcname)

    def _add_directory_snapshot_to_archive(
        self,
        archive: tarfile.TarFile,
        source_dir: Path,
        arcname_root: str,
    ) -> dict[str, tuple[str, int]]:
        resolved_root = source_dir.resolve()
        native_root = _native_filesystem_path(resolved_root)
        if not os.path.isdir(native_root):
            return {}
        archived: dict[str, tuple[str, int]] = {}
        for dirpath, dirnames, filenames in os.walk(native_root):
            dirnames.sort()
            for filename in sorted(filenames):
                source_path = os.path.join(dirpath, filename)
                native_source_path = _native_filesystem_path(Path(source_path))
                if os.path.islink(native_source_path) or not os.path.isfile(native_source_path):
                    continue
                relative = os.path.relpath(source_path, native_root).replace("\\", "/")
                if not relative or relative.startswith("../") or relative == "..":
                    continue
                try:
                    digest = hashlib.sha256()
                    size = 0
                    with open(native_source_path, "rb") as source:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            digest.update(chunk)
                            size += len(chunk)
                        source.seek(0)
                        info = archive.gettarinfo(
                            native_source_path,
                            arcname=f"{arcname_root}/{relative}",
                        )
                        info.size = size
                        archive.addfile(info, source)
                except FileNotFoundError:
                    continue
                archived[relative] = (digest.hexdigest(), size)
        return archived


backup_service = BackupService()
