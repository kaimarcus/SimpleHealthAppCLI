"""
app.py
------
Entry point for the Simple Health App CLI.

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

Module layout:
  constants.py   — lookup tables (LOINC codes, encounter classes, statuses, roles)
  ui.py          — display helpers, QuitRequested exception, shared parsers
  cli_inputs.py  — input-gathering functions (prompt / display only, no DB writes)
  actions.py     — database operations and business logic (no user prompting)
  menus.py       — navigation loops that tie CLI and actions together
  database.py    — SQLAlchemy models and session management
  fhir_utils.py  — FHIR R4 serialisation helpers
"""

from menus import main

if __name__ == "__main__":
    main()
