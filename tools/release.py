import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)

import app_meta  # noqa: E402
import updater  # noqa: E402

DEFAULT_SIGNTOOL = r"C:\Program Files (x86)\Windows Kits\10\bin\10.0.26100.0\x64\signtool.exe"


def run(cmd, cwd=REPO_ROOT, check=True, capture_output=False):
    return subprocess.run(
        cmd,
        cwd=cwd,
        check=check,
        capture_output=capture_output,
        text=True,
    )


def git(*args, capture_output=True):
    result = run(["git", *args], capture_output=capture_output)
    return (result.stdout or "").strip()


def parse_version_tag(tag):
    return updater.parse_version(tag)


def find_last_version_tag():
    tag = ""
    try:
        tag = git("describe", "--tags", "--abbrev=0")
    except subprocess.CalledProcessError:
        tag = ""
    if tag and parse_version_tag(tag):
        return tag

    try:
        tags = git(
            "for-each-ref",
            "--merged",
            "HEAD",
            "--sort=-creatordate",
            "--format=%(refname:short)",
            "refs/tags",
        ).splitlines()
    except subprocess.CalledProcessError:
        tags = []

    for candidate in tags:
        if parse_version_tag(candidate):
            return candidate
    return None


def get_commits_since(tag):
    range_ref = f"{tag}..HEAD" if tag else "HEAD"
    raw = git(
        "log",
        range_ref,
        "--pretty=format:%s%n%b%x1e",
    )
    commits = []
    for entry in raw.split("\x1e"):
        entry = entry.strip()
        if not entry:
            continue
        lines = entry.splitlines()
        subject = lines[0].strip() if lines else ""
        body = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
        commits.append({"subject": subject, "body": body})
    return commits


def classify_commit(commit):
    subject = commit["subject"]
    body = commit["body"]
    text = f"{subject}\n{body}".lower()
    if "breaking change" in text or re.search(r"^[a-z]+\!:", subject.lower()):
        return "Breaking"
    if subject.lower().startswith("feat") or "feature" in text:
        return "Features"
    if subject.lower().startswith("fix") or "fix" in text or "bug" in text:
        return "Fixes"
    return "Other"


def summarize_commits(commits):
    sections = {"Breaking": [], "Features": [], "Fixes": [], "Other": []}
    for commit in commits:
        subject = commit["subject"]
        if not subject or subject.lower().startswith("merge"):
            continue
        sections[classify_commit(commit)].append(subject)
    return sections


def build_release_notes(commits):
    sections = summarize_commits(commits)
    output = []
    for title in ("Breaking", "Features", "Fixes", "Other"):
        items = sections[title]
        if not items:
            continue
        output.append(f"## {title}")
        output.extend([f"- {item}" for item in items])
        output.append("")
    return "\n".join(output).strip() or "## Other\n- No notable changes."


def determine_bump(commits):
    sections = summarize_commits(commits)
    if sections["Breaking"]:
        return "major"
    if sections["Features"]:
        return "minor"
    return "patch"


def bump_version(base, bump):
    major, minor, patch = base
    if bump == "major":
        return major + 1, 0, 0
    if bump == "minor":
        return major, minor + 1, 0
    return major, minor, patch + 1


def format_version(version_tuple):
    return f"{version_tuple[0]}.{version_tuple[1]}.{version_tuple[2]}"


def update_version_file(new_version):
    path = os.path.join(REPO_ROOT, "app_meta.py")
    with open(path, "r", encoding="utf-8") as handle:
        data = handle.read()
    updated = re.sub(
        r'APP_VERSION\s*=\s*"[^\"]+"',
        f'APP_VERSION = "{new_version}"',
        data,
    )
    if data == updated:
        raise RuntimeError("Failed to update APP_VERSION in app_meta.py.")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(updated)


def clean_build_artifacts():
    for folder in ("build", "dist"):
        path = os.path.join(REPO_ROOT, folder)
        if os.path.isdir(path):
            shutil.rmtree(path)


def run_pyinstaller():
    run(["pyinstaller", "--noconfirm", "main.spec"])


def sign_executable(exe_path):
    signtool = os.environ.get("SIGNTOOL_PATH", DEFAULT_SIGNTOOL)
    if not os.path.exists(signtool):
        raise RuntimeError(f"signtool.exe not found at: {signtool}")
    run(
        [
            signtool,
            "sign",
            "/fd",
            "SHA256",
            "/tr",
            "http://timestamp.digicert.com",
            "/td",
            "SHA256",
            "/a",
            exe_path,
        ]
    )


def get_signing_thumbprint(exe_path):
    override = os.environ.get("SIGN_CERT_THUMBPRINT", "").strip()
    if override:
        return override
    result = run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                f"$sig = Get-AuthenticodeSignature -FilePath '{exe_path}'; "
                "if ($sig.SignerCertificate) { $sig.SignerCertificate.Thumbprint }"
            ),
        ],
        check=False,
        capture_output=True,
    )
    thumbprint = (result.stdout or "").strip()
    return thumbprint


def zip_folder(source_dir, zip_path):
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, _, files in os.walk(source_dir):
            for filename in files:
                full_path = os.path.join(root, filename)
                rel_path = os.path.relpath(full_path, source_dir)
                zipf.write(full_path, rel_path)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def build_assets(version, release_notes, signing_thumbprint=None):
    dist_dir = os.path.join(REPO_ROOT, "dist", "iptvclient")
    if not os.path.isdir(dist_dir):
        raise RuntimeError("Build output not found at dist\\iptvclient.")

    assets_dir = os.path.join(REPO_ROOT, "dist", "release")
    ensure_dir(assets_dir)

    asset_filename = app_meta.UPDATE_ASSET_TEMPLATE.format(app=app_meta.APP_NAME, version=version)
    asset_path = os.path.join(assets_dir, asset_filename)
    zip_folder(dist_dir, asset_path)

    latest_filename = app_meta.UPDATE_ASSET_LATEST.format(app=app_meta.APP_NAME)
    latest_path = os.path.join(assets_dir, latest_filename)
    shutil.copy2(asset_path, latest_path)

    asset_sha = sha256_file(asset_path)
    download_url = (
        f"https://github.com/{app_meta.GITHUB_OWNER}/{app_meta.GITHUB_REPO}/releases/download/"
        f"v{version}/{asset_filename}"
    )
    summary = updater.summarize_release_notes(release_notes)
    manifest_data = updater.build_manifest(
        version=version,
        asset_filename=asset_filename,
        download_url=download_url,
        sha256=asset_sha,
        release_notes_summary=summary,
        signing_thumbprint=signing_thumbprint,
    )
    manifest_path = os.path.join(assets_dir, app_meta.UPDATE_MANIFEST_NAME)
    with open(manifest_path, "w", encoding="utf-8") as handle:
        handle.write(json_dump(manifest_data))

    notes_path = os.path.join(assets_dir, "release_notes.md")
    with open(notes_path, "w", encoding="utf-8") as handle:
        handle.write(release_notes)

    return {
        "asset_path": asset_path,
        "latest_path": latest_path,
        "manifest_path": manifest_path,
        "notes_path": notes_path,
    }


def json_dump(payload):
    return json.dumps(payload, indent=2)


def git_commit_and_tag(version):
    run(["git", "add", "app_meta.py"])
    run(["git", "commit", "-m", f"chore(release): v{version}"])
    run(["git", "tag", f"v{version}"])


def git_push(version):
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    run(["git", "push", "origin", branch])
    run(["git", "push", "origin", f"v{version}"])


def gh_release_create(version, assets):
    cmd = [
        "gh",
        "release",
        "create",
        f"v{version}",
        assets["asset_path"],
        assets["latest_path"],
        assets["manifest_path"],
        "--title",
        f"v{version}",
        "--notes-file",
        assets["notes_path"],
    ]
    run(cmd)


def print_dry_run(version, tag, bump, assets):
    print("Dry run summary:")
    print(f"- last_tag: {tag or 'none'}")
    print(f"- bump: {bump}")
    print(f"- next_version: {version}")
    print(f"- build: pyinstaller --noconfirm main.spec")
    print(f"- sign: {app_meta.EXE_NAME}")
    print(f"- zip: {assets['asset_path']}")
    print(f"- manifest: {assets['manifest_path']}")
    print(f"- release: gh release create v{version} ...")


def compute_next_version():
    tag = find_last_version_tag()
    base_version = None
    if tag:
        base_version = parse_version_tag(tag)

    if not base_version:
        base_version = (1, 4, 2)

    commits = get_commits_since(tag)
    bump = determine_bump(commits)
    next_version = bump_version(base_version, bump)
    return tag, format_version(next_version), commits, bump


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["release", "build", "dry-run"])
    args = parser.parse_args()

    if args.mode == "release":
        tag, next_version, commits, bump = compute_next_version()
        release_notes = build_release_notes(commits)
        update_version_file(next_version)
        clean_build_artifacts()
        run_pyinstaller()
        exe_path = os.path.join(REPO_ROOT, "dist", "iptvclient", app_meta.EXE_NAME)
        sign_executable(exe_path)
        signing_thumbprint = get_signing_thumbprint(exe_path)
        assets = build_assets(next_version, release_notes, signing_thumbprint)
        git_commit_and_tag(next_version)
        git_push(next_version)
        gh_release_create(next_version, assets)
        return

    if args.mode == "build":
        release_notes = "## Other\n- Local build."
        clean_build_artifacts()
        run_pyinstaller()
        exe_path = os.path.join(REPO_ROOT, "dist", "iptvclient", app_meta.EXE_NAME)
        sign_executable(exe_path)
        signing_thumbprint = get_signing_thumbprint(exe_path)
        build_assets(app_meta.APP_VERSION, release_notes, signing_thumbprint)
        return

    if args.mode == "dry-run":
        tag, next_version, commits, bump = compute_next_version()
        release_notes = build_release_notes(commits)
        assets = {
            "asset_path": os.path.join(REPO_ROOT, "dist", "release", "asset.zip"),
            "manifest_path": os.path.join(REPO_ROOT, "dist", "release", app_meta.UPDATE_MANIFEST_NAME),
        }
        print_dry_run(next_version, tag, bump, assets)
        print("")
        print(release_notes)


if __name__ == "__main__":
    main()
