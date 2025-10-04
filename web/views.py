# web/views.py
from __future__ import annotations

import logging
from typing import Dict, Any, List

from django.utils import timezone
from django.http import JsonResponse, HttpRequest
from django.views.decorators.http import require_http_methods
from drf_spectacular.utils import extend_schema, OpenApiExample, OpenApiParameter, extend_schema_view
from rest_framework.decorators import api_view
from rest_framework.response import Response

from django.db.models import Count, Min, Max
from .models import History, User, Favorite

from .services.bot_client import BotHttpClient
from .serializers import LinkRequestSerializer, SendSongRequestSerializer
from .utils import (
    set_linked_session,
    clear_linked_session,
    get_code_from_request_or_session,
    normalize_code,
    parse_limited_int,
)

_client = BotHttpClient()


@extend_schema(
    operation_id="linkAccount",
    description="Link a browser session with a Telegram bot user using a short numeric code the bot shows to the user.",
    request={'application/json': {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    responses={200: {"type": "object", "properties": {"status": {"type": "string"}, "code": {"type": "string"}}}, 400: {"type": "object", "properties": {"error": {"type": "string"}}}},
    examples=[OpenApiExample("Successful link", value={"code": "12345678"})]
)
@api_view(["POST"])
def link(request: HttpRequest):
    ser = LinkRequestSerializer(data=request.data)
    if not ser.is_valid():
        return Response({"error": "code required"}, status=400)
    code = normalize_code(ser.validated_data["code"])
    if not code:
        return Response({"error": "invalid code"}, status=400)

    status_code, data = _client.link_by_code(code)
    if status_code == 200:
        set_linked_session(request, code)
        return Response({"status": "linked", "code": code})
    if status_code == 401:
        return Response({"error": "bot unauthorized (check API key)"}, status=502)
    return Response({"error": data.get("error", f"link failed ({status_code})")}, status=400)


@extend_schema(
    operation_id="sendSong",
    description="Schedule a song download to be delivered in Telegram. Accepts either a search query or a direct URL.",
    request={'application/json': {"type": "object", "properties": {"query": {"type": "string"}, "code": {"type": "string"}}, "required": ["query"]}},
    responses={200: {"type": "object", "properties": {"status": {"type": "string"}}}, 400: {"type": "object", "properties": {"error": {"type": "string"}}}},
    examples=[OpenApiExample("Send by session code", value={"query": "Daft Punk Get Lucky"})]
)
@api_view(["POST"])
def send_song(request: HttpRequest):
    ser = SendSongRequestSerializer(data=request.data)
    if not ser.is_valid():
        return Response({"error": "query required"}, status=400)
    query: str = str(ser.validated_data["query"]).strip()
    code_input = ser.validated_data.get("code") or request.session.get("linked_code") or ""
    code = normalize_code(code_input)
    if not query:
        return Response({"error": "query required"}, status=400)
    if not code:
        return Response({"error": "no linked code (link first)"}, status=400)

    status_code, data = _client.send_song_by_code(code, query)
    if status_code == 200:
        # refresh session code (idempotent)
        set_linked_session(request, code)
        return Response({"status": "scheduled"})
    if status_code == 401:
        return Response({"error": "bot unauthorized (check API key)"}, status=502)
    return Response({"error": data.get("error", f"send failed ({status_code})")}, status=400)


@extend_schema(
    operation_id="downloadHistory",
    parameters=[OpenApiParameter(name="code", description="Optional code; if omitted uses linked session code", required=False, type=str)],
    responses={200: {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object"}}}}, 400: {"type": "object", "properties": {"error": {"type": "string"}}}},
    description="Return recent download history for the linked Telegram user from local DB (no bot HTTP call).")
@api_view(["GET"])
def history(request: HttpRequest):
    code = normalize_code(request.GET.get("code") or request.session.get("linked_code") or "")
    if not code:
        return Response({"error": "no linked code"}, status=400)

    try:
        user_id = User.objects.filter(website_link_code=int(code)).values_list("id", flat=True).first()
    except Exception as e:
        logging.exception("history: ORM lookup failed")
        return Response({"error": "history unavailable", "detail": str(e)}, status=400)

    if not user_id:
        return Response({"error": "code not found"}, status=400)

    qs = (
        History.objects
        .select_related("track")
        .filter(user_id=user_id)
        .order_by("-downloaded_at")[:200]
    )

    items: List[Dict[str, Any]] = [{
        "id": h.track.id,
        "title": h.track.title,
        "artist": h.track.artist,
        "youtube_url": h.track.youtube_url,
        "thumbnail_url": h.track.thumbnail_url,
        "duration": h.track.duration,
        "downloaded_at": h.downloaded_at,
    } for h in qs]

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
def logout(request: HttpRequest):
    code_raw = get_code_from_request_or_session(request)
    code = normalize_code(code_raw)
    if not code:
        clear_linked_session(request)
        return Response({"status": "not_linked"}, status=200)

    status_code, data = _client.logout_by_code(code)
    logging.info("WEB logout: code=%s bot_status=%s bot_payload=%s", code, status_code, data)

    if status_code == 200:
        clear_linked_session(request)
        return Response({"status": "logged_out"})
    if data.get("error") in {"code not found", "user not found", "invalid code", "user_id or code required"}:
        clear_linked_session(request)
        return Response({"status": "not_linked"}, status=200)
    if status_code == 401:
        return Response({"error": "bot unauthorized (check API key)"}, status=502)
    if 500 <= status_code < 600:
        return Response({"error": "bot service error", "detail": data.get("error")}, status=502)
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
    limit = parse_limited_int(request.GET.get("limit") or "20", default=20, min_v=1, max_v=100)
    if limit is None:
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
    code = normalize_code(request.GET.get("code") or request.session.get("linked_code") or "")
    if not code:
        return Response({"error": "no linked code"}, status=400)

    limit = parse_limited_int(request.GET.get("limit") or "500", default=500, min_v=1, max_v=1000)
    if limit is None:
        return Response({"error": "invalid limit"}, status=400)

    try:
        user_id = User.objects.filter(website_link_code=int(code)).values_list("id", flat=True).first()
    except Exception as e:
        logging.exception("favorites: ORM lookup failed")
        return Response({"error": "favorites unavailable", "detail": str(e)}, status=400)

    if not user_id:
        return Response({"error": "code not found"}, status=400)

    qs = (
        Favorite.objects
        .select_related("track")
        .filter(user_id=user_id)
        .order_by("-id")[:limit]
    )

    items: List[Dict[str, Any]] = [{
        "id": f.track.id,
        "title": f.track.title,
        "artist": f.track.artist,
        "youtube_url": f.track.youtube_url,
        "thumbnail_url": f.track.thumbnail_url,
        "duration": f.track.duration,
    } for f in qs]

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
    token = (request.GET.get("token") or request.GET.get("code") or "").strip()
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
        logging.exception("get_user_by_token: ORM lookup failed")
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
