# Mixtape Bug Hunt — Submission

## AI Usage

I used AI tools (Claude) throughout, mainly for **navigation and verification** rather than generating fixes. The honest summary:

**Where AI helped.** For orientation, I used it to trace call chains through the `services/` and `routes/` layers and to build the feed data-flow diagram in my codebase map. During debugging, I used it to compare two code paths side by side (the working `add_to_playlist` vs. the broken `rate_song` for Issue #4), to confirm Python's `datetime.weekday()` convention (Monday=0/Sunday=6) for Issue #1, and to reason about whether a fix touched other branches.

**Where I verified — and where AI was wrong.** I reproduced every bug myself before fixing, and this caught two cases where AI reasoning was confidently wrong:

- **Issue #3 (search duplicates):** an AI code review insisted the `song_tags` join would return a song once per tag, producing duplicates, and recommended removing the join. I tested this directly in `flask shell` — searching 3-tag songs and a broad match — and got **no duplicates** (13 results, 13 unique ids). The reason: SQLAlchemy's identity map collapses joined rows back to one object per primary key, so the ORM never actually returns duplicates. The "fix" would have been code for a bug that doesn't occur, and I couldn't have written a truthful reproduction for it, so I did not submit it.
- **Issue #2 (stale feed):** AI review flagged a naive-vs-aware datetime mismatch as the cause. I wrote a feed test (`tests/test_feed.py`) forcing a 25-hour-old event and it was **correctly excluded** — the 24h filter works. The datetime mismatch is real but latent under SQLite, not the reported bug. Without a reproduction, I did not submit a fix.

**Takeaway.** The AI was reliable for explaining code I'd already found and for mechanical comparisons, but unreliable at diagnosing bugs before I'd read and run the code myself — exactly the pattern the brief warns about. Reproducing first was what separated the three real bugs (#1, #4, #5) from the two false leads.

---

## Milestone 1: Codebase Map

### Main files and their roles

- `models.py` — 7 models: `User`, `Tag`, `Song`, `ListeningEvent`, `Rating`, `Playlist`, `Notification`, plus three association tables: `friendships` (symmetric many-to-many, `User`↔`User`), `song_tags` (`Song`↔`Tag`), and `playlist_entries` (`Playlist`↔`Song`, carries `position`, `added_by`, `added_at` — order is explicit, not insertion-order). `Rating` has a unique constraint on `(user_id, song_id)` — one rating per user per song. All primary keys are UUID strings, not auto-increment ints.
- `routes/songs.py` — song sharing, search, and rating routes, including `POST /songs/<song_id>/listen` (`listen()`), which pulls `user_id` from the request body and delegates to the streak service.
- `routes/feed.py` — feed endpoints: `GET /feed/<user_id>/listening-now` and `GET /feed/<user_id>/activity`.
- `routes/playlists.py` — playlist creation and song management.
- `routes/users.py` — user profiles, streaks, notifications.
- `services/streak_service.py` — `record_listening_event(user_id, song_id)` creates the `ListeningEvent` row and calls `update_listening_streak()` to bump/reset streak state; commits both together. **Issue #1** (streak resets) lives here.
- `services/feed_service.py` — `get_friends_listening_now()` (last-24h, deduped to one song per friend) and `get_activity_feed()` (most recent N events, no time filter, no dedup). **Issue #2** (stale "listening now") lives here.
- `services/search_service.py` — song search logic. **Issue #3** (duplicate search results) lives here.
- `services/notification_service.py` — notification creation/retrieval; validates song existence at `notify_song_rated()` — worth noting `record_listening_event` does *not* do the same validation (see Pattern below). **Issue #4** (missing rating notification) lives here.
- `services/playlist_service.py` — playlist retrieval logic. **Issue #5** (last playlist song missing) lives here.

### Data flow: a listen reaching a friend's feed

There is no `feed` table — feeds are computed on read from `ListeningEvent` rows. A listen recorded by one user surfaces in a friend's feed purely through the friendship relation queried at read time.

**Write path (a listen gets recorded):**
`POST /songs/<song_id>/listen` → `routes/songs.py: listen()` → `streak_service.record_listening_event(user_id, song_id)`
- loads the user (raises → 400 if not found)
- creates a `ListeningEvent(user_id, song_id, listened_at=now)`, added to the session
- calls `update_listening_streak(user, now)` to bump/reset `listening_streak` / `last_listened_at`
- `db.session.commit()` persists the event and the streak change together

**Read path (a friend's feed materializes):**
`GET /feed/<user_id>/listening-now` → `feed_service.get_friends_listening_now()` — reads friends' `ListeningEvent`s from the last 24h, deduped to the latest song per friend.
`GET /feed/<user_id>/activity` → `feed_service.get_activity_feed()` — reads friends' most recent N events, no time filter, no dedup.

```
User A listens                          User B views feed
──────────────                          ─────────────────
POST /songs/{id}/listen                 GET /feed/{B}/listening-now
   │                                        │
   ▼                                        ▼
record_listening_event(A, song)         get_friends_listening_now(B)
   ├─ new ListeningEvent(A, song) ─┐        │
   ├─ update_listening_streak(A)   │        ▼
   └─ commit                       │     query friends' events, last 24h
                                   │     dedup → one latest song per friend
                                   └────────►  A + song appear in B's feed
                          (linked only by the ListeningEvent row + B's friends list)
```

The write and read paths never call each other directly — they're joined only by the shared `ListeningEvent` row and the friendship relation.

### Patterns noticed

- **Routes delegate immediately to services.** Route functions parse the request and format the response; business logic lives in `services/`.
- **No feed table — feeds are derived on read.** This means a bug in either the write path (what gets recorded) or the read path (how it's filtered/deduped) can silently change what a user sees, with no single place to inspect "the feed" directly.
- **Inconsistent validation across services.** `notification_service.notify_song_rated()` validates the song exists before acting; `streak_service.record_listening_event()` does not — a bad `song_id` gets stored and only fails later, at read time, when `feed_service` calls `.to_dict()` on a missing song.
- **Explicit ordering, not insertion order.** `playlist_entries` stores a `position` column rather than relying on row order — any playlist read logic that doesn't sort by `position` (or mishandles the boundary of that ordering) is a likely home for Issue #5.
- **UUID string PKs everywhere.** Every model uses `default=generate_uuid` string PKs rather than auto-increment ints — worth remembering if any bug involves comparing or looking up IDs.

> Add any other patterns you spot while reading the remaining files (e.g. error-handling style, how notifications are triggered elsewhere).

### AI disclosure

Used AI assistance to trace the write/read call chain for the listening-feed feature and to generate the accompanying diagram, based on reading `routes/songs.py`, `routes/feed.py`, `services/streak_service.py`, and `services/feed_service.py`.

---

## Milestone 3: Root Cause Analyses

> One entry per bug, all 5 fields filled before moving to the next. Reproduce first — don't open the fix until you've confirmed the bug exists.

### Issue #1 — My listening streak keeps resetting
1. **Issue:** #1 — My listening streak keeps resetting (`streak_service.py`)
2. **How reproduced:** Ran `pytest tests/test_streaks.py`: `test_streak_increments_on_sunday` failed with `assert 1 == 2` (Saturday listen → 1, Sunday listen expected 2, got 1). Also confirmed live via `flask shell` on seeded user `darius` (streak 3, last listened yesterday) — a listen today reset him to 1 instead of 4.
3. **How root cause found:** Read `update_listening_streak()` in `streak_service.py`. The consecutive-day branch carried `and today.weekday() != 6`; since `weekday()` returns 6 for Sunday, this line suppresses the increment on Sundays. The test's name confirmed it.
4. **Root cause:** The increment branch read `elif days_since_last == 1 and today.weekday() != 6:`. On Sundays that condition is false, so a valid consecutive-day listen fell through to the `else` and reset the streak to 1. Every other day worked, making it look intermittent.
5. **Fix + side-effect check:** Removed the `and today.weekday() != 6` clause, leaving `elif days_since_last == 1:`. All 5 streak tests now pass, including the Sunday case; the other branches (first listen, same-day, gap > 1 day) still behave correctly. Committed as `fix: remove Sunday exclusion from streak increment logic` (`eb764f5`).

   *AI disclosure:* Used AI to confirm the `weekday()` convention and check the other branches were unaffected.

### Issues #2 and #3 — investigated, not fixed (three-bug scope)
I chose to fix three bugs (#1, #4, #5). I also investigated #2 (stale feed) and #3 (duplicate search) but could not reproduce either after genuine attempts, so — per the brief's guidance to "try a different one from the list" when a bug won't reproduce — I did not submit fixes for them. Notes on what I found are in the AI usage section at the top.

### Issue #4 — Missing notification when a friend rates my song
1. **Issue:** #4 — Notified when a friend adds my song to a playlist, but not when they rate it (`notification_service.py`)
2. **How reproduced:** In `flask shell`, counted the sharer's notifications, called `rate_song(rater_id, song_id, 5)` for a song shared by someone else, then re-counted. Count stayed at `before: 1, after: 1` — the rating saved but no notification was created.
3. **How root cause found:** Compared the two sibling functions in `notification_service.py`. `add_to_playlist` ends by calling `create_notification(...)` for the song's sharer; `rate_song` ends with `db.session.commit(); return rating` and never calls `create_notification` at all. The missing call — not a typo, but an absent step — was the cause.
4. **Root cause:** `rate_song` was never wired to notify. The playlist path notifies the sharer; the rating path only persists the `Rating` and returns. So rating another user's song produced no `song_rated` notification, while the playlist path worked.
5. **Fix + side-effect check:** Added a `create_notification(...)` call at the end of `rate_song`, mirroring `add_to_playlist` — including the self-check `if song.shared_by != user_id` so a user isn't notified for rating their own song. Re-ran the reproduction: count now goes `before: 1, after: 2`. Rating logic itself (score validation, update-existing-rating) is unchanged, so no other behavior was affected.

   *AI disclosure:* Used AI to compare the working (`add_to_playlist`) and broken (`rate_song`) paths side by side and confirm the notification step was missing rather than misplaced.

### Issue #5 — The last song in a playlist never shows up
1. **Issue:** #5 — The last song in a playlist never shows up (`playlist_service.py`)
2. **How reproduced:** Ran `pytest tests/test_playlists.py`: `test_playlist_returns_all_songs` failed with `assert 4 == 5` (5 songs seeded, only 4 returned), and `test_playlist_returns_songs_in_order` failed missing the final `Track 5` — two symptoms of the same bug.
3. **How root cause found:** Read `get_playlist_songs()` in `playlist_service.py`. The query itself is correct (joins `playlist_entries`, orders by `position`, `.all()`), so the loss had to be after the query. The return line sliced the results with `songs[:-1]`. The docstring says the function "returns all songs," so code and stated intent disagreed — confirming the slice was the cause.
4. **Root cause:** The return statement was `[song.to_dict() for song in songs[:-1]]`. The `[:-1]` slice drops the last element, so the highest-`position` song was always excluded before returning.
5. **Fix + side-effect check:** Changed the slice to the full list: `[song.to_dict() for song in songs]`. Both playlist tests now pass, confirming all songs return in correct position order. Committed as `fix: return all playlist songs instead of dropping the last`.

   *AI disclosure:* Used AI to confirm the query was correct so the search could focus on the return line.

---

## Commit History

One commit per fix on the `bugfix/mixtape` branch, plus a regression-test commit:

![git log --oneline showing one commit per fix](screenshots/git-log.png)