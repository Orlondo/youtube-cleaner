"""Where the cleaner keeps its work, per channel, under ~/.youtube-cleaner (or the folder in YT_CLEANER_HOME):

  videos.json     every video as last pulled (id, title, description and the rest of its snippet)
  proposals.json  one entry per video: the cleaned text, its status, and any edits the person made
  backup.jsonl    the original of every video, written and flushed BEFORE the video is changed (what undo reads)
  quota.json      the update units used today, so a daily limit just means "continue tomorrow"
  rules.json      the person's cleaning settings (optional)

Statuses: pending (waiting for review), approved (will be applied), applied, skipped (the person left it alone),
same (already clean, nothing to do)."""
import datetime
import json
import os
from pathlib import Path

UPDATE_COST = 50            # YouTube's quota units for one videos.update call
DAILY_UNITS = 10000         # YouTube's default free daily quota
SAFE_BUDGET = 9000          # stop here, so reading and a few retries still fit in the day


def home():
    return Path(os.environ.get("YT_CLEANER_HOME") or Path.home() / ".youtube-cleaner")


def pacific_today(now=None):
    """YouTube's quota resets at midnight Pacific time."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return now.astimezone(ZoneInfo("America/Los_Angeles")).date().isoformat()
    except Exception:   # no timezone data: Pacific standard time is close enough for a daily counter
        return (now - datetime.timedelta(hours=8)).date().isoformat()


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


class Store:
    def __init__(self, channel_id, root=None):
        self.channel_id = channel_id
        self.dir = Path(root or home()) / "channels" / channel_id

    # ---- videos and proposals ----
    def videos(self):
        return _read_json(self.dir / "videos.json", {})

    def save_videos(self, videos):
        _write_json(self.dir / "videos.json", videos)

    def proposals(self):
        return _read_json(self.dir / "proposals.json", {})

    def save_proposals(self, proposals):
        _write_json(self.dir / "proposals.json", proposals)

    def rules(self):
        return _read_json(self.dir / "rules.json", {})

    # ---- backup: the original is on disk before the video is touched ----
    def backup(self, video_id, snippet, new_title, new_description, when=None):
        path = self.dir / "backup.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"at": (when or datetime.datetime.now(datetime.timezone.utc)).isoformat(), "videoId": video_id,
                 "snippet": snippet, "newTitle": new_title, "newDescription": new_description}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def backups(self):
        path = self.dir / "backup.jsonl"
        out = []
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            out.append(json.loads(line))
                        except ValueError:
                            continue
        except OSError:
            pass
        return out

    def originals(self):
        """{videoId: the snippet as it was before the first change}: what undo puts back."""
        first = {}
        for entry in self.backups():
            first.setdefault(entry["videoId"], entry["snippet"])
        return first

    # ---- quota ----
    def quota_used(self, now=None):
        q = _read_json(self.dir / "quota.json", {})
        return q.get("used", 0) if q.get("date") == pacific_today(now) else 0

    def add_quota(self, units, now=None):
        today = pacific_today(now)
        used = self.quota_used(now) + units
        _write_json(self.dir / "quota.json", {"date": today, "used": used})
        return used

    def can_update(self, now=None, budget=SAFE_BUDGET):
        return self.quota_used(now) + UPDATE_COST <= budget

    def updates_left_today(self, now=None, budget=SAFE_BUDGET):
        return max(0, (budget - self.quota_used(now)) // UPDATE_COST)

    def counts(self):
        out = {}
        for p in self.proposals().values():
            out[p["status"]] = out.get(p["status"], 0) + 1
        return out


def mark_quota_full(st, now=None, budget=SAFE_BUDGET):
    """YouTube said the daily limit is used up: count the rest of today as spent."""
    st.add_quota(max(0, budget - st.quota_used(now)), now)
