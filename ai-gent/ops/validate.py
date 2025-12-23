#!/usr/bin/env python3
"""
AI-GENT Validator — v2.0
-----------------------
Implements AI_RULES.md v2.0

Design principles:
- Branch validation by `type`
- Parameter changes are lightweight
- Logic changes are explicit and versioned
- Fail fast, fail once, fail clearly
"""

from pathlib import Path
import yaml
import sys

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def fail(msg: str):
    raise ValueError(msg)


def require(data: dict, field: str):
    if field not in data:
        fail(f"Missing required field: {field}")


def load_yaml(path: Path) -> dict:
    try:
        return yaml.safe_load(path.read_text())
    except Exception as e:
        fail(f"Invalid YAML: {e}")


# ---------------------------------------------------------------------
# Validation logic
# ---------------------------------------------------------------------

def validate_parameter_change(doc: dict):
    required = [
        "type",
        "target_file",
        "parameter",
        "change",
        "timestamp",
        "statement",
    ]

    for field in required:
        require(doc, field)

    if not isinstance(doc["change"], dict):
        fail("Field 'change' must be a mapping")

    for sub in ("from", "to"):
        if sub not in doc["change"]:
            fail(f"Missing change.{sub}")

    return True


def validate_logic_change(doc: dict):
    required = [
        "type",
        "target_file",
        "tagline",
        "version",
    ]

    for field in required:
        require(doc, field)

    version = doc["version"]
    if not isinstance(version, dict):
        fail("Field 'version' must be a mapping")

    for sub in ("from", "to"):
        if sub not in version:
            fail(f"Missing version.{sub}")

    return True


# ---------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------

def validate(path: Path):
    doc = load_yaml(path)

    if not isinstance(doc, dict):
        fail("Top-level YAML must be a mapping")

    require(doc, "type")
    require(doc, "target_file")

    change_type = doc["type"]

    if change_type == "parameter":
        validate_parameter_change(doc)
    elif change_type == "logic":
        validate_logic_change(doc)
    else:
        fail(f"Unknown change type: {change_type}")

    return True


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: validate.py <change.yaml>")
        sys.exit(1)

    try:
        validate(Path(sys.argv[1]))
        print("Validation passed")
    except Exception as e:
        print(f"Validation failed: {e}")
        sys.exit(1)
