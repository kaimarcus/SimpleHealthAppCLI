"""
cli_inputs.py
-------------
CLI / input-gathering layer.

Every function here interacts only with the user (prompt / print).
Functions may receive plain values or ORM objects for display purposes but
make no database writes and contain no business logic.
They return plain data (dicts / scalars) or raise QuitRequested.
"""

from datetime import datetime, timezone

from constants import (
    KNOWN_OBSERVATIONS,
    KNOWN_ENCOUNTER_CLASSES,
    KNOWN_STATUSES,
)
from ui import (
    QuitRequested,
    prompt,
    prompt_until,
    _parse_float,
    _parse_iso_date,
    _parse_datetime,
    _find_patient,
    _find_encounter,
)


def cli_select_patient(session):
    """Prompt for a patient ID; return the matching Patient or raise QuitRequested."""
    return prompt_until(
        "Patient ID",
        lambda v: _find_patient(session, v),
        "No patient with that ID — enter a valid patient ID.",
    )


def cli_select_encounter(session):
    """Prompt for an encounter ID; return the matching Encounter or raise QuitRequested."""
    return prompt_until(
        "Encounter ID",
        lambda v: _find_encounter(session, v),
        "No encounter with that ID — enter a valid encounter ID.",
    )


def cli_patient_fields(defaults: "dict | None" = None) -> dict:
    """Prompt for patient demographic fields.

    *defaults* may contain: first, last, birth_date (date object), gender.
    When provided the user may press Enter to keep each current value.

    Returns dict {first, last, birth_date, gender} or raises QuitRequested.
    """
    d = defaults or {}
    first = prompt("First name", default=d.get("first", ""))
    last  = prompt("Last name",  default=d.get("last",  ""))
    birth_date = prompt_until(
        "Date of birth (YYYY-MM-DD)",
        _parse_iso_date,
        "Invalid date — use YYYY-MM-DD (e.g. 1990-06-15).",
        default=str(d["birth_date"]) if d.get("birth_date") else "",
    )
    gender = prompt_until(
        "Gender (male / female / other / unknown)",
        lambda v: v if v in ("male", "female", "other", "unknown") else None,
        "Must be one of: male, female, other, unknown.",
        default=d.get("gender", "unknown"),
    )
    return {"first": first, "last": last, "birth_date": birth_date, "gender": gender}


def cli_observation_inputs(patient) -> dict:
    """Display the observation-type menu and collect type and value.

    *patient* is used only for display (first_name, last_name).

    Returns dict {code, display, value, unit} or raises QuitRequested.
    """
    print(f"\n  Adding observation for {patient.first_name} {patient.last_name}")
    print("\n  Choose observation type:")
    for key, (code, display, unit) in KNOWN_OBSERVATIONS.items():
        print(f"    {key}. {display} ({unit})  [LOINC {code}]")

    code, display, unit = prompt_until(
        "Choice",
        lambda v: KNOWN_OBSERVATIONS.get(v),
        f"Enter a number 1–{len(KNOWN_OBSERVATIONS)}.",
    )
    value = prompt_until(
        f"Value ({unit})",
        _parse_float,
        "Value must be a number.",
    )
    return {"code": code, "display": display, "value": value, "unit": unit}


def cli_encounter_fields(defaults: "dict | None" = None) -> dict:
    """Prompt for encounter fields.

    *defaults* may contain: class_key, status_key, reason,
    start_date_str (formatted string), end_date_str.

    Returns dict {class_code, class_display, status, reason, start_date,
    end_date} or raises QuitRequested.
    """
    d = defaults or {}

    print("\n  Encounter class:")
    for key, (code, display) in KNOWN_ENCOUNTER_CLASSES.items():
        print(f"    {key}.  {display}  [{code}]")
    class_code, class_display = prompt_until(
        "Choice",
        lambda v: KNOWN_ENCOUNTER_CLASSES.get(v),
        f"Enter a number 1–{len(KNOWN_ENCOUNTER_CLASSES)}.",
        default=d.get("class_key", ""),
    )

    print("\n  Status:")
    for key, status in KNOWN_STATUSES.items():
        print(f"    {key}.  {status}")
    status = prompt_until(
        "Choice",
        lambda v: KNOWN_STATUSES.get(v),
        f"Enter a number 1–{len(KNOWN_STATUSES)}.",
        default=d.get("status_key", "3"),
    )

    reason = prompt(
        "Reason / chief complaint (optional)",
        default=d.get("reason", ""),
        required=False,
    )

    start_default = d.get(
        "start_date_str",
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
    )
    while True:
        start_raw = prompt(
            "Start date/time (YYYY-MM-DD or YYYY-MM-DDTHH:MM)",
            default=start_default,
        )
        try:
            start_date = _parse_datetime(start_raw) or datetime.now(timezone.utc)
            break
        except ValueError as exc:
            print(f"  ✗  {exc}")

    while True:
        end_raw = prompt(
            "End date/time (optional — leave blank if ongoing)",
            default=d.get("end_date_str", ""),
            required=False,
        )
        try:
            end_date = _parse_datetime(end_raw)
            break
        except ValueError as exc:
            print(f"  ✗  {exc}")

    return {
        "class_code":    class_code,
        "class_display": class_display,
        "status":        status,
        "reason":        reason or None,
        "start_date":    start_date,
        "end_date":      end_date,
    }


def cli_provider_fields(defaults: "dict | None" = None) -> dict:
    """Prompt for provider fields (name, specialty, NPI).

    Returns dict {first, last, specialty, npi} or raises QuitRequested.
    """
    d = defaults or {}
    first     = prompt("First name",           default=d.get("first",     ""))
    last      = prompt("Last name",            default=d.get("last",      ""))
    specialty = prompt("Specialty (optional)", default=d.get("specialty", ""), required=False)

    while True:
        npi = prompt(
            "NPI — 10-digit National Provider Identifier (optional)",
            default=d.get("npi", ""),
            required=False,
        )
        if not npi or (npi.isdigit() and len(npi) == 10):
            break
        print("  ✗  NPI must be exactly 10 digits (or leave blank).")

    return {
        "first":     first,
        "last":      last,
        "specialty": specialty or None,
        "npi":       npi or None,
    }


def cli_fhir_import() -> str:
    """Prompt for a FHIR Patient JSON string.

    Returns the string or raises QuitRequested.
    """
    print("  Paste a FHIR Patient JSON string (single line), then press Enter:")
    print("  (Type 'quit' to cancel.)")
    while True:
        fhir_json = input("  > ").strip()
        if fhir_json.lower() == "quit":
            raise QuitRequested()
        if fhir_json:
            return fhir_json
        print("  ✗  No input — paste the JSON or type 'quit' to cancel.")
