"""A small client for the parts of the YouTube Data API v3 this tool uses. All network traffic goes through one
`http(method, url, headers, body) -> (status, bytes)` function, so tests can stand in for YouTube."""
import json
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://www.googleapis.com/youtube/v3/"
PAGE = 50


class ApiError(Exception):
    def __init__(self, message, status=0, reason=""):
        super().__init__(message)
        self.status, self.reason = status, reason


class QuotaExceeded(ApiError):
    """YouTube's daily limit is used up: stop, and carry on after the quota resets (midnight Pacific time)."""


def urllib_http(method, url, headers, body):
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


class YouTube:
    def __init__(self, access_token, http=urllib_http):
        self._token = access_token   # a string, or a function that returns a fresh one
        self._http = http

    def _call(self, method, path, params=None, body=None):
        token = self._token() if callable(self._token) else self._token
        url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        status, raw = self._http(method, url, headers, data)
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            payload = {}
        if status >= 400:
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            reasons = [e.get("reason", "") for e in error.get("errors", []) if isinstance(e, dict)]
            message = error.get("message") or f"YouTube answered {status}"
            if any(r in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded") for r in reasons):
                raise QuotaExceeded(message, status, reasons[0])
            raise ApiError(message, status, reasons[0] if reasons else "")
        return payload

    def my_channel(self):
        """(channelId, title, uploadsPlaylistId) of the channel the person signed in as."""
        data = self._call("GET", "channels", {"part": "snippet,contentDetails", "mine": "true"})
        items = data.get("items", [])
        if not items:
            raise ApiError("This Google account has no YouTube channel to sign in to")
        item = items[0]
        return item["id"], item["snippet"]["title"], item["contentDetails"]["relatedPlaylists"]["uploads"]

    def video_ids(self, uploads_playlist):
        ids, token = [], None
        while True:
            params = {"part": "contentDetails", "playlistId": uploads_playlist, "maxResults": PAGE}
            if token:
                params["pageToken"] = token
            data = self._call("GET", "playlistItems", params)
            ids += [i["contentDetails"]["videoId"] for i in data.get("items", [])]
            token = data.get("nextPageToken")
            if not token:
                return ids

    def videos(self, ids):
        """{videoId: {"snippet": the whole snippet}} for every id (50 per call)."""
        out = {}
        for start in range(0, len(ids), PAGE):
            chunk = ids[start:start + PAGE]
            data = self._call("GET", "videos", {"part": "snippet", "id": ",".join(chunk), "maxResults": PAGE})
            for item in data.get("items", []):
                out[item["id"]] = {"snippet": item["snippet"]}
        return out

    def update(self, video_id, snippet, title, description):
        """Sets a video's title and description. The WHOLE existing snippet is sent back with only those two changed,
        because YouTube replaces the snippet it is given (tags, category and language would otherwise be wiped)."""
        keep = {k: v for k, v in snippet.items() if k not in ("thumbnails", "channelId", "channelTitle", "publishedAt",
                                                              "liveBroadcastContent", "localized", "publishedAt")}
        keep["title"], keep["description"] = title, description
        self._call("PUT", "videos", {"part": "snippet"}, {"id": video_id, "snippet": keep})
