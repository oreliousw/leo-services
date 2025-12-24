"""
ai-gent/ops/changelog.py — Auto-update CHANGELOG.md from YAML change files
Improved: newest-first, graceful YAML errors, deduplication, standard format
"""
import yaml
from pathlib import Path
from datetime import datetime

REPO_ROOT = Path.cwd()  # or Path.home() / "leo-services" if called from elsewhere
CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"
YAML_DIR = REPO_ROOT / "ai-gent"  # adjust if your change YAMLs live elsewhere

def load_change_yamls():
    """Load all change YAMLs, skip malformed ones gracefully."""
    changes = []
    for yaml_path in YAML_DIR.rglob("*.yaml"):
        try:
            data = yaml.safe_load(yaml_path.read_text())
            if not data or "timestamp" not in data:
                continue
            changes.append({
                "timestamp": data["timestamp"],
                "type": data["type"],
                "target": data["target_file"],
                "content": data.get("content", data.get("statement", data.get("tagline", ""))).strip(),
                "path": yaml_path,
            })
        except Exception as e:
            print(f"⚠️  Skipping malformed YAML {yaml_path}: {e}")
    return changes

def dedupe_and_sort(changes):
    """Remove duplicates and sort newest first."""
    seen = set()
    unique = []
    for c in changes:
        key = (c["timestamp"], c["target"], c["content"])
        if key not in seen:
            seen.add(key)
            unique.append(c)
    # Newest first
    unique.sort(key=lambda x: x["timestamp"], reverse=True)
    return unique

def format_entry(change):
    ts = change["timestamp"]
    date_str = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d")
    type_label = change["type"].capitalize()
    return f"- **{type_label}** `{change['target']}` — {change['content']} ({date_str})"

def update_changelog():
    changes = load_change_yamls()
    if not changes:
        print("No valid change YAMLs found.")
        return

    processed = dedupe_and_sort(changes)
    new_entries = "\n".join(format_entry(c) for c in processed)

    if CHANGELOG_PATH.exists():
        content = CHANGELOG_PATH.read_text()
        # Split on first ## header (usually [Unreleased])
        parts = content.split("\n## ", 1)
        header = parts[0]
        rest = "## " + parts[1] if len(parts) > 1 else ""
    else:
        header = "# Changelog\n\n## [Unreleased]\n"
        rest = ""

    # Rebuild with new entries under [Unreleased]
    new_content = f"{header}## [Unreleased]\n\n{new_entries}\n\n{rest}".strip() + "\n"

    CHANGELOG_PATH.write_text(new_content)
    print(f"CHANGELOG.md updated with {len(processed)} new entries (newest on top).")

if __name__ == "__main__":
    update_changelog()