"""
app.py
------
Command-line health app demonstrating Python + SQLite + FHIR R4.

Run with:
    python app.py

Main menu → sub-menus:
  1. Patients      → Add, Edit, List
  2. Observations  → Add, View
  3. Encounters    → Add (with inline multi-provider assignment), Edit, View, History
  4. Providers     → Add, Edit, List
  5. FHIR          → Export Bundle, Import Patient
  6. Seed Demo Data
  0. Exit

Typing 'quit' (case-insensitive) at any input prompt cancels the current
operation and returns to the previous menu.  Typing 'quit' at the main-menu
prompt exits the program.  A partially entered add or update is never saved
when the user quits mid-way.

Code is organised into four sections:
  1. Reference data   — lookup tables used for menus and validation
  2. UI helpers       — prompt(), header(), and related display utilities
  3. CLI layer        — input-gathering functions (prompt / display only)
  4. Action layer     — database operations (no user prompting)
  5. Menu layer       — navigation loops that tie CLI and actions together
"""

import json
from datetime import date, datetime, timezone

from database import (
    init_db, get_session,
    Patient, Observation, Encounter, EncounterParticipant, Provider,
    EncounterHistory, snapshot_encounter,
)
from fhir_utils import (
    build_patient_bundle,
    bundle_to_json,
    fhir_patient_to_dict,
)

# ---------------------------------------------------------------------------
# Reference data — codes used when prompting the user
# ---------------------------------------------------------------------------

KNOWN_OBSERVATIONS = {
    "1": ("8867-4",  "Heart rate",           "/min"),
    "2": ("8310-5",  "Body temperature",     "Cel"),
    "3": ("8480-6",  "Systolic BP",          "mm[Hg]"),
    "4": ("8462-4",  "Diastolic BP",         "mm[Hg]"),
    "5": ("29463-7", "Body weight",          "kg"),
    "6": ("8302-2",  "Body height",          "cm"),
}

KNOWN_ENCOUNTER_CLASSES = {
    "1": ("AMB",    "Ambulatory encounter"),
    "2": ("IMP",    "Inpatient encounter"),
    "3": ("EMER",   "Emergency encounter"),
    "4": ("OBSENC", "Observation encounter"),
}

KNOWN_STATUSES = {
    "1": "planned",
    "2": "in-progress",
    "3": "finished",
    "4": "cancelled",
}

KNOWN_ROLES = {
    "1": "attending",
    "2": "consultant",
    "3": "referring",
    "4": "admitting",
}


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class QuitRequested(Exception):
    """Raised when the user types 'quit' at any prompt to cancel the operation."""


# ---------------------------------------------------------------------------
# UI helpers
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
# Data parsers / shared utilities
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


# ===========================================================================
# CLI / Input-gathering layer
#
# Every function in this section interacts only with the user (prompt / print).
# Functions may receive plain values or ORM objects for display purposes but
# make no database writes and contain no business logic.
# They return plain data (dicts / scalars) or raise QuitRequested.
# ===========================================================================

def cli_select_patient(session) -> "Patient":
    """Prompt for a patient ID; return the matching Patient or raise QuitRequested."""
    return prompt_until(
        "Patient ID",
        lambda v: _find_patient(session, v),
        "No patient with that ID — enter a valid patient ID.",
    )


def cli_select_encounter(session) -> "Encounter":
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
    first     = prompt("First name",         default=d.get("first",     ""))
    last      = prompt("Last name",          default=d.get("last",      ""))
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


# ===========================================================================
# Action layer  (DB / business-logic operations — no user prompting)
#
# Functions in this section perform database operations.  They accept plain
# data values or ORM objects, carry out their operation, and print results.
# They never call prompt() or input().
# ===========================================================================

# ── Patients ─────────────────────────────────────────────────────────────────

def action_save_patient(data: dict):
    with get_session() as session:
        patient = Patient(
            first_name=data["first"],
            last_name=data["last"],
            birth_date=data["birth_date"],
            gender=data["gender"],
        )
        session.add(patient)
        session.commit()
        print(f"\n  Patient saved  (id={patient.id})")


def action_update_patient(session, patient, data: dict):
    patient.first_name = data["first"]
    patient.last_name  = data["last"]
    patient.birth_date = data["birth_date"]
    patient.gender     = data["gender"]
    session.commit()
    print(f"\n  Patient #{patient.id} updated.")


def action_list_patients():
    with get_session() as session:
        patients = session.query(Patient).order_by(Patient.id).all()
        if not patients:
            print("  No patients found.")
            return
        print(f"  {'ID':<5} {'Name':<30} {'DOB':<12} {'Gender'}")
        print(f"  {'──':<5} {'────':<30} {'───':<12} {'──────'}")
        for p in patients:
            name = f"{p.first_name} {p.last_name}"
            print(f"  {p.id:<5} {name:<30} {str(p.birth_date):<12} {p.gender}")


# ── Observations ─────────────────────────────────────────────────────────────

def action_add_observation(session, patient_id: int, data: dict):
    obs = Observation(
        patient_id=patient_id,
        code=data["code"],
        display=data["display"],
        value=data["value"],
        unit=data["unit"],
        recorded_at=datetime.now(timezone.utc),
    )
    session.add(obs)
    session.commit()
    print(f"\n  Observation saved  (id={obs.id})")


def action_view_observations(session, patient):
    obs_list = (
        session.query(Observation)
        .filter(Observation.patient_id == patient.id)
        .order_by(Observation.recorded_at)
        .all()
    )
    print(f"\n  Observations for {patient.first_name} {patient.last_name}:\n")
    if not obs_list:
        print("  No observations recorded yet.")
        return
    print(f"  {'ID':<5} {'Date/Time':<20} {'Measurement':<25} {'Value':<10} {'Unit'}")
    print(f"  {'──':<5} {'─────────':<20} {'───────────':<25} {'─────':<10} {'────'}")
    for o in obs_list:
        ts = o.recorded_at.strftime("%Y-%m-%d %H:%M")
        print(f"  {o.id:<5} {ts:<20} {o.display:<25} {o.value:<10} {o.unit}")


# ── Encounters ───────────────────────────────────────────────────────────────

def action_create_encounter(session, patient_id: int, fields: dict) -> "Encounter":
    """Create and flush a new Encounter (not yet committed).  Returns the object."""
    encounter = Encounter(
        patient_id=patient_id,
        class_code=fields["class_code"],
        class_display=fields["class_display"],
        status=fields["status"],
        reason=fields["reason"],
        start_date=fields["start_date"],
        end_date=fields["end_date"],
    )
    session.add(encounter)
    session.flush()
    print(f"\n  Encounter created  (id={encounter.id})")
    return encounter


def action_update_encounter(session, encounter, fields: dict, change_reason: str):
    """Snapshot the current state, apply *fields*, and flush (not yet committed)."""
    snapshot_encounter(session, encounter, change_reason=change_reason or None)
    encounter.class_code    = fields["class_code"]
    encounter.class_display = fields["class_display"]
    encounter.status        = fields["status"]
    encounter.reason        = fields["reason"]
    encounter.start_date    = fields["start_date"]
    encounter.end_date      = fields["end_date"]
    session.flush()


def action_view_encounters(session, patient):
    encounters = (
        session.query(Encounter)
        .filter(Encounter.patient_id == patient.id)
        .order_by(Encounter.start_date)
        .all()
    )
    print(f"\n  Encounters for {patient.first_name} {patient.last_name}:\n")
    if not encounters:
        print("  No encounters recorded yet.")
        return
    for enc in encounters:
        start = enc.start_date.strftime("%Y-%m-%d %H:%M")
        end   = enc.end_date.strftime("%Y-%m-%d %H:%M") if enc.end_date else "ongoing"
        reason_str = f"  Reason: {enc.reason}" if enc.reason else ""
        print(f"  ID {enc.id}  |  {enc.class_display}  |  Status: {enc.status}")
        print(f"           Start: {start}   End: {end}{reason_str}")
        if enc.participants:
            for p in enc.participants:
                name = f"{p.provider.first_name} {p.provider.last_name}"
                spec = f" ({p.provider.specialty})" if p.provider.specialty else ""
                print(f"           └─ {p.role.capitalize()}: {name}{spec} "
                      f"[Provider #{p.provider_id}]")
        else:
            print("           └─ (no providers assigned)")
        print()


def action_view_encounter_history(session, encounter):
    history = (
        session.query(EncounterHistory)
        .filter(EncounterHistory.encounter_id == encounter.id)
        .order_by(EncounterHistory.version)
        .all()
    )
    patient = session.get(Patient, encounter.patient_id)
    print(f"\n  Audit trail for Encounter #{encounter.id}  —  "
          f"{encounter.class_display}  "
          f"({patient.first_name} {patient.last_name})\n")
    if not history:
        print("  No edits recorded — this encounter has never been modified.")
        return
    print(f"  {'Ver':<5} {'Changed at':<18} {'Changed by':<20} {'Status':<14} "
          f"{'Class':<10} {'Reason'}")
    print("  " + "─" * 85)
    for h in history:
        changed_at = h.changed_at.strftime("%Y-%m-%d %H:%M")
        changed_by = h.changed_by or "—"
        reason     = h.reason or "—"
        note       = f"  ↳ Note: {h.change_reason}" if h.change_reason else ""
        print(f"  v{h.version:<4} {changed_at:<18} {changed_by:<20} "
              f"{h.status:<14} {h.class_code:<10} {reason}{note}")
    print()
    print("  (Current values are shown in 'View Encounters')")


# ── Encounter participants ────────────────────────────────────────────────────

def action_add_participant(session, encounter_id: int, provider_id: int, role: str):
    ep = EncounterParticipant(
        encounter_id=encounter_id,
        provider_id=provider_id,
        role=role,
    )
    session.add(ep)
    session.flush()
    provider = session.get(Provider, provider_id)
    print(f"\n  Added {provider.first_name} {provider.last_name} "
          f"as {role}  (participant id={ep.id})")


def action_remove_participant(session, ep):
    name         = f"{ep.provider.first_name} {ep.provider.last_name}"
    encounter_id = ep.encounter_id
    session.delete(ep)
    session.flush()
    print(f"  Removed {name} from encounter #{encounter_id}.")


# ── Providers ─────────────────────────────────────────────────────────────────

def action_save_provider(data: dict):
    with get_session() as session:
        provider = Provider(
            first_name=data["first"],
            last_name=data["last"],
            specialty=data["specialty"],
            npi=data["npi"],
        )
        session.add(provider)
        session.commit()
        print(f"\n  Provider saved  (id={provider.id})")


def action_update_provider(session, provider, data: dict):
    provider.first_name = data["first"]
    provider.last_name  = data["last"]
    provider.specialty  = data["specialty"]
    provider.npi        = data["npi"]
    session.commit()
    print(f"\n  Provider #{provider.id} updated.")


def action_list_providers():
    with get_session() as session:
        providers = session.query(Provider).order_by(Provider.id).all()
        if not providers:
            print("  No providers found.")
            return
        print(f"  {'ID':<5} {'Name':<28} {'Specialty':<25} {'NPI'}")
        print(f"  {'──':<5} {'────':<28} {'─────────':<25} {'───'}")
        for pv in providers:
            name = f"{pv.first_name} {pv.last_name}"
            print(f"  {pv.id:<5} {name:<28} {pv.specialty or '—':<25} {pv.npi or '—'}")


# ── FHIR ─────────────────────────────────────────────────────────────────────

def action_export_fhir_bundle(session, patient):
    """Build and write a FHIR Bundle for *patient* to a JSON file."""
    _ = patient.observations
    for enc in patient.encounters:
        for p in enc.participants:
            _ = p.provider

    bundle = build_patient_bundle(patient)
    output = bundle_to_json(bundle)

    filename = f"patient_{patient.id}_bundle.json"
    with open(filename, "w") as f:
        f.write(output)

    resource_counts = {}
    for entry in bundle["entry"]:
        rt = entry["resource"]["resourceType"]
        resource_counts[rt] = resource_counts.get(rt, 0) + 1

    print(f"\n  FHIR Bundle written to: {filename}")
    print(f"  Resources included:")
    for rt, count in resource_counts.items():
        print(f"    {count}× {rt}")
    print(f"\n  Preview (first 40 lines):\n")
    for i, line in enumerate(output.splitlines()):
        if i >= 40:
            print("  ... (truncated — open the file to see the rest)")
            break
        print(f"  {line}")


def action_import_fhir_patient(fhir_json: str):
    try:
        data = fhir_patient_to_dict(fhir_json)
    except Exception as e:
        print(f"  ✗  Could not parse FHIR Patient: {e}")
        return

    try:
        birth_date = date.fromisoformat(data["birth_date"])
    except (TypeError, ValueError):
        print(f"  ✗  Invalid birthDate in FHIR resource: {data.get('birth_date')}")
        return

    with get_session() as session:
        patient = Patient(
            first_name=data["first_name"],
            last_name=data["last_name"],
            birth_date=birth_date,
            gender=data["gender"],
        )
        session.add(patient)
        session.commit()
        print(f"\n  Patient imported and saved  (id={patient.id})")


# ── Seed demo data ────────────────────────────────────────────────────────────

def action_seed_demo_data():
    """Populate the database with demo patients, providers, observations, and encounters."""
    with get_session() as session:
        existing = session.query(Patient).count()
        if existing > 0:
            print(f"  Database already has {existing} patient(s) — skipping seed.")
            _show_db_summary(session)
            return

        alice = Patient(
            first_name="Alice", last_name="Walker",
            birth_date=date(1985, 6, 20), gender="female",
        )
        bob = Patient(
            first_name="Bob", last_name="Nguyen",
            birth_date=date(1973, 11, 3), gender="male",
        )
        session.add_all([alice, bob])
        session.flush()

        session.add_all([
            Observation(patient_id=alice.id, code="8867-4",
                        display="Heart rate",       value=72.0,  unit="/min",
                        recorded_at=datetime(2025, 1, 10, 9, 0)),
            Observation(patient_id=alice.id, code="8480-6",
                        display="Systolic BP",      value=118.0, unit="mm[Hg]",
                        recorded_at=datetime(2025, 1, 10, 9, 1)),
            Observation(patient_id=alice.id, code="8462-4",
                        display="Diastolic BP",     value=76.0,  unit="mm[Hg]",
                        recorded_at=datetime(2025, 1, 10, 9, 1)),
            Observation(patient_id=alice.id, code="29463-7",
                        display="Body weight",      value=62.5,  unit="kg",
                        recorded_at=datetime(2025, 1, 10, 9, 2)),
            Observation(patient_id=bob.id,   code="8310-5",
                        display="Body temperature", value=37.1,  unit="Cel",
                        recorded_at=datetime(2025, 2, 5, 14, 30)),
            Observation(patient_id=bob.id,   code="8302-2",
                        display="Body height",      value=178.0, unit="cm",
                        recorded_at=datetime(2025, 2, 5, 14, 31)),
            Observation(patient_id=bob.id,   code="29463-7",
                        display="Body weight",      value=84.0,  unit="kg",
                        recorded_at=datetime(2025, 2, 5, 14, 32)),
        ])

        dr_chen = Provider(
            first_name="Linda",  last_name="Chen",
            specialty="Internal Medicine", npi="1234567890",
        )
        dr_patel = Provider(
            first_name="Rajesh", last_name="Patel",
            specialty="Cardiology",        npi="0987654321",
        )
        session.add_all([dr_chen, dr_patel])
        session.flush()

        enc1 = Encounter(
            patient_id=alice.id, class_code="AMB",
            class_display="Ambulatory encounter", status="finished",
            reason="Annual wellness visit",
            start_date=datetime(2025, 1, 10, 9, 0),
            end_date=datetime(2025, 1, 10, 10, 0),
        )
        enc2 = Encounter(
            patient_id=bob.id, class_code="AMB",
            class_display="Ambulatory encounter", status="finished",
            reason="Follow-up for hypertension",
            start_date=datetime(2025, 2, 5, 14, 0),
            end_date=datetime(2025, 2, 5, 14, 45),
        )
        enc3 = Encounter(
            patient_id=bob.id, class_code="EMER",
            class_display="Emergency encounter", status="finished",
            reason="Chest pain",
            start_date=datetime(2025, 3, 12, 22, 15),
            end_date=datetime(2025, 3, 13, 2, 30),
        )
        session.add_all([enc1, enc2, enc3])
        session.flush()

        session.add_all([
            EncounterParticipant(encounter_id=enc1.id,
                                 provider_id=dr_chen.id,  role="attending"),
            EncounterParticipant(encounter_id=enc2.id,
                                 provider_id=dr_chen.id,  role="attending"),
            EncounterParticipant(encounter_id=enc3.id,
                                 provider_id=dr_patel.id, role="attending"),
            EncounterParticipant(encounter_id=enc3.id,
                                 provider_id=dr_chen.id,  role="referring"),
        ])

        session.commit()

        print("  Demo data seeded successfully.\n")
        _show_db_summary(session)

        print("\n  Patients:")
        print(f"    #{alice.id}  Alice Walker  (DOB: {alice.birth_date}, female)")
        print(f"    #{bob.id}  Bob Nguyen    (DOB: {bob.birth_date}, male)")
        print("\n  Providers:")
        print(f"    #{dr_chen.id}  Dr. Linda Chen   — Internal Medicine  (NPI: 1234567890)")
        print(f"    #{dr_patel.id}  Dr. Rajesh Patel — Cardiology         (NPI: 0987654321)")
        print("\n  Encounters:")
        print(f"    #{enc1.id}  Alice  — Annual wellness visit      (AMB / finished)")
        print(f"         Attending: Dr. Linda Chen")
        print(f"    #{enc2.id}  Bob    — Follow-up for hypertension  (AMB / finished)")
        print(f"         Attending: Dr. Linda Chen")
        print(f"    #{enc3.id}  Bob    — Chest pain                  (EMER / finished)")
        print(f"         Attending: Dr. Rajesh Patel  |  Referring: Dr. Linda Chen")
        print("\n  Observations: 4 for Alice, 3 for Bob")


# ===========================================================================
# Menu / Navigation layer
#
# This section contains all control-flow and user-navigation logic.
#
# _manage_encounter_providers — interactive provider sub-loop for encounters.
# do_*  — thin orchestrators: call CLI functions for input, action functions
#         to persist, and catch QuitRequested so cancellation is clean.
# menu_* / _submenu / main — navigation loops.
# ===========================================================================

# ── Encounter provider sub-loop ───────────────────────────────────────────────

def _manage_encounter_providers(session, encounter):
    """Interactive sub-loop for adding / removing providers on an encounter.

    QuitRequested propagates to the caller, which rolls back the session and
    prints a cancellation message — nothing is saved.
    """
    while True:
        subheader(f"Providers — Encounter #{encounter.id}")

        participants = (
            session.query(EncounterParticipant)
            .filter_by(encounter_id=encounter.id)
            .all()
        )
        if participants:
            print(f"  {'Part.ID':<9} {'Role':<12} {'Provider':<28} {'Specialty'}")
            print(f"  {'───────':<9} {'────':<12} {'────────':<28} {'─────────'}")
            for ep in participants:
                name = f"{ep.provider.first_name} {ep.provider.last_name}"
                spec = ep.provider.specialty or "—"
                print(f"  {ep.id:<9} {ep.role.capitalize():<12} {name:<28} {spec}")
        else:
            print("  (no providers assigned yet)")

        print()
        print("    a.  Add provider")
        print("    r.  Remove provider")
        print("    d.  Done (save and return)")
        sub = prompt("Action", default="d").lower()

        if sub == "d":
            break

        elif sub == "a":
            providers = session.query(Provider).order_by(Provider.id).all()
            if not providers:
                print("\n  ✗  No providers in the database — add providers first.")
                continue

            print()
            print(f"  {'ID':<5} {'Name':<28} {'Specialty':<25} {'NPI'}")
            print(f"  {'──':<5} {'────':<28} {'─────────':<25} {'───'}")
            for pv in providers:
                name = f"{pv.first_name} {pv.last_name}"
                print(f"  {pv.id:<5} {name:<28} {pv.specialty or '—':<25} {pv.npi or '—'}")

            provider = prompt_until(
                "Provider ID to add",
                lambda v: _find_provider(session, v),
                "No provider with that ID.",
            )

            existing = (
                session.query(EncounterParticipant)
                .filter_by(encounter_id=encounter.id, provider_id=provider.id)
                .first()
            )
            if existing:
                print(f"  ✗  {provider.first_name} {provider.last_name} is already "
                      f"linked to this encounter (role: {existing.role}).")
                continue

            print("\n  Participant role:")
            for key, role_name in KNOWN_ROLES.items():
                print(f"    {key}.  {role_name}")
            role = prompt_until(
                "Choice",
                lambda v: KNOWN_ROLES.get(v),
                "Enter a number 1–4.",
                default="1",
            )
            action_add_participant(session, encounter.id, provider.id, role)

        elif sub == "r":
            ep_str = prompt("Participant ID to remove")
            try:
                ep_id = int(ep_str)
            except ValueError:
                print("  ✗  Must be a number.")
                continue
            ep = session.get(EncounterParticipant, ep_id)
            if not ep or ep.encounter_id != encounter.id:
                print("  ✗  Participant not found on this encounter.")
                continue
            action_remove_participant(session, ep)

        else:
            print("  ✗  Enter a, r, or d.")


# ── Patient operations ────────────────────────────────────────────────────────

def do_add_patient():
    header("Add Patient")
    try:
        data = cli_patient_fields()
        action_save_patient(data)
    except QuitRequested:
        print("  Cancelled.")


def do_edit_patient():
    header("Edit Patient")
    try:
        with get_session() as session:
            patient = cli_select_patient(session)
            print(f"\n  Editing: {patient.first_name} {patient.last_name}  "
                  f"(DOB: {patient.birth_date}  Gender: {patient.gender})")
            print("  Press Enter to keep the current value.\n")
            data = cli_patient_fields(defaults={
                "first":      patient.first_name,
                "last":       patient.last_name,
                "birth_date": patient.birth_date,
                "gender":     patient.gender,
            })
            action_update_patient(session, patient, data)
    except QuitRequested:
        print("  Cancelled — no changes saved.")


def do_list_patients():
    header("Patients")
    action_list_patients()


# ── Observation operations ────────────────────────────────────────────────────

def do_add_observation():
    header("Add Observation")
    try:
        with get_session() as session:
            patient = cli_select_patient(session)
            data = cli_observation_inputs(patient)
            action_add_observation(session, patient.id, data)
    except QuitRequested:
        print("  Cancelled.")


def do_view_observations():
    header("View Observations")
    try:
        with get_session() as session:
            patient = cli_select_patient(session)
            action_view_observations(session, patient)
    except QuitRequested:
        print("  Cancelled.")


# ── Encounter operations ──────────────────────────────────────────────────────

def do_add_encounter():
    header("Add Encounter")
    try:
        with get_session() as session:
            patient = cli_select_patient(session)
            print(f"\n  Adding encounter for {patient.first_name} {patient.last_name}")
            fields = cli_encounter_fields()
            encounter = action_create_encounter(session, patient.id, fields)
            if prompt("\n  Assign providers now? (y/n)", default="y").lower() == "y":
                _manage_encounter_providers(session, encounter)
            session.commit()
            print(f"\n  Encounter #{encounter.id} saved.")
    except QuitRequested:
        print("  Cancelled — encounter not saved.")


def do_edit_encounter():
    header("Edit Encounter")
    try:
        with get_session() as session:
            encounter = cli_select_encounter(session)
            patient = session.get(Patient, encounter.patient_id)
            print(f"\n  Editing Encounter #{encounter.id}  —  {encounter.class_display}"
                  f"  ({patient.first_name} {patient.last_name})")
            print("  Press Enter to keep the current value.\n")

            change_reason = prompt(
                "Reason for this edit (optional, stored in audit log)",
                required=False,
            )

            defaults = {
                "class_key": next(
                    (k for k, (c, _) in KNOWN_ENCOUNTER_CLASSES.items()
                     if c == encounter.class_code), "1"
                ),
                "status_key": next(
                    (k for k, s in KNOWN_STATUSES.items() if s == encounter.status), "3"
                ),
                "reason":         encounter.reason or "",
                "start_date_str": encounter.start_date.strftime("%Y-%m-%d %H:%M"),
                "end_date_str": (
                    encounter.end_date.strftime("%Y-%m-%d %H:%M")
                    if encounter.end_date else ""
                ),
            }
            fields = cli_encounter_fields(defaults=defaults)
            action_update_encounter(session, encounter, fields, change_reason)

            print("\n  Manage providers for this encounter:")
            _manage_encounter_providers(session, encounter)

            session.commit()
            print(f"\n  Encounter #{encounter.id} updated.")
    except QuitRequested:
        print("  Cancelled — no changes saved.")


def do_view_encounters():
    header("View Encounters")
    try:
        with get_session() as session:
            patient = cli_select_patient(session)
            action_view_encounters(session, patient)
    except QuitRequested:
        print("  Cancelled.")


def do_view_encounter_history():
    header("Encounter Edit History")
    try:
        with get_session() as session:
            encounter = cli_select_encounter(session)
            action_view_encounter_history(session, encounter)
    except QuitRequested:
        print("  Cancelled.")


# ── Provider operations ───────────────────────────────────────────────────────

def do_add_provider():
    header("Add Provider")
    try:
        data = cli_provider_fields()
        action_save_provider(data)
    except QuitRequested:
        print("  Cancelled.")


def do_edit_provider():
    header("Edit Provider")
    try:
        with get_session() as session:
            provider = prompt_until(
                "Provider ID to edit",
                lambda v: _find_provider(session, v),
                "No provider with that ID.",
            )
            print(f"\n  Editing: {provider.first_name} {provider.last_name}  "
                  f"Specialty: {provider.specialty or '—'}  NPI: {provider.npi or '—'}")
            print("  Press Enter to keep the current value.\n")
            data = cli_provider_fields(defaults={
                "first":     provider.first_name,
                "last":      provider.last_name,
                "specialty": provider.specialty or "",
                "npi":       provider.npi or "",
            })
            action_update_provider(session, provider, data)
    except QuitRequested:
        print("  Cancelled — no changes saved.")


def do_list_providers():
    header("Providers")
    action_list_providers()


# ── FHIR operations ───────────────────────────────────────────────────────────

def do_export_fhir_bundle():
    header("Export FHIR Bundle")
    try:
        with get_session() as session:
            patient = cli_select_patient(session)
            action_export_fhir_bundle(session, patient)
    except QuitRequested:
        print("  Cancelled.")


def do_import_fhir_patient():
    header("Import FHIR Patient")
    try:
        fhir_json = cli_fhir_import()
        action_import_fhir_patient(fhir_json)
    except QuitRequested:
        print("  Cancelled.")


# ── Seed demo data ────────────────────────────────────────────────────────────

def do_seed_demo_data():
    header("Seed Demo Data")
    action_seed_demo_data()


# ── Sub-menus ─────────────────────────────────────────────────────────────────

def _submenu(title: str, options: dict):
    """Generic sub-menu loop.

    *options*: ordered dict mapping key str → (label str, callable).
    Typing '0' or 'quit' at the menu choice returns to the previous menu.
    """
    while True:
        print(f"\n  ┌──────────────────────────────────────────────┐")
        print(f"  │  {title:<44}│")
        print(f"  ├──────────────────────────────────────────────┤")
        for key, (label, _) in options.items():
            row = f"{key}.  {label}"
            print(f"  │  {row:<44}│")
        print(f"  │  {'0.  Back':<44}│")
        print(f"  └──────────────────────────────────────────────┘")

        choice = input("\n  Choice: ").strip()

        if choice == "0" or choice.lower() == "quit":
            break
        elif choice in options:
            try:
                options[choice][1]()
            except QuitRequested:
                print("  Cancelled.")
            except Exception as e:
                print(f"\n  ✗  Unexpected error: {e}")
            pause()
        else:
            print("  ✗  Invalid choice.")


def menu_patients():
    _submenu("Patients", {
        "1": ("Add patient",   do_add_patient),
        "2": ("Edit patient",  do_edit_patient),
        "3": ("List patients", do_list_patients),
    })


def menu_observations():
    _submenu("Observations", {
        "1": ("Add observation",   do_add_observation),
        "2": ("View observations", do_view_observations),
    })


def menu_encounters():
    _submenu("Encounters", {
        "1": ("Add encounter",     do_add_encounter),
        "2": ("Edit encounter",    do_edit_encounter),
        "3": ("View encounters",   do_view_encounters),
        "4": ("View edit history", do_view_encounter_history),
    })


def menu_providers():
    _submenu("Providers", {
        "1": ("Add provider",   do_add_provider),
        "2": ("Edit provider",  do_edit_provider),
        "3": ("List providers", do_list_providers),
    })


def menu_fhir():
    _submenu("FHIR", {
        "1": ("Export FHIR Bundle (JSON)",  do_export_fhir_bundle),
        "2": ("Import FHIR Patient (JSON)", do_import_fhir_patient),
    })


# ── Main menu ─────────────────────────────────────────────────────────────────

MAIN_MENU = """
  ┌──────────────────────────────────────────────┐
  │        Python · SQLite · FHIR R4             │
  │           Simple Health App                  │
  ├──────────────────────────────────────────────┤
  │  1.  Patients                                │
  │  2.  Observations                            │
  │  3.  Encounters                              │
  │  4.  Providers                               │
  │  5.  FHIR                                    │
  │  6.  Seed Demo Data                          │
  │  0.  Exit                                    │
  └──────────────────────────────────────────────┘"""

MAIN_ACTIONS = {
    "1": menu_patients,
    "2": menu_observations,
    "3": menu_encounters,
    "4": menu_providers,
    "5": menu_fhir,
    "6": do_seed_demo_data,
}


def main():
    init_db()
    print("\n  Database ready  (health.db)")

    while True:
        print(MAIN_MENU)
        choice = input("\n  Choice: ").strip()

        if choice == "0" or choice.lower() == "quit":
            print("\n  Bye!\n")
            break
        elif choice in MAIN_ACTIONS:
            try:
                MAIN_ACTIONS[choice]()
            except Exception as e:
                print(f"\n  ✗  Unexpected error: {e}")
            if choice == "6":
                pause()
        else:
            print("  ✗  Invalid choice.")


if __name__ == "__main__":
    main()
