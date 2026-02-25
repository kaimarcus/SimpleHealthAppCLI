"""
database.py
-----------
SQLAlchemy setup and ORM models for our simple FHIR-backed health app.

We use SQLite so there's nothing to install or configure — the DB file
(health.db) is created automatically in the current directory.

Tables:
  - patients              ↔  FHIR Patient
  - observations          ↔  FHIR Observation
  - providers             ↔  FHIR Practitioner
  - encounters            ↔  FHIR Encounter
  - encounter_participants   (join: Encounter ↔ Provider with a role)
  - noa_rules             —  configurable criteria that trigger a Notice of Admission
  - notices_of_admission  ↔  FHIR Communication (Notice of Admission)
"""

from datetime import date, datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Date, DateTime, Float, ForeignKey,
    func,
)
from sqlalchemy.orm import DeclarativeBase, relationship, Session

# ---------------------------------------------------------------------------
# Engine & base
# ---------------------------------------------------------------------------

ENGINE = create_engine("sqlite:///health.db", echo=False)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# ORM models
# ---------------------------------------------------------------------------

class Patient(Base):
    """Represents a FHIR Patient stored in SQL."""
    __tablename__ = "patients"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    first_name = Column(String(100), nullable=False)
    last_name  = Column(String(100), nullable=False)
    birth_date = Column(Date, nullable=False)
    # FHIR gender values: male | female | other | unknown
    gender     = Column(String(20), nullable=False, default="unknown")

    observations = relationship("Observation", back_populates="patient",
                                cascade="all, delete-orphan")
    encounters   = relationship("Encounter", back_populates="patient",
                                cascade="all, delete-orphan")

    def __repr__(self):
        return (f"<Patient id={self.id} name='{self.first_name} {self.last_name}' "
                f"dob={self.birth_date} gender={self.gender}>")


class Observation(Base):
    """
    Represents a FHIR Observation stored in SQL.

    A single numeric value with a unit, identified by a LOINC code
    (e.g. '8867-4' for heart rate, '8310-5' for body temperature).
    """
    __tablename__ = "observations"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    patient_id  = Column(Integer, ForeignKey("patients.id"), nullable=False)
    # LOINC code that identifies what was measured
    code        = Column(String(50),  nullable=False)
    # Human-readable label matching the code
    display     = Column(String(200), nullable=False)
    value       = Column(Float,       nullable=False)
    unit        = Column(String(50),  nullable=False)
    recorded_at = Column(DateTime,    nullable=False, default=datetime.utcnow)

    patient = relationship("Patient", back_populates="observations")

    def __repr__(self):
        return (f"<Observation id={self.id} patient_id={self.patient_id} "
                f"{self.display}={self.value} {self.unit}>")


class Provider(Base):
    """
    Represents a FHIR Practitioner stored in SQL.

    A provider is any clinician (physician, nurse, therapist, etc.) who
    participates in patient encounters.  The NPI (National Provider
    Identifier) is the standard US identifier for practitioners.
    """
    __tablename__ = "providers"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    first_name = Column(String(100), nullable=False)
    last_name  = Column(String(100), nullable=False)
    specialty  = Column(String(100), nullable=True)
    # National Provider Identifier — 10-digit US standard
    npi        = Column(String(20),  nullable=True)

    # A provider can participate in many encounters
    participations = relationship("EncounterParticipant", back_populates="provider",
                                  cascade="all, delete-orphan")

    def __repr__(self):
        return (f"<Provider id={self.id} name='{self.first_name} {self.last_name}' "
                f"specialty={self.specialty}>")


class Encounter(Base):
    """
    Represents a FHIR Encounter stored in SQL.

    An Encounter is an interaction between a patient and healthcare provider(s)
    for the purpose of providing healthcare services.

    class_code / class_display map to the HL7 v3 Act Code system:
      AMB    → Ambulatory
      IMP    → Inpatient
      EMER   → Emergency
      OBSENC → Observation (non-admitting)

    status values mirror FHIR's Encounter.status:
      planned | in-progress | finished | cancelled
    """
    __tablename__ = "encounters"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    patient_id    = Column(Integer, ForeignKey("patients.id"), nullable=False)
    # HL7 v3 Act Code (AMB, IMP, EMER, OBSENC)
    class_code    = Column(String(10),  nullable=False)
    class_display = Column(String(100), nullable=False)
    # FHIR Encounter.status
    status        = Column(String(30),  nullable=False, default="finished")
    # Free-text reason for the encounter (maps to FHIR reasonCode[].text)
    reason        = Column(String(200), nullable=True)
    start_date    = Column(DateTime,    nullable=False, default=datetime.utcnow)
    end_date      = Column(DateTime,    nullable=True)

    patient      = relationship("Patient", back_populates="encounters")
    participants = relationship("EncounterParticipant", back_populates="encounter",
                                cascade="all, delete-orphan")

    def __repr__(self):
        return (f"<Encounter id={self.id} patient_id={self.patient_id} "
                f"class={self.class_code} status={self.status}>")


class EncounterParticipant(Base):
    """
    Join table linking a Provider to an Encounter with a clinical role.

    role values mirror FHIR's v3-ParticipationType codes:
      attending  → ATND  (primary clinician responsible for the patient)
      consultant → CON   (specialist providing advice)
      referring  → REF   (provider who referred the patient)
      admitting  → ADM   (provider who admitted the patient)
    """
    __tablename__ = "encounter_participants"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    encounter_id = Column(Integer, ForeignKey("encounters.id"), nullable=False)
    provider_id  = Column(Integer, ForeignKey("providers.id"), nullable=False)
    # Clinical role in this specific encounter
    role         = Column(String(50), nullable=False, default="attending")

    encounter = relationship("Encounter", back_populates="participants")
    provider  = relationship("Provider",  back_populates="participations")

    def __repr__(self):
        return (f"<EncounterParticipant encounter_id={self.encounter_id} "
                f"provider_id={self.provider_id} role={self.role}>")


class EncounterHistory(Base):
    """
    Immutable audit log — one row per edit, storing the values that existed
    *before* the change was applied.

    The encounters table always holds the current version; this table is
    append-only and should never be updated or deleted.

    version:       monotonically increasing per encounter (1, 2, 3 …)
    changed_by:    free-text username or user ID (nullable for now)
    change_reason: optional note explaining why the edit was made
    """
    __tablename__ = "encounter_history"

    history_id    = Column(Integer, primary_key=True, autoincrement=True)

    encounter_id  = Column(Integer, ForeignKey("encounters.id"), nullable=False)
    version       = Column(Integer, nullable=False)

    changed_at    = Column(DateTime, nullable=False, default=datetime.utcnow)
    changed_by    = Column(String(100), nullable=True)
    change_reason = Column(String(200), nullable=True)

    # Snapshot of the OLD field values (before the edit)
    patient_id    = Column(Integer,      nullable=False)
    class_code    = Column(String(10),   nullable=False)
    class_display = Column(String(100),  nullable=False)
    status        = Column(String(30),   nullable=False)
    reason        = Column(String(200),  nullable=True)
    start_date    = Column(DateTime,     nullable=False)
    end_date      = Column(DateTime,     nullable=True)

    encounter = relationship("Encounter", backref="history")

    def __repr__(self):
        return (f"<EncounterHistory history_id={self.history_id} "
                f"encounter_id={self.encounter_id} version={self.version} "
                f"changed_at={self.changed_at}>")


class NoaRule(Base):
    """
    A single trigger rule for auto-generating a Notice of Admission.

    When an encounter is created, all active rules are evaluated.  A rule
    matches when every non-null criterion matches the encounter (AND logic).
    If *any* rule matches (OR logic across rows), a NoticeOfAdmission is
    generated automatically.

    Leaving both class_code and status as NULL creates a catch-all rule that
    triggers a notice for every encounter.

    class_code:  HL7 v3 ActCode (AMB, IMP, EMER, OBSENC) or NULL for any class.
    status:      FHIR Encounter.status or NULL for any status.
    """
    __tablename__ = "noa_rules"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    class_code = Column(String(10),  nullable=True)
    status     = Column(String(30),  nullable=True)
    created_at = Column(DateTime,    nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return (f"<NoaRule id={self.id} class_code={self.class_code!r} "
                f"status={self.status!r}>")


class NoticeOfAdmission(Base):
    """
    A Notice of Admission (NOA) generated when an encounter matches a NoaRule.

    Maps to a FHIR R4 Communication resource (resourceType='Communication').

    triggered_by:  human-readable description of which rule criteria matched,
                   e.g. 'class=IMP' or 'class=IMP, status=in-progress'.
    """
    __tablename__ = "notices_of_admission"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    encounter_id = Column(Integer, ForeignKey("encounters.id"), nullable=False)
    patient_id   = Column(Integer, ForeignKey("patients.id"),   nullable=False)
    generated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    triggered_by = Column(String(200), nullable=False)

    encounter = relationship("Encounter")
    patient   = relationship("Patient")

    def __repr__(self):
        return (f"<NoticeOfAdmission id={self.id} encounter_id={self.encounter_id} "
                f"patient_id={self.patient_id} generated_at={self.generated_at}>")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def init_db():
    """Create all tables (safe to call multiple times — won't overwrite data)."""
    Base.metadata.create_all(ENGINE)


def get_session() -> Session:
    """Return a new SQLAlchemy session."""
    return Session(ENGINE)


def snapshot_encounter(session: Session, encounter, changed_by: str = None,
                       change_reason: str = None) -> EncounterHistory:
    """
    Write the encounter's current state to encounter_history before an edit.

    Call this inside an open session, before applying new values to the
    encounter.  The caller is responsible for committing the session.
    """
    last_version = (
        session.query(func.max(EncounterHistory.version))
        .filter(EncounterHistory.encounter_id == encounter.id)
        .scalar()
    ) or 0

    history = EncounterHistory(
        encounter_id  = encounter.id,
        version       = last_version + 1,
        changed_by    = changed_by or None,
        change_reason = change_reason or None,
        patient_id    = encounter.patient_id,
        class_code    = encounter.class_code,
        class_display = encounter.class_display,
        status        = encounter.status,
        reason        = encounter.reason,
        start_date    = encounter.start_date,
        end_date      = encounter.end_date,
    )
    session.add(history)
    return history
