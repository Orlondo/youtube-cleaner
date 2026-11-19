import copy
import datetime
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from youtube_cleaner import api, engine, plan, review, store

NOW = datetime.datetime(2026, 11, 18, 20, 0, tzinfo=datetime.timezone.utc)


class FakeYT:
    """A channel on a pretend YouTube: keeps each video's snippet and records every call."""
    def __init__(self, st, videos):
        self.st, self.remote, self.updates, self.fail = st, copy.deepcopy(videos), [], {}
        self.order_ok = True

    def my_channel(self):
        return "UC1", "Example Channel", "UU1"

    def video_ids(self, uploads):
        return list(self.remote)

    def videos(self, ids):
        return {i: copy.deepcopy(self.remote[i]) for i in ids if i in self.remote}

    def update(self, vid, snippet, title, description):
        if vid in self.fail:
            raise self.fail[vid]
        backups = [b for b in self.st.backups() if b["videoId"] == vid]
        if not backups or backups[-1]["snippet"]["title"] != self.remote[vid]["snippet"]["title"]:
            self.order_ok = False   # the original must already be on disk when YouTube is changed
        self.updates.append((vid, copy.deepcopy(snippet), title, description))
        self.remote[vid]["snippet"] = dict(snippet, title=title, description=description)


def vids(*titles):
    return {f"v{i}": {"snippet": {"title": t, "description": "Hello — world!!", "categoryId": "26", "tags": ["a", "b"]}}
            for i, t in enumerate(titles)}


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = store.Store("UC1", Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def start(self, *titles):
        self.yt = FakeYT(self.st, vids(*titles))
        return engine.pull(self.yt, self.st, now=NOW)

    def test_pull_changes_nothing_on_youtube(self):
        counts = self.start("modern kitchen", "Already Clean")
        self.assertEqual(counts.get("pending"), 2)   # the description's "—" and "!!" need cleaning in both
        self.assertEqual(self.yt.updates, [])
        self.assertEqual(self.st.proposals()["v0"]["afterTitle"], "Modern Kitchen")

    def test_apply_backs_up_first_keeps_tags_and_counts_quota(self):
        self.start("modern kitchen")
        r = engine.apply(self.yt, self.st, {"v0": {"title": "Modern Kitchen", "description": "Hello - world!"}}, now=NOW)
        self.assertEqual((r["applied"], r["stopped"], r["left"]), (1, "", 0))
        self.assertTrue(self.yt.order_ok)
        vid, snippet, title, desc = self.yt.updates[0]
        self.assertEqual((title, desc, snippet["tags"], snippet["categoryId"]), ("Modern Kitchen", "Hello - world!", ["a", "b"], "26"))
        self.assertEqual(self.st.originals()["v0"]["title"], "modern kitchen")
        self.assertEqual(self.st.proposals()["v0"]["status"], "applied")
        self.assertEqual(self.st.quota_used(NOW), 3 + store.UPDATE_COST + 1)   # the pull's reads, then one look-up and one update

    def test_skipped_videos_are_left_alone(self):
        self.start("a b", "c d")
        r = engine.apply(self.yt, self.st, {"v0": {"title": "A B", "description": "x"}}, skip=["v1"], now=NOW)
        self.assertEqual(r["applied"], 1)
        self.assertEqual(self.st.proposals()["v1"]["status"], "skipped")
        self.assertEqual([u[0] for u in self.yt.updates], ["v0"])

    def test_a_video_edited_on_youtube_since_the_pull_is_not_overwritten(self):
        self.start("modern kitchen")
        self.yt.remote["v0"]["snippet"]["title"] = "Edited by hand"
        r = engine.apply(self.yt, self.st, {"v0": {"title": "Modern Kitchen", "description": "x"}}, now=NOW)
        self.assertEqual(r["applied"], 0)
        self.assertIn("Changed on YouTube", r["failed"][0][1])
        self.assertEqual(self.yt.updates, [])
        self.assertEqual(self.st.backups(), [])
        self.assertEqual(self.st.proposals()["v0"]["status"], "error")

    def test_text_youtube_would_refuse_is_stopped_before_it_is_sent(self):
        self.start("a b")
        for title, desc in (("", "x"), ("x" * 101, "x"), ("fine", "has <b> tags")):
            engine.apply(self.yt, self.st, {"v0": {"title": title, "description": desc}}, now=NOW)
            self.assertEqual(self.st.proposals()["v0"]["status"], "error")
        self.assertEqual(self.yt.updates, [])

    def test_the_budget_stops_the_run_and_the_rest_waits_approved(self):
        self.start("a a", "b b", "c c")
        edits = {v: {"title": p["afterTitle"], "description": p["afterDescription"]} for v, p in self.st.proposals().items()}
        # room for two updates (50 + 1 look-up each) in today's budget
        r = engine.apply(self.yt, self.st, edits, now=NOW, budget=2 * 51 + 49)
        self.assertEqual((r["applied"], r["left"], r["stopped"]), (2, 1, "quota"))
        self.assertEqual(self.st.counts(), {"applied": 2, "approved": 1})
        # next day: running again with nothing new finishes the approved one
        tomorrow = NOW + datetime.timedelta(days=1)
        r2 = engine.apply(self.yt, self.st, {}, now=tomorrow)
        self.assertEqual((r2["applied"], r2["left"], r2["stopped"]), (1, 0, ""))

    def test_youtube_saying_quota_exceeded_stops_cleanly(self):
        self.start("a a", "b b")
        edits = {v: {"title": p["afterTitle"], "description": p["afterDescription"]} for v, p in self.st.proposals().items()}
        self.yt.fail["v0"] = api.QuotaExceeded("quota", 403, "quotaExceeded")
        r = engine.apply(self.yt, self.st, edits, now=NOW)
        self.assertEqual((r["applied"], r["stopped"], r["left"]), (0, "quota", 2))
        self.assertFalse(self.st.can_update(NOW))

    def test_another_api_error_marks_that_video_and_continues(self):
        self.start("a a", "b b")
        edits = {v: {"title": p["afterTitle"], "description": p["afterDescription"]} for v, p in self.st.proposals().items()}
        self.yt.fail["v0"] = api.ApiError("nope", 400, "invalidDescription")
        r = engine.apply(self.yt, self.st, edits, now=NOW)
        self.assertEqual((r["applied"], len(r["failed"])), (1, 1))
        self.assertEqual(self.st.proposals()["v0"]["status"], "error")

    def test_undo_restores_only_title_and_description(self):
        self.start("modern kitchen")
        engine.apply(self.yt, self.st, {"v0": {"title": "Modern Kitchen", "description": "Hello - world!"}}, now=NOW)
        self.yt.remote["v0"]["snippet"]["tags"] = ["edited-later"]
        r = engine.undo(self.yt, self.st, now=NOW)
        self.assertEqual(r["restored"], 1)
        s = self.yt.remote["v0"]["snippet"]
        self.assertEqual((s["title"], s["description"], s["tags"]), ("modern kitchen", "Hello — world!!", ["edited-later"]))
        self.assertEqual(self.st.proposals()["v0"]["status"], "skipped")
        self.assertEqual(engine.undo(self.yt, self.st, now=NOW)["restored"], 0)   # already original: nothing to do

    def test_undo_one_video_and_pull_afterwards_does_not_propose_it_again(self):
        self.start("a b", "c d")
        edits = {v: {"title": p["afterTitle"], "description": p["afterDescription"]} for v, p in self.st.proposals().items()}
        engine.apply(self.yt, self.st, edits, now=NOW)
        self.assertEqual(engine.undo(self.yt, self.st, ["v1"], now=NOW)["restored"], 1)
        engine.pull(self.yt, self.st)
        self.assertEqual(self.st.proposals()["v1"]["status"], "skipped")
        self.assertEqual(self.st.proposals()["v0"]["status"], "applied")


class ReviewServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = store.Store("UC1", Path(self.tmp.name))
        self.yt = FakeYT(self.st, vids("modern kitchen", "second one"))
        engine.pull(self.yt, self.st)
        self.server, self.url = review.make_server(self.yt, self.st, "Example Channel", size=1)
        import threading
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base, self.token = self.url.split("/?t=")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def get(self, path, token=True, host=None):
        headers = {"X-Token": self.token} if token else {}
        if host:
            headers["Host"] = host
        return urllib.request.urlopen(urllib.request.Request(self.base + path, headers=headers))

    def test_the_page_needs_the_secret_and_the_data_needs_it_too(self):
        self.assertIn(b"YouTube cleaner", self.get("/?t=" + self.token, token=False).read())
        with self.assertRaises(urllib.error.HTTPError) as c:
            self.get("/", token=False)
        self.assertEqual(c.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as c:
            self.get("/api/state", token=False)
        self.assertEqual(c.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as c:
            self.get("/api/state", host="evil.example")
        self.assertEqual(c.exception.code, 403)

    def test_batches_are_the_size_asked_and_apply_changes_the_checked_ones(self):
        state = json.loads(self.get("/api/state").read())
        self.assertEqual((state["channel"], state["counts"]["pending"]), ("Example Channel", 2))
        batch = json.loads(self.get("/api/batch").read())["items"]
        self.assertEqual(len(batch), 1)
        item = batch[0]
        body = json.dumps({"edits": {item["id"]: {"title": "Chosen Title", "description": item["afterDescription"]}}}).encode()
        req = urllib.request.Request(self.base + "/api/apply", data=body, method="POST",
                                     headers={"X-Token": self.token, "Content-Type": "application/json"})
        result = json.loads(urllib.request.urlopen(req).read())
        self.assertEqual(result["applied"], 1)
        self.assertEqual(self.yt.remote[item["id"]]["snippet"]["title"], "Chosen Title")
        nxt = json.loads(self.get("/api/batch").read())["items"]
        self.assertEqual(len(nxt), 1)
        self.assertNotEqual(nxt[0]["id"], item["id"])

    def test_apply_without_the_secret_changes_nothing(self):
        req = urllib.request.Request(self.base + "/api/apply", data=b'{"edits":{}}', method="POST")
        with self.assertRaises(urllib.error.HTTPError) as c:
            urllib.request.urlopen(req)
        self.assertEqual(c.exception.code, 403)
        self.assertEqual(self.yt.updates, [])


if __name__ == "__main__":
    unittest.main()
