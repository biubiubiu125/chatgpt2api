"""Global outbound proxy and Cloudflare clearance helpers."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import json
from pathlib import Path
import re
import threading
import time
from typing import Callable, Mapping
from urllib import request as urllib_request
from urllib.parse import quote, urlparse

from curl_cffi.requests import Session

from services.config import DATA_DIR, config


FlareSolverrRequestMethod = Callable[[str, bytes, dict[str, str], float], bytes]
PROXY_POOL_CONNECT_TIMEOUT_SECONDS = 2.0
PROXY_POOL_MAX_LATENCY_MS = 2000
PROXY_POOL_STATE_FILE = DATA_DIR / "proxy_pool_state.json"


def normalize_proxy_url(url: str) -> str:
    """Normalize proxy URLs for curl_cffi.

    SOCKS proxies should use remote-DNS resolution by default, so generic
    ``socks://`` and ``socks5://`` inputs are upgraded to ``socks5h://``.
    HTTP/HTTPS/socks5h inputs are otherwise left untouched except trimming.
    """
    candidate = str(url or "").strip()
    if candidate and "://" not in candidate:
        candidate = _colon_proxy_to_url(candidate)
    lowered = candidate.lower()
    if lowered.startswith("socks://"):
        return "socks5h://" + candidate[len("socks://") :]
    if lowered.startswith("socks5://"):
        return "socks5h://" + candidate[len("socks5://") :]
    return candidate


@dataclass(frozen=True)
class ProxyRuntimeProfile:
    proxy_url: str = ""
    proxy_source: str = "direct"
    resource: bool = False
    runtime_enabled: bool = False
    egress_mode: str = "direct"
    skip_ssl_verify: bool = False
    reset_session_status_codes: tuple[int, ...] = field(default_factory=lambda: (403,))
    clearance: dict[str, object] = field(default_factory=dict, repr=False)

    @property
    def clearance_enabled(self) -> bool:
        return (
            self.runtime_enabled
            and bool(self.clearance.get("enabled"))
            and self.clearance_mode in {"manual", "flaresolverr"}
        )

    @property
    def clearance_mode(self) -> str:
        return str(self.clearance.get("mode") or "none").strip().lower()

    @property
    def refresh_interval(self) -> int:
        try:
            return max(0, int(self.clearance.get("refresh_interval") or 0))
        except (OverflowError, TypeError, ValueError):
            return 0

    @property
    def timeout_sec(self) -> int:
        try:
            return max(1, int(self.clearance.get("timeout_sec") or 60))
        except (OverflowError, TypeError, ValueError):
            return 60


@dataclass(frozen=True)
class ClearanceBundle:
    target_host: str
    proxy_url: str = ""
    cookies: dict[str, str] = field(default_factory=dict, repr=False)
    user_agent: str = ""
    created_at: float = field(default_factory=time.time)
    expires_at: float | None = None

    def is_valid_for(self, target_host: str, proxy_url: str, *, now: float | None = None) -> bool:
        host = _normalize_host(target_host)
        if self.target_host and host and _normalize_host(self.target_host) != host:
            return False
        if normalize_proxy_url(self.proxy_url) != normalize_proxy_url(proxy_url):
            return False
        if self.expires_at is not None and (time.time() if now is None else now) >= self.expires_at:
            return False
        return bool(self.cookies or self.user_agent)

    def cookie_header(self) -> str:
        return _cookies_to_header(self.cookies)


class FlareSolverrClearanceProvider:
    def __init__(self, flaresolverr_url: str, request_method: FlareSolverrRequestMethod | None = None) -> None:
        self.flaresolverr_url = str(flaresolverr_url or "").strip().rstrip("/")
        self._request_method = request_method or self._urllib_post

    def get_clearance(self, target_url: str, proxy_url: str = "", timeout_sec: int = 60) -> ClearanceBundle | None:
        if not self.flaresolverr_url:
            return None

        timeout = _coerce_timeout(timeout_sec)
        payload: dict[str, object] = {
            "cmd": "request.get",
            "url": str(target_url or ""),
            "maxTimeout": int(timeout * 1000),
        }
        proxy_url = normalize_proxy_url(proxy_url)
        if proxy_url:
            payload["proxy"] = {"url": proxy_url}

        endpoint = f"{self.flaresolverr_url}/v1"
        try:
            body = json.dumps(payload).encode("utf-8")
            raw_response = self._request_method(
                endpoint,
                body,
                {"Content-Type": "application/json"},
                timeout,
            )
            data = json.loads(raw_response.decode("utf-8") if isinstance(raw_response, bytes) else raw_response)
        except Exception:
            return None

        if not isinstance(data, dict) or str(data.get("status") or "").lower() != "ok":
            return None
        solution = data.get("solution")
        if not isinstance(solution, dict):
            return None

        target_host = _host_from_url(target_url)
        cookies = _filter_flaresolverr_cookies(solution.get("cookies"), target_host)
        user_agent = str(solution.get("userAgent") or "").strip()
        if not cookies and not user_agent:
            return None
        return ClearanceBundle(
            target_host=target_host,
            proxy_url=proxy_url,
            cookies=cookies,
            user_agent=user_agent,
        )

    @staticmethod
    def _urllib_post(endpoint: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        req = urllib_request.Request(endpoint, data=body, headers=headers, method="POST")
        with urllib_request.urlopen(req, timeout=timeout) as response:
            return response.read()


class ProxySettingsStore:
    def __init__(
        self,
        config_store=None,
        clearance_provider_factory: Callable[[str], FlareSolverrClearanceProvider] | None = None,
        state_path: str | Path | None = None,
    ) -> None:
        self._config = config_store or config
        self._clearance_provider_factory = clearance_provider_factory or FlareSolverrClearanceProvider
        self._clearance_cache: dict[tuple[str, str], ClearanceBundle] = {}
        self._provider_cache: dict[str, FlareSolverrClearanceProvider] = {}
        self._flight_locks: dict[tuple[str, str], threading.Lock] = {}
        self._pool_index = 0
        self._state_path = Path(state_path) if state_path is not None else (PROXY_POOL_STATE_FILE if config_store is None else None)
        self._pool_failures, self._pool_failure_sources = self._load_pool_failure_state()
        self._lock = threading.RLock()

    @staticmethod
    def _account_pool_key(account: dict | None) -> str:
        if not isinstance(account, dict):
            return ""
        for key in ("email", "user_id", "id", "access_token"):
            value = str(account.get(key) or "").strip()
            if value:
                return value
        return ""

    def get_profile(
        self,
        account: dict | None = None,
        proxy: str = "",
        resource: bool = False,
        upstream: bool = False,
        select_pool: bool = True,
    ) -> ProxyRuntimeProfile:
        runtime = self._get_runtime_settings()
        clearance = dict(runtime.get("clearance") if isinstance(runtime.get("clearance"), dict) else {})
        runtime_enabled = bool(runtime.get("enabled"))
        egress_mode = str(runtime.get("egress_mode") or "direct").strip().lower()

        account_proxy = normalize_proxy_url(_clean((account or {}).get("proxy") if isinstance(account, dict) else ""))
        explicit_proxy = normalize_proxy_url(_clean(proxy))
        pool_proxy = ""
        has_proxy_pool = bool(self._get_proxy_pool())

        account_pool_key = self._account_pool_key(account)
        if select_pool and not account_proxy and not explicit_proxy:
            pool_proxy = self._select_pool_proxy(account_pool_key)

        runtime_proxy = ""
        runtime_proxy_source = "runtime"
        if upstream and runtime_enabled and egress_mode == "single_proxy" and not pool_proxy and not (select_pool and has_proxy_pool):
            resource_proxy = _clean(runtime.get("resource_proxy_url")) if resource else ""
            runtime_proxy = resource_proxy or _clean(runtime.get("proxy_url"))
            runtime_proxy_source = "runtime_resource" if resource_proxy else "runtime"

        selected_proxy = ""
        source = "direct"
        if account_proxy:
            selected_proxy = account_proxy
            source = "account"
        elif explicit_proxy:
            selected_proxy = explicit_proxy
            source = "explicit"
        elif pool_proxy:
            selected_proxy = pool_proxy
            source = "global_pool"
        elif runtime_proxy:
            selected_proxy = runtime_proxy
            source = runtime_proxy_source

        return ProxyRuntimeProfile(
            proxy_url=normalize_proxy_url(selected_proxy),
            proxy_source=source,
            resource=bool(resource),
            runtime_enabled=runtime_enabled,
            egress_mode=egress_mode,
            skip_ssl_verify=bool(runtime.get("skip_ssl_verify")) if runtime_enabled else False,
            reset_session_status_codes=_status_codes_tuple(runtime.get("reset_session_status_codes")),
            clearance=clearance,
        )

    def build_session_kwargs(
        self,
        account: dict | None = None,
        proxy: str = "",
        resource: bool = False,
        upstream: bool = False,
        profile: ProxyRuntimeProfile | None = None,
        select_pool: bool = True,
        **session_kwargs,
    ) -> dict[str, object]:
        profile = profile or self.get_profile(
            account=account,
            proxy=proxy,
            resource=resource,
            upstream=upstream,
            select_pool=select_pool,
        )
        if profile.proxy_url:
            session_kwargs["proxy"] = profile.proxy_url
        if profile.runtime_enabled and profile.skip_ssl_verify:
            session_kwargs["verify"] = False
        return session_kwargs

    def build_headers(
        self,
        headers: Mapping[str, object] | None = None,
        target_url: str = "https://chatgpt.com",
        account: dict | None = None,
        proxy: str = "",
        resource: bool = False,
        upstream: bool = True,
        select_pool: bool = True,
    ) -> dict[str, object]:
        merged_headers: dict[str, object] = dict(headers or {})
        profile = self.get_profile(account=account, proxy=proxy, resource=resource, upstream=upstream, select_pool=select_pool)
        if not profile.clearance_enabled:
            return merged_headers

        target_host = _host_from_url(target_url)
        bundle = self._bundle_for_headers(profile, target_host)
        if bundle is None or not bundle.is_valid_for(target_host, profile.proxy_url):
            return merged_headers

        if bundle.user_agent and _find_header_key(merged_headers, "user-agent") is None:
            merged_headers["User-Agent"] = bundle.user_agent

        if bundle.cookies:
            cookie_key = _find_header_key(merged_headers, "cookie") or "Cookie"
            existing_cookie = str(merged_headers.get(cookie_key) or "")
            cookie_header = _merge_cookie_header(existing_cookie, bundle.cookies)
            if cookie_header:
                merged_headers[cookie_key] = cookie_header
        return merged_headers

    def refresh_clearance(
        self,
        target_url: str = "https://chatgpt.com",
        account: dict | None = None,
        proxy: str = "",
        resource: bool = False,
        force: bool = False,
        upstream: bool = True,
        select_pool: bool = True,
    ) -> ClearanceBundle | None:
        profile = self.get_profile(account=account, proxy=proxy, resource=resource, upstream=upstream, select_pool=select_pool)
        if not profile.clearance_enabled:
            return None

        target_host = _host_from_url(target_url)
        key = self._cache_key(profile.proxy_url, target_host)
        if profile.clearance_mode == "manual":
            bundle = self._build_manual_bundle(profile, target_host)
            if bundle is not None:
                self._set_cached_bundle(key, bundle)
            return bundle
        if profile.clearance_mode != "flaresolverr":
            return None

        cached_before = self._get_cached_bundle(key)
        if cached_before is not None and not force and cached_before.is_valid_for(target_host, profile.proxy_url):
            return cached_before

        lock = self._get_flight_lock(key)
        if not lock.acquire(blocking=False):
            with lock:
                pass
            return self._get_cached_bundle(key) or cached_before

        try:
            cached_now = self._get_cached_bundle(key)
            if cached_now is not None and not force and cached_now.is_valid_for(target_host, profile.proxy_url):
                return cached_now

            flaresolverr_url = str(profile.clearance.get("flaresolverr_url") or "").strip()
            provider = self._get_provider(flaresolverr_url)
            new_bundle = provider.get_clearance(target_url, proxy_url=profile.proxy_url, timeout_sec=profile.timeout_sec)
            if new_bundle is not None:
                expires_at = time.time() + profile.refresh_interval if profile.refresh_interval else None
                if (
                    not new_bundle.target_host
                    or normalize_proxy_url(new_bundle.proxy_url) != normalize_proxy_url(profile.proxy_url)
                    or new_bundle.expires_at != expires_at
                ):
                    new_bundle = replace(
                        new_bundle,
                        target_host=new_bundle.target_host or target_host,
                        proxy_url=profile.proxy_url,
                        expires_at=expires_at,
                    )
                self._set_cached_bundle(key, new_bundle)
                return new_bundle
            return cached_now or cached_before
        finally:
            lock.release()

    def invalidate_clearance(
        self,
        target_url: str = "https://chatgpt.com",
        account: dict | None = None,
        proxy: str = "",
        resource: bool = False,
        upstream: bool = True,
        select_pool: bool = True,
    ) -> None:
        profile = self.get_profile(account=account, proxy=proxy, resource=resource, upstream=upstream, select_pool=select_pool)
        target_host = _host_from_url(target_url)
        key = self._cache_key(profile.proxy_url, target_host)
        with self._lock:
            self._clearance_cache.pop(key, None)

    def get_runtime_status(self) -> dict[str, object]:
        profile = self.get_profile(upstream=True, select_pool=False)
        pool = self._get_proxy_pool()
        with self._lock:
            self._prune_pool_failures_locked(pool)
            cached_hosts = [host for _proxy, host in self._clearance_cache]
            cached_count = len(self._clearance_cache)
            pool_set = {normalize_proxy_url(proxy) for proxy in pool}
            pool_failures = {
                _redact_url_credentials(proxy): self._pool_failures.get(normalize_proxy_url(proxy), 0)
                for proxy in pool
            }
            account_proxy_failures = {
                _redact_url_credentials(proxy): count
                for proxy, count in sorted(self._pool_failures.items())
                if proxy not in pool_set and count > 0
            }
        return {
            "enabled": profile.runtime_enabled,
            "egress_mode": profile.egress_mode,
            "proxy_source": profile.proxy_source,
            "has_proxy": bool(profile.proxy_url),
            "proxy_pool_count": len(pool),
            "proxy_pool_mode": self._get_proxy_pool_mode(),
            "proxy_pool_failures": pool_failures,
            "account_proxy_failure_count": len(account_proxy_failures),
            "account_proxy_failures": account_proxy_failures,
            "clearance_enabled": profile.clearance_enabled,
            "clearance_mode": profile.clearance_mode,
            "has_clearance_bundle": cached_count > 0,
            "cached_clearance_hosts": sorted(set(cached_hosts)),
        }

    def reset_proxy_failures(self) -> int:
        with self._lock:
            count = len(self._pool_failures)
            if count:
                self._pool_failures.clear()
                self._pool_failure_sources.clear()
                self._save_pool_failures_locked()
            return count

    def _get_runtime_settings(self) -> dict[str, object]:
        try:
            runtime = self._config.get_proxy_runtime_settings()
        except AttributeError:
            runtime = {}
        return runtime if isinstance(runtime, dict) else {}

    def _get_proxy_pool(self) -> list[str]:
        try:
            pool = self._config.get_proxy_pool()
        except AttributeError:
            pool = []
        return [normalize_proxy_url(item) for item in pool if normalize_proxy_url(item)]

    def _get_proxy_pool_failover_threshold(self) -> int:
        try:
            return max(1, int(self._config.get_proxy_pool_failover_threshold()))
        except Exception:
            return 2

    def _get_proxy_pool_mode(self) -> str:
        try:
            mode = str(self._config.get_proxy_pool_mode() or "").strip().lower()
        except Exception:
            mode = ""
        return mode if mode in {"sticky", "round_robin"} else "sticky"

    def _is_proxy_blocked(self, proxy: str) -> bool:
        normalized = normalize_proxy_url(proxy)
        if not normalized:
            return False
        threshold = self._get_proxy_pool_failover_threshold()
        with self._lock:
            return self._pool_failures.get(normalized, 0) >= threshold

    def _load_pool_failure_state(self) -> tuple[dict[str, int], dict[str, str]]:
        if self._state_path is None:
            return {}, {}
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}, {}
        failures = data.get("failures") if isinstance(data, dict) else {}
        if not isinstance(failures, dict):
            return {}, {}
        sources = data.get("sources") if isinstance(data, dict) else {}
        sources = sources if isinstance(sources, dict) else {}
        result: dict[str, int] = {}
        source_result: dict[str, str] = {}
        for raw_proxy, raw_count in failures.items():
            proxy = normalize_proxy_url(str(raw_proxy or ""))
            try:
                count = int(raw_count)
            except (TypeError, ValueError):
                continue
            if proxy and count > 0:
                result[proxy] = count
                source_result[proxy] = str(sources.get(raw_proxy) or sources.get(proxy) or "unknown")
        return result, source_result

    def _save_pool_failures_locked(self) -> None:
        if self._state_path is None:
            return
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "failures": dict(sorted(self._pool_failures.items())),
                "sources": {
                    proxy: self._pool_failure_sources.get(proxy, "unknown")
                    for proxy in sorted(self._pool_failures)
                },
                "updated_at": int(time.time()),
            }
            self._state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _prune_pool_failures_locked(self, pool: list[str]) -> None:
        valid = {normalize_proxy_url(proxy) for proxy in pool if normalize_proxy_url(proxy)}
        stale = [
            proxy
            for proxy in self._pool_failures
            if proxy not in valid and self._pool_failure_sources.get(proxy) != "account"
        ]
        if not stale:
            return
        for proxy in stale:
            self._pool_failures.pop(proxy, None)
            self._pool_failure_sources.pop(proxy, None)
        self._save_pool_failures_locked()

    def _available_pool_locked(self, pool: list[str], threshold: int, *, mutate: bool = True) -> list[str]:
        available = [
            proxy
            for proxy in pool
            if self._pool_failures.get(normalize_proxy_url(proxy), 0) < threshold
        ]
        return available

    @staticmethod
    def _pool_index_for_key(key: str, count: int) -> int:
        if count <= 0:
            return 0
        digest = hashlib.sha256(key.encode("utf-8", errors="ignore")).digest()
        return int.from_bytes(digest[:8], "big") % count

    def _select_pool_proxy(self, account_key: str = "") -> str:
        pool = self._get_proxy_pool()
        if not pool:
            return ""
        threshold = self._get_proxy_pool_failover_threshold()
        mode = self._get_proxy_pool_mode()
        with self._lock:
            self._prune_pool_failures_locked(pool)
            available = self._available_pool_locked(pool, threshold)
            if not available:
                return ""
            if account_key and mode == "sticky":
                return available[self._pool_index_for_key(account_key, len(available))]
            proxy = available[self._pool_index % len(available)]
            self._pool_index += 1
            return proxy

    def _next_pool_proxy(self) -> str:
        return self._select_pool_proxy("")

    def record_proxy_result(self, proxy_url: str, *, ok: bool, source: str = "") -> None:
        proxy = normalize_proxy_url(proxy_url)
        if not proxy:
            return
        source = str(source or "").strip() or "unknown"
        with self._lock:
            if ok:
                if self._pool_failures.pop(proxy, None) is not None:
                    self._pool_failure_sources.pop(proxy, None)
                    self._save_pool_failures_locked()
                return

        probe = test_proxy(proxy, timeout=PROXY_POOL_CONNECT_TIMEOUT_SECONDS)
        probe_ok = bool(probe.get("ok"))
        try:
            latency_ms = int(probe.get("latency_ms") or 0)
        except (TypeError, ValueError):
            latency_ms = 0
        try:
            status = int(probe.get("status") or 0)
        except (TypeError, ValueError):
            status = 0
        connected = status != 407 and (probe_ok or (status > 0 and status < 600))
        if connected and latency_ms <= PROXY_POOL_MAX_LATENCY_MS:
            with self._lock:
                if self._pool_failures.pop(proxy, None) is not None:
                    self._pool_failure_sources.pop(proxy, None)
                    self._save_pool_failures_locked()
            return

        with self._lock:
            self._pool_failures[proxy] = self._pool_failures.get(proxy, 0) + 1
            self._pool_failure_sources[proxy] = source
            self._save_pool_failures_locked()

    def _bundle_for_headers(self, profile: ProxyRuntimeProfile, target_host: str) -> ClearanceBundle | None:
        key = self._cache_key(profile.proxy_url, target_host)
        if profile.clearance_mode == "manual":
            bundle = self._build_manual_bundle(profile, target_host)
            if bundle is not None:
                self._set_cached_bundle(key, bundle)
            return bundle
        if profile.clearance_mode == "flaresolverr":
            return self._get_cached_bundle(key)
        return None

    def _build_manual_bundle(self, profile: ProxyRuntimeProfile, target_host: str) -> ClearanceBundle | None:
        cookies = _parse_cookie_header(str(profile.clearance.get("cf_cookies") or ""))
        cf_clearance = str(profile.clearance.get("cf_clearance") or "").strip()
        if cf_clearance and "cf_clearance" not in cookies:
            cookies["cf_clearance"] = cf_clearance
        user_agent = str(profile.clearance.get("user_agent") or "").strip()
        if not cookies and not user_agent:
            return None

        now = time.time()
        expires_at = now + profile.refresh_interval if profile.refresh_interval else None
        return ClearanceBundle(
            target_host=target_host,
            proxy_url=profile.proxy_url,
            cookies=cookies,
            user_agent=user_agent,
            created_at=now,
            expires_at=expires_at,
        )

    def _get_provider(self, flaresolverr_url: str) -> FlareSolverrClearanceProvider:
        url = str(flaresolverr_url or "").strip().rstrip("/")
        with self._lock:
            provider = self._provider_cache.get(url)
            if provider is None:
                provider = self._clearance_provider_factory(url)
                self._provider_cache[url] = provider
            return provider

    def _get_flight_lock(self, key: tuple[str, str]) -> threading.Lock:
        with self._lock:
            lock = self._flight_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._flight_locks[key] = lock
            return lock

    def _get_cached_bundle(self, key: tuple[str, str]) -> ClearanceBundle | None:
        with self._lock:
            return self._clearance_cache.get(key)

    def _set_cached_bundle(self, key: tuple[str, str], bundle: ClearanceBundle) -> None:
        with self._lock:
            self._clearance_cache[key] = bundle

    @staticmethod
    def _cache_key(proxy_url: str, target_host: str) -> tuple[str, str]:
        return (normalize_proxy_url(proxy_url), _normalize_host(target_host))


def _clean(value: object) -> str:
    return str(value or "").strip()


def _colon_proxy_to_url(url: str) -> str:
    parts = url.split(":", 3)
    if len(parts) == 4 and parts[1].isdigit():
        host, port, username, password = parts
        return f"http://{quote(username, safe='')}:{quote(password, safe='')}@{host}:{port}"
    if len(parts) == 2 and parts[1].isdigit():
        return f"http://{url}"
    return url


def _normalize_host(host: str) -> str:
    return str(host or "").strip().strip(".").lower()


def _host_from_url(url: str) -> str:
    candidate = str(url or "").strip()
    parsed = urlparse(candidate)
    if not parsed.hostname and candidate and "://" not in candidate:
        parsed = urlparse(f"https://{candidate}")
    return _normalize_host(parsed.hostname or "")


def _status_codes_tuple(value: object) -> tuple[int, ...]:
    source = value if isinstance(value, list) else [403]
    codes: list[int] = []
    for item in source:
        if isinstance(item, bool):
            continue
        try:
            code = int(item)
        except (OverflowError, TypeError, ValueError):
            continue
        if 100 <= code <= 599 and code not in codes:
            codes.append(code)
    return tuple(codes or [403])


def _coerce_timeout(value: object) -> float:
    try:
        timeout = float(value)
    except (OverflowError, TypeError, ValueError):
        timeout = 60.0
    return max(1.0, timeout)


def _is_valid_proxy_url(url: str) -> bool:
    parsed = urlparse(normalize_proxy_url(url))
    return parsed.scheme in {"http", "https", "socks5", "socks5h"} and bool(parsed.netloc)


def _domain_matches(host: str, domain: str) -> bool:
    normalized_host = _normalize_host(host)
    normalized_domain = _normalize_host(domain.lstrip("."))
    if not normalized_domain:
        return True
    return normalized_host == normalized_domain or normalized_host.endswith(f".{normalized_domain}")


def _filter_flaresolverr_cookies(raw_cookies: object, target_host: str) -> dict[str, str]:
    if not isinstance(raw_cookies, list):
        return {}

    filtered_cookies: dict[str, str] = {}
    for item in raw_cookies:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        value = str(item.get("value") or "")
        domain = str(item.get("domain") or "").strip()
        if not domain or _domain_matches(target_host, domain):
            filtered_cookies[name] = value
    return filtered_cookies


def _parse_cookie_header(header: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in str(header or "").split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name:
            cookies[name.strip()] = value.strip()
    return cookies


def _cookies_to_header(cookies: Mapping[str, str]) -> str:
    return "; ".join(f"{name}={value}" for name, value in cookies.items() if name)


def _merge_cookie_header(existing_header: str, cookies: Mapping[str, str]) -> str:
    existing = str(existing_header or "").strip()
    existing_names = set(_parse_cookie_header(existing).keys())
    additions = [f"{name}={value}" for name, value in cookies.items() if name and name not in existing_names]
    if existing and additions:
        return existing.rstrip("; ") + "; " + "; ".join(additions)
    if existing:
        return existing
    return "; ".join(additions)


def _find_header_key(headers: Mapping[str, object], name: str) -> str | None:
    target = name.lower()
    for key in headers:
        if str(key).lower() == target:
            return str(key)
    return None


def _redact_url_credentials(text: str) -> str:
    return re.sub(
        r"((?:https?|socks5h?|socks)://)([^\s/@:]+):([^\s/@]+)@",
        r"\1[REDACTED]@",
        str(text or ""),
        flags=re.IGNORECASE,
    )


def is_proxy_transport_error(message: object) -> bool:
    text = str(message or "").lower()
    return (
        "curl: (28)" in text
        or "operation timed out" in text
        or "connection timed out" in text
        or "read timed out" in text
        or "connect timeout" in text
        or "curl: (35)" in text
        or "tls connect error" in text
        or "openssl_internal" in text
        or "ssl: wrong_version_number" in text
        or "ssl: certificate_verify_failed" in text
        or "connection aborted" in text
        or "remote disconnected" in text
        or "connection reset by peer" in text
        or "failed to connect" in text
        or "could not connect" in text
        or "couldn't connect" in text
        or "proxy connection" in text
        or "proxyconnect" in text
        or "socks connect" in text
        or "socks error" in text
        or "tunnel connection failed" in text
        or "connect error" in text
        or "connection refused" in text
    )


def record_backend_proxy_result(backend: object, ok: bool) -> None:
    recorder = getattr(backend, "record_proxy_result", None)
    if callable(recorder):
        try:
            recorder(ok)
        except Exception:
            pass
        return
    profile = getattr(backend, "proxy_profile", None)
    proxy_url = str(getattr(profile, "proxy_url", "") or "")
    if proxy_url:
        proxy_settings.record_proxy_result(proxy_url, ok=ok, source=str(getattr(profile, "proxy_source", "") or "unknown"))


def test_proxy(url: str = "", *, timeout: float = 15.0) -> dict:
    candidate = normalize_proxy_url(_clean(url))
    proxy_source = "input"
    if not candidate:
        profile = proxy_settings.get_profile(upstream=True)
        candidate = profile.proxy_url
        proxy_source = profile.proxy_source
    result_base = {"proxy_source": proxy_source, "has_proxy": bool(candidate)}
    if not candidate:
        return {
            "ok": False,
            "status": 0,
            "latency_ms": 0,
            "error": "no active proxy configured",
            **result_base,
        }
    if not _is_valid_proxy_url(candidate):
        return {
            "ok": False,
            "status": 0,
            "latency_ms": 0,
            "error": "invalid proxy url",
            **result_base,
        }
    session = Session(impersonate="edge101", verify=True, proxy=candidate)
    started = time.perf_counter()
    try:
        response = session.get(
            "https://chatgpt.com/api/auth/csrf",
            headers={"user-agent": "Mozilla/5.0 (chatgpt2api proxy test)"},
            timeout=timeout,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        status_code = int(response.status_code)
        ok = status_code < 500 and status_code != 407
        return {
            "ok": ok,
            "status": status_code,
            "latency_ms": latency_ms,
            "error": None if ok else f"HTTP {status_code}",
            **result_base,
        }
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return {
            "ok": False,
            "status": 0,
            "latency_ms": latency_ms,
            "error": _redact_url_credentials(str(exc) or exc.__class__.__name__),
            **result_base,
        }
    finally:
        session.close()


def test_proxy_pool(*, timeout: float = 15.0) -> dict:
    pool = proxy_settings._get_proxy_pool()
    results = []
    for proxy in pool:
        results.append(test_proxy(proxy, timeout=timeout))
    ok_count = sum(1 for item in results if item.get("ok"))
    return {
        "ok": bool(pool) and ok_count == len(pool),
        "total": len(pool),
        "ok_count": ok_count,
        "items": results,
    }


def test_clearance(target_url: str = "https://chatgpt.com") -> dict:
    target_url = str(target_url or "https://chatgpt.com").strip() or "https://chatgpt.com"
    started = time.perf_counter()
    status = proxy_settings.get_runtime_status()
    if not status.get("clearance_enabled"):
        return {
            "ok": False,
            "status": "disabled",
            "latency_ms": 0,
            "has_cookies": False,
            "user_agent": "",
            "error": "clearance is disabled",
            "runtime": status,
        }
    try:
        bundle = proxy_settings.refresh_clearance(target_url=target_url, force=True, upstream=True)
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return {
            "ok": False,
            "status": "error",
            "latency_ms": latency_ms,
            "has_cookies": False,
            "user_agent": "",
            "error": _redact_url_credentials(str(exc) or exc.__class__.__name__),
            "runtime": proxy_settings.get_runtime_status(),
        }

    latency_ms = int((time.perf_counter() - started) * 1000)
    runtime = proxy_settings.get_runtime_status()
    if bundle is None:
        return {
            "ok": False,
            "status": "failed",
            "latency_ms": latency_ms,
            "has_cookies": False,
            "user_agent": "",
            "error": "clearance refresh returned no bundle",
            "runtime": runtime,
        }
    return {
        "ok": True,
        "status": "ok",
        "latency_ms": latency_ms,
        "has_cookies": bool(bundle.cookies),
        "user_agent": bundle.user_agent or "",
        "error": None,
        "runtime": runtime,
    }


proxy_settings = ProxySettingsStore()
