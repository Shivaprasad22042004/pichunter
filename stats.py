"""
Privacy-preserving usage counters, durable across restarts.

Storage is a Redis-compatible database reached over its REST API (Upstash).
Only the standard library is used, so no extra dependency is shipped.

Why not a file?  The free hosting tier has an ephemeral filesystem: the file is
wiped on every restart, and the free service restarts whenever it sleeps. A
file is kept as a last-resort fallback so the site still works without a
database configured, but it does not survive a restart.

What is recorded
  * one counter bump per search: the day, and a daily-salted hash of the
    caller's IP.

What is NOT recorded
  * the IP address itself (not even a prefix)
  * the uploaded image, the face vector, or anything derived from it
  * user agents, referrers, or any cross-day identifier

The salt changes every day and is never stored next to the data, so the same
person on two days produces two unrelated hashes.
"""
import os
import json
import time
import hashlib
import threading
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATS_DIR = Path(os.environ.get('STATS_DIR', HERE / 'data' / 'stats'))
STATS_DIR.mkdir(parents=True, exist_ok=True)

EXCLUDE_IPS = {s.strip() for s in os.environ.get('EXCLUDE_IPS', '').split(',') if s.strip()}

# Upstash REST credentials, injected by the Vercel/Render integration.
REDIS_URL = os.environ.get('UPSTASH_REDIS_REST_URL', '').rstrip('/')
REDIS_TOKEN = os.environ.get('UPSTASH_REDIS_REST_TOKEN', '')

_lock = threading.Lock()


def _today():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')


def _salt_for(day):
    """Deterministic per-day salt derived from a secret, never stored."""
    secret = os.environ.get('STATS_SECRET', '').encode()
    return hashlib.sha256(secret + day.encode()).hexdigest()[:32]


def visitor_hash(ip, day=None):
    day = day or _today()
    return hashlib.sha256((_salt_for(day) + '|' + (ip or '')).encode()).hexdigest()[:16]


# ----------------------------------------------------------------- redis REST

def _redis(*command):
    """Run one Redis command via the Upstash REST API. Returns parsed result."""
    if not (REDIS_URL and REDIS_TOKEN):
        return None
    body = json.dumps([str(c) for c in command]).encode()
    req = urllib.request.Request(
        REDIS_URL, data=body, method='POST',
        headers={'Authorization': f'Bearer {REDIS_TOKEN}',
                 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            payload = json.loads(r.read().decode())
        return payload.get('result')
    except Exception:
        return None


def _redis_ok():
    """True when a database is configured and answering."""
    if not (REDIS_URL and REDIS_TOKEN):
        return False
    return _redis('PING') is not None


class Stats:
    """
    Counters per day, in Redis sets/counters, falling back to a local file.

      stats:day:<YYYY-MM-DD>:people    SET of visitor hashes  (SCARD = people)
      stats:day:<YYYY-MM-DD>:searches  INCR counter
      stats:day:<YYYY-MM-DD>:matched   SET of visitor hashes that found photos
      stats:days                       SET of active days (for listing)
    """

    def __init__(self):
        self.file = STATS_DIR / 'usage.jsonl'
        self._cache = None
        self._cache_mtime = 0

    # ---- writing -------------------------------------------------------
    def record(self, ip, found_matches, n_matches=0):
        if ip in EXCLUDE_IPS:
            return
        day = _today()
        v = visitor_hash(ip, day)

        if REDIS_URL and REDIS_TOKEN:
            base = f'stats:day:{day}'
            _redis('SADD', f'{base}:people', v)
            _redis('INCR', f'{base}:searches')
            if found_matches:
                _redis('SADD', f'{base}:matched', v)
            _redis('SADD', 'stats:days', day)
            return

        entry = {'t': int(time.time()), 'd': day, 'v': v, 'hit': bool(found_matches)}
        if os.environ.get('STATS_STORE_MATCH_COUNT') == '1':
            entry['n'] = int(n_matches)
        with _lock:
            try:
                with open(self.file, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(entry, separators=(',', ':')) + '\n')
            except OSError:
                pass  # a counter must never break a search

    # ---- reading -------------------------------------------------------
    def summary(self):
        if REDIS_URL and REDIS_TOKEN:
            out = self._summary_redis()
            if out is not None:
                return out
            # Database configured but unreachable (bad token, outage, quota).
            # Say so rather than silently reporting zeroes as if they were real.
            fb = self._summary_file()
            fb['storage'] = 'database unreachable - showing local fallback'
            fb['warning'] = 'Could not read the usage database. These numbers may be incomplete.'
            return fb
        return self._summary_file()

    def _summary_redis(self):
        days = _redis('SMEMBERS', 'stats:days')
        if days is None:
            return None
        per_day, total_searches, total_found = [], 0, 0
        for d in sorted(days):
            base = f'stats:day:{d}'
            people = _redis('SCARD', f'{base}:people') or 0
            searches = _redis('GET', f'{base}:searches') or 0
            matched = _redis('SCARD', f'{base}:matched') or 0
            searches, matched = int(searches), int(matched)
            total_searches += searches
            total_found += matched
            per_day.append({'date': d, 'people': people, 'searches': searches,
                            'searches_with_matches': matched,
                            'people_who_matched': matched})
        return self._shape(per_day, total_searches, total_found)

    def _summary_file(self):
        rows = self._load()
        days = {}
        for r in rows:
            d = r.get('d', '?')
            s = days.setdefault(d, {'people': set(), 'searches': 0, 'found_people': set()})
            s['people'].add(r.get('v'))
            s['searches'] += 1
            if r.get('hit'):
                s['found_people'].add(r.get('v'))
        per_day = []
        for d in sorted(days):
            s = days[d]
            per_day.append({'date': d, 'people': len(s['people']), 'searches': s['searches'],
                            'searches_with_matches': len(s['found_people']),
                            'people_who_matched': len(s['found_people'])})
        return self._shape(per_day, len(rows), sum(x['people_who_matched'] for x in per_day))

    @staticmethod
    def _shape(per_day, total_searches, total_found):
        return {
            'total_searches': total_searches,
            'total_unique_days_visited': sum(x['people'] for x in per_day),
            'searches_with_matches': total_found,
            'days': per_day,
            'storage': 'database' if (REDIS_URL and REDIS_TOKEN) else 'local file (resets on restart)',
            'note': 'Unique counts are per day. The same person on two days counts once each '
                    'day, because no cross-day identifier is kept.',
        }

    def _load(self):
        if self._cache is not None:
            try:
                if self.file.stat().st_mtime == self._cache_mtime:
                    return self._cache
            except OSError:
                pass
        rows = []
        try:
            with open(self.file, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            rows.append(json.loads(line))
                        except ValueError:
                            pass
        except OSError:
            rows = []
        self._cache = rows
        try:
            self._cache_mtime = self.file.stat().st_mtime
        except OSError:
            self._cache_mtime = 0
        return rows

    # ---- migration -----------------------------------------------------
    def import_rows(self, rows):
        """Merge previously exported rows (from STATS_SEED or an old file)."""
        if not rows:
            return 0
        if REDIS_URL and REDIS_TOKEN:
            added = 0
            for r in rows:
                d, v = r.get('d'), r.get('v')
                if not d or not v:
                    continue
                _redis('SADD', f'stats:day:{d}:people', v)
                _redis('INCR', f'stats:day:{d}:searches')
                if r.get('hit'):
                    _redis('SADD', f'stats:day:{d}:matched', v)
                _redis('SADD', 'stats:days', d)
                added += 1
            return added
        have = {(r.get('t'), r.get('v')) for r in self._load()}
        added = 0
        with _lock:
            try:
                with open(self.file, 'a', encoding='utf-8') as f:
                    for r in rows:
                        if (r.get('t'), r.get('v')) in have:
                            continue
                        f.write(json.dumps(r, separators=(',', ':')) + '\n')
                        added += 1
            except OSError:
                return 0
        self._cache = None
        return added

    def export(self):
        return self._load()


stats = Stats()


def bootstrap_from_env():
    """Optionally seed counters from STATS_SEED (a JSON array)."""
    raw = os.environ.get('STATS_SEED')
    if not raw:
        return 0
    try:
        rows = json.loads(raw)
        if isinstance(rows, list):
            return stats.import_rows(rows)
    except ValueError:
        pass
    return 0
