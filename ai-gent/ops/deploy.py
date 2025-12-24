"""
ai-gent/ops/deploy.py — Simple safe deploy for AI-GENT changes
Creates a dated git tag, pushes it, and optionally runs a local hook
"""
import subprocess
from pathlib import Path
from datetime import datetime

REPO_ROOT = Path.home() / "leo-services"

class DeployError(Exception):
    pass

def run(cmd, **kwargs):
    result = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        **kwargs,
    )
    if result.returncode != 0:
        raise DeployError(result.stderr.strip())
    return result.stdout.strip()

def ensure_clean_and_main():
    status = run(["git", "status", "--porcelain"])
    if status:
        raise DeployError("Working tree is not clean — commit or stash changes first")
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if branch != "main":
        raise DeployError(f"Deploy only allowed from 'main' branch (current: {branch})")

def create_and_push_tag():
    today = datetime.now().strftime("%Y.%m.%d")
    tag_name = f"ai-deploy-{today}"
    # Check if tag already exists locally or remote
    existing = run(["git", "tag", "-l", tag_name])
    if existing:
        raise DeployError(f"Tag {tag_name} already exists locally")
    remote_tags = run(["git", "ls-remote", "--tags", "origin", tag_name])
    if remote_tags:
        raise DeployError(f"Tag {tag_name} already exists on remote")

    print(f"Creating and pushing deploy tag: {tag_name}")
    run(["git", "tag", "-a", tag_name, "-m", f"AI-GENT automated deploy {today}"])
    run(["git", "push", "--quiet", "origin", tag_name])

def run_post_deploy_hook():
    """
    Optional: run something after successful tag push.
    Example ideas (uncomment one you want):
    """
    # Restart your MES trading service
    # run(["systemctl", "--user", "restart", "mes_auto.service"])
    
    # Touch a reload file that your trader watches
    # (REPO_ROOT / "deploy_trigger.txt").touch()
    
    # Or just print success
    print("Deploy tag pushed successfully. No post-deploy hook configured.")

def deploy():
    try:
        ensure_clean_and_main()
        create_and_push_tag()
        run_post_deploy_hook()
        print("🚀 AI-GENT deploy complete!")
    except DeployError as e:
        print(f"❌ Deploy failed: {e}")

if __name__ == "__main__":
    deploy()