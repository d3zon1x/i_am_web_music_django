from __future__ import annotations

import json
import logging
import requests
import os
from typing import Dict, Any, List

from django.conf import settings
from django.http import JsonResponse, HttpRequest
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from dotenv import load_dotenv
from rest_framework.decorators import api_view
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, OpenApiExample, OpenApiParameter, extend_schema_view

from django.db.models import Count, Min, Max
from .models import History, User, Favorite  # Track reachable via related fields

load_dotenv()

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
        request.session["linked_code"] = code
        return Response({"status": "scheduled"})
    return Response({"error": data.get("error", f"send failed ({status_code})")}, status=400)


@extend_schema(
    operation_id="downloadHistory",
    parameters=[OpenApiParameter(name="code", description="Optional code; if omitted uses linked session code", required=False, type=str)],
    responses={200: {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object"}}}}, 400: {"type": "object", "properties": {"error": {"type": "string"}}}},
    description="Return recent download history for the linked Telegram user from local DB (no bot HTTP call).")
@api_view(["GET"])
def history(request: HttpRequest):
    code = request.GET.get("code") or request.session.get("linked_code") or ""
    code = str(code).strip()
    if not code:
        return Response({"error": "no linked code"}, status=400)
    if not code.isdigit():
        return Response({"error": "invalid code"}, status=400)

    # Resolve Telegram user_id by website_link_code using ORM (BotUser unmanaged model)
    try:
        user_id = User.objects.filter(website_link_code=int(code)).values_list("id", flat=True).first()
    except Exception as e:
        logging.exception("history: failed to resolve user by code using ORM")
        return Response({"error": "history unavailable", "detail": str(e)}, status=400)

    if not user_id:
        return Response({"error": "code not found"}, status=400)

    # Fetch recent history for this user; cap to a reasonable window
    qs = (
        History.objects
        .select_related("track")
        .filter(user_id=user_id)
        .order_by("-downloaded_at")[:200]
    )

    items: List[Dict[str, Any]] = []
    for h in qs:
        t = h.track
        items.append({
            "id": t.id,
            "title": t.title,
            "artist": t.artist,
            "youtube_url": t.youtube_url,
            "thumbnail_url": t.thumbnail_url,
            "duration": t.duration,
            "downloaded_at": h.downloaded_at,
        })

    return Response({"items": items})


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


@extend_schema(
    operation_id="charts",
    description="Return most downloaded tracks aggregated over a period. period in {week,month,year,all}. Default week. limit default 20 (max 100).",
    parameters=[
        OpenApiParameter(name="period", required=False, type=str, description="Aggregation window: week|month|year|all"),
        OpenApiParameter(name="limit", required=False, type=int, description="Number of tracks to return (1-100)"),
    ],
    responses={200: {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object"}}}}, 400: {"type": "object", "properties": {"error": {"type": "string"}}}},
    examples=[OpenApiExample("Weekly top 10", value={"items": [{"id": 1, "title": "Track", "downloads": 42}]})]
)
@api_view(["GET"])
def charts(request: HttpRequest):
    period = (request.GET.get("period") or "week").lower().strip()
    limit_raw = request.GET.get("limit") or "20"
    try:
        limit = max(1, min(100, int(limit_raw)))
    except ValueError:
        return Response({"error": "invalid limit"}, status=400)

    days_map = {"week": 7, "month": 30, "year": 365}
    cutoff = None
    if period in days_map:
        cutoff = timezone.now() - timezone.timedelta(days=days_map[period])
    elif period not in {"all", "*", ""}:
        return Response({"error": "invalid period"}, status=400)

    qs = History.objects.select_related("track")
    if cutoff is not None:
        qs = qs.filter(downloaded_at__gte=cutoff)

    # Aggregate using ORM
    agg = (
        qs.values(
            "track_id",
            "track__title",
            "track__artist",
            "track__youtube_url",
            "track__thumbnail_url",
            "track__duration",
        )
        .annotate(
            downloads=Count("id"),
            first_downloaded=Min("downloaded_at"),
            last_downloaded=Max("downloaded_at"),
        )
        .order_by("-downloads", "-last_downloaded")[:limit]
    )

    items: List[Dict[str, Any]] = [
        {
            "id": row["track_id"],
            "title": row["track__title"],
            "artist": row["track__artist"],
            "youtube_url": row["track__youtube_url"],
            "thumbnail_url": row["track__thumbnail_url"],
            "duration": row["track__duration"],
            "downloads": row["downloads"],
            "first_downloaded": row["first_downloaded"],
            "last_downloaded": row["last_downloaded"],
        }
        for row in agg
    ]
    return Response({"items": items, "period": period, "limit": limit})


@extend_schema(
    operation_id="favorites",
    description="Return favorite tracks for the linked Telegram user from local DB (no bot HTTP call).",
    parameters=[
        OpenApiParameter(name="code", description="Optional code; if omitted uses linked session code", required=False, type=str),
        OpenApiParameter(name="limit", description="Optional limit of items to return (1-1000). Default 500.", required=False, type=int),
    ],
    responses={200: {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object"}}}}, 400: {"type": "object", "properties": {"error": {"type": "string"}}}},
)
@api_view(["GET"])
def favorites(request: HttpRequest):
    code = request.GET.get("code") or request.session.get("linked_code") or ""
    code = str(code).strip()
    if not code:
        return Response({"error": "no linked code"}, status=400)
    if not code.isdigit():
        return Response({"error": "invalid code"}, status=400)

    limit_raw = request.GET.get("limit") or "500"
    try:
        limit = max(1, min(1000, int(limit_raw)))
    except ValueError:
        return Response({"error": "invalid limit"}, status=400)

    try:
        user_id = User.objects.filter(website_link_code=int(code)).values_list("id", flat=True).first()
    except Exception as e:
        logging.exception("favorites: failed to resolve user by code using ORM")
        return Response({"error": "favorites unavailable", "detail": str(e)}, status=400)

    if not user_id:
        return Response({"error": "code not found"}, status=400)

    qs = (
        Favorite.objects
        .select_related("track")
        .filter(user_id=user_id)
        .order_by("-id")[:limit]
    )

    items: List[Dict[str, Any]] = []
    for f in qs:
        t = f.track
        items.append({
            "id": t.id,
            "title": t.title,
            "artist": t.artist,
            "youtube_url": t.youtube_url,
            "thumbnail_url": t.thumbnail_url,
            "duration": t.duration,
        })

    return Response({"items": items, "limit": limit})


@extend_schema(
    operation_id="getUserByToken",
    description="Resolve a Telegram user by website link token (numeric code). Returns basic user info.",
    parameters=[OpenApiParameter(name="token", description="Link token/code shown by the bot", required=True, type=str)],
    responses={
        200: {"type": "object", "properties": {"user": {"type": "object"}}},
        400: {"type": "object", "properties": {"error": {"type": "string"}}},
        404: {"type": "object", "properties": {"error": {"type": "string"}}},
    },
    examples=[OpenApiExample("Found", value={"user": {"id": 123456789, "username": "john", "first_name": "John", "last_name": "Doe", "website_linked": "true"}})],
)
@api_view(["GET"])
def get_user_by_token(request: HttpRequest):
    token = request.GET.get("token") or request.GET.get("code") or ""
    token = str(token).strip()
    if not token:
        return Response({"error": "token required"}, status=400)
    if not token.isdigit():
        return Response({"error": "invalid token"}, status=400)

    try:
        user = (
            User.objects
            .filter(website_link_code=int(token))
            .values("id", "username", "first_name", "last_name", "website_linked", "created_at")
            .first()
        )
    except Exception as e:
        logging.exception("get_user_by_token: failed to query user")
        return Response({"error": "lookup failed", "detail": str(e)}, status=400)

    if not user:
        return Response({"error": "user not found"}, status=404)

    return Response({"user": user})


@require_http_methods(["GET"])
def root_info(_request: HttpRequest):
    return JsonResponse({
        "message": "Music Bot API",
        "endpoints": {
            "POST /api/link": "Link session with Telegram code",
            "POST /api/send": "Send song query to Telegram",
            "POST /api/logout": "Logout linked session (also GET /api/logout for session-only)",
            "GET /api/history": "Download history",
            "GET /api/favorites": "Favorite tracks",
            "GET /api/user_by_token": "Resolve user by link token",
            "GET /api/schema/": "OpenAPI schema",
            "GET /api/docs/": "Swagger UI",
        }
    })


@extend_schema(exclude=True)
@api_view(["GET", "POST"])
def logout_alias(request):
    return logout(request)
