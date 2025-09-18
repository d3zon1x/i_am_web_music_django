from __future__ import annotations

import json
import logging
import requests
from typing import Dict, Any

from django.conf import settings
from django.shortcuts import render
from django.views.decorators.http import require_http_methods


def _headers() -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    api_key = getattr(settings, 'BOT_HTTP_API_KEY', '')
    if api_key:
        headers['X-Api-Key'] = api_key
    return headers


def _api_post(path: str, payload: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
    base = getattr(settings, 'BOT_HTTP_API_BASE', 'http://127.0.0.1:5001').rstrip('/')
    url = f"{base}{path}"
    try:
        r = requests.post(url, headers=_headers(), data=json.dumps(payload), timeout=10)
        data = {}
        try:
            data = r.json()
        except Exception:
            data = {"error": r.text}
        return r.status_code, data
    except Exception as e:
        logging.exception("Failed calling %s", url)
        return 500, {"error": str(e)}


@require_http_methods(["GET", "POST"])
def home(request):
    ctx: Dict[str, Any] = {
        "linked": request.session.get("linked", False),
        "linked_code": request.session.get("linked_code", ""),
        "message": None,
        "error": None,
    }

    if request.method == "POST":
        # Distinguish forms via a hidden input 'form'
        form_kind = request.POST.get("form")
        if form_kind == "link":
            code = (request.POST.get("link_code") or "").strip()
            if not code:
                ctx["error"] = "Enter the 8-digit code"
            else:
                status, data = _api_post("/api/link_by_code", {"code": code})
                if status == 200:
                    request.session["linked"] = True
                    request.session["linked_code"] = code
                    ctx["linked"] = True
                    ctx["linked_code"] = code
                    ctx["message"] = "Linked successfully"
                else:
                    ctx["error"] = data.get("error", f"Failed: HTTP {status}")
        elif form_kind == "send":
            code = (request.POST.get("code") or request.session.get("linked_code") or "").strip()
            query = (request.POST.get("query") or "").strip()
            if not code or not query:
                ctx["error"] = "Provide code and song name/URL"
            else:
                status, data = _api_post("/api/send_song_by_code", {"code": code, "query": query})
                if status == 200:
                    ctx["message"] = "Scheduled. Check your Telegram."
                    # remember code for convenience
                    request.session["linked_code"] = code
                    ctx["linked_code"] = code
                else:
                    ctx["error"] = data.get("error", f"Failed: HTTP {status}")
        else:
            ctx["error"] = "Unknown action"

    return render(request, "index.html", ctx)

