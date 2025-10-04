# web/utils.py
from __future__ import annotations

from typing import Optional
from django.http import HttpRequest


def set_linked_session(request: HttpRequest, code: str) -> None:
    request.session["linked"] = True
    request.session["linked_code"] = code


def clear_linked_session(request: HttpRequest) -> None:
    request.session.pop("linked", None)
    request.session.pop("linked_code", None)


def get_code_from_request_or_session(request: HttpRequest) -> str:
    # POST JSON body may already be parsed by DRF, fallback to GET or session
    body = getattr(request, "data", None)
    body_code = (body.get("code") if isinstance(body, dict) else None) if body is not None else None
    code = body_code or request.GET.get("code") or request.session.get("linked_code") or ""
    return str(code).strip()


def normalize_code(code: str) -> str:
    c = (code or "").strip()
    return c if c.isdigit() else ""


def parse_limited_int(val: str, default: int, min_v: int, max_v: int) -> Optional[int]:
    try:
        return max(min_v, min(max_v, int(val)))
    except Exception:
        return default
