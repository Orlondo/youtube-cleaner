"""Turns the pulled videos into proposals, keeping what the person already decided."""
from . import rules as rules_mod


def build(videos, proposals, rules=None):
    """{videoId: proposal}. A video the person already applied, skipped or approved keeps that decision as long as
    its text on YouTube has not changed since; anything else is cleaned afresh and waits for review."""
    out = {}
    for vid, video in videos.items():
        snippet = video["snippet"]
        title, description = snippet.get("title", ""), snippet.get("description", "")
        old = proposals.get(vid)
        if old:
            same_before = old["beforeTitle"] == title and old["beforeDescription"] == description
            is_applied_text = old["status"] == "applied" and old["afterTitle"] == title and old["afterDescription"] == description
            if (same_before and old["status"] in ("applied", "skipped", "approved")) or is_applied_text:
                out[vid] = old
                continue
        cleaned = rules_mod.clean_video(title, description, rules)
        out[vid] = {
            "beforeTitle": title, "beforeDescription": description,
            "afterTitle": cleaned["title"], "afterDescription": cleaned["description"],
            "flags": cleaned["flags"], "status": "pending" if cleaned["changed"] else "same",
        }
    return out


def next_batch(proposals, videos, size=20, only=("pending",)):
    """The next `size` proposals that wait for review, newest videos first as pulled."""
    items = [(vid, p) for vid, p in proposals.items() if p["status"] in only]
    return items[:size]
