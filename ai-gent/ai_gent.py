#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI-GENT v0.11
Governance + GitOps Steward for leo
Enhancements:
- Uses updated Git ops (quiet pushes, pre-push remote history check)
- Staged diff safety
- Better branch reuse messaging
- Optional post-push deploy tag (commented, easy enable)
"""
from __future__ import annotations
import sys
import re
from pathlib import Path
from typing import Dict, Any, Iterable
import yaml
from ops.validate import validate_update, ValidationError
from ops.diffcheck import enforce_diff_safety, DiffError
from ops.github import (
    ensure_clean_worktree,
    current_branch,
    create_branch,          # Modern switch -c
    ensure_ai_branch,
    branch_exists_remote,
    write_file,
    commit_files,
    push_branch,            # Now quiet + pre-push safety
    GitError,
)
# Optional: import deploy for post-push tagging
# from ops.deploy import deploy as run_deploy

REPO_ROOT = Path.home() / "leo-services"
CHANGELOG_PATH = REPO_ROOT / "mes" / "CHANGELOG.md"

# ------------------------------
# YAML load
# ------------------------------
def load_update(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

# ------------------------------
# Version increment class (legacy compat; harmless if unused)
# ------------------------------
def determine_increment_class(update: dict) -> str:
    categories = set(update.get("change_categories", []))
    if "strategy_behavior" in categories and "hotfix_critical" in categories:
        return "SUFFIX"
    priority = [
        ("risk_model_rewrite", "MAJOR"),
        ("strategy_behavior", "MINOR"),
        ("execution_logic", "PATCH"),
        ("diagnostics_logging", "PATCH"),
        ("infrastructure", "PATCH"),
        ("hotfix_critical", "SUFFIX"),
    ]
    for cat, cls in priority:
        if cat in categories:
            return cls
    return "PATCH"

# ------------------------------
# CHANGELOG handling
# ------------------------------
def ensure_changelog_entry(update: dict) -> bool:
    version = update.get("version_to")
    if not version:
        return False
    if CHANGELOG_PATH.exists():
        current_content = CHANGELOG_PATH.read_text(encoding="utf-8")
    else:
        current_content = "# MES Change Log\n\n"
        CHANGELOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if re.search(rf"^##\s+{re.escape(str(version))}\b", current_content, re.MULTILINE):
        return False
    inc_class = determine_increment_class(update)
    summary = (update.get("summary") or update.get("tagline") or "Automated update").strip()
    if summary and not summary.endswith("."):
        summary += "."
    new_entry = f"\n## {version} ({inc_class})\n- {summary}\n"
    CHANGELOG_PATH.write_text(current_content + new_entry, encoding="utf-8")
    return True

# ------------------------------
# Parameter patching
# ------------------------------
_PARAM_LINE = re.compile(r"^([A-Z][A-Z0-9_]*\s*=\s*)(.+)$", re.MULTILINE)

def _format_value_for_python(value: Any) -> str:
    s = str(value).strip()
    if re.fullmatch(r"[+-]?(\d+(\.\d*)?|\.\d+)", s):
        return s
    if s in {"True", "False", "None"}:
        return s
    return repr(s)

def patch_parameter_in_file(rel_path: str, param: str, new_value: Any) -> bool:
    path = REPO_ROOT / rel_path
    text = path.read_text(encoding="utf-8")
    replaced = False
    def _repl(m: re.Match) -> str:
        nonlocal replaced
        if text[m.start():m.end()].lstrip().startswith(f"{param}"):
            replaced = True
            return f"{m.group(1)}{_format_value_for_python(new_value)}"
        return m.group(0)
    new_text = _PARAM_LINE.sub(_repl, text)
    if not replaced:
        raise GitError(f"Parameter '{param}' not found in {rel_path}")
    path.write_text(new_text, encoding="utf-8")
    return True

# ------------------------------
# UI
# ------------------------------
def usage():
    print("Usage: ai-gent <validate|commit|push> <update.yaml>")
    sys.exit(1)

# ------------------------------
# Main
# ------------------------------
def main():
    if len(sys.argv) < 3:
        usage()
    command = sys.argv[1]
    update_path = Path(sys.argv[2])
    if not update_path.exists():
        print(f"Update file not found: {update_path}")
        sys.exit(1)

    update = load_update(update_path)

    try:
        validate_update(update)
    except ValidationError as e:
        print(f"Validation failed: {e}")
        sys.exit(1)

    if command == "validate":
        print("Validation passed")
        sys.exit(0)

    if command == "commit":
        try:
            ensure_clean_worktree()
            utype = update.get("type")
            target = update["target_file"]
            rel_path = f"mes/{target}"

            version_to = update.get("version_to")
            if utype == "logic" and version_to:
                branch_name = f"ai/{target.replace('.', '-')}-{version_to}"
            else:
                branch_name = f"ai/{target.replace('.', '-')}-param"

            print(f"→ Creating/reusing branch: {branch_name}")
            create_branch(branch_name)  # Modern + quiet

            if utype == "logic":
                content = update["content"]
                write_file(rel_path, content)
            elif utype == "parameter":
                param = update["parameter"]
                new = update["change"]["to"]
                patch_parameter_in_file(rel_path, param, new)
            else:
                raise GitError(f"Unknown change type: {utype}")

            print("→ Ensuring CHANGELOG entry (if version provided)...")
            changelog_modified = ensure_changelog_entry(update)

            enforce_diff_safety(update)

            msg_head = update.get("tagline") or update.get("statement") or f"{utype} update"
            commit_msg = f"{target} – {msg_head}\n\nChange type: {utype}\nManaged by AI-GENT"

            files_to_commit: Iterable[str] = [rel_path]
            if changelog_modified:
                files_to_commit = [*files_to_commit, "mes/CHANGELOG.md"]

            commit_files(commit_msg, files_to_commit)
            print("Commit created")
            print(f"Branch ready: {branch_name}")
            print("Next: python ai_gent.py push <update.yaml>")
        except (ValidationError, DiffError, GitError, Exception) as e:
            print(f"Commit failed: {e}")
            sys.exit(1)

    elif command == "push":
        try:
            ensure_clean_worktree()
            branch = current_branch()
            ensure_ai_branch(branch)
            print(f"→ Pushing branch: {branch}")
            push_branch(branch)  # Now with quiet + pre-push safety
            print("Branch pushed successfully")

            # Optional: auto-tag deploy after successful push
            # print("→ Running post-push deploy tagging...")
            # run_deploy()

            print("All done! Review on GitHub and merge when ready.")
        except (GitError, Exception) as e:
            print(f"Push failed: {e}")
            sys.exit(1)

    else:
        usage()

if __name__ == "__main__":
    main()