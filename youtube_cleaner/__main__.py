"""python3 -m youtube_cleaner <command>   (run from this folder)

  setup     how to get the Google sign-in file (one time), and a check that it is in place
  login     sign in with Google and pick the channel (run again to add another channel)
  channels  list the channels that are signed in
  pull      read every video and prepare the cleaned titles and descriptions (changes nothing on YouTube)
  review    open the before/after review in your browser, in batches; checked ones are changed on YouTube
  status    how many videos are waiting, applied or already clean, and today's quota
  undo      put the original titles and descriptions back (one video with --video ID, or every changed one with --all)
"""
import argparse
import json
import sys
from pathlib import Path

from . import api, auth, engine, review, store

SETUP = """One-time setup (about 10 minutes, free):
 1. Go to https://console.cloud.google.com/ and create a project (any name).
 2. APIs & Services > Library > search "YouTube Data API v3" > Enable.
 3. APIs & Services > OAuth consent screen: choose External, fill the app name and your email, and add your own Google
    account under "Test users". (It stays in Testing mode; that is fine for you alone.)
 4. APIs & Services > Credentials > Create credentials > OAuth client ID > Application type "Desktop app".
 5. Download the JSON and save it as: {path}
 Then run:  python3 -m youtube_cleaner login
Note: while the app is in Testing mode Google signs you out after 7 days; just run login again.
"""


def client_path(args):
    return Path(args.client_secret) if args.client_secret else store.home() / "client_secret.json"


def pick_channel(args):
    channels = auth.signed_in_channels()
    if args.channel:
        return args.channel
    if len(channels) == 1:
        return channels[0][0]
    if not channels:
        sys.exit("Not signed in. Run: python3 -m youtube_cleaner login")
    sys.exit("More than one channel is signed in. Add --channel with one of:\n" + "\n".join(f"  {c}  {t}" for c, t in channels))


def connect(args):
    channel = pick_channel(args)
    client = auth.load_client(client_path(args))
    tokens = auth.Tokens(auth.token_path(channel), client)
    yt = api.YouTube(tokens.access_token)
    return channel, tokens.load().get("channelTitle", channel), yt, store.Store(channel)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="youtube_cleaner", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["setup", "login", "channels", "pull", "review", "status", "undo"])
    parser.add_argument("--channel", help="channel id (needed only when more than one is signed in)")
    parser.add_argument("--client-secret", help="path to the Google sign-in JSON (default: ~/.youtube-cleaner/client_secret.json)")
    parser.add_argument("--title-style", choices=["title", "sentence", "keep"], help="pull: capitalization for titles (default title)")
    parser.add_argument("--keep-emoji", action="store_true", help="pull: do not remove emoji")
    parser.add_argument("--size", type=int, default=20, help="review: videos per batch (default 20)")
    parser.add_argument("--port", type=int, default=0, help="review: port for the local page")
    parser.add_argument("--video", help="undo: one video id")
    parser.add_argument("--all", action="store_true", help="undo: every video that was changed")
    args = parser.parse_args(argv)

    if args.command == "setup":
        path = client_path(args)
        print(SETUP.format(path=path))
        try:
            auth.load_client(path)
            print(f"Found a sign-in file at {path}. Next: python3 -m youtube_cleaner login")
        except auth.AuthError as error:
            print(f"Not ready yet: {error}")
        return 0

    try:
        if args.command == "login":
            client = auth.load_client(client_path(args))
            tokens = auth.login(client)
            yt = api.YouTube(tokens["access_token"])
            channel_id, title, _ = yt.my_channel()
            auth.Tokens(auth.token_path(channel_id), client).save(tokens, channel_id, title)
            print(f"Signed in to the channel: {title} ({channel_id})")
            return 0
        if args.command == "channels":
            for channel_id, title in auth.signed_in_channels():
                print(f"{channel_id}  {title}")
            return 0

        channel, title, yt, st = connect(args)
        if args.command == "pull":
            if args.title_style or args.keep_emoji:
                rules = st.rules()
                if args.title_style:
                    rules["titleStyle"] = args.title_style
                if args.keep_emoji:
                    rules["stripEmoji"] = False
                store._write_json(st.dir / "rules.json", rules)
            counts = engine.pull(yt, st)
            print(f"Pulled {sum(counts.values())} videos for {title}: {json.dumps(counts)}")
            print("Nothing was changed on YouTube. Next: python3 -m youtube_cleaner review")
        elif args.command == "review":
            review.serve(yt, st, title, args.size, args.port)
        elif args.command == "status":
            counts = st.counts()
            print(f"{title}: {json.dumps(counts)}")
            print(f"Quota used today: {st.quota_used()} units; about {st.updates_left_today()} more updates fit today")
        elif args.command == "undo":
            if not args.video and not args.all:
                sys.exit("Say what to undo: --video ID, or --all")
            ids = [args.video] if args.video else None
            n = len(ids) if ids else len(st.originals())
            if input(f"Put the original title and description back on {n} video(s)? Type YES: ").strip() != "YES":
                print("Nothing changed.")
                return 0
            result = engine.undo(yt, st, ids)
            print(f"Restored {result['restored']}." + (f" {len(result['failed'])} failed." if result["failed"] else "")
                  + (f" Daily limit reached; {result['left']} left: run undo again after midnight Pacific time." if result["stopped"] else ""))
    except (auth.AuthError, api.ApiError) as error:
        sys.exit(f"Stopped: {error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
