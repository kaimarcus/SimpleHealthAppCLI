"""
menus.py
--------
Menu / navigation layer.

Sections:
  - _manage_encounter_providers : interactive provider sub-loop for encounters
  - do_*  : thin orchestrators that combine CLI input + action calls
  - menu_* / _submenu / main : navigation loops
"""

from database import get_session, Patient, Provider, EncounterParticipant
from constants import KNOWN_ENCOUNTER_CLASSES, KNOWN_STATUSES, KNOWN_ROLES
from ui import (
    QuitRequested,
    header,
    subheader,
    prompt,
    prompt_until,
    pause,
    _find_provider,
)
from cli_inputs import (
    cli_select_patient,
    cli_select_encounter,
    cli_patient_fields,
    cli_observation_inputs,
    cli_encounter_fields,
    cli_provider_fields,
    cli_fhir_import,
)
from actions import (
    action_save_patient,
    action_update_patient,
    action_list_patients,
    action_add_observation,
    action_view_observations,
    action_create_encounter,
    action_update_encounter,
    action_view_encounters,
    action_view_encounter_history,
    action_add_participant,
    action_remove_participant,
    action_save_provider,
    action_update_provider,
    action_list_providers,
    action_export_fhir_bundle,
    action_import_fhir_patient,
    action_seed_demo_data,
)


# ---------------------------------------------------------------------------
# Encounter provider sub-loop
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Patient operations
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Observation operations
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Encounter operations
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Provider operations
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# FHIR operations
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Seed demo data
# ---------------------------------------------------------------------------

def do_seed_demo_data():
    header("Seed Demo Data")
    action_seed_demo_data()


# ---------------------------------------------------------------------------
# Sub-menus
# ---------------------------------------------------------------------------

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
    "6": do_seed_demo_data,
}


def main():
    from database import init_db
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
