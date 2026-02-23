"""
constants.py
------------
Reference / lookup tables used across the CLI for menus and validation.
"""

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
