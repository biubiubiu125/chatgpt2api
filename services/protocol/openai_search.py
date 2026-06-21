from __future__ import annotations

from services.account_service import account_service
from services.openai_backend_api import OpenAIBackendAPI, SEARCH_MODEL
from services.proxy_service import is_proxy_transport_error, record_backend_proxy_result

MODEL = SEARCH_MODEL


def handle(body: dict[str, object]) -> dict[str, object]:
    token = account_service.get_text_access_token()
    account = account_service.get_account(token) or {}
    backend = OpenAIBackendAPI(token)
    try:
        result = backend.search(str(body["prompt"]))
        record_backend_proxy_result(backend, True)
        account_service.mark_text_used(token)
        result["_account_email"] = str(account.get("email") or "")
        return result
    except Exception as exc:
        record_backend_proxy_result(backend, not is_proxy_transport_error(exc))
        raise
