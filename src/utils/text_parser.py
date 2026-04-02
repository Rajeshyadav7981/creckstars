"""
Text parser for @mentions and #hashtags in community posts/comments.
"""
import re

MENTION_RE = re.compile(r'@([a-z0-9][a-z0-9._]{1,28}[a-z0-9])', re.IGNORECASE)
HASHTAG_RE = re.compile(r'#([a-zA-Z0-9_]{1,50})')
USERNAME_RE = re.compile(r'^[a-z0-9][a-z0-9_]{1,28}[a-z0-9]$')

RESERVED_USERNAMES = frozenset({
    'admin', 'support', 'creckstars', 'official', 'help', 'mod',
    'moderator', 'system', 'api', 'www', 'app', 'root', 'null',
})


def extract_mentions(text: str) -> list[str]:
    """Extract @username handles from text, returned lowercase and deduplicated."""
    return list(set(m.lower() for m in MENTION_RE.findall(text)))


def extract_hashtags(text: str) -> list[str]:
    """Extract #hashtag tags from text, returned lowercase and deduplicated."""
    return list(set(h.lower() for h in HASHTAG_RE.findall(text)))


def validate_username(username: str) -> tuple[bool, str]:
    """Validate a username. Returns (is_valid, error_message).
    Rules: 3-30 chars, lowercase letters + numbers + underscores only."""
    if not username:
        return False, "Username is required"
    username = username.lower()
    if len(username) < 3:
        return False, "Username must be at least 3 characters"
    if len(username) > 30:
        return False, "Username must be under 30 characters"
    if not re.match(r'^[a-z0-9_]+$', username):
        return False, "Only lowercase letters, numbers, and underscores allowed"
    if username.startswith('_') or username.endswith('_'):
        return False, "Cannot start or end with underscore"
    if '__' in username:
        return False, "No consecutive underscores allowed"
    if username in RESERVED_USERNAMES:
        return False, "This username is reserved"
    return True, ""


def generate_username(first_name: str, user_id: int) -> str:
    """Generate a default username from first name + user ID."""
    base = re.sub(r'[^a-z0-9]', '', (first_name or 'user').lower())
    if len(base) < 2:
        base = 'user'
    return f"{base}_{user_id}"
