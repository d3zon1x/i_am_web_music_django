from __future__ import annotations

import json
import logging
import requests
import os
from typing import Dict, Any

from django.conf import settings
from django.http import JsonResponse, HttpRequest
from django.views.decorators.http import require_http_methods
from rest_framework.decorators import api_view
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, OpenApiExample, OpenApiParameter, extend_schema_view


def _resolve_api_key() -> str | None:
    return (
        getattr(settings, 'BOT_HTTP_API_KEY', None)
        or getattr(settings, 'FLASK_API_KEY', None)
        or os.getenv('BOT_HTTP_API_KEY')
        or os.getenv('FLASK_API_KEY')
    )


def _headers() -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    api_key = _resolve_api_key()
    if api_key:
        headers['X-Api-Key'] = api_key
    return headers


def _api_post(path: str, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
    base = getattr(settings, 'BOT_HTTP_API_BASE', 'http://127.0.0.1:5001').rstrip('/')
    url = f"{base}{path}"
    try:
        r = requests.post(url, headers=_headers(), data=json.dumps(payload), timeout=10)
        try:
            data = r.json()
        except Exception:
            data = {"error": r.text}
        return r.status_code, data
    except Exception as e:
        logging.exception("Failed calling %s", url)
        return 500, {"error": str(e)}


def _api_get(path: str, params: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
    base = getattr(settings, 'BOT_HTTP_API_BASE', 'http://127.0.0.1:5001').rstrip('/')
    url = f"{base}{path}"
    try:
        r = requests.get(url, headers=_headers(), params=params, timeout=10)
        try:
            data = r.json()
        except Exception:
            data = {"error": r.text}
        return r.status_code, data
    except Exception as e:
        logging.exception("Failed calling %s", url)
        return 500, {"error": str(e)}


@extend_schema(
    operation_id="linkAccount",
    description="Link a browser session with a Telegram bot user using a short numeric code the bot shows to the user.",
    request={'application/json': {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    responses={200: {"type": "object", "properties": {"status": {"type": "string"}, "code": {"type": "string"}}}, 400: {"type": "object", "properties": {"error": {"type": "string"}}}},
    examples=[OpenApiExample("Successful link", value={"code": "12345678"})]
)
@api_view(["POST"])
def link(request):
    code = (request.data.get("code") if isinstance(request.data, dict) else None) or ""
    code = str(code).strip()
    if not code:
        return Response({"error": "code required"}, status=400)
    status_code, data = _api_post("/api/link_by_code", {"code": code})
    if status_code == 200:
        request.session["linked"] = True
        request.session["linked_code"] = code
        return Response({"status": "linked", "code": code})
    return Response({"error": data.get("error", f"link failed ({status_code})")}, status=400)


@extend_schema(
    operation_id="sendSong",
    description="Schedule a song download to be delivered in Telegram. Accepts either a search query or a direct URL.",
    request={'application/json': {"type": "object", "properties": {"query": {"type": "string"}, "code": {"type": "string"}}, "required": ["query"]}},
    responses={200: {"type": "object", "properties": {"status": {"type": "string"}}}, 400: {"type": "object", "properties": {"error": {"type": "string"}}}},
    examples=[OpenApiExample("Send by session code", value={"query": "Daft Punk Get Lucky"})]
)
@api_view(["POST"])
def send_song(request):
    query = (request.data.get("query") if isinstance(request.data, dict) else None) or ""
    code = (request.data.get("code") if isinstance(request.data, dict) else None) or request.session.get("linked_code") or ""
    query = str(query).strip()
    code = str(code).strip()
    if not query:
        return Response({"error": "query required"}, status=400)
    if not code:
        return Response({"error": "no linked code (link first)"}, status=400)
    status_code, data = _api_post("/api/send_song_by_code", {"code": code, "query": query})
    if status_code == 200:
        # Refresh stored code for convenience
        request.session["linked_code"] = code
        return Response({"status": "scheduled"})
    return Response({"error": data.get("error", f"send failed ({status_code})")}, status=400)


@extend_schema(
    operation_id="downloadHistory",
    parameters=[OpenApiParameter(name="code", description="Optional code; if omitted uses linked session code", required=False, type=str)],
    responses={200: {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object"}}}}, 400: {"type": "object", "properties": {"error": {"type": "string"}}}},
    description="Return recent download history for the linked Telegram user. This delegates to the bot service.")
@api_view(["GET"])
def history(request: HttpRequest):
    code = request.GET.get("code") or request.session.get("linked_code") or ""
    if not code:
        return Response({"error": "no linked code"}, status=400)
    status_code, data = _api_get("/api/history_by_code", {"code": code})
    if status_code == 200:
        return Response({"items": data.get("items", data.get("history", []))})
    return Response({"error": data.get("error", f"history failed ({status_code})")}, status=400)


# Replace previous single extend_schema decorator + function for logout with method-specific schema.
@extend_schema_view(
    get=extend_schema(
        operation_id="logoutSessionGet",
        description="Idempotent logout (GET). If already disconnected or no code in session returns status not_linked.",
        parameters=[OpenApiParameter(name="code", required=False, type=str, description="Optional linking code")],
        responses={200: {"type": "object", "properties": {"status": {"type": "string"}}}},
    ),
    post=extend_schema(
        operation_id="logoutSessionPost",
        description="Logout (POST). Accepts JSON {code}. If missing/invalid code but session empty returns not_linked.",
        request={'application/json': {"type": "object", "properties": {"code": {"type": "string"}}}},
        responses={200: {"type": "object", "properties": {"status": {"type": "string"}}}},
    ),
)
@api_view(["POST", "GET"])
def logout(request):
    if request.method == "GET":
        code = request.GET.get("code") or request.session.get("linked_code") or ""
    else:
        body_code = (request.data.get("code") if isinstance(request.data, dict) else None)
        code = body_code or request.GET.get("code") or request.session.get("linked_code") or ""
    code = str(code).strip()
    if code and not code.isdigit():
        code = ""
    if not code:
        request.session.pop("linked", None)
        request.session.pop("linked_code", None)
        return Response({"status": "not_linked"}, status=200)

    status_code, data = _api_post("/api/logout", {"code": code, "source": "web"})
    # Debug log
    logging.info("WEB logout: code=%s bot_status=%s bot_payload=%s", code, status_code, data)

    if status_code == 200:
        request.session.pop("linked", None)
        request.session.pop("linked_code", None)
        return Response({"status": "logged_out"})
    if data.get("error") in {"code not found", "user not found", "invalid code", "user_id or code required"}:
        request.session.pop("linked", None)
        request.session.pop("linked_code", None)
        return Response({"status": "not_linked"}, status=200)
    if status_code == 401:
        expected = _resolve_api_key()
        return Response({
            "error": "bot unauthorized (check API key)",
            "hint": "Ensure Django and bot use the same key in X-Api-Key header.",
            "expected_key_present": bool(expected),
        }, status=502)
    if 500 <= status_code < 600:
        return Response({"error": "bot service error", "detail": data.get("error")}, status=502)
    # Fallback
    return Response({"error": data.get("error", f"logout failed ({status_code})"), "detail": data}, status=400)


@require_http_methods(["GET"])
def root_info(_request: HttpRequest):
    return JsonResponse({
        "message": "Music Bot API",
        "endpoints": {
            "POST /api/link": "Link session with Telegram code",
            "POST /api/send": "Send song query to Telegram",
            "POST /api/logout": "Logout linked session (also GET /api/logout for session-only)",
            "GET /api/history": "Download history",
            "GET /api/schema/": "OpenAPI schema",
            "GET /api/docs/": "Swagger UI",
        }
    })


# Alias view for /logout excluded from schema to avoid operationId collisions
@extend_schema(exclude=True)
@api_view(["GET", "POST"])
def logout_alias(request):
    return logout(request)
