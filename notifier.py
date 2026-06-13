"""Output channels for detected events.

`dispatch` is called by the poller with the list of new events each sweep.
Right now it just logs to stdout and the dashboard feed (via events.json).
Wire up real channels by filling in the stubs below — each is intentionally
self-contained so you can enable one without touching the rest.
"""
import os


def _log(msg):
    """Print without dying on legacy Windows consoles that can't encode emoji."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode())


def post_to_x(event):
    """Post a single event to X / Twitter. STUB.

    To enable: `pip install tweepy`, set the four env vars below, and
    uncomment the body. Keep tweets to the headline; it's already <280 chars.
    """
    if not os.environ.get("X_API_KEY"):
        return False
    # import tweepy
    # client = tweepy.Client(
    #     consumer_key=os.environ["X_API_KEY"],
    #     consumer_secret=os.environ["X_API_SECRET"],
    #     access_token=os.environ["X_ACCESS_TOKEN"],
    #     access_token_secret=os.environ["X_ACCESS_SECRET"],
    # )
    # client.create_tweet(text=event["headline"])
    return True


def post_to_discord(event):
    """Post a single event to a Discord webhook. STUB."""
    url = os.environ.get("HUBBLE_DISCORD_WEBHOOK")
    if not url:
        return False
    # import requests
    # requests.post(url, json={"content": event["headline"]}, timeout=15)
    return True


# Only the most "announceable" event types get pushed to social channels;
# climbers/price-drops still show in the dashboard feed but don't spam X.
SOCIAL_TYPES = {"new_leader", "new_model"}


def dispatch(events):
    """Fan a batch of new events out to all enabled channels."""
    for e in events:
        _log(f"[hubble:event] {e['headline']}")
        if e.get("type") in SOCIAL_TYPES:
            post_to_x(e)
            post_to_discord(e)
