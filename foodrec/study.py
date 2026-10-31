"""Study population: generated participant accounts, and login sessions.

    "study": {
      "login_required": true,
      "population": {"size": 40, "prefix": "p", "digits": 3, "roles": {"full": 50, "baseline": 50},
                     "shuffle": true, "seed": 7, "password_words": 2},
      "participants": [{"username": "p001", "password": "maple-river-42", "role": "full"}, ...]}

`generate()` turns the population settings into participants: role counts by percentage
(largest remainder, so they always add up to the size), roles spread over the usernames in
a seeded random order (or in blocks when shuffle is false), and a readable password per
account. Existing participants keep their password. The admin reviews and edits the list on
the Config page (or in the JSON) and saves it.

Participants log in with their username and password when login is required. Passwords are
stored as written in the config file, because the study team has to hand them out: treat the
file as confidential while it holds real credentials (see docs/configuration.md).
"""
import base64
import hashlib
import hmac
import json
import random
import re
import secrets
import time
from pathlib import Path

USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$")
WORDS = ("amber", "apple", "basil", "berry", "birch", "bloom", "brook", "cedar", "cherry", "clover", "cocoa", "coral",
         "cotton", "daisy", "delta", "ember", "fern", "fig", "forest", "garden", "ginger", "harbor", "hazel", "honey",
         "island", "ivy", "jasmine", "juniper", "kiwi", "lagoon", "lemon", "lilac", "lime", "linen", "lotus", "maple",
         "meadow", "mint", "moss", "nectar", "nutmeg", "oak", "olive", "orchid", "otter", "pebble", "pepper", "pine",
         "plum", "poppy", "quartz", "rain", "river", "robin", "saffron", "sage", "sierra", "spruce", "stone", "sunny",
         "thyme", "tulip", "valley", "velvet", "willow", "wren", "yarrow", "zephyr")


def password(rng=None, words=2):
    """A readable password such as "maple-river-42" (about 2^20 combinations for two words:
    fine for study access codes on a local server, not for anything sensitive)."""
    rng = rng or secrets.SystemRandom()
    return "-".join(rng.choice(WORDS) for _ in range(max(1, words))) + f"-{rng.randint(10, 99)}"


def allocate(size, shares):
    """Role counts for `size` users from percentages, largest remainder (sums to size)."""
    tot = sum(shares.values())
    if size <= 0 or tot <= 0:
        return {r: 0 for r in shares}
    exact = {r: size * v / tot for r, v in shares.items()}
    counts = {r: int(x) for r, x in exact.items()}
    left = size - sum(counts.values())
    for r in sorted(exact, key=lambda r: (-(exact[r] - counts[r]), list(shares).index(r)))[:left]:
        counts[r] += 1
    return counts


def generate(population, existing=None):
    """Participants for a population spec; keeps the password (and note) of existing usernames."""
    size = int(population.get("size") or 0)
    prefix = str(population.get("prefix") or "p")
    digits = int(population.get("digits") or max(2, len(str(size))))
    shares = {r: float(v) for r, v in (population.get("roles") or {}).items() if float(v) > 0}
    counts = allocate(size, shares)
    roles = [r for r, n in counts.items() for _ in range(n)]
    seed = population.get("seed", 7)
    if population.get("shuffle", True):
        random.Random(seed).shuffle(roles)
    old = {p["username"]: p for p in existing or []}
    rng = secrets.SystemRandom()
    out = []
    for i, role in enumerate(roles, 1):
        name = f"{prefix}{i:0{digits}d}"
        prev = old.get(name) or {}
        out.append({"username": name, "password": prev.get("password") or password(rng, population.get("password_words", 2)),
                    "role": role, **({"note": prev["note"]} if prev.get("note") else {})})
    return out, counts


def validate_study(st, roles):
    errs = []
    pop = st.get("population")
    if pop is not None:
        if not isinstance(pop, dict):
            errs.append("study.population: must be an object")
        else:
            size = pop.get("size", 0)
            if not isinstance(size, int) or size < 0 or size > 10000:
                errs.append("study.population.size: a whole number from 0 to 10000")
            prefix = pop.get("prefix", "p")
            if not isinstance(prefix, str) or not re.match(r"^[A-Za-z][A-Za-z0-9_-]{0,15}$", prefix):
                errs.append("study.population.prefix: letters, digits, _ or - (starting with a letter)")
            shares = pop.get("roles") or {}
            if not isinstance(shares, dict):
                errs.append("study.population.roles: an object {role: percent}")
            else:
                for r, v in shares.items():
                    if r not in roles:
                        errs.append(f"study.population.roles: role '{r}' is not defined")
                    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                        errs.append(f"study.population.roles.{r}: a percentage >= 0")
                tot = sum(v for v in shares.values() if isinstance(v, (int, float)) and not isinstance(v, bool))
                if shares and abs(tot - 100) > 0.01:
                    errs.append(f"study.population.roles: the percentages add up to {tot:g}, not 100")
    parts = st.get("participants") or []
    if not isinstance(parts, list):
        return errs + ["study.participants: must be a list"]
    seen = set()
    for i, p in enumerate(parts, 1):
        if not isinstance(p, dict):
            errs.append(f"study.participants[{i}]: must be an object")
            continue
        u = p.get("username")
        if not isinstance(u, str) or not USERNAME_RE.match(u):
            errs.append(f"study.participants[{i}]: username {u!r}: 1-40 letters, digits, _ . -")
        elif u in seen:
            errs.append(f"study.participants: duplicate username '{u}'")
        seen.add(u)
        pw = p.get("password")
        if not isinstance(pw, str) or len(pw) < 4:
            errs.append(f"study.participants[{i}] ({u}): password of at least 4 characters")
        if p.get("role") not in roles:
            errs.append(f"study.participants[{i}] ({u}): role '{p.get('role')}' is not defined")
    return errs


# ---- login sessions ---------------------------------------------------------------------------------
class Sessions:
    """Signed, expiring session tokens (HMAC-SHA256). The secret lives in the data folder, so
    sessions survive a restart and are invalidated by deleting the file."""

    def __init__(self, data_dir, ttl_hours=24 * 14):
        self.path = Path(data_dir) / ".session_secret"
        if not self.path.exists():
            self.path.write_text(secrets.token_hex(32))
            self.path.chmod(0o600)
        self.secret = self.path.read_text().strip().encode()
        self.ttl = ttl_hours * 3600

    def _sig(self, body):
        return hmac.new(self.secret, body.encode(), hashlib.sha256).hexdigest()[:32]

    def issue(self, username):
        body = base64.urlsafe_b64encode(json.dumps({"u": username, "exp": int(time.time() + self.ttl)}).encode()).decode()
        return f"{body}.{self._sig(body)}"

    def user(self, token):
        """The username of a valid token, else None."""
        try:
            body, sig = (token or "").rsplit(".", 1)
        except ValueError:
            return None
        if not hmac.compare_digest(sig, self._sig(body)):
            return None
        try:
            data = json.loads(base64.urlsafe_b64decode(body.encode()))
        except (ValueError, json.JSONDecodeError):
            return None
        return data["u"] if data.get("exp", 0) > time.time() else None


def check_password(cfg, username, pw):
    p = cfg.participant(username)
    return bool(p) and isinstance(pw, str) and hmac.compare_digest(str(p.get("password", "")).encode(), pw.encode())


def admin_link_token(data_dir):
    """A per-installation admin token for the Config page link that `foodrec config open`
    prints (readable only by the local user who owns the data folder)."""
    path = Path(data_dir) / ".admin_session"
    if not path.exists():
        path.write_text(secrets.token_urlsafe(24))
        path.chmod(0o600)
    return path.read_text().strip()
