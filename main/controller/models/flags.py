"""Start flags: the /start option domain — defaults, parsing, validation,
and the flag -> stdin mapping that must stay in sync with main.py's prompts."""

# Names come straight from the bot component so the board always shows the
# real boost list.
from bot import BOOST_CHOICES

BOOST_COUNT = len(BOOST_CHOICES)
BOOST_NAMES = [name for name, _ in BOOST_CHOICES]

DEFAULT_FLAGS = {"fs": "n", "cr": "n", "rb": "n", "relic": "n", "7j": "n"}


def flags_to_answers(flags):
    answers = [flags["fs"], flags["cr"]]
    if flags["rb"] == "n":
        answers.append("n")
    else:
        answers.append("y")
        answers.append(flags["rb"])
    answers.append(flags["relic"])
    answers.append(flags["7j"])
    return answers


def flags_to_stdin(flags):
    return "\n".join(flags_to_answers(flags)) + "\n"


def parse_start_args(args_text, base):
    flags = dict(base)
    for token in args_text.split():
        if "=" not in token:
            raise ValueError(f"Invalid flag '{token}'. Use key=value, e.g. fs=y")
        key, _, value = token.partition("=")
        key = key.strip().lower()
        value = value.strip().lower()
        if key not in DEFAULT_FLAGS:
            raise ValueError(f"Unknown flag '{key}'. Valid flags: fs, cr, rb, relic, 7j")
        flags[key] = value
    return flags


def validate_flags(flags):
    errors = []
    for key in ("fs", "cr", "relic", "7j"):
        value = flags.get(key)
        if value not in ("y", "n"):
            errors.append(f"{key} must be y or n (got '{value}')")
    rb = str(flags.get("rb"))
    if rb != "n" and not (rb.isdigit() and 1 <= int(rb) <= BOOST_COUNT):
        errors.append(f"rb must be 'n' or 1-{BOOST_COUNT} (got '{rb}')")
    if errors:
        raise ValueError("; ".join(errors))
