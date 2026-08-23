"""Output channels for detected events, shared across the observatory.

`dispatch` is called by the poller with each sweep's new events. Channels are
opt-in via env vars, so an unconfigured install just logs to stdout and the
dashboard feed. Each channel is self-contained — enable one without the rest.

Every headline embeds a `name` pulled straight from an unauthenticated public
source (a GitHub repo, an npm package, an SEC Form D filer, a
ClinicalTrials.gov sponsor, an HN post title, ...) — anyone can register any
of those under any string they like. Discord parses `@everyone`/`@here`/role/
user mentions out of plain message text by default, so relaying that text
unmodified would let an attacker-chosen entity name page an entire server the
moment its event fires. Every Discord payload here sets
`allowed_mentions: {"parse": []}` to suppress all mention parsing regardless
of message content — Discord's own documented mechanism for this exact case,
not string-scrubbing for "@" ourselves. Slack's webhook `text` field doesn't
have this problem: a plain "@everyone" renders as literal text there, real
mentions need Slack's own bracketed `<!channel>`/`<@U...>` syntax, which no
external source's freeform name field can produce by accident.
"""
import os

import requests

# Only the most announceable shapes get pushed to social; movers and deltas
# still appear in the dashboard feed but don't spam a timeline.
SOCIAL_TYPES = {
    "new_leader", "new_model", "new_entrant", "stealth_raise",
    "faint_signal", "regime_change", "big_award", "whale_move",
    "new_program", "budget_shift",
}


def _log(msg):
    """Print without dying on legacy consoles that can't encode emoji."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode())


def post_to_discord(event, scope):
    url = os.environ.get("OBSERVATORY_DISCORD_WEBHOOK")
    if not url:
        return False
    try:
        requests.post(
            url,
            json={"content": f"**{scope.name}** · {event['headline']}",
                  "allowed_mentions": {"parse": []}},
            timeout=15,
        )
        return True
    except requests.RequestException:
        return False


def post_to_slack(event, scope):
    url = os.environ.get("OBSERVATORY_SLACK_WEBHOOK")
    if not url:
        return False
    try:
        requests.post(
            url,
            json={"text": f"*{scope.name}* · {event['headline']}"},
            timeout=15,
        )
        return True
    except requests.RequestException:
        return False


def post_to_x(event, scope):
    """Post to X / Twitter. Requires `pip install tweepy` + the four env vars."""
    if not os.environ.get("X_API_KEY"):
        return False
    try:
        import tweepy
    except ImportError:
        return False
    try:
        client = tweepy.Client(
            consumer_key=os.environ["X_API_KEY"],
            consumer_secret=os.environ["X_API_SECRET"],
            access_token=os.environ["X_ACCESS_TOKEN"],
            access_token_secret=os.environ["X_ACCESS_SECRET"],
        )
        client.create_tweet(text=event["headline"][:280])
        return True
    except Exception:
        return False


CHANNELS = (post_to_discord, post_to_slack, post_to_x)


def dispatch(events, scope):
    """Fan a batch of new events out to every enabled channel."""
    for e in events:
        _log(f"[{scope.slug}:event] {e['headline']}")
        if e.get("type") in SOCIAL_TYPES:
            for channel in CHANNELS:
                channel(e, scope)


def post_text_to_discord(text):
    """Same webhook as post_to_discord, a raw digest instead of one event."""
    url = os.environ.get("OBSERVATORY_DISCORD_WEBHOOK")
    if not url:
        return False
    try:
        requests.post(
            url,
            json={"content": text[:2000], "allowed_mentions": {"parse": []}},
            timeout=15,
        )
        return True
    except requests.RequestException:
        return False


def post_text_to_slack(text):
    url = os.environ.get("OBSERVATORY_SLACK_WEBHOOK")
    if not url:
        return False
    try:
        requests.post(url, json={"text": text}, timeout=15)
        return True
    except requests.RequestException:
        return False


def dispatch_brief(text):
    """Push a composed digest (telescope/brief.py) through the same channels
    regular events use. Unconfigured is the honest default: this always
    logs, and only reaches Discord/Slack if their webhook env vars are set
    — the same condition every other channel here already depends on."""
    _log(f"[observatory:brief]\n{text}")
    post_text_to_discord(text)
    post_text_to_slack(text)
