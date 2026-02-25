"""
fhir_utils.py
-------------
Utilities for converting between our SQL models and FHIR R4 resources.

We represent FHIR resources as plain Python dicts / JSON strings rather than
using an extra library — this makes the structure fully visible and helps you
learn the FHIR R4 data model directly.

Key FHIR concepts shown here:
  - resourceType     : every FHIR object starts with this field (required)
  - Identifier       : a namespaced ID  { "system": "...", "value": "..." }
  - HumanName        : structured name  { "family": "Smith", "given": ["John"] }
  - CodeableConcept  : a coded value    { "coding": [{ "system", "code", "display" }] }
  - Quantity         : a measured value { "value": 72.0, "unit": "/min", "system": "..." }
  - Reference        : pointer to another resource  { "reference": "Patient/42" }
  - Bundle           : container that groups multiple resources together

FHIR resource types implemented here:
  - Patient               ↔  SQL Patient
  - Observation           ↔  SQL Observation
  - Practitioner          ↔  SQL Provider
  - Encounter             ↔  SQL Encounter  (with EncounterParticipant links)
  - Communication (NOA)   ↔  SQL NoticeOfAdmission
"""

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from database import Patient, Observation, Provider, Encounter, NoticeOfAdmission

# LOINC is the standard coding system for clinical observations.
LOINC_SYSTEM = "http://loinc.org"
# UCUM is the standard for measurement units in FHIR.
UCUM_SYSTEM  = "http://unitsofmeasure.org"
# HL7 v3 ActCode is used for Encounter class (AMB, IMP, EMER, …).
V3_ACT_CODE_SYSTEM = "http://terminology.hl7.org/CodeSystem/v3-ActCode"
# v3 ParticipationType codes (ATND = attender, CON = consultant, REF = referrer, …).
V3_PARTICIPATION_SYSTEM = "http://terminology.hl7.org/CodeSystem/v3-ParticipationType"

# HL7 communication-category coding system (used for Notice of Admission).
COMMUNICATION_CATEGORY_SYSTEM = (
    "http://terminology.hl7.org/CodeSystem/communication-category"
)

# Map our human-readable role names to the standard v3 participation codes.
ROLE_TO_V3_CODE = {
    "attending":  "ATND",
    "consultant": "CON",
    "referring":  "REF",
    "admitting":  "ADM",
}


# ---------------------------------------------------------------------------
# Patient  ↔  FHIR Patient
# ---------------------------------------------------------------------------

def patient_to_fhir(patient: "Patient") -> dict:
    """
    Convert a SQL Patient row into a FHIR R4 Patient resource (as a dict).

    FHIR Patient structure:
    {
      "resourceType": "Patient",       ← always required
      "id": "42",                      ← server-assigned logical ID
      "identifier": [                  ← business identifiers (can be many)
        { "system": "...", "value": "42" }
      ],
      "name": [                        ← list of names (legal, nickname, etc.)
        { "family": "Smith", "given": ["John"] }
      ],
      "gender": "male",                ← male | female | other | unknown
      "birthDate": "1990-01-15"        ← YYYY-MM-DD
    }
    """
    return {
        "resourceType": "Patient",
        "id": str(patient.id),
        "identifier": [
            {
                "system": "http://example.org/fhir/patients",
                "value": str(patient.id),
            }
        ],
        "name": [
            {
                "family": patient.last_name,
                "given":  [patient.first_name],
            }
        ],
        "gender":    patient.gender,
        "birthDate": patient.birth_date.isoformat(),
    }


def fhir_patient_to_dict(fhir_json: str) -> dict:
    """
    Parse a FHIR Patient JSON string and return a plain dict with the fields
    our app needs — useful when *importing* a patient from an external system.

    We do basic validation: check resourceType and required fields.
    """
    data = json.loads(fhir_json)

    if data.get("resourceType") != "Patient":
        raise ValueError(
            f"Expected resourceType 'Patient', got '{data.get('resourceType')}'"
        )

    # name is a list; we take the first entry
    name       = (data.get("name") or [{}])[0]
    first_name = (name.get("given") or ["Unknown"])[0]
    last_name  =  name.get("family", "Unknown")

    return {
        "first_name": first_name,
        "last_name":  last_name,
        "birth_date": data.get("birthDate"),      # "YYYY-MM-DD" string or None
        "gender":     data.get("gender", "unknown"),
    }


# ---------------------------------------------------------------------------
# Observation  ↔  FHIR Observation
# ---------------------------------------------------------------------------

def observation_to_fhir(obs: "Observation") -> dict:
    """
    Convert a SQL Observation row into a FHIR R4 Observation resource (as a dict).

    FHIR Observation structure:
    {
      "resourceType": "Observation",
      "id": "7",
      "status": "final",               ← registered|preliminary|final|amended|…
      "subject": {                     ← who the observation is about
        "reference": "Patient/42"      ← relative URL to the Patient resource
      },
      "code": {                        ← what was measured (CodeableConcept)
        "coding": [
          {
            "system":  "http://loinc.org",
            "code":    "8867-4",
            "display": "Heart rate"
          }
        ],
        "text": "Heart rate"
      },
      "valueQuantity": {               ← the actual result (Quantity)
        "value":  72.0,
        "unit":   "/min",
        "system": "http://unitsofmeasure.org",
        "code":   "/min"               ← UCUM code (same as unit here)
      },
      "effectiveDateTime": "2025-01-10T09:00:00+00:00"
    }
    """
    return {
        "resourceType": "Observation",
        "id": str(obs.id),
        "status": "final",
        "subject": {
            "reference": f"Patient/{obs.patient_id}"
        },
        "code": {
            "coding": [
                {
                    "system":  LOINC_SYSTEM,
                    "code":    obs.code,
                    "display": obs.display,
                }
            ],
            "text": obs.display,
        },
        "valueQuantity": {
            "value":  obs.value,
            "unit":   obs.unit,
            "system": UCUM_SYSTEM,
            "code":   obs.unit,
        },
        "effectiveDateTime": (
            obs.recorded_at.replace(tzinfo=timezone.utc).isoformat()
        ),
    }


# ---------------------------------------------------------------------------
# Provider  ↔  FHIR Practitioner
# ---------------------------------------------------------------------------

def provider_to_fhir(provider: "Provider") -> dict:
    """
    Convert a SQL Provider row into a FHIR R4 Practitioner resource (as a dict).

    FHIR Practitioner structure:
    {
      "resourceType": "Practitioner",
      "id": "5",
      "identifier": [                  ← NPI or other business identifier
        { "system": "http://hl7.org/fhir/sid/us-npi", "value": "1234567890" }
      ],
      "name": [
        { "family": "Smith", "given": ["Jane"] }
      ],
      "qualification": [               ← specialty / credentials
        {
          "code": {
            "coding": [{ "system": "http://snomed.info/sct", "display": "Cardiology" }],
            "text": "Cardiology"
          }
        }
      ]
    }
    """
    resource: dict = {
        "resourceType": "Practitioner",
        "id": str(provider.id),
        "name": [
            {
                "family": provider.last_name,
                "given":  [provider.first_name],
            }
        ],
    }

    if provider.npi:
        resource["identifier"] = [
            {
                "system": "http://hl7.org/fhir/sid/us-npi",
                "value":  provider.npi,
            }
        ]

    if provider.specialty:
        resource["qualification"] = [
            {
                "code": {
                    "coding": [
                        {
                            "system":  "http://snomed.info/sct",
                            "display": provider.specialty,
                        }
                    ],
                    "text": provider.specialty,
                }
            }
        ]

    return resource


# ---------------------------------------------------------------------------
# Encounter  ↔  FHIR Encounter
# ---------------------------------------------------------------------------

def encounter_to_fhir(enc: "Encounter") -> dict:
    """
    Convert a SQL Encounter row into a FHIR R4 Encounter resource (as a dict).

    FHIR Encounter structure:
    {
      "resourceType": "Encounter",
      "id": "3",
      "status": "finished",            ← planned|in-progress|finished|cancelled
      "class": {                       ← encounter class (v3 ActCode)
        "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
        "code": "AMB",
        "display": "Ambulatory encounter"
      },
      "subject": { "reference": "Patient/42" },
      "participant": [                 ← providers involved in the encounter
        {
          "type": [{                   ← role of this participant (CodeableConcept)
            "coding": [{
              "system": "http://terminology.hl7.org/CodeSystem/v3-ParticipationType",
              "code": "ATND",
              "display": "attending"
            }]
          }],
          "individual": { "reference": "Practitioner/5" }
        }
      ],
      "period": {
        "start": "2025-01-10T09:00:00+00:00",
        "end":   "2025-01-10T10:00:00+00:00"  ← omitted if still in-progress
      },
      "reasonCode": [{ "text": "Annual checkup" }]
    }
    """
    resource: dict = {
        "resourceType": "Encounter",
        "id": str(enc.id),
        "status": enc.status,
        "class": {
            "system":  V3_ACT_CODE_SYSTEM,
            "code":    enc.class_code,
            "display": enc.class_display,
        },
        "subject": {
            "reference": f"Patient/{enc.patient_id}"
        },
        "period": {
            "start": enc.start_date.replace(tzinfo=timezone.utc).isoformat(),
        },
    }

    if enc.end_date:
        resource["period"]["end"] = enc.end_date.replace(tzinfo=timezone.utc).isoformat()

    if enc.reason:
        resource["reasonCode"] = [{"text": enc.reason}]

    if enc.participants:
        resource["participant"] = [
            {
                "type": [
                    {
                        "coding": [
                            {
                                "system":  V3_PARTICIPATION_SYSTEM,
                                "code":    ROLE_TO_V3_CODE.get(p.role, p.role.upper()[:4]),
                                "display": p.role,
                            }
                        ]
                    }
                ],
                "individual": {
                    "reference": f"Practitioner/{p.provider_id}"
                },
            }
            for p in enc.participants
        ]

    return resource


# ---------------------------------------------------------------------------
# Bundle  —  grouping resources for export
# ---------------------------------------------------------------------------

def build_patient_bundle(patient: "Patient") -> dict:
    """
    Create a FHIR Bundle of type 'collection' containing:
      - the Patient resource
      - all of the patient's Observation resources
      - all of the patient's Encounter resources
      - all distinct Practitioner (Provider) resources referenced by those encounters

    A Bundle wraps multiple resources into a single JSON document — the
    standard way to exchange a set of related FHIR resources between systems.

    Bundle structure:
    {
      "resourceType": "Bundle",
      "type": "collection",            ← collection | transaction | searchset | …
      "timestamp": "...",              ← when the bundle was assembled
      "entry": [                       ← list of resources
        { "resource": { "resourceType": "Patient", … } },
        { "resource": { "resourceType": "Observation", … } },
        { "resource": { "resourceType": "Encounter", … } },
        { "resource": { "resourceType": "Practitioner", … } },
        …
      ]
    }
    """
    entries = [{"resource": patient_to_fhir(patient)}]

    for obs in patient.observations:
        entries.append({"resource": observation_to_fhir(obs)})

    # Collect unique providers referenced by any encounter so we include
    # each Practitioner resource only once in the bundle.
    seen_provider_ids: set[int] = set()

    for enc in patient.encounters:
        entries.append({"resource": encounter_to_fhir(enc)})
        for participant in enc.participants:
            if participant.provider_id not in seen_provider_ids:
                seen_provider_ids.add(participant.provider_id)
                entries.append({"resource": provider_to_fhir(participant.provider)})

    return {
        "resourceType": "Bundle",
        "type":         "collection",
        "timestamp":    datetime.now(tz=timezone.utc).isoformat(),
        "entry":        entries,
    }


def bundle_to_json(bundle: dict, indent: int = 2) -> str:
    """Serialise a Bundle dict to a pretty-printed JSON string."""
    return json.dumps(bundle, indent=indent, default=str)


# ---------------------------------------------------------------------------
# NoticeOfAdmission  ↔  FHIR Communication
# ---------------------------------------------------------------------------

def noa_to_fhir(notice: "NoticeOfAdmission") -> dict:
    """
    Convert a SQL NoticeOfAdmission row into a FHIR R4 Communication resource.

    The Communication resource is the standard FHIR mechanism for representing
    a notice or message exchanged in the context of care.  A Notice of
    Admission is modelled as a completed notification Communication linked to
    the triggering Encounter and the Patient.

    FHIR Communication structure:
    {
      "resourceType": "Communication",
      "id": "1",
      "status": "completed",           ← registered|in-progress|completed|…
      "category": [{                   ← kind of communication (CodeableConcept)
        "coding": [{
          "system": "http://terminology.hl7.org/CodeSystem/communication-category",
          "code": "notification",
          "display": "Notification"
        }],
        "text": "Notice of Admission"
      }],
      "subject": { "reference": "Patient/42" },    ← who the notice is about
      "encounter": { "reference": "Encounter/3" },  ← the triggering encounter
      "sent": "2026-02-22T10:00:00+00:00",         ← when the notice was generated
      "payload": [{                                  ← content of the notice
        "contentString": "Notice of Admission. Triggered by rule: class=IMP."
      }]
    }
    """
    return {
        "resourceType": "Communication",
        "id": str(notice.id),
        "status": "completed",
        "category": [
            {
                "coding": [
                    {
                        "system":  COMMUNICATION_CATEGORY_SYSTEM,
                        "code":    "notification",
                        "display": "Notification",
                    }
                ],
                "text": "Notice of Admission",
            }
        ],
        "subject":   {"reference": f"Patient/{notice.patient_id}"},
        "encounter": {"reference": f"Encounter/{notice.encounter_id}"},
        "sent": notice.generated_at.replace(tzinfo=timezone.utc).isoformat(),
        "payload": [
            {
                "contentString": (
                    f"Notice of Admission. "
                    f"Triggered by rule: {notice.triggered_by}."
                )
            }
        ],
    }
