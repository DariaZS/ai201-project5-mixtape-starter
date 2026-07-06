"""
tests/test_notifications.py — Mixtape

Regression tests for Issue #4: rating a song should notify its sharer,
the same way adding it to a playlist does.
"""

import pytest

from app import create_app, db
from models import Notification, Song, User
from services.notification_service import rate_song


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def sharer_and_song(app):
    """A user who shares a song, and the song itself."""
    with app.app_context():
        sharer = User(username="sharer", email="sharer@example.com")
        db.session.add(sharer)
        db.session.flush()

        song = Song(title="Test Track", artist="Tester", shared_by=sharer.id)
        db.session.add(song)
        db.session.commit()

        yield {"sharer_id": sharer.id, "song_id": song.id}


def test_rating_notifies_the_sharer(app, sharer_and_song):
    """
    Rating someone else's song should create a notification for the sharer.
    This is the core Issue #4 fix.
    """
    with app.app_context():
        rater = User(username="rater", email="rater@example.com")
        db.session.add(rater)
        db.session.commit()

        before = Notification.query.filter_by(user_id=sharer_and_song["sharer_id"]).count()
        rate_song(rater.id, sharer_and_song["song_id"], 5)
        after = Notification.query.filter_by(user_id=sharer_and_song["sharer_id"]).count()

        assert after == before + 1


def test_rating_own_song_does_not_notify(app, sharer_and_song):
    """
    Side-effect check: a user rating their OWN song should NOT get a
    notification about themselves — mirrors the same guard used in
    add_to_playlist.
    """
    with app.app_context():
        before = Notification.query.filter_by(user_id=sharer_and_song["sharer_id"]).count()
        rate_song(sharer_and_song["sharer_id"], sharer_and_song["song_id"], 5)
        after = Notification.query.filter_by(user_id=sharer_and_song["sharer_id"]).count()

        assert after == before  # no self-notification