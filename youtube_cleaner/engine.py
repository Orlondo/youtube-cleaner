"""The steps: pull every video, apply approved changes (backup first), and undo."""
from . import api, plan, store

MAX_TITLE = 100
MAX_DESCRIPTION_BYTES = 5000


def pull(yt, st, rules=None, uploads_playlist=None, now=None):
    """Reads every video of the channel and (re)builds the proposals. Returns the status counts."""
    if uploads_playlist is None:
        _id, _title, uploads_playlist = yt.my_channel()
    ids = yt.video_ids(uploads_playlist)
    videos = yt.videos(ids)
    st.save_videos(videos)
    st.add_quota(1 + (len(ids) + api.PAGE - 1) // api.PAGE * 2, now)   # reads cost 1 unit a call
    st.save_proposals(plan.build(videos, st.proposals(), rules if rules is not None else st.rules()))
    return st.counts()


def check_text(title, description):
    """Why this text cannot go to YouTube, or "" when it can."""
    if not title.strip():
        return "The title is empty"
    if len(title) > MAX_TITLE:
        return f"The title is over {MAX_TITLE} characters"
    if len(description.encode("utf-8")) > MAX_DESCRIPTION_BYTES:
        return "The description is over YouTube's 5000-byte limit"
    if any(c in title + description for c in "<>"):
        return "YouTube does not allow < or > in a title or description"
    return ""


def _same_text(a, b):
    """Equal once line endings and spaces at the edges are ignored (YouTube may store text slightly differently)."""
    norm = lambda t: str(t or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return norm(a) == norm(b)


def apply(yt, st, edits=None, skip=(), now=None, budget=store.SAFE_BUDGET):
    """Approves `edits` ({videoId: {"title", "description"}}, the person's final text), skips `skip`, then changes
    every approved video that fits in today's quota. Each original is written to the backup file BEFORE its video
    is changed. Returns {"applied", "failed": [(id, why)], "left", "stopped": "" | "quota"}."""
    proposals, videos = st.proposals(), st.videos()
    for vid in skip:
        if vid in proposals and proposals[vid]["status"] in ("pending", "error"):
            proposals[vid]["status"] = "skipped"
    for vid, edit in (edits or {}).items():
        p = proposals.get(vid)
        if not p or p["status"] not in ("pending", "error", "approved"):
            continue
        title, description = str(edit.get("title", p["afterTitle"])), str(edit.get("description", p["afterDescription"]))
        why = check_text(title, description)
        if why:
            p["status"], p["error"] = "error", why
            continue
        p["afterTitle"], p["afterDescription"], p["status"] = title, description, "approved"
        p.pop("error", None)
    st.save_proposals(proposals)

    applied, failed, stopped = 0, [], ""
    for vid, p in proposals.items():
        if p["status"] != "approved":
            continue
        if not st.can_update(now, budget):
            stopped = "quota"
            break
        try:
            # Look at the video as it is on YouTube right now: if it was edited since the pull, leave it alone.
            fresh = yt.videos([vid]).get(vid)
            st.add_quota(1, now)
            if not fresh:
                raise api.ApiError("The video is no longer on the channel")
            if not (_same_text(fresh["snippet"].get("title", ""), p["beforeTitle"]) and _same_text(fresh["snippet"].get("description", ""), p["beforeDescription"])):
                raise api.ApiError("Changed on YouTube since it was pulled. Pull again, then review it")
            snippet = fresh["snippet"]
            st.backup(vid, snippet, p["afterTitle"], p["afterDescription"])   # the original is on disk before anything changes
            yt.update(vid, snippet, p["afterTitle"], p["afterDescription"])
        except api.QuotaExceeded:
            store.mark_quota_full(st, now, budget)
            stopped = "quota"
            break
        except api.ApiError as error:
            p["status"], p["error"] = "error", str(error)
            failed.append((vid, str(error)))
            st.save_proposals(proposals)
            continue
        st.add_quota(store.UPDATE_COST, now)
        snippet["title"], snippet["description"] = p["afterTitle"], p["afterDescription"]
        videos[vid]["snippet"] = snippet
        p["status"] = "applied"
        p.pop("error", None)
        applied += 1
        st.save_proposals(proposals)
        st.save_videos(videos)
    st.save_proposals(proposals)
    return {"applied": applied, "failed": failed, "left": sum(1 for p in proposals.values() if p["status"] == "approved"),
            "stopped": stopped}


def undo(yt, st, video_ids=None, now=None, budget=store.SAFE_BUDGET):
    """Puts the ORIGINAL title and description back (from the backup file) on the given videos, or on every changed one.
    Returns {"restored", "failed", "left", "stopped"}."""
    originals, videos, proposals = st.originals(), st.videos(), st.proposals()
    wanted = [v for v in (video_ids or list(originals)) if v in originals]
    restored, failed, stopped, left = 0, [], "", 0
    for index, vid in enumerate(wanted):
        original = originals[vid]
        current = videos.get(vid, {}).get("snippet", {})
        if current.get("title") == original.get("title") and current.get("description") == original.get("description"):
            continue
        if not st.can_update(now, budget):
            stopped, left = "quota", sum(1 for v in wanted[index:] if v in originals)
            break
        try:
            # Only the title and description go back; tags and everything else stay as they are on YouTube now.
            fresh = yt.videos([vid]).get(vid)
            st.add_quota(1, now)
            if not fresh:
                raise api.ApiError("The video is no longer on the channel")
            yt.update(vid, fresh["snippet"], original.get("title", ""), original.get("description", ""))
        except api.QuotaExceeded:
            store.mark_quota_full(st, now, budget)
            stopped, left = "quota", len(wanted) - index
            break
        except api.ApiError as error:
            failed.append((vid, str(error)))
            continue
        st.add_quota(store.UPDATE_COST, now)
        now_title, now_description = original.get("title", ""), original.get("description", "")
        try:   # what YouTube actually holds now (it may store the text a little differently from what was sent)
            again = yt.videos([vid]).get(vid)
            st.add_quota(1, now)
            if again:
                now_title, now_description = again["snippet"].get("title", ""), again["snippet"].get("description", "")
        except api.ApiError:
            pass
        if vid in videos:
            videos[vid]["snippet"]["title"], videos[vid]["snippet"]["description"] = now_title, now_description
        if vid in proposals:
            proposals[vid]["status"] = "skipped"   # put back on purpose: not proposed again
            proposals[vid]["beforeTitle"], proposals[vid]["beforeDescription"] = now_title, now_description
        restored += 1
        st.save_videos(videos)
        st.save_proposals(proposals)
    return {"restored": restored, "failed": failed, "left": left, "stopped": stopped}
