# Python · SQLite · FHIR R4 — Simple Health App

A minimal learning project showing how Python, SQL, and FHIR fit together.

## What it demonstrates

| Layer | Technology | File |
|-------|-----------|------|
| Data storage | SQLite via SQLAlchemy ORM | `database.py` |
| Interoperability | FHIR R4 resources (Patient, Observation, Encounter, Practitioner, Bundle) | `fhir_utils.py` |
| Application | Python CLI with sub-menus | `app.py` |

## Quick start

```bash
# 1 — install dependencies (Python 3.11+ recommended)
pip install -r requirements.txt

# 2 — run the app
python app.py
```

On first run, choose **option 6 (Seed Demo Data)** to populate the database with
two patients, two providers, three encounters, and seven observations.

## Menu structure

```
Main menu
├── 1. Patients
│       1. Add patient
│       2. Edit patient
│       3. List patients
├── 2. Observations
│       1. Add observation
│       2. View observations
├── 3. Encounters
│       1. Add encounter   ← assign multiple providers with roles inline
│       2. Edit encounter  ← also manage provider assignments
│       3. View encounters
├── 4. Providers
│       1. Add provider
│       2. Edit provider
│       3. List providers
├── 5. FHIR
│       1. Export FHIR Bundle (JSON)
│       2. Import FHIR Patient (JSON)
└── 6. Seed Demo Data      ← always shows current DB state
```

## Project layout

```
app.py          # CLI menu — entry point
database.py     # SQLAlchemy engine + ORM models
fhir_utils.py   # SQL ↔ FHIR conversion; Bundle builder
requirements.txt
health.db       # SQLite file — created automatically on first run
```

## FHIR concepts covered

### Resources
- **Patient** — demographic info (name, DOB, gender)
- **Observation** — a clinical measurement linked to a patient (heart rate, BP, weight…)
- **Encounter** — an interaction between a patient and the health system
- **Practitioner** — a healthcare provider (maps from the local `Provider` model)
- **Bundle** — a collection of resources exported together as FHIR JSON

### Coding systems
- **LOINC** (`http://loinc.org`) — universal codes for lab tests and observations
- **UCUM** (`http://unitsofmeasure.org`) — standard measurement units
- **HL7 v3 ActCode** — Encounter class codes (AMB, IMP, EMER, OBSENC)
- **v3-ParticipationType** — provider roles (attending, consultant, referring, admitting)

### How the mapping works

```
SQL Patient row  ──→  FHIR Patient  ──→  { "resourceType": "Patient", … }
SQL Observation  ──→  FHIR Observation
SQL Encounter    ──→  FHIR Encounter (with participant references)
SQL Provider     ──→  FHIR Practitioner
All together     ──→  FHIR Bundle  ──→  patient_<id>_bundle.json
```

## Try it end-to-end

1. Run the app and seed demo data (main menu → **6**)
2. Browse patients (**1 → 3**) — note Alice's id (probably `1`)
3. View her observations (**2 → 2**)
4. View her encounters (**3 → 3**) — shows providers with roles
5. Export her FHIR Bundle (**5 → 1**) — writes `patient_1_bundle.json`
6. Open the JSON file and explore the FHIR structure

## Import example

To test the FHIR import flow (**5 → 2**), paste this minimal FHIR Patient JSON:

```json
{"resourceType":"Patient","name":[{"family":"Patel","given":["Priya"]}],"gender":"female","birthDate":"1992-04-18"}
```

## Dependencies

| Package | Purpose |
|---------|---------|
| `sqlalchemy` | ORM for SQLite — models, sessions, queries |

FHIR resources are built as plain Python dicts and serialised with the
standard library `json` module — no extra FHIR library needed. This keeps
the FHIR JSON structure fully visible so you can read and learn it directly.
