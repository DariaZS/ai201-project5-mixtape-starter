"""
tests/test_feed.py — Mixtape

Tests for the "Friends Listening Now" feed logic.
Issue #2: stale events (older than 24h) must NOT appear in listening-now.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app import create_app, db
from models import ListeningEvent, Song, User, friendships
from services.feed_service import get_friends_listening_now


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def feed_setup(app):
    """
    One viewer, one friend, one song. The friend has exactly one listening
    event, whose age we set per-test. Returns the ids we need.
    """
    with app.app_context():
        viewer = User(username="viewer", email="viewer@example.com")
        friend = User(username="friend", email="friend@example.com")
        db.session.add_all([viewer, friend])
        db.session.flush()

        # symmetric friendship
        db.session.execute(friendships.insert().values(user_id=viewer.id, friend_id=friend.id))
        db.session.execute(friendships.insert().values(user_id=friend.id, friend_id=viewer.id))

        song = Song(title="Test Track", artist="Tester", shared_by=friend.id)
        db.session.add(song)
        db.session.commit()

        yield {"viewer_id": viewer.id, "friend_id": friend.id, "song_id": song.id}


def _add_event(friend_id, song_id, age):
    """Add a listening event for friend, `age` (timedelta) before now."""
    listened_at = datetime.now(timezone.utc) - age
    db.session.add(ListeningEvent(user_id=friend_id, song_id=song_id, listened_at=listened_at))
    db.session.commit()


def test_recent_listen_appears(app, feed_setup):
    """A listen from 10 minutes ago should appear in listening-now."""
    with app.app_context():
        _add_event(feed_setup["friend_id"], feed_setup["song_id"], timedelta(minutes=10))
        feed = get_friends_listening_now(feed_setup["viewer_id"])
        assert len(feed) == 1


def test_stale_listen_excluded(app, feed_setup):
    """
    A listen from 25 hours ago is older than the 24h window and must NOT
    appear. This is the Issue #2 reproduction: 'shows people from yesterday'.
    """
    with app.app_context():
        _add_event(feed_setup["friend_id"], feed_setup["song_id"], timedelta(hours=25))
        feed = get_friends_listening_now(feed_setup["viewer_id"])
        assert len(feed) == 0  # stale event should be filtered out