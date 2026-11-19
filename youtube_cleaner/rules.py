"""The cleaning rules for a video's title and description. Nothing is trimmed: only stray marks, chatbot labels,
bot-sounding stock phrases and shouting are removed or fixed, so the text keeps its length and meaning. Web links,
times (00:00), #hashtags and @names are never touched. Wording that still sounds written by a chatbot but cannot be
fixed safely by a rule is flagged for the person to look at in the review.

Not tied to one channel: the style is a setting (`titleStyle`: "title" Title Case, "sentence", or "keep")."""
import re

DEFAULT_RULES = {
    "titleStyle": "title",   # "title", "sentence" or "keep" (leave the capitalization as it is)
    "stripEmoji": True,      # emoji read as spam in titles and descriptions
    "dashes": True,          # em dashes are a chatbot habit: " — " becomes " - "
    "fillers": True,         # "In today's video," openers, "Let's dive in!", "dive into" -> "look at"
}

TITLE_LIMIT = 100


def merged(rules=None):
    out = dict(DEFAULT_RULES)
    for key in DEFAULT_RULES:
        if rules and key in rules:
            out[key] = rules[key]
    if out["titleStyle"] not in ("title", "sentence", "keep"):
        out["titleStyle"] = DEFAULT_RULES["titleStyle"]
    return out


# ---- protected spans: links, times, hashtags and names are left exactly as written ----
_PROTECT = re.compile(r"https?://\S+|www\.\S+|\b\d{1,2}:\d{2}(?::\d{2})?\b|(?<!\w)[#@][\w\-]+", re.I)


def _protect(text):
    spans = []

    def keep(match):
        spans.append(match.group(0))
        return f"\x00{len(spans) - 1}\x00"

    return _PROTECT.sub(keep, text), spans


def _restore(text, spans):
    return re.sub(r"\x00(\d+)\x00", lambda m: spans[int(m.group(1))], text)


_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿⌀-⏿️‍⃣]")

# words a chatbot puts in front of its answer, and markdown a YouTube page cannot show
_LABEL = re.compile(
    r"^\s*(?:here(?:'s| is| are)[^\n:]{0,60}:|(?:youtube\s+)?(?:title|description|video description|"
    r"suggested title|optimized title)\s*[:\-–]\s*)", re.I)

_FILLER_SENTENCE = re.compile(
    r"(?i)(^|(?<=[.!?]\s)|(?<=\n))(?:let'?s dive in|let'?s get started|without further ado|buckle up|"
    r"welcome back to (?:the|my|our) channel|stay tuned)[!.,]*[ \t]*")
_OPENER = re.compile(r"(?i)(^|(?<=[.!?]\s)|(?<=\n))in today'?s video,?[ \t]+(?=[a-z])")
_SWAPS = [
    (re.compile(r"(?i)\b(?:delve(?:s|d)?|dive(?:s|d)?)(?: deep)? into\b"), "look at"),
    (re.compile(r"(?i)\bdeep dive into\b"), "look at"),
]

# bot-sounding words that cannot be swapped safely by a rule: flagged for the person to look at
_FLAG_WORDS = [
    "delve", "tapestry", "testament to", "embark", "unleash", "unlock", "elevate", "game-changer", "game changer",
    "seamless", "seamlessly", "vibrant", "bustling", "nestled", "crafted", "curated", "ultimate guide",
    "whether you're a", "in the realm of", "ever-evolving", "journey", "transform your", "stunning", "must-see",
    "look no further", "take it to the next level", "elevated",
]
_FLAG_RE = re.compile(r"(?i)\b(" + "|".join(re.escape(w) for w in _FLAG_WORDS) + r")\b")

_SMALL = {"a", "an", "and", "as", "at", "but", "by", "for", "if", "in", "nor", "of", "on", "or", "per", "the", "to",
          "vs", "vs.", "via", "with", "from", "into", "over", "up", "off"}
_KEEP_UPPER = {"AI", "DIY", "TV", "USA", "US", "UK", "LED", "HVAC", "RV", "ADU", "HGTV", "ASMR", "FAQ", "NYC", "LA", "DC",
               "OK", "VR", "UV", "PVC", "HD", "4K", "8K", "HOA", "IKEA", "NASA"}


def _words_case(token, first):
    """Title-case one space-separated token, keeping acronyms and words that already have inner capitals."""
    if "\x00" in token:
        return token
    core = re.match(r"^(\W*)(.*?)(\W*)$", token, re.S)
    lead, word, tail = core.group(1), core.group(2), core.group(3)
    if not word:
        return token
    if "-" in word and not word.startswith("-"):
        parts = word.split("-")
        return lead + "-".join(_title_word(p, True) for p in parts) + tail
    return lead + _title_word(word, first) + tail


def _title_word(word, force):
    if not word:
        return word
    if word.upper() in _KEEP_UPPER and (word.isupper() or word.islower()):
        return word.upper()
    if any(c.isupper() for c in word[1:]) and not word.isupper():
        return word   # iPhone, YouTube, McCoy: already deliberate
    low = word.lower()
    if not force and low in _SMALL:
        return low
    return low[:1].upper() + low[1:]


def title_case(text):
    """Title Case with small words lowercase (not first, last or after a colon or dash), keeping acronyms."""
    tokens = text.split(" ")
    out = []
    last = max((i for i, t in enumerate(tokens) if t.strip()), default=-1)
    after_break = True
    for i, token in enumerate(tokens):
        if not token.strip():
            out.append(token)
            continue
        out.append(_words_case(token, after_break or i == last))
        after_break = token.endswith((":", "-", "–", "—", "|", "?", "!")) or token in ("-", "–", "—", "|")
    return " ".join(out)


def sentence_case_line(line):
    """ALL CAPS shouting becomes a sentence: first letter capital, the rest lowercase, acronyms kept."""
    words = line.lower().split(" ")
    out = []
    for w in words:
        stripped = re.sub(r"\W", "", w).upper()
        out.append(w.upper() if stripped in _KEEP_UPPER and stripped else w)
    text = " ".join(out)
    return re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text, count=0)


def _shouting(line):
    letters = [c for c in line if c.isalpha()]
    return len(letters) >= 12 and all(c.isupper() for c in letters)


def _strip_emoji(line):
    """The line without emoji, and without the space an emoji at its start used to sit before."""
    out = _EMOJI.sub("", line)
    return out.lstrip() if out != line and not line[:1].isspace() else out


def _common(text, rules):
    """Rules shared by titles and descriptions. Works on text with protected spans already swapped out."""
    lines = []
    for line in text.splitlines():
        line = re.sub(r"\*{1,3}([^*\n]+)\*{1,3}", r"\1", line)      # **bold** and *italic*
        line = line.replace("*", "")
        line = re.sub(r"^\s{0,3}#{1,6}\s+(?=\S)", "", line)          # # headings (not #hashtags: those are protected)
        line = line.replace("`", "")
        line = _LABEL.sub("", line)
        lines.append(line)
    text = "\n".join(lines)
    if rules["stripEmoji"]:
        text = "\n".join(_strip_emoji(line) for line in text.split("\n"))
    if rules["dashes"]:
        text = re.sub(r"[ \t]*—[ \t]*", " - ", text)
        text = re.sub(r"(?<=\d)–(?=\d)", "-", text)
        text = re.sub(r"[ \t]+–[ \t]+", " - ", text)
    if rules["fillers"]:
        for pattern, replacement in _SWAPS:
            text = pattern.sub(lambda m: replacement.capitalize() if m.group(0)[:1].isupper() else replacement, text)
        text = _OPENER.sub(lambda m: m.group(1) + "\x01", text)
        text = _FILLER_SENTENCE.sub(lambda m: m.group(1) + "\x01", text)
        # where an opener was removed, the sentence that is left gets its capital back; nowhere else is touched
        text = re.sub(r"\x01([a-z])", lambda m: m.group(1).upper(), text).replace("\x01", "")
    text = re.sub(r"([!?])\1{1,}", r"\1", text)                        # !!! -> !
    text = re.sub(r"\.{4,}", "...", text)
    return text


def flags_for(text):
    """Bot-sounding wording still in the text, for the review to point at."""
    seen = []
    for match in _FLAG_RE.finditer(text or ""):
        word = match.group(1).lower()
        if word not in seen:
            seen.append(word)
    return seen


def clean_title(text, rules=None):
    """(cleaned title, flags). A title that would end up empty or over 100 characters is left as it was."""
    rules = merged(rules)
    original = str(text or "")
    body, spans = _protect(original.strip())
    body = _common(body, rules)
    body = re.sub(r"\s+", " ", body).strip()
    if len(body) >= 2 and body[0] == body[-1] and body[0] in "\"'“”" and body.count(body[0]) == 2:
        body = body[1:-1].strip()                                      # a chatbot wraps its title in quotes
    if rules["titleStyle"] == "title":
        body = title_case(body)
    elif rules["titleStyle"] == "sentence":
        body = sentence_case_line(body) if _shouting(body) else body[:1].upper() + body[1:]
    result = _restore(body, spans).strip()
    if not result or len(result) > TITLE_LIMIT:
        return original, flags_for(original)
    return result, flags_for(result)


def clean_description(text, rules=None):
    """(cleaned description, flags). Line breaks stay where they are; only marks and wording are fixed."""
    rules = merged(rules)
    original = str(text or "")
    body, spans = _protect(original.replace("\r\n", "\n"))
    body = _common(body, rules)
    out = []
    for line in body.split("\n"):
        line = re.sub(r"(?<=\S)[ \t]{2,}", " ", line).rstrip()
        if _shouting(line) and rules["titleStyle"] != "keep":
            line = sentence_case_line(line)
        elif rules["titleStyle"] != "keep":
            # a line that starts with a plain lowercase word (not a link, hashtag or deliberate "iPhone") gets a capital
            m = re.match(r"^(\s*)([a-z][a-z']*)(\b)(?!\S*\x00)", line)
            if m and len(line.strip()) > 20 and not line.lstrip().startswith(("-", "*", "•")):
                line = m.group(1) + m.group(2).capitalize() + line[m.end(2):]
        out.append(line)
    result = _restore("\n".join(out), spans)
    result = re.sub(r"\n{3,}", "\n\n", result).strip("\n")
    return result, flags_for(result)


def clean_video(title, description, rules=None):
    """{title, description, flags, changed} for one video."""
    new_title, title_flags = clean_title(title, rules)
    new_description, description_flags = clean_description(description, rules)
    return {
        "title": new_title,
        "description": new_description,
        "flags": sorted(set(title_flags + description_flags)),
        # Spacing at the edges (a description of one space, a trailing space) is not worth a review line.
        "changed": new_title.strip() != (title or "").strip() or new_description.strip() != (description or "").strip(),
    }
