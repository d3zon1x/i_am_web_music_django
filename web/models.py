from __future__ import annotations
from django.db import models

class Track(models.Model):
    id = models.AutoField(primary_key=True)
    title = models.CharField(max_length=512)
    artist = models.CharField(max_length=512, null=True, blank=True)
    youtube_url = models.CharField(max_length=512, unique=True)
    thumbnail_url = models.CharField(max_length=512, null=True, blank=True)
    duration = models.IntegerField(default=0)
    created_at = models.DateTimeField()

    class Meta:
        db_table = 'tracks'
        managed = False  # table is created/managed by the bot (SQLAlchemy/alembic)

class History(models.Model):
    id = models.AutoField(primary_key=True)
    user_id = models.BigIntegerField()  # references users.id in bot DB
    track = models.ForeignKey(Track, on_delete=models.DO_NOTHING, db_column='track_id', related_name='histories')
    downloaded_at = models.DateTimeField()

    class Meta:
        db_table = 'history'
        managed = False
        indexes = [
            models.Index(fields=["user_id"], name="ix_history_user_django"),
            models.Index(fields=["track"], name="ix_history_track_django"),
        ]

class User(models.Model):
    id = models.BigIntegerField(primary_key=True)
    username = models.CharField(max_length=255, null=True, blank=True)
    first_name = models.CharField(max_length=255, null=True, blank=True)
    last_name = models.CharField(max_length=255, null=True, blank=True)
    website_linked = models.BooleanField(default=False)
    website_link_code = models.BigIntegerField(unique=True)
    user_state = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = 'users'
        managed = False
        indexes = [
            models.Index(fields=["created_at"], name="ix_users_created_at_django"),
        ]

class Favorite(models.Model):
    id = models.AutoField(primary_key=True)
    user_id = models.BigIntegerField()
    track = models.ForeignKey(Track, on_delete=models.DO_NOTHING, db_column='track_id', related_name='favorites')

    class Meta:
        db_table = 'favorites'
        managed = False
        indexes = [
            models.Index(fields=["user_id"], name="ix_favorites_user_django"),
            models.Index(fields=["track"], name="ix_favorites_track_django"),
        ]
