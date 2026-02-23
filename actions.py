"""
actions.py
----------
Action layer — database operations and business logic.

Functions here accept plain data values or ORM objects, carry out their
operation, and print results.  They never call prompt() or input().

Sections:
  - Patients
  - Observations
  - Encounters
  - Encounter participants
  - Providers
  - FHIR
  - Seed demo data
"""

from datetime import date, datetime, timezone

from database import (
    get_session,
    Patient, Observation, Encounter, EncounterParticipant, Provider,
)
from fhir_utils import build_patient_bundle, bundle_to_json, fhir_patient_to_dict
from ui import _show_db_summary


# ---------------------------------------------------------------------------
# Patients
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Encounters
# ---------------------------------------------------------------------------

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
    from database import snapshot_encounter
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
    from database import EncounterHistory
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


# ---------------------------------------------------------------------------
# Encounter participants
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# FHIR
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Seed demo data
# ---------------------------------------------------------------------------

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
