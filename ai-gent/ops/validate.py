"""
Validation logic for AI-GENT
Enforces AI_RULES.md and VERSION_POLICY.md invariants
"""

from pathlib import Path
import ast
import re


REPO_ROOT = Path.home() / "leo-services"
MES_DIR = REPO_ROOT / "mes"
POLICY_FILE = MES_DIR / "VERSION_POLICY.md"
CHANGELOG_FILE = MES_DIR / "CHANGELOG.md"


class ValidationError(Exception):
    pass


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def require_fields(update, fields):
    for f in fields:
        if f not in update:
            raise ValidationError(f"Missing required field: {f}")


def parse_version(version: str):
    """
    vMAJOR.MINOR.PATCH[suffix]
    """
    m = re.match(r"v(\d+)\.(\d+)\.(\d+)([a-z]?)$", version)
    if not m:
        raise ValidationError(f"Invalid version format: {version}")
    major, minor, patch, suffix = m.groups()
    return int(major), int(minor), int(patch), suffix or None


def format_version(major, minor, patch, suffix=None):
    v = f"v{major}.{minor}.{patch}"
    if suffix:
        v += suffix
    return v


# ------------------------------------------------------------
# VERSION POLICY
# ------------------------------------------------------------

def load_version_policy():
    if not POLICY_FILE.exists():
        raise ValidationError("VERSION_POLICY.md not found")

    text = POLICY_FILE.read_text()

    table = {}
    overrides = []

    in_table = False
    in_overrides = False

    for line in text.splitlines():
        line = line.strip()

        if line.startswith("decision_table:"):
            in_table = True
            continue
        if line.startswith("overrides:"):
            in_overrides = True
            in_table = False
            continue

        if in_table and ":" in line:
            k, v = line.split(":", 1)
            table[k.strip()] = v.strip().upper()

        if in_overrides and line.startswith("- when:"):
            categories = line.split("[", 1)[1].split("]")[0]
            categories = [c.strip() for c in categories.split(",")]
            overrides.append({"when": categories})

        if in_overrides and line.startswith("then:"):
            overrides[-1]["then"] = line.split(":", 1)[1].strip().upper()

    return table, overrides


def compute_version(version_from, categories):
    major, minor, patch, suffix = parse_version(version_from)

    decision_table, overrides = load_version_policy()

    increments = set()
    for cat in categories:
        if cat not in decision_table:
            raise ValidationError(f"Unknown change category: {cat}")
        increments.add(decision_table[cat])

    # Apply override rules first
    for rule in overrides:
        if all(c in categories for c in rule["when"]):
            increments = {rule["then"]}

    # Collapse precedence
    if "MAJOR" in increments:
        return format_version(major + 1, 0, 0)
    if "MINOR" in increments:
        return format_version(major, minor + 1, 0)
    if "PATCH" in increments:
        return format_version(major, minor, patch + 1)
    if "SUFFIX" in increments:
        next_suffix = "a" if not suffix else chr(ord(suffix) + 1)
        return format_version(major, minor, patch, next_suffix)

    raise ValidationError("Unable to determine version increment")


# ------------------------------------------------------------
# VALIDATORS
# ------------------------------------------------------------

def validate_changelog(version):
    if not CHANGELOG_FILE.exists():
        raise ValidationError("CHANGELOG.md not found in mes/ directory")

    if version not in CHANGELOG_FILE.read_text():
        raise ValidationError(
            f"CHANGELOG.md missing entry for version {version}"
        )


def validate_file_scope(update):
    require_fields(update, ["target_file"])

    allowed = {
        "mes_scalp.py": MES_DIR / "mes_scalp.py",
        "mes_swing.py": MES_DIR / "mes_swing.py",
        "mes-run": MES_DIR / "mes-run",
    }

    target = update["target_file"]
    if target not in allowed:
        raise ValidationError(f"Target file not allowed: {target}")

    declared = update.get("declared_files", [target])
    for f in declared:
        if f not in allowed:
            raise ValidationError(f"Forbidden file modification: {f}")

    if "mes_scalp.py" in declared and "mes_swing.py" in declared:
        raise ValidationError("Cross-contamination detected")


def validate_ast(update):
    target = update["target_file"]
    if not target.endswith(".py"):
        return

    require_fields(update, ["content"])

    try:
        ast.parse(update["content"])
    except SyntaxError as e:
        raise ValidationError(
            f"Python syntax error in {target}: {e.msg} (line {e.lineno})"
        )


# ------------------------------------------------------------
# ENTRY POINT
# ------------------------------------------------------------

def validate_update(update):
    require_fields(update, ["type", "target_file", "version_from", "change_categories"])

    validate_file_scope(update)

    computed = compute_version(update["version_from"], update["change_categories"])
    update["version_to"] = computed

    validate_changelog(computed)
    validate_ast(update)
