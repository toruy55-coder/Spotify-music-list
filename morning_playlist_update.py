#!/usr/bin/env python3
"""Update a "morning" playlist with fresh minor/quiet pop tracks daily.

Designed to run from cron/launchd; uses Spotipy and env vars for credentials.

Behaviour:
* load Spotify credentials from .env
* authenticate via Authorization Code Flow (first run opens browser)
* locate or create a private playlist by name
* search a few included genres and filter by popularity/audio features
* avoid tracks used in the last N days (history.json by default)
* randomize/limit to configured number of tracks
* replace playlist contents with the new selection
* log progress

Scopes required:
    playlist-read-private playlist-modify-private

"""

import argparse
import json
import logging
import os
import random
import sys
import time
from datetime import date, datetime, timedelta

import urllib.error
import urllib.request

import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

# ---- constants --------------------------------------------------------------
DEFAULT_PLAYLIST_NAME = "MTB Daily Morning"
DEFAULT_HISTORY_FILE = "history.json"
DEFAULT_DAYS_HISTORY = 3
DEFAULT_MIN_TRACKS = 20
DEFAULT_MAX_TRACKS = 40
GENRES = ["indie pop", "chill pop", "dream pop"]
MARKET = "JP"
MAX_POPULARITY = 60  # prefer less popular
MAX_TEMPO = 120
MAX_ENERGY = 0.6
LOG_FILE = "morning_playlist_update.log"
SEARCH_LIMIT = 10
SEARCH_OFFSETS = (0, 10, 20)

# ---- utility routines -------------------------------------------------------

def setup_logging():
    logger = logging.getLogger("morning")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    fh = logging.FileHandler(LOG_FILE)
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger


def load_history(path):
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return {}


def save_history(path, history):
    with open(path, "w") as f:
        json.dump(history, f, indent=2)


def prune_history(history, days):
    cutoff = date.today() - timedelta(days=days)
    new = {}
    for d, tracks in history.items():
        try:
            dt = datetime.strptime(d, "%Y-%m-%d").date()
        except ValueError:
            continue
        if dt >= cutoff:
            new[d] = tracks
    return new


def get_recent_ids(history):
    ids = []
    for tracks in history.values():
        ids.extend(tracks)
    return set(ids)


def avoid_consecutive_artists(tracks):
    # tracks: list of track dicts
    if not tracks:
        return []
    result = []
    pool = tracks.copy()
    random.shuffle(pool)
    while pool:
        for idx, t in enumerate(pool):
            if not result or t["artists"][0]["id"] != result[-1]["artists"][0]["id"]:
                result.append(t)
                pool.pop(idx)
                break
        else:
            # can't satisfy constraint, just append the first
            result.append(pool.pop(0))
    return result


def spotify_retry(func, *args, **kwargs):
    """Wrapper to retry on 429/timeout. 401 should be handled by OAuth manager."""
    for attempt in range(5):
        try:
            return func(*args, **kwargs)
        except spotipy.SpotifyException as e:
            status = e.http_status
            if status == 429:
                retry = int(e.headers.get("Retry-After", "5"))
                logger.warning("Rate limited, sleeping for %s seconds", retry)
                time.sleep(retry + 1)
                continue
            elif status == 401:
                logger.info("Received 401, refreshing token and retrying")
                # Spotipy's auth manager should refresh automatically on next call
                time.sleep(1)
                continue
            else:
                raise
        except Exception as e:
            # network/timeouts
            logger.warning("spotify request error %s, attempt %d", e, attempt + 1)
            time.sleep(2)
            continue
    raise RuntimeError("spotify request failed after retries")


def spotify_api_request(auth_manager, method, path, json_body=None):
    """Call Spotify Web API directly with bearer token + retry for 401/429/5xx."""
    url = f"https://api.spotify.com{path}"
    for attempt in range(5):
        token = auth_manager.get_access_token(as_dict=False)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        body = None if json_body is None else json.dumps(json_body).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                status_code = resp.getcode()
                resp_headers = resp.headers
                resp_text = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            status_code = e.code
            resp_headers = e.headers
            resp_text = e.read().decode("utf-8")
        except urllib.error.URLError as e:
            logger.warning("spotify api request error %s, attempt %d", e, attempt + 1)
            time.sleep(2)
            continue

        if status_code == 429:
            retry = int(resp_headers.get("Retry-After", "5"))
            logger.warning("Rate limited, sleeping for %s seconds", retry)
            time.sleep(retry + 1)
            continue
        if status_code == 401:
            logger.info("Received 401, refreshing token and retrying")
            # get_access_token will refresh on next iteration as needed
            time.sleep(1)
            continue
        if status_code >= 500:
            logger.warning("spotify server error %s, attempt %d", status_code, attempt + 1)
            time.sleep(2)
            continue
        if status_code >= 400:
            raise RuntimeError(f"Spotify API {method} {path} failed: {status_code} {resp_text}")

        if status_code == 204 or not resp_text:
            return None
        return json.loads(resp_text)

    raise RuntimeError(f"Spotify API {method} {path} failed after retries")

# ---- playlist & search logic ------------------------------------------------

def ensure_playlist(sp, auth_manager, name):
    # search user's playlists
    limit = 50
    offset = 0
    while True:
        res = spotify_retry(sp.current_user_playlists, limit=limit, offset=offset)
        for p in res["items"]:
            if p.get("name") == name:
                logger.info("Found existing playlist %s (%s)", name, p["id"])
                return p["id"]
        if res["next"]:
            offset += limit
            continue
        break
    logger.info("Creating new playlist %s", name)
    p = spotify_api_request(
        auth_manager,
        "POST",
        "/v1/me/playlists",
        json_body={
            "name": name,
            "public": False,
            "description": "Automated morning picks",
        },
    )
    return p["id"]


def gather_candidates(sp):
    tracks = []
    seen = set()
    for genre in GENRES:
        q = f'genre:"{genre}"'
        for offset in SEARCH_OFFSETS:
            res = spotify_retry(
                sp.search,
                q=q,
                type="track",
                market=MARKET,
                limit=SEARCH_LIMIT,
                offset=offset,
            )
            for item in res.get("tracks", {}).get("items", []):
                tid = item["id"]
                if tid in seen:
                    continue
                seen.add(tid)
                tracks.append(item)
    return tracks


def filter_candidates(sp, auth_manager, candidates):
    # remove too popular and enforce audio features
    ids = [t["id"] for t in candidates]
    features = []
    audio_features_forbidden = False

    for i in range(0, len(ids), 50):
        batch = ids[i : i + 50]
        try:
            res = spotify_api_request(
                auth_manager,
                "GET",
                f"/v1/audio-features?ids={','.join(batch)}",
            )
            features.extend(res.get("audio_features", []))
        except RuntimeError as e:
            if " 403 " in str(e):
                audio_features_forbidden = True
                logger.warning(
                    "audio-features endpoint returned 403 in this Spotify Dev Mode app; "
                    "falling back to popularity-only filtering"
                )
                break
            raise

    if audio_features_forbidden:
        return [t for t in candidates if t.get("popularity", 0) <= MAX_POPULARITY]

    filtered = []
    for track, feat in zip(candidates, features):
        if feat is None:
            continue
        if track.get("popularity", 0) > MAX_POPULARITY:
            continue
        if feat.get("tempo", 0) > MAX_TEMPO:
            continue
        if feat.get("energy", 1) > MAX_ENERGY:
            continue
        filtered.append(track)
    return filtered

# ---- main ------------------------------------------------------------------

def main():
    global logger
    logger = setup_logging()

    parser = argparse.ArgumentParser(description="Update morning playlist on Spotify")
    parser.add_argument("--playlist-name", default=os.getenv("PLAYLIST_NAME", DEFAULT_PLAYLIST_NAME))
    parser.add_argument("--history-file", default=os.getenv("HISTORY_FILE", DEFAULT_HISTORY_FILE))
    parser.add_argument("--days-history", type=int, default=int(os.getenv("DAYS_HISTORY", DEFAULT_DAYS_HISTORY)))
    parser.add_argument("--min-tracks", type=int, default=int(os.getenv("MIN_TRACKS", DEFAULT_MIN_TRACKS)))
    parser.add_argument("--max-tracks", type=int, default=int(os.getenv("MAX_TRACKS", DEFAULT_MAX_TRACKS)))
    args = parser.parse_args()

    load_dotenv()
    client_id = os.getenv("CLIENT_ID")
    client_secret = os.getenv("CLIENT_SECRET")
    redirect_uri = os.getenv("REDIRECT_URI")
    if not all([client_id, client_secret, redirect_uri]):
        logger.error("CLIENT_ID, CLIENT_SECRET and REDIRECT_URI must be set in environment")
        sys.exit(1)

    auth = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope="playlist-read-private playlist-modify-private",
        cache_path=".cache",
    )
    sp = spotipy.Spotify(auth_manager=auth)

    try:
        spotify_retry(sp.current_user)
    except Exception as e:
        logger.error("unable to get current user: %s", e)
        sys.exit(1)

    playlist_id = ensure_playlist(sp, auth, args.playlist_name)

    candidates = gather_candidates(sp)
    logger.info("found %d raw candidate tracks", len(candidates))
    candidates = filter_candidates(sp, auth, candidates)
    logger.info("after audio/popularity filtering: %d", len(candidates))

    # history
    history = load_history(args.history_file)
    history = prune_history(history, args.days_history)
    used = get_recent_ids(history)
    candidates = [t for t in candidates if t["id"] not in used]
    logger.info("after excluding recent %d tracks: %d remaining", len(used), len(candidates))

    if not candidates:
        logger.error("no candidates left to choose from")
        sys.exit(2)

    count = random.randint(args.min_tracks, args.max_tracks)
    if count > len(candidates):
        count = len(candidates)
    selected = random.sample(candidates, count)
    selected = avoid_consecutive_artists(selected)

    uris = [t["uri"] for t in selected]
    logger.info("updating playlist with %d tracks", len(uris))
    try:
        spotify_api_request(
            auth,
            "PUT",
            f"/v1/playlists/{playlist_id}/items",
            json_body={"uris": uris},
        )
    except Exception as e:
        logger.error("failed to update playlist: %s", e)
        sys.exit(3)

    today = date.today().strftime("%Y-%m-%d")
    history[today] = [t["id"] for t in selected]
    save_history(args.history_file, history)

    logger.info("done")


if __name__ == "__main__":
    main()
