import datetime
import json
import tempfile
import unittest
from pathlib import Path

from youtube_cleaner import api, auth, plan, rules, store


class RulesTests(unittest.TestCase):
    def test_titles(self):
        cases = {
            "**Modern Farmhouse Kitchen Tour** 🏡✨": "Modern Farmhouse Kitchen Tour",
            "YouTube Title: A Cozy Living Room — Full Tour!!!": "A Cozy Living Room - Full Tour!",
            "DIY kitchen makeover on a budget": "DIY Kitchen Makeover on a Budget",
            '"Dive Into This Stunning Foyer"': "Look at This Stunning Foyer",
            "THE BEST KITCHEN IDEAS FOR SMALL HOMES": "The Best Kitchen Ideas for Small Homes",
            "Tour of the Smith House 4K": "Tour of the Smith House 4K",
            "my 3-light pendant & the best iPhone tips: part 2": "My 3-Light Pendant & the Best iPhone Tips: Part 2",
        }
        for before, after in cases.items():
            self.assertEqual(rules.clean_title(before)[0], after, before)

    def test_nothing_is_trimmed_and_titles_never_grow_past_the_limit(self):
        long = "a" * 100
        self.assertEqual(len(rules.clean_title(long)[0]), 100)
        self.assertEqual(rules.clean_title("x" * 101)[0], "x" * 101)   # over the limit: left exactly as it was
        self.assertEqual(rules.clean_title("🏡")[0], "🏡")              # would be empty: left as it was

    def test_links_times_hashtags_and_names_are_never_touched(self):
        text = "see https://www.lampsplus.com/p/Julian__7161x/ at 01:23 #Kitchen @Example dive into it"
        out = rules.clean_description(text)[0]
        self.assertIn("https://www.lampsplus.com/p/Julian__7161x/", out)
        self.assertIn("01:23", out)
        self.assertIn("#Kitchen", out)
        self.assertIn("@Example", out)
        self.assertIn("look at it", out)

    def test_description_cleanup(self):
        d = ("Here's a description:\n**In today's video, we dive into a vibrant kitchen — and it's stunning!!!**\n"
             "00:00 Intro\nlet's dive in! welcome back to the channel.\nSHOP THE LOOK HERE FOR ALL THE ITEMS\n✨✨✨ Follow us!")
        out, flags = rules.clean_description(d)
        self.assertEqual(out.splitlines()[0], "We look at a vibrant kitchen - and it's stunning!")
        self.assertIn("00:00 Intro", out)
        self.assertIn("Shop the look here for all the items", out)
        self.assertTrue(out.endswith("Follow us!"))
        self.assertNotIn("dive in", out.lower())
        self.assertEqual(flags, ["vibrant", "stunning"])

    def test_the_users_own_in_this_video_heading_is_kept(self):
        out = rules.clean_description("In this video:\n1. Foyer\nIn today's video: the kitchen")[0]
        self.assertIn("In this video:", out)
        self.assertIn("In today's video: the kitchen", out)

    def test_styles(self):
        self.assertEqual(rules.clean_title("the best tips", {"titleStyle": "keep"})[0], "the best tips")
        self.assertEqual(rules.clean_title("the best TIPS for Homes", {"titleStyle": "sentence"})[0], "The best TIPS for Homes")
        self.assertEqual(rules.clean_title("a 🏡 b", {"stripEmoji": False, "titleStyle": "keep"})[0], "a 🏡 b")

    def test_clean_video_reports_a_change_only_when_there_is_one(self):
        self.assertFalse(rules.clean_video("Modern Kitchen Tour", "A nice tour.")["changed"])
        self.assertTrue(rules.clean_video("modern kitchen tour", "A nice tour.")["changed"])


class StoreAndPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = store.Store("UC1", Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def video(self, title, description="Hello."):
        return {"snippet": {"title": title, "description": description, "categoryId": "26", "tags": ["a"]}}

    def test_backup_is_kept_in_order_and_the_first_is_the_original(self):
        self.store.backup("v1", {"title": "Orig"}, "New1", "d")
        self.store.backup("v1", {"title": "New1"}, "New2", "d")
        self.assertEqual(len(self.store.backups()), 2)
        self.assertEqual(self.store.originals()["v1"]["title"], "Orig")

    def test_quota_counts_per_pacific_day_and_resets(self):
        day1 = datetime.datetime(2026, 11, 18, 20, 0, tzinfo=datetime.timezone.utc)
        day2 = datetime.datetime(2026, 11, 19, 20, 0, tzinfo=datetime.timezone.utc)
        self.store.add_quota(store.UPDATE_COST * 3, day1)
        self.assertEqual(self.store.quota_used(day1), 150)
        self.assertEqual(self.store.quota_used(day2), 0)
        self.assertEqual(self.store.updates_left_today(day1), (store.SAFE_BUDGET - 150) // 50)
        self.store.add_quota(store.SAFE_BUDGET, day1)
        self.assertFalse(self.store.can_update(day1))
        self.assertTrue(self.store.can_update(day2))

    def test_plan_marks_clean_videos_same_and_keeps_decisions(self):
        videos = {"a": self.video("modern kitchen"), "b": self.video("Already Clean")}
        proposals = plan.build(videos, {})
        self.assertEqual((proposals["a"]["status"], proposals["b"]["status"]), ("pending", "same"))
        self.assertEqual(proposals["a"]["afterTitle"], "Modern Kitchen")
        proposals["a"]["status"] = "skipped"
        again = plan.build(videos, proposals)
        self.assertEqual(again["a"]["status"], "skipped")
        videos["a"] = self.video("modern kitchen, edited on youtube")   # changed since: asked about again
        self.assertEqual(plan.build(videos, proposals)["a"]["status"], "pending")

    def test_an_applied_video_stays_applied_after_a_new_pull(self):
        videos = {"a": self.video("modern kitchen")}
        proposals = plan.build(videos, {})
        proposals["a"]["status"] = "applied"
        videos["a"] = self.video(proposals["a"]["afterTitle"], proposals["a"]["afterDescription"])
        self.assertEqual(plan.build(videos, proposals)["a"]["status"], "applied")


class FakeYouTube:
    """Stands in for YouTube's servers: records calls, serves canned answers."""
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        status, payload = self.answers.pop(0)
        return status, json.dumps(payload).encode()


class ApiTests(unittest.TestCase):
    def test_channel_and_paging(self):
        http = FakeYouTube([
            (200, {"items": [{"id": "UC1", "snippet": {"title": "Example Channel"}, "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]}),
            (200, {"items": [{"contentDetails": {"videoId": "v1"}}], "nextPageToken": "p2"}),
            (200, {"items": [{"contentDetails": {"videoId": "v2"}}]}),
            (200, {"items": [{"id": "v1", "snippet": {"title": "T1"}}, {"id": "v2", "snippet": {"title": "T2"}}]}),
        ])
        yt = api.YouTube("tok", http)
        self.assertEqual(yt.my_channel(), ("UC1", "Example Channel", "UU1"))
        ids = yt.video_ids("UU1")
        self.assertEqual(ids, ["v1", "v2"])
        self.assertEqual(yt.videos(ids)["v2"]["snippet"]["title"], "T2")
        self.assertIn("pageToken=p2", http.calls[2][1])
        self.assertEqual(http.calls[0][2]["Authorization"], "Bearer tok")

    def test_update_sends_the_whole_snippet_with_only_title_and_description_changed(self):
        http = FakeYouTube([(200, {})])
        snippet = {"title": "old", "description": "olddesc", "categoryId": "26", "tags": ["a", "b"], "defaultLanguage": "en",
                   "thumbnails": {"x": 1}, "channelId": "UC1", "publishedAt": "t"}
        api.YouTube("tok", http).update("v1", snippet, "New", "NewDesc")
        sent = json.loads(http.calls[0][3])
        self.assertEqual(http.calls[0][0], "PUT")
        self.assertIn("part=snippet", http.calls[0][1])
        self.assertEqual(sent["snippet"], {"title": "New", "description": "NewDesc", "categoryId": "26", "tags": ["a", "b"],
                                           "defaultLanguage": "en"})

    def test_quota_errors_are_told_apart(self):
        err = {"error": {"message": "quota", "errors": [{"reason": "quotaExceeded"}]}}
        with self.assertRaises(api.QuotaExceeded):
            api.YouTube("t", FakeYouTube([(403, err)])).update("v", {"categoryId": "1"}, "a", "b")
        with self.assertRaises(api.ApiError) as ctx:
            api.YouTube("t", FakeYouTube([(404, {"error": {"message": "gone", "errors": [{"reason": "videoNotFound"}]}})])).update("v", {}, "a", "b")
        self.assertNotIsInstance(ctx.exception, api.QuotaExceeded)
        self.assertEqual(ctx.exception.reason, "videoNotFound")


class AuthTests(unittest.TestCase):
    def test_client_file(self):
        with tempfile.TemporaryDirectory() as d:
            good, bad = Path(d, "good.json"), Path(d, "bad.json")
            good.write_text(json.dumps({"installed": {"client_id": "id", "client_secret": "s"}}))
            bad.write_text("{}")
            self.assertEqual(auth.load_client(good), ("id", "s"))
            with self.assertRaises(auth.AuthError):
                auth.load_client(bad)
            with self.assertRaises(auth.AuthError):
                auth.load_client(Path(d, "missing.json"))

    def test_pkce_and_url(self):
        verifier, challenge = auth.pkce_pair()
        import base64, hashlib
        self.assertEqual(challenge, base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode())
        url = auth.auth_url("cid", "http://127.0.0.1:1", challenge, "st")
        for part in ("client_id=cid", "code_challenge_method=S256", "access_type=offline", "scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fyoutube"):
            self.assertIn(part, url)

    def test_login_receives_the_code_on_this_computer_and_exchanges_it(self):
        import urllib.request
        sent = {}

        def http(method, url, headers, body):
            sent["body"] = body.decode()
            return 200, json.dumps({"access_token": "A", "refresh_token": "R", "expires_in": 3600}).encode()

        def browser(url):   # plays the person: Google redirects back with the code
            import urllib.parse, threading
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            redirect, state = q["redirect_uri"][0], q["state"][0]
            threading.Thread(target=lambda: urllib.request.urlopen(f"{redirect}/?code=CODE123&state={state}").read()).start()

        tokens = auth.login(("cid", "secret"), http, browser, say=lambda *a: None, timeout=10)
        self.assertEqual(tokens["refresh_token"], "R")
        self.assertIn("code=CODE123", sent["body"])
        self.assertIn("code_verifier=", sent["body"])

    def test_a_wrong_state_is_ignored(self):
        import urllib.request, urllib.error
        port, state = [], "right"
        server, result = auth._wait_for_code(port, state, 5)
        try:
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(f"http://127.0.0.1:{port[0]}/?code=X&state=wrong")
            self.assertEqual(result, {})
        finally:
            server.shutdown()
            server.server_close()

    def test_tokens_refresh_when_expired_and_files_are_private(self):
        with tempfile.TemporaryDirectory() as d:
            clock = [1000.0]
            calls = []

            def http(method, url, headers, body):
                calls.append(body.decode())
                return 200, json.dumps({"access_token": "NEW", "expires_in": 3600}).encode()

            t = auth.Tokens(Path(d, "channels", "c", "token.json"), ("cid", "s"), http, now=lambda: clock[0])
            t.save({"refresh_token": "R", "access_token": "OLD", "expires_in": 3600}, "UC1", "Example Channel")
            self.assertEqual(t.access_token(), "OLD")
            self.assertEqual(calls, [])
            clock[0] += 4000
            self.assertEqual(t.access_token(), "NEW")
            self.assertIn("refresh_token=R", calls[0])
            self.assertEqual(oct(Path(d, "channels", "c", "token.json").stat().st_mode & 0o777), "0o600")
            self.assertEqual(auth.signed_in_channels(Path(d)), [("c", "Example Channel")])


if __name__ == "__main__":
    unittest.main()
