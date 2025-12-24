#!/usr/bin/env python3
"""
AI-GENT Validator — v2.2 (compat shim for 'content' + dry-run mode)
----------------------------------------------------------------
- Implements AI_RULES.md v2.0 semantics.
- Adds forward-compat for ai-gent commit expecting top-level 'content'.
- Adds --check dry-run mode to preview normalizations.
- Fails early with clear errors; can normalize YAML in-place (optional).
"""
from __future__ import annotations
import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict
import yaml
# ---------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------
class ValidationError(Exception):
    """Raised for user-facing validation failures."""
# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def fail(msg: str) -> None:
    raise ValidationError(msg)
def require(data: Dict[str, Any], field: str) -> None:
    if field not in data:
        fail(f"Missing required field: {field}")
def load_yaml(path: Path) -> Dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text())
    except Exception as e:
        fail(f"Invalid YAML: {e}")
    if not isinstance(data, dict):
        fail("Top-level YAML must be a mapping")
    return data
def dump_yaml(path: Path, data: Dict[str, Any]) -> None:
    try:
        # Keep output stable & human-friendly
        path.write_text(yaml.safe_dump(data, sort_keys=False))
    except Exception as e:
        fail(f"Failed to write YAML: {e}")
# ---------------------------------------------------------------------
# Validation logic
# ---------------------------------------------------------------------
@dataclass
class ValidationOutcome:
    ok: bool
    normalized: Dict[str, Any]
def _validate_change_mapping(mapping: Any, field_name: str) -> None:
    if not isinstance(mapping, dict):
        fail(f"Field '{field_name}' must be a mapping")
    for sub in ("from", "to"):
        if sub not in mapping:
            fail(f"Missing {field_name}.{sub}")
def _ensure_content(doc: Dict[str, Any], default_source: str) -> None:
    """
    Ensures top-level 'content' exists.
    If absent, maps from another field (statement/tagline).
    """
    if "content" in doc:
        if not isinstance(doc["content"], str):
            fail("Field 'content' must be a string")
        if not doc["content"].strip():
            fail("Field 'content' must not be empty")
        return
    if default_source not in doc:
        fail(f"Missing required field: {default_source} (needed to derive 'content')")
    src = doc[default_source]
    if not isinstance(src, str) or not src.strip():
        fail(f"Field '{default_source}' must be a non-empty string")
    # Compat shim: auto-fill content to satisfy ai-gent commit
    doc["content"] = src
def validate_parameter_change(doc: Dict[str, Any]) -> None:
    for field in ("type", "target_file", "parameter", "change", "timestamp", "statement"):
        require(doc, field)
    _validate_change_mapping(doc["change"], "change")
    _ensure_content(doc, default_source="statement")
def validate_logic_change(doc: Dict[str, Any]) -> None:
    for field in ("type", "target_file", "tagline", "version"):
        require(doc, field)
    _validate_change_mapping(doc["version"], "version")
    _ensure_content(doc, default_source="tagline")
def validate_document(doc: Dict[str, Any]) -> ValidationOutcome:
    require(doc, "type")
    require(doc, "target_file")
    change_type = doc["type"]
    if change_type == "parameter":
        validate_parameter_change(doc)
    elif change_type == "logic":
        validate_logic_change(doc)
    else:
        fail(f"Unknown change type: {change_type}")
    return ValidationOutcome(ok=True, normalized=doc)
# ---------------------------------------------------------------------
# Backward-compatible entrypoints for ai-gent
# ---------------------------------------------------------------------
def validate(path: Path) -> bool:
    doc = load_yaml(path)
    outcome = validate_document(doc)
    return outcome.ok
def validate_update(update: Any) -> bool:
    """
    ai-gent may pass either a path or a parsed dict.
    We normalize in-memory so callers that reuse `doc` also get 'content'.
    """
    if isinstance(update, dict):
        doc = update
    else:
        doc = load_yaml(Path(update))
    _ = validate_document(doc) # raises on failure
    return True
# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------
def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Validate ai-gent change YAML. Optional: normalize in-place or dry-run preview."
    )
    p.add_argument("change_yaml", type=Path, help="Path to change YAML")
    p.add_argument(
        "--inplace",
        action="store_true",
        help="Write normalized YAML back (adds 'content' if missing).",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="Dry-run: show what would be normalized without writing.",
    )
    return p.parse_args(argv)
def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    try:
        doc = load_yaml(args.change_yaml)
        outcome = validate_document(doc)

        if args.check:
            # Reload original for clean comparison
            original = load_yaml(args.change_yaml)
            if outcome.normalized == original:
                print("✅ No changes needed — file already valid and normalized")
            else:
                print("🔍 Dry-run: The following changes would be applied with --inplace:")
                for key in sorted(set(outcome.normalized) | set(original)):
                    if outcome.normalized.get(key) != original.get(key):
                        old_val = original.get(key, "<missing>")
                        new_val = outcome.normalized[key]
                        print(f"   • {key}: {old_val!r} → {new_val!r}")
            print("Validation passed (dry-run)")
            return 0

        if args.inplace:
            original = load_yaml(args.change_yaml)  # reload to check if changed
            dump_yaml(args.change_yaml, outcome.normalized)
            if outcome.normalized != original:
                print("File normalized and written.")
            else:
                print("No changes needed.")
        
        print("Validation passed")
        return 0
    except ValidationError as e:
        print(f"Validation failed: {e}")
        return 1
if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))