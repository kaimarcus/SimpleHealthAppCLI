"""
ui.py
-----
UI helpers, shared utilities, and the QuitRequested exception.

Sections:
  - QuitRequested exception
  - Display helpers  : header, subheader, prompt, prompt_until, pause
  - Data parsers     : _parse_iso_date, _parse_datetime
  - Lookup helpers   : _find_patient, _find_encounter, _find_provider
  - Summary display  : _show_db_summary
"""

from datetime import date, datetime

from database import (
    Patient, Observation, Encounter, EncounterParticipant, Provider,
)


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class QuitRequested(Exception):
    """Raised when the user types 'quit' at any prompt to cancel the operation."""


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def header(title: str):
    print(f"\n{'─' * 52}")
    print(f"  {title}")
    print(f"{'─' * 52}")


def subheader(title: str):
    print(f"\n  ── {title} ──")


def prompt(msg: str, default: str = "", required: bool = True) -> str:
    """Display a prompt and return the user's trimmed response.

    Behaviour:
    - 'quit' / 'QUIT' (any case) → raise QuitRequested.
    - Non-blank input             → return it as-is.
    - Blank + *default* present   → return the default.
    - Blank + required=True       → reprompt with a hint.
    - Blank + required=False      → return "".
    """
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"  {msg}{suffix}: ").strip()
        if value.lower() == "quit":
            raise QuitRequested()
        if value:
            return value
        if default:
            return default
        if not required:
            return ""
        print("  ✗  This field is required — enter a value, or type 'quit' to cancel.")


def prompt_until(msg: str, validator, error: str, default: str = ""):
    """Reprompt until *validator(value)* returns something other than None.

    Args:
        msg:       Prompt text shown to the user.
        validator: Callable(str) → value | None.  None signals invalid input;
                   any other value is accepted and returned.
        error:     Message printed when validation fails.
        default:   Optional default shown in brackets; blank input uses it.
                   When absent a blank response triggers a required-field hint.

    Raises QuitRequested if the user types 'quit'.
    """
    while True:
        value = prompt(msg, default, required=not bool(default))
        result = validator(value)
        if result is not None:
            return result
        print(f"  ✗  {error}")


def _parse_float(v: str) -> "float | None":
    """Validator for prompt_until: return float or None."""
    try:
        return float(v)
    except ValueError:
        return None


def pause():
    input("\n  Press Enter to continue...")


# ---------------------------------------------------------------------------
# Data parsers
# ---------------------------------------------------------------------------

def _parse_iso_date(value: str) -> "date | None":
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _parse_datetime(raw: str) -> "datetime | None":
    """Try several common date/time formats.

    Returns None if *raw* is blank.
    Raises ValueError if *raw* is non-empty but cannot be parsed.
    """
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    raise ValueError(
        f"Cannot parse '{raw}' — use YYYY-MM-DD, YYYY-MM-DD HH:MM, or YYYY-MM-DDTHH:MM"
    )


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

def _find_patient(session, pid_str: str) -> "Patient | None":
    """Look up a Patient by ID string; return None on any failure (no side effects)."""
    try:
        return session.get(Patient, int(pid_str))
    except (ValueError, TypeError):
        return None


def _find_encounter(session, enc_str: str) -> "Encounter | None":
    try:
        return session.get(Encounter, int(enc_str))
    except (ValueError, TypeError):
        return None


def _find_provider(session, prov_str: str) -> "Provider | None":
    try:
        return session.get(Provider, int(prov_str))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Summary display
# ---------------------------------------------------------------------------

def _show_db_summary(session):
    """Print a concise summary of current database row counts."""
    n_patients  = session.query(Patient).count()
    n_providers = session.query(Provider).count()
    n_enc       = session.query(Encounter).count()
    n_parts     = session.query(EncounterParticipant).count()
    n_obs       = session.query(Observation).count()
    print(f"\n  ┌──────────────────────────────────────────┐")
    print(f"  │  Database state                         │")
    print(f"  ├──────────────────────────────────────────┤")
    print(f"  │  Patients               : {n_patients:<14} │")
    print(f"  │  Providers              : {n_providers:<14} │")
    print(f"  │  Encounters             : {n_enc:<14} │")
    print(f"  │  Encounter participants : {n_parts:<14} │")
    print(f"  │  Observations           : {n_obs:<14} │")
    print(f"  └─────────────────────────────────────────┘")
