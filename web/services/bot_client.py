# web/services/bot_client.py
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional, Tuple

import requests
from django.conf import settings


class BotHttpClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 10.0,
        session: Optional[requests.Session] = None,
    ):
        self.base_url = (base_url or getattr(settings, "BOT_HTTP_API_BASE", "http://127.0.0.1:5001")).rstrip("/")
        self.api_key = api_key or getattr(settings, "BOT_HTTP_API_KEY", None) or getattr(settings, "FLASK_API_KEY", None) or os.getenv("BOT_HTTP_API_KEY") or os.getenv("FLASK_API_KEY")
        self.timeout = timeout
        self._session = session or requests.Session()

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["X-Api-Key"] = self.api_key
        return h

    def _post(self, path: str, payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        url = f"{self.base_url}{path}"
        try:
            r = self._session.post(url, headers=self._headers(), data=json.dumps(payload), timeout=self.timeout)
            try:
                data = r.json()
            except Exception:
                data = {"error": r.text}
            return r.status_code, data
        except Exception as e:
            logging.exception("BotHttpClient POST failed: %s", url)
            return 500, {"error": str(e)}

    def _get(self, path: str, params: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        url = f"{self.base_url}{path}"
        try:
            r = self._session.get(url, headers=self._headers(), params=params, timeout=self.timeout)
            try:
                data = r.json()
            except Exception:
                data = {"error": r.text}
            return r.status_code, data
        except Exception as e:
            logging.exception("BotHttpClient GET failed: %s", url)
            return 500, {"error": str(e)}

    # High-level domain calls
    def link_by_code(self, code: str) -> Tuple[int, Dict[str, Any]]:
        return self._post("/api/link_by_code", {"code": code})

    def send_song_by_code(self, code: str, query: str) -> Tuple[int, Dict[str, Any]]:
        return self._post("/api/send_song_by_code", {"code": code, "query": query})

    def logout_by_code(self, code: str) -> Tuple[int, Dict[str, Any]]:
        return self._post("/api/logout", {"code": code, "source": "web"})
