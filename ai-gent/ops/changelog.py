"""
CHANGELOG management for AI-GENT
Auto-generates version stubs when missing
"""

from pathlib import Path
from datetime import date


class ChangelogError(Exception):
    pass


def ensure_changelog_entry(
    changelog_path: Path,
    version: str,
    change_type: str,
    summary: str,
):
    today = date.today().isoformat()

    if not changelog_path.exists():
        changelog_path.write_text("# MES Changelog\n\n")

    text = changelog_path.read_text()

    if version in text:
        return  # already present

    entry = (
        f"## {version} — {today}\n"
        f"**Type:** {change_type}\n\n"
        f"- {summary.strip()}\n\n"
    )

    # Prepend newest entries at top (after title)
    lines = text.splitlines()
    if lines and lines[0].startswith("#"):
        new_text = lines[0] + "\n\n" + entry + "\n".join(lines[1:]) + "\n"
    else:
        new_text = entry + text

    changelog_path.write_text(new_text)
