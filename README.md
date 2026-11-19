# YouTube cleaner

No installs needed: it uses only Python 3 (3.9 or newer) and its standard library.

Cleans the titles and descriptions of a channel's videos. Works with any channel: sign in with Google and pick which one.
Nothing changes on YouTube until you check a video in the review and press Apply, every original is saved to a backup file
before its video is changed, and anything can be undone.

## What it fixes (nothing is trimmed, so text keeps its length)
- Stray asterisks, `#` heading marks, backticks and chatbot labels ("Here's a description:", "YouTube title:").
- Emoji (turn off with `--keep-emoji`), em dashes (` — ` becomes ` - `), `!!!` and `???`.
- Bot-sounding filler: "In today's video," openers, "Let's dive in!", "Without further ado", "dive into" / "delve into" become "look at".
- Capitalization: titles get Title Case (acronyms like DIY, AI, LED and words like iPhone are kept; change with `--title-style sentence` or `keep`); ALL CAPS lines become sentences; a line that starts in lowercase gets a capital.
- Words that still sound like a chatbot but cannot be swapped safely (vibrant, stunning, tapestry, unlock, elevate...) are **flagged** in red in the review for you to reword. You can edit any new title or description right on the page.
- Links, times (00:00), #hashtags and @names are never touched.

## One-time setup (about 10 minutes, free)
1. https://console.cloud.google.com/ : create a project (any name).
2. APIs & Services > Library: enable **YouTube Data API v3**.
3. APIs & Services > OAuth consent screen: External; fill in an app name and your email; add your Google account under **Test users**. Leave it in Testing mode.
4. APIs & Services > Credentials > Create credentials > **OAuth client ID** > Application type **Desktop app**.
5. Download the JSON and save it as `~/.youtube-cleaner/client_secret.json`.

Google shows an "unverified app" warning because it is your own private app: choose Advanced, then continue. In Testing mode
Google signs you out after 7 days; just run `login` again.

## Use (run these in this folder)
```
python3 -m youtube_cleaner setup      # checks the sign-in file is in place
python3 -m youtube_cleaner login      # opens Google; pick the channel (run again for another channel)
python3 -m youtube_cleaner pull       # reads every video, prepares the cleaned text (changes nothing)
python3 -m youtube_cleaner review     # opens the before/after page in your browser, 20 videos at a time
python3 -m youtube_cleaner status     # counts and today's quota
python3 -m youtube_cleaner undo --video VIDEO_ID     # or: undo --all
```
In the review, every changed video is checked. Edit any text, uncheck what you want left alone, then press
**Apply checked, skip the rest**. Videos you skip are not asked about again (unless they change on YouTube).

## Limits and safety
- YouTube allows 10,000 quota units a day; one change costs 50 (+1 to double-check the video first). The tool stops at
  9,000, which is about 175 videos a day, and remembers where it stopped: run `review` again the next day (the limit resets at
  midnight Pacific time) and the approved ones finish first.
- Before changing a video it re-reads it on YouTube; if you edited it by hand since the pull, it leaves that video alone and says so.
- Only the title and description are sent back; tags, category and language stay as they are.
- Everything is kept in `~/.youtube-cleaner/channels/<channel id>/`: `backup.jsonl` (originals, written before each change),
  `proposals.json`, `videos.json`, `quota.json`, and `token.json` (only your user can read it). Remove access any time at
  https://myaccount.google.com/permissions.
- The review page only answers this computer and needs the secret address the terminal prints.

## Tests
```
python3 -m unittest discover -s tests -t .
```
The tests use a pretend YouTube and a pretend Google sign-in; nothing touches a real channel.
