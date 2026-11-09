"""
Lightweight, privacy-preserving usage counters.

What it records
  * one line per search: timestamp, a daily-salted hash of the caller's IP,
    and whether the search produced any matches.

What it deliberately does NOT record
  * the IP address itself, or any prefix of it
  * the uploaded image, the face vector, or anything derived from it
  * user agents, referrers, or any cross-day identifier

The salt rotates every day and is thrown away, so hashes from different days
cannot be linked. A hash is only ever used to answer "how many distinct people
searched today", never to identify anyone.

Storage
  Render's free tier has no persistent disk, so counters live in JSON files
  under data/stats/. Render does keep the build-time data/ directory in the
  image, so the directory exists, but writes are lost on redeploy/restart.
  To survive that, Stats.export() returns everything needed to rebuild, and the
  container re-imports it from an optional STATS_SEED env var on boot.
"""
import os
import json
import time
import hashlib
import threading
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATS_DIR = Path(os.environ.get('STATS_DIR', HERE / 'data' / 'stats'))
STATS_DIR.mkdir(parents=True, exist_ok=True)

# Both you and the owner of this machine should never be counted.
EXCLUDE_IPS = {
    s.strip() for s in os.environ.get('EXCLUDE_IPS', '').split(',') if s.strip()
}

_lock = threading.Lock()


def _today():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')


def _salt_for(day):
    """Deterministic per-day salt. Regenerated from the date, never stored
    alongside the hashes, so a leaked stats file cannot be brute-forced
    against a known IP without also guessing the day and the secret."""
    secret = os.environ.get('STATS_SECRET', '').encode()
    return hashlib.sha256(secret + day.encode()).hexdigest()[:32]


def visitor_hash(ip, day=None):
    day = day or _today()
    return hashlib.sha256((_salt_for(day) + '|' + (ip or '')).encode()).hexdigest()[:16]


class Stats:
    def __init__(self):
        self.file = STATS_DIR / 'usage.jsonl'
        self._cache = None
        self._cache_mtime = 0

    # ---- writing -------------------------------------------------------
    def record(self, ip, found_matches, n_matches=0):
        if ip in EXCLUDE_IPS:
            return
        entry = {
            't': int(time.time()),
            'd': _today(),
            'v': visitor_hash(ip),
            'hit': bool(found_matches),
        }
        # The number of matches is only useful for diagnostics, and it hints at
        # how distinctive someone's search was. Keep it off by default.
        if os.environ.get('STATS_STORE_MATCH_COUNT') == '1':
            entry['n'] = int(n_matches)
        line = json.dumps(entry, separators=(',', ':')) + '\n'
        with _lock:
            try:
                with open(self.file, 'a', encoding='utf-8') as f:
                    f.write(line)
            except OSError:
                pass  # never fail a user's search because a counter could not write

    # ---- reading -------------------------------------------------------
    def _load(self):
        if self._cache is not None:
            try:
                st = self.file.stat()
                if st.st_mtime == self._cache_mtime:
                    return self._cache
            except OSError:
                pass
        rows = []
        try:
            with open(self.file, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            rows = []
        self._cache = rows
        try:
            self._cache_mtime = self.file.stat().st_mtime
        except OSError:
            self._cache_mtime = 0
        return rows

    def summary(self):
        rows = self._load()
        days = {}
        for r in rows:
            d = r.get('d', '?')
            s = days.setdefault(d, {'people': set(), 'searches': 0,
                                    'found': 0, 'found_people': set()})
            s['people'].add(r.get('v'))
            s['searches'] += 1
            if r.get('hit'):
                s['found'] += 1
                s['found_people'].add(r.get('v'))

        per_day = []
        for d in sorted(days):
            s = days[d]
            per_day.append({
                'date': d,
                'people': len(s['people']),
                'searches': s['searches'],
                'searches_with_matches': s['found'],
                'people_who_matched': len(s['found_people']),
            })

        all_people = set()
        for s in days.values():
            all_people |= s['people']
        return {
            'total_searches': len(rows),
            'total_unique_days_visited': sum(len(s['people']) for s in days.values()),
            'searches_with_matches': sum(1 for r in rows if r.get('hit')),
            'days': per_day,
            'note': 'unique counts are per-day; the same person on two days counts twice '
                    'by design, because no cross-day identifier is kept',
        }

    def export(self):
        """Everything needed to restore counters after a redeploy."""
        return self._load()

    def import_rows(self, rows):
        """Merge previously exported rows (e.g. from STATS_SEED) on boot."""
        if not rows:
            return 0
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


stats = Stats()


def bootstrap_from_env():
    """Optionally seed counters from STATS_SEED (a JSON array) so numbers
    survive a redeploy on hosts with no persistent disk."""
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
