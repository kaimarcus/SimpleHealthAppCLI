"""
app.py
------
Command-line health app demonstrating Python + SQLite + FHIR R4.

Run with:
    python app.py

Main menu → sub-menus:
  1. Patients      → Add, Edit, List
  2. Observations  → Add, View
  3. Encounters    → Add (with inline multi-provider assignment), Edit, View
  4. Providers     → Add, Edit, List
  5. FHIR          → Export Bundle, Import Patient
  6. Seed Demo Data
  0. Exit
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

# LOINC codes for common vital-sign observations
KNOWN_OBSERVATIONS = {
    "1": ("8867-4",  "Heart rate",           "/min"),
    "2": ("8310-5",  "Body temperature",     "Cel"),
    "3": ("8480-6",  "Systolic BP",          "mm[Hg]"),
    "4": ("8462-4",  "Diastolic BP",         "mm[Hg]"),
    "5": ("29463-7", "Body weight",          "kg"),
    "6": ("8302-2",  "Body height",          "cm"),
}

# HL7 v3 ActCode values for the Encounter class
KNOWN_ENCOUNTER_CLASSES = {
    "1": ("AMB",    "Ambulatory encounter"),
    "2": ("IMP",    "Inpatient encounter"),
    "3": ("EMER",   "Emergency encounter"),
    "4": ("OBSENC", "Observation encounter"),
}

# FHIR Encounter.status values
KNOWN_STATUSES = {
    "1": "planned",
    "2": "in-progress",
    "3": "finished",
    "4": "cancelled",
}

# v3 ParticipationType codes mapped to readable labels
KNOWN_ROLES = {
    "1": "attending",
    "2": "consultant",
    "3": "referring",
    "4": "admitting",
}


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

def header(title: str):
    print(f"\n{'─' * 52}")
    print(f"  {title}")
    print(f"{'─' * 52}")


def subheader(title: str):
    print(f"\n  ── {title} ──")


def prompt(msg: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"  {msg}{suffix}: ").strip()
    return value if value else default


def pause():
    input("\n  Press Enter to continue...")


def _parse_datetime(raw: str, default: datetime | None = None) -> datetime | None:
    """
    Try several common datetime / date formats and return a datetime object.
    Returns *default* if raw is empty; raises ValueError if unparseable.
    """
    if not raw:
        return default
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M")
    except ValueError:
        raise ValueError(f"Cannot parse '{raw}' — YYYY-MM-DDTHH:MM")


def _show_db_summary(session):
    """Print a concise summary of current database row counts."""
    n_patients  = session.query(Patient).count()
    n_providers = session.query(Provider).count()
    n_enc       = session.query(Encounter).count()
    n_parts     = session.query(EncounterParticipant).count()
    n_obs       = session.query(Observation).count()
    print(f"\n  ┌─────────────────────────────────────────┐")
    print(f"  │  Database state                         │")
    print(f"  ├─────────────────────────────────────────┤")
    print(f"  │  Patients              : {n_patients:<14} │")
    print(f"  │  Providers             : {n_providers:<14} │")
    print(f"  │  Encounters            : {n_enc:<14} │")
    print(f"  │  Encounter participants : {n_parts:<14} │")
    print(f"  │  Observations          : {n_obs:<14} │")
    print(f"  └─────────────────────────────────────────┘")


# ---------------------------------------------------------------------------
# Patient actions
# ---------------------------------------------------------------------------

def add_patient():
    header("Add Patient")
    first  = prompt("First name")
    last   = prompt("Last name")
    dob    = prompt("Date of birth (YYYY-MM-DD)")
    gender = prompt("Gender (male/female/other/unknown)", default="unknown")

    try:
        birth_date = date.fromisoformat(dob)
    except ValueError:
        print("  ✗  Invalid date format.")
        return

    if gender not in ("male", "female", "other", "unknown"):
        print("  ✗  Gender must be one of: male, female, other, unknown")
        return

    with get_session() as session:
        patient = Patient(
            first_name=first,
            last_name=last,
            birth_date=birth_date,
            gender=gender,
        )
        session.add(patient)
        session.commit()
        print(f"\n  Patient saved  (id={patient.id})")


def edit_patient():
    header("Edit Patient")
    with get_session() as session:
        pid_str = prompt("Patient ID to edit")
        try:
            patient = session.get(Patient, int(pid_str))
        except (ValueError, TypeError):
            print("  ✗  Patient ID must be a number.")
            return
        if not patient:
            print("  ✗  Patient not found.")
            return

        print(f"\n  Editing: {patient.first_name} {patient.last_name}  "
              f"(DOB: {patient.birth_date}  Gender: {patient.gender})")
        print("  Press Enter to keep the current value.\n")

        first  = prompt("First name", default=patient.first_name)
        last   = prompt("Last name",  default=patient.last_name)
        dob    = prompt("Date of birth (YYYY-MM-DD)", default=str(patient.birth_date))
        gender = prompt("Gender (male/female/other/unknown)", default=patient.gender)

        try:
            birth_date = date.fromisoformat(dob)
        except ValueError:
            print("  ✗  Invalid date format.")
            return

        if gender not in ("male", "female", "other", "unknown"):
            print("  ✗  Gender must be one of: male, female, other, unknown")
            return

        patient.first_name = first
        patient.last_name  = last
        patient.birth_date = birth_date
        patient.gender     = gender
        session.commit()
        print(f"\n  Patient #{patient.id} updated.")


def list_patients():
    header("Patients")
    with get_session() as session:
        patients = session.query(Patient).order_by(Patient.id).all()
        if not patients:
            print("  No patients found. Use 'Seed Demo Data' to populate the database.")
            return
        print(f"  {'ID':<5} {'Name':<30} {'DOB':<12} {'Gender'}")
        print(f"  {'──':<5} {'────':<30} {'───':<12} {'──────'}")
        for p in patients:
            name = f"{p.first_name} {p.last_name}"
            print(f"  {p.id:<5} {name:<30} {str(p.birth_date):<12} {p.gender}")


# ---------------------------------------------------------------------------
# Observation actions
# ---------------------------------------------------------------------------

def add_observation():
    header("Add Observation")

    with get_session() as session:
        pid_str = prompt("Patient ID")
        try:
            patient = session.get(Patient, int(pid_str))
        except (ValueError, TypeError):
            print("  ✗  Patient ID must be a number.")
            return
        if not patient:
            print("  ✗  Patient not found.")
            return

        print(f"\n  Adding observation for {patient.first_name} {patient.last_name}")
        print("\n  Choose observation type:")
        for key, (code, display, unit) in KNOWN_OBSERVATIONS.items():
            print(f"    {key}. {display} ({unit})  [LOINC {code}]")

        choice = prompt("Choice")
        if choice not in KNOWN_OBSERVATIONS:
            print("  ✗  Invalid choice.")
            return

        code, display, unit = KNOWN_OBSERVATIONS[choice]
        value_str = prompt(f"Value ({unit})")

        try:
            value = float(value_str)
        except ValueError:
            print("  ✗  Value must be a number.")
            return

        obs = Observation(
            patient_id=patient.id,
            code=code,
            display=display,
            value=value,
            unit=unit,
            recorded_at=datetime.now(timezone.utc),
        )
        session.add(obs)
        session.commit()
        print(f"\n  Observation saved  (id={obs.id})")


def view_observations():
    header("View Observations")

    with get_session() as session:
        pid_str = prompt("Patient ID")
        try:
            patient = session.get(Patient, int(pid_str))
        except (ValueError, TypeError):
            print("  ✗  Patient ID must be a number.")
            return
        if not patient:
            print("  ✗  Patient not found.")
            return

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


# ---------------------------------------------------------------------------
# Encounter actions
# ---------------------------------------------------------------------------

def _manage_encounter_providers(session, encounter):
    """
    Interactive sub-loop for adding / removing providers on an encounter.
    Supports entering multiple providers with different roles.
    Called from both add_encounter and edit_encounter.
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
            # Show the provider list so the user can pick an ID
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

            prov_str = prompt("\n  Provider ID to add")
            try:
                provider = session.get(Provider, int(prov_str))
            except (ValueError, TypeError):
                print("  ✗  Provider ID must be a number.")
                continue
            if not provider:
                print("  ✗  Provider not found.")
                continue

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
            for key, role in KNOWN_ROLES.items():
                print(f"    {key}.  {role}")
            role_choice = prompt("  Choice", default="1")
            role = KNOWN_ROLES.get(role_choice, "attending")

            ep = EncounterParticipant(
                encounter_id=encounter.id,
                provider_id=provider.id,
                role=role,
            )
            session.add(ep)
            session.flush()
            print(f"\n  Added {provider.first_name} {provider.last_name} "
                  f"as {role}  (participant id={ep.id})")

        elif sub == "r":
            ep_str = prompt("  Participant ID to remove")
            try:
                ep = session.get(EncounterParticipant, int(ep_str))
            except (ValueError, TypeError):
                print("  ✗  Must be a number.")
                continue
            if not ep or ep.encounter_id != encounter.id:
                print("  ✗  Participant not found on this encounter.")
                continue
            name = f"{ep.provider.first_name} {ep.provider.last_name}"
            session.delete(ep)
            session.flush()
            print(f"  Removed {name} from encounter #{encounter.id}.")

        else:
            print("  ✗  Invalid choice — enter a, r, or d.")


def add_encounter():
    """
    Create a new Encounter for a patient, then optionally assign
    one or more providers with roles inline.
    """
    header("Add Encounter")

    with get_session() as session:
        pid_str = prompt("Patient ID")
        try:
            patient = session.get(Patient, int(pid_str))
        except (ValueError, TypeError):
            print("  ✗  Patient ID must be a number.")
            return
        if not patient:
            print("  ✗  Patient not found.")
            return

        print(f"\n  Adding encounter for {patient.first_name} {patient.last_name}")

        print("\n  Encounter class:")
        for key, (code, display) in KNOWN_ENCOUNTER_CLASSES.items():
            print(f"    {key}.  {display}  [{code}]")
        class_choice = prompt("Choice")
        if class_choice not in KNOWN_ENCOUNTER_CLASSES:
            print("  ✗  Invalid choice.")
            return
        class_code, class_display = KNOWN_ENCOUNTER_CLASSES[class_choice]

        print("\n  Status:")
        for key, status in KNOWN_STATUSES.items():
            print(f"    {key}.  {status}")
        status_choice = prompt("Choice", default="3")
        status = KNOWN_STATUSES.get(status_choice, "finished")

        reason = prompt("Reason / chief complaint (optional)")

        start_raw = prompt(
            "Start date/time (YYYY-MM-DD or YYYY-MM-DD HH:MM)",
            default=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        )
        try:
            start_date = _parse_datetime(start_raw, default=datetime.now(timezone.utc))
        except ValueError as e:
            print(f"  ✗  {e}")
            return

        end_raw = prompt("End date/time (leave blank if ongoing)")
        try:
            end_date = _parse_datetime(end_raw)
        except ValueError as e:
            print(f"  ✗  {e}")
            return

        encounter = Encounter(
            patient_id=patient.id,
            class_code=class_code,
            class_display=class_display,
            status=status,
            reason=reason if reason else None,
            start_date=start_date,
            end_date=end_date,
        )
        session.add(encounter)
        session.flush()
        print(f"\n  Encounter created  (id={encounter.id})")

        # Inline multi-provider assignment
        if prompt("\n  Assign providers now? (y/n)", default="y").lower() == "y":
            _manage_encounter_providers(session, encounter)

        session.commit()
        print(f"\n  Encounter #{encounter.id} saved.")


def edit_encounter():
    """Edit an existing Encounter and manage its provider assignments."""
    header("Edit Encounter")

    with get_session() as session:
        enc_str = prompt("Encounter ID to edit")
        try:
            encounter = session.get(Encounter, int(enc_str))
        except (ValueError, TypeError):
            print("  ✗  Encounter ID must be a number.")
            return
        if not encounter:
            print("  ✗  Encounter not found.")
            return

        patient = session.get(Patient, encounter.patient_id)
        print(f"\n  Editing Encounter #{encounter.id}  —  {encounter.class_display}"
              f"  ({patient.first_name} {patient.last_name})")
        print("  Press Enter to keep the current value.\n")

        change_reason = prompt("Reason for this edit (optional, stored in audit log)")

        # Encounter class
        current_class_key = next(
            (k for k, (c, _) in KNOWN_ENCOUNTER_CLASSES.items()
             if c == encounter.class_code), "1"
        )
        print("  Encounter class:")
        for key, (code, display) in KNOWN_ENCOUNTER_CLASSES.items():
            print(f"    {key}.  {display}  [{code}]")
        class_choice = prompt("Choice", default=current_class_key)
        if class_choice not in KNOWN_ENCOUNTER_CLASSES:
            print("  ✗  Invalid choice.")
            return
        class_code, class_display = KNOWN_ENCOUNTER_CLASSES[class_choice]

        # Status
        current_status_key = next(
            (k for k, s in KNOWN_STATUSES.items() if s == encounter.status), "3"
        )
        print("\n  Status:")
        for key, status in KNOWN_STATUSES.items():
            print(f"    {key}.  {status}")
        status_choice = prompt("Choice", default=current_status_key)
        status = KNOWN_STATUSES.get(status_choice, encounter.status)

        reason = prompt("Reason / chief complaint", default=encounter.reason or "")

        start_default = encounter.start_date.strftime("%Y-%m-%d %H:%M")
        start_raw = prompt(
            "Start date/time (YYYY-MM-DD or YYYY-MM-DD HH:MM)",
            default=start_default,
        )
        try:
            start_date = _parse_datetime(start_raw, default=encounter.start_date)
        except ValueError as e:
            print(f"  ✗  {e}")
            return

        end_default = encounter.end_date.strftime("%Y-%m-%d %H:%M") if encounter.end_date else ""
        end_raw = prompt("End date/time (leave blank to clear)", default=end_default)
        try:
            end_date = _parse_datetime(end_raw)
        except ValueError as e:
            print(f"  ✗  {e}")
            return

        snapshot_encounter(session, encounter, change_reason=change_reason)

        encounter.class_code    = class_code
        encounter.class_display = class_display
        encounter.status        = status
        encounter.reason        = reason if reason else None
        encounter.start_date    = start_date
        encounter.end_date      = end_date
        session.flush()

        # Provider management
        print("\n  Manage providers for this encounter:")
        _manage_encounter_providers(session, encounter)

        session.commit()
        print(f"\n  Encounter #{encounter.id} updated.")


def view_encounters():
    """List all encounters for a patient, including linked providers."""
    header("View Encounters")

    with get_session() as session:
        pid_str = prompt("Patient ID")
        try:
            patient = session.get(Patient, int(pid_str))
        except (ValueError, TypeError):
            print("  ✗  Patient ID must be a number.")
            return
        if not patient:
            print("  ✗  Patient not found.")
            return

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


def view_encounter_history():
    """Show the full audit trail of edits for a single encounter."""
    header("Encounter Edit History")

    with get_session() as session:
        enc_str = prompt("Encounter ID")
        try:
            encounter = session.get(Encounter, int(enc_str))
        except (ValueError, TypeError):
            print("  ✗  Encounter ID must be a number.")
            return
        if not encounter:
            print("  ✗  Encounter not found.")
            return

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
            changed_at  = h.changed_at.strftime("%Y-%m-%d %H:%M")
            changed_by  = h.changed_by or "—"
            reason      = h.reason or "—"
            note        = f"  ↳ Note: {h.change_reason}" if h.change_reason else ""
            print(f"  v{h.version:<4} {changed_at:<18} {changed_by:<20} "
                  f"{h.status:<14} {h.class_code:<10} {reason}{note}")

        print()
        print("  (Current values are shown in 'View Encounters')")


# ---------------------------------------------------------------------------
# Provider actions
# ---------------------------------------------------------------------------

def add_provider():
    """
    Create a new Provider (FHIR Practitioner).

    A Provider is any clinician who can participate in encounters — physician,
    nurse practitioner, therapist, etc.  The NPI is the standard US identifier.
    """
    header("Add Provider")
    first     = prompt("First name")
    last      = prompt("Last name")
    specialty = prompt("Specialty (optional)")
    npi       = prompt("NPI — 10-digit National Provider Identifier (optional)")

    if npi and (not npi.isdigit() or len(npi) != 10):
        print("  ✗  NPI must be exactly 10 digits.")
        return

    with get_session() as session:
        provider = Provider(
            first_name=first,
            last_name=last,
            specialty=specialty if specialty else None,
            npi=npi if npi else None,
        )
        session.add(provider)
        session.commit()
        print(f"\n  Provider saved  (id={provider.id})")


def edit_provider():
    header("Edit Provider")
    with get_session() as session:
        pid_str = prompt("Provider ID to edit")
        try:
            provider = session.get(Provider, int(pid_str))
        except (ValueError, TypeError):
            print("  ✗  Provider ID must be a number.")
            return
        if not provider:
            print("  ✗  Provider not found.")
            return

        print(f"\n  Editing: {provider.first_name} {provider.last_name}  "
              f"Specialty: {provider.specialty or '—'}  NPI: {provider.npi or '—'}")
        print("  Press Enter to keep the current value.\n")

        first     = prompt("First name", default=provider.first_name)
        last      = prompt("Last name",  default=provider.last_name)
        specialty = prompt("Specialty",  default=provider.specialty or "")
        npi       = prompt("NPI",        default=provider.npi or "")

        if npi and (not npi.isdigit() or len(npi) != 10):
            print("  ✗  NPI must be exactly 10 digits.")
            return

        provider.first_name = first
        provider.last_name  = last
        provider.specialty  = specialty if specialty else None
        provider.npi        = npi if npi else None
        session.commit()
        print(f"\n  Provider #{provider.id} updated.")


def list_providers():
    header("Providers")
    with get_session() as session:
        providers = session.query(Provider).order_by(Provider.id).all()
        if not providers:
            print("  No providers found.")
            return
        print(f"  {'ID':<5} {'Name':<28} {'Specialty':<25} {'NPI'}")
        print(f"  {'──':<5} {'────':<28} {'─────────':<25} {'───'}")
        for pv in providers:
            name = f"{pv.first_name} {pv.last_name}"
            spec = pv.specialty or "—"
            npi  = pv.npi or "—"
            print(f"  {pv.id:<5} {name:<28} {spec:<25} {npi}")


# ---------------------------------------------------------------------------
# FHIR export / import
# ---------------------------------------------------------------------------

def export_fhir_bundle():
    """
    Export a FHIR Bundle for one patient, including all linked resources:
    Patient → Observations → Encounters → Practitioners (Providers).
    """
    header("Export FHIR Bundle")

    with get_session() as session:
        pid_str = prompt("Patient ID")
        try:
            patient = session.get(Patient, int(pid_str))
        except (ValueError, TypeError):
            print("  ✗  Patient ID must be a number.")
            return
        if not patient:
            print("  ✗  Patient not found.")
            return

        # Eagerly load all relationships before the session closes
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


def import_fhir_patient():
    header("Import FHIR Patient")

    print("  Paste a FHIR Patient JSON string (single line), then press Enter:")
    fhir_json = input("  > ").strip()

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


# ---------------------------------------------------------------------------
# Demo seed data
# ---------------------------------------------------------------------------

def seed_demo_data():
    """
    Populate the database with two patients, providers, observations, and
    encounters (with participant assignments).  If data already exists the
    seed is skipped, but the current DB state is always printed.
    """
    header("Seed Demo Data")

    with get_session() as session:
        existing = session.query(Patient).count()
        if existing > 0:
            print(f"  Database already has {existing} patient(s) — skipping seed.")
            _show_db_summary(session)
            return

        # ── Patients ──────────────────────────────────────────────────────────
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

        # ── Observations ──────────────────────────────────────────────────────
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

        # ── Providers ─────────────────────────────────────────────────────────
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

        # ── Encounters ────────────────────────────────────────────────────────
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

        # ── Encounter Participants ────────────────────────────────────────────
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


# ---------------------------------------------------------------------------
# Sub-menus
# ---------------------------------------------------------------------------

def _submenu(title: str, options: dict):
    """
    Generic sub-menu loop.
    options: ordered dict mapping key str → (label str, callable).
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

        if choice == "0":
            break
        elif choice in options:
            try:
                options[choice][1]()
            except Exception as e:
                print(f"\n  ✗  Unexpected error: {e}")
            pause()
        else:
            print("  ✗  Invalid choice.")


def menu_patients():
    _submenu("Patients", {
        "1": ("Add patient",   add_patient),
        "2": ("Edit patient",  edit_patient),
        "3": ("List patients", list_patients),
    })


def menu_observations():
    _submenu("Observations", {
        "1": ("Add observation",   add_observation),
        "2": ("View observations", view_observations),
    })


def menu_encounters():
    _submenu("Encounters", {
        "1": ("Add encounter",         add_encounter),
        "2": ("Edit encounter",        edit_encounter),
        "3": ("View encounters",       view_encounters),
        "4": ("View edit history",     view_encounter_history),
    })


def menu_providers():
    _submenu("Providers", {
        "1": ("Add provider",   add_provider),
        "2": ("Edit provider",  edit_provider),
        "3": ("List providers", list_providers),
    })


def menu_fhir():
    _submenu("FHIR", {
        "1": ("Export FHIR Bundle (JSON)",   export_fhir_bundle),
        "2": ("Import FHIR Patient (JSON)",  import_fhir_patient),
    })


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------

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
    "6": seed_demo_data,
}


def main():
    init_db()
    print("\n  Database ready  (health.db)")

    while True:
        print(MAIN_MENU)
        choice = input("\n  Choice: ").strip()

        if choice == "0":
            print("\n  Bye!\n")
            break
        elif choice in MAIN_ACTIONS:
            try:
                MAIN_ACTIONS[choice]()
            except Exception as e:
                print(f"\n  ✗  Unexpected error: {e}")
            # Seed demo data already prints its own output; still pause for
            # sub-menu entries to let the user read before returning
            if choice == "6":
                pause()
        else:
            print("  ✗  Invalid choice.")


if __name__ == "__main__":
    main()
