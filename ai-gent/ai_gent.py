#!/usr/bin/env python3
"""
AI-GENT v0.9
Governance + GitOps Steward for leo
Capabilities:
- Validate update intent (rules, scope, versions, AST)
- Create isolated AI branches
- Enforce diff safety before commit
- Auto-stub CHANGELOG entries (strictly idempotent + correct precedence)
- Commit and push safely (no merge, no deploy)
"""
import sys
from pathlib import Path
import yaml
import re
from ops.validate import validate_update, ValidationError
from ops.diffcheck import enforce_diff_safety, DiffError
from ops.github import (
    ensure_clean_worktree,
    current_branch,
    create_branch,
    ensure_ai_branch,
    branch_exists_remote,
    write_file,
    commit_files,
    push_branch,
    GitError,
)

REPO_ROOT = Path.home() / "leo-services"
CHANGELOG_PATH = REPO_ROOT / "mes" / "CHANGELOG.md"


def load_update(path: Path):
    with open(path, "r") as f:
        return yaml.safe_load(f)


def determine_increment_class(update: dict) -> str:
    """
    Deterministic collapsed precedence per VERSION_POLICY.md:
    MAJOR > MINOR > PATCH > SUFFIX
    Override: strategy_behavior + hotfix_critical → SUFFIX
    """
    categories = set(update.get("change_categories", []))

    # Explicit override first
    if "strategy_behavior" in categories and "hotfix_critical" in categories:
        return "SUFFIX"

    # Fixed priority mapping (highest to lowest)
    priority = [
        ("risk_model_rewrite", "MAJOR"),
        ("strategy_behavior", "MINOR"),
        ("execution_logic", "PATCH"),
        ("diagnostics_logging", "PATCH"),
        ("infrastructure", "PATCH"),
        ("hotfix_critical", "SUFFIX"),
    ]

    for cat, inc_class in priority:
        if cat in categories:
            return inc_class

    return "PATCH"  # safe default


def ensure_changelog_entry(update: dict):
    """
    Idempotent: appends a stub entry only if version_to is missing.
    Returns True if file was modified, False otherwise.
    """
    version = update["version_to"]

    # Snapshot before state
    if CHANGELOG_PATH.exists():
        before_content = CHANGELOG_PATH.read_text(encoding="utf-8")
    else:
        before_content = ""
        # Create minimal valid changelog
        CHANGELOG_PATH.write_text("# MES Change Log\n\n", encoding="utf-8")

    current_content = CHANGELOG_PATH.read_text(encoding="utf-8")

    # Already present? → nothing to do
    if re.search(rf"^##\s+{re.escape(version)}\b", current_content, re.MULTILINE):
        return False

    inc_class = determine_increment_class(update)
    summary = update.get("summary", "Automated update – no summary provided").strip()
    if summary and not summary.endswith("."):
        summary += "."

    new_entry = f"\n## {version} ({inc_class})\n- {summary}\n"
    CHANGELOG_PATH.write_text(current_content + new_entry, encoding="utf-8")
    return True  # file was modified


def usage():
    print("Usage: ai-gent <validate|commit|push> <update.yaml>")
    sys.exit(1)


def main():
    if len(sys.argv) < 3:
        usage()
    command = sys.argv[1]
    update_path = Path(sys.argv[2])
    if not update_path.exists():
        print(f"Update file not found: {update_path}")
        sys.exit(1)

    update = load_update(update_path)

    # --------------------------------------------------
    # 1. GOVERNANCE VALIDATION (always first)
    # --------------------------------------------------
    try:
        validate_update(update)
    except ValidationError as e:
        print(f"Validation failed: {e}")
        sys.exit(1)

    if command == "validate":
        print("Validation passed")
        sys.exit(0)

    # --------------------------------------------------
    # 2. COMMIT WORKFLOW
    # --------------------------------------------------
    if command == "commit":
        try:
            ensure_clean_worktree()
            target = update["target_file"]
            version = update["version_to"]
            branch_name = f"ai/{target.replace('.', '-')}-{version}"
            print(f"→ Creating branch: {branch_name}")
            create_branch(branch_name)

            rel_path = f"mes/{target}"
            write_file(rel_path, update["content"])

            # ← CORRECTED: Idempotent + accurate CHANGELOG handling
            print("→ Ensuring CHANGELOG entry...")
            changelog_modified = ensure_changelog_entry(update)

            # Diff safety enforcement (must happen BEFORE commit)
            enforce_diff_safety(update)

            commit_msg = (
                f"{target} {version}\n\n"
                f"Change type: {update.get('type', 'unspecified')}\n"
                f"Managed by AI-GENT"
            )

            files_to_commit = [rel_path]
            if changelog_modified:
                files_to_commit.append("mes/CHANGELOG.md")

            commit_files(commit_msg, files_to_commit)
            print("Commit created (CHANGELOG updated only if needed)")
            print(f"Branch: {branch_name}")
            print("ℹ No merge or push performed")
        except (ValidationError, DiffError, GitError, Exception) as e:
            print(f"Commit failed: {e}")
            sys.exit(1)
        sys.exit(0)

    # --------------------------------------------------
    # 3. PUSH WORKFLOW (NO MERGE)
    # --------------------------------------------------
    if command == "push":
        try:
            ensure_clean_worktree()
            branch = current_branch()
            ensure_ai_branch(branch)
            if branch_exists_remote(branch):
                raise GitError(f"Branch already exists on origin: {branch}")
            print(f"→ Pushing branch to origin: {branch}")
            push_branch(branch)
            print("Branch pushed successfully")
            print("ℹ No merge performed")
        except (GitError, Exception) as e:
            print(f"Push failed: {e}")
            sys.exit(1)
        sys.exit(0)

    usage()


if __name__ == "__main__":
    main()
