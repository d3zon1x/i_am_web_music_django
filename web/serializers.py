# web/serializers.py
from __future__ import annotations

from rest_framework import serializers


class LinkRequestSerializer(serializers.Serializer):
    code = serializers.CharField(required=True, allow_blank=False)


class SendSongRequestSerializer(serializers.Serializer):
    query = serializers.CharField(required=True, allow_blank=False)
    code = serializers.CharField(required=False, allow_blank=True)
