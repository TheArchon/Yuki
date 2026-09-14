import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.enums import ButtonStyle
from pyrogram.types import InlineKeyboardMarkup

import config
from Client.premium import premium_button, premium_emoji

log = logging.getLogger("Yuki.Updater")
ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / ".yuki_update.json"
BACKUPS = ROOT / "backups" / "updater"
LOCK = asyncio.Lock()
AUTO_CHECK = os.getenv("AUTO_UPDATE_CHECK", "true").lower() in {"1", "true", "yes", "on"}
CHECK_INTERVAL = max(300, config.UPDATE_CHECK_INTERVAL)
PROTECTED = {
    ".env", ".git", "venv", ".venv", "__pycache__", "backups",
    ".yuki_update.json", ".yuki_update.json.tmp",
}


def _request(url):
    headers = {
        "User-Agent": "Yuki-Updater",
        "Accept": "application/vnd.github+json",
    }
    if config.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read()


def github_json(url):
    return json.loads(_request(url).decode("utf-8"))


def latest_commit():
    data = github_json(
        f"https://api.github.com/repos/{config.GITHUB_REPO}/commits/{config.GITHUB_BRANCH}"
    )
    return data["sha"], data.get("commit", {}).get("message", "").splitlines()[0]


def latest_release():
    try:
        data = github_json(f"https://api.github.com/repos/{config.GITHUB_REPO}/releases/latest")
        return {"tag": data.get("tag_name", ""), "url": data.get("html_url", "")}
    except Exception:
        return {}


def read_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(**values):
    state = read_state()
    state.update(values)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE)


def local_commit():
    git = shutil.which("git")
    if git and (ROOT / ".git").exists():
        try:
            result = subprocess.run(
                [git, "rev-parse", "HEAD"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            return result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return read_state().get("commit")


def protected(path):
    return any(part in PROTECTED for part in Path(path).parts)


def safe_rel(path):
    p = Path(path)
    return not p.is_absolute() and ".." not in p.parts and not protected(p)


def extract_zip(archive, destination):
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            if not safe_rel(info.filename):
                raise RuntimeError(f"Unsafe archive path: {info.filename}")
        z.extractall(destination)
    roots = [p for p in destination.iterdir() if p.is_dir()]
    if len(roots) != 1:
        raise RuntimeError("Invalid GitHub archive layout.")
    return roots[0]


def repo_files(source):
    return [
        p.relative_to(source)
        for p in source.rglob("*")
        if p.is_file() and safe_rel(p.relative_to(source))
    ]


def mutable_local_files():
    """Return all existing project files that the repository is allowed to replace."""
    return [
        p.relative_to(ROOT)
        for p in ROOT.rglob("*")
        if p.is_file() and safe_rel(p.relative_to(ROOT))
    ]


def backup_files(files, backup):
    """Back up the complete mutable project state, including files GitHub removed."""
    incoming = {Path(x) for x in files if safe_rel(x)}
    current = set(mutable_local_files())
    to_backup = current | incoming
    old = []
    new = []
    backup.mkdir(parents=True, exist_ok=True)

    for rel in sorted(to_backup, key=str):
        if not safe_rel(rel):
            continue
        src, dst = ROOT / rel, backup / rel
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            old.append(rel)
        elif not src.exists():
            new.append(rel)
    return old, new


def apply_files(source, files):
    """Synchronize project files with GitHub, including deletion of removed files."""
    incoming = {Path(x) for x in files if safe_rel(x)}
    current = set(mutable_local_files())

    for rel in sorted(current - incoming, key=str, reverse=True):
        if safe_rel(rel):
            path = ROOT / rel
            if path.is_file():
                path.unlink()

    for rel in sorted(incoming, key=str):
        src, dst = source / rel, ROOT / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def rollback(backup, old, new):
    """Restore exactly the state that existed before the update."""
    backup_files_set = {
        p.relative_to(backup)
        for p in backup.rglob("*")
        if p.is_file() and safe_rel(p.relative_to(backup))
    }
    current = set(mutable_local_files())
    for rel in sorted(current - backup_files_set, key=str, reverse=True):
        path = ROOT / rel
        if path.is_file() and safe_rel(rel):
            path.unlink()

    for rel in sorted(backup_files_set, key=str):
        src, dst = backup / rel, ROOT / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def validate(files):
    for rel in files:
        if rel.suffix == ".py" and safe_rel(rel):
            path = ROOT / rel
            compile(path.read_text(encoding="utf-8"), str(path), "exec")


def requirements_changed(backup):
    current, old = ROOT / "requirements.txt", backup / "requirements.txt"
    if not current.exists():
        return old.exists()
    if not old.exists():
        return True
    return current.read_bytes() != old.read_bytes()


def install_requirements(path=None):
    req = Path(path) if path else ROOT / "requirements.txt"
    if not req.exists():
        return
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(req)],
        cwd=ROOT,
        timeout=900,
        check=True,
    )


def changed_summary(files):
    names = sorted(str(x).replace("\\", "/") for x in files if safe_rel(x))
    return names if len(names) <= 15 else names[:15] + [f"... +{len(names)-15} more"]


def latest_backup():
    if not BACKUPS.exists():
        return None
    dirs = sorted((p for p in BACKUPS.iterdir() if p.is_dir()), reverse=True)
    return dirs[0] if dirs else None


def restore_backup(backup):
    if not backup or not backup.exists():
        raise RuntimeError("Backup not found.")
    files = [
        p.relative_to(backup)
        for p in backup.rglob("*")
        if p.is_file() and safe_rel(p.relative_to(backup))
    ]
    if not files:
        raise RuntimeError("Backup is empty.")
    rollback(backup, [], [])
    req = backup / "requirements.txt"
    if req.exists():
        install_requirements(req)
    return len(files)


def restart_bot():
    os.execv(sys.executable, [sys.executable, *sys.argv])


def authorized(user_id):
    return user_id == config.OWNER_ID or user_id in config.SUDO_USERS


def keyboard():
    return InlineKeyboardMarkup([
        [
            premium_button("Update", "update", ButtonStyle.SUCCESS, callback_data="yupd:update"),
            premium_button("Cancel", "cancel", ButtonStyle.DANGER, callback_data="yupd:cancel"),
        ],
        [
            premium_button("Check Again", "update", ButtonStyle.SUCCESS, callback_data="yupd:check"),
            premium_button("History", "stats", ButtonStyle.PRIMARY, callback_data="yupd:history"),
        ],
        [premium_button("Rollback", "back", ButtonStyle.DANGER, callback_data="yupd:rollback")],
    ])


def history_text():
    items = read_state().get("history", [])[:10]
    if not items:
        return f"{premium_emoji('stats', '📜')} <b>No update history found.</b>"
    lines = [f"{premium_emoji('stats', '📜')} <b>Update History</b>", ""]
    for item in items:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(item.get("time", 0)))
        lines.append(
            f"<code>{when}</code> • <code>{str(item.get('to', ''))[:7]}</code> • "
            f"<b>{item.get('status', 'unknown')}</b>"
        )
    return "\n".join(lines)


def add_history(entry):
    state = read_state()
    items = state.get("history", [])
    items.insert(0, entry)
    state["history"] = items[:20]
    state["repository"] = config.GITHUB_REPO
    state["branch"] = config.GITHUB_BRANCH
    state["updated_at"] = int(time.time())
    save_state(**state)


async def update_bot():
    async with LOCK:
        latest, message = await asyncio.to_thread(latest_commit)
        current = local_commit()
        if current == latest:
            return False, latest, message, [], None

        with tempfile.TemporaryDirectory(prefix="yuki-update-") as tmp_name:
            tmp = Path(tmp_name)
            archive, source_dir = tmp / "update.zip", tmp / "source"
            url = f"https://github.com/{config.GITHUB_REPO}/archive/refs/heads/{config.GITHUB_BRANCH}.zip"
            archive.write_bytes(await asyncio.to_thread(_request, url))
            source_dir.mkdir()
            source = await asyncio.to_thread(extract_zip, archive, source_dir)
            files = repo_files(source)
            if not files:
                raise RuntimeError("GitHub update is empty.")

            backup = BACKUPS / time.strftime("%Y%m%d-%H%M%S")
            old, new = await asyncio.to_thread(backup_files, files, backup)
            try:
                await asyncio.to_thread(apply_files, source, files)
                await asyncio.to_thread(validate, files)
                if requirements_changed(backup):
                    await asyncio.to_thread(install_requirements)

                save_state(
                    commit=latest,
                    repository=config.GITHUB_REPO,
                    branch=config.GITHUB_BRANCH,
                    last_backup=str(backup.relative_to(ROOT)),
                )
                add_history({
                    "time": int(time.time()),
                    "from": current,
                    "to": latest,
                    "message": message[:500],
                    "backup": str(backup.relative_to(ROOT)),
                    "status": "success",
                    "files": changed_summary(files),
                })
                return True, latest, message, changed_summary(files), backup
            except Exception:
                await asyncio.to_thread(rollback, backup, old, new)
                old_req = backup / "requirements.txt"
                if old_req.exists():
                    try:
                        await asyncio.to_thread(install_requirements, old_req)
                    except Exception:
                        log.exception("Dependency rollback failed")
                add_history({
                    "time": int(time.time()),
                    "from": current,
                    "to": latest,
                    "message": message[:500],
                    "backup": str(backup.relative_to(ROOT)),
                    "status": "failed_rolled_back",
                    "files": changed_summary(files),
                })
                raise


async def check_message(message):
    latest, commit_message = await asyncio.to_thread(latest_commit)
    current = local_commit()
    if current == latest:
        await message.edit_text(
            f"{premium_emoji('confirm', '✅')} <b>Yuki is already up to date.</b>\n\n"
            f"Version: <code>{latest[:7]}</code>",
            reply_markup=keyboard(),
        )
        return

    release = await asyncio.to_thread(latest_release)
    release_text = release.get("tag") or "None"
    await message.edit_text(
        f"{premium_emoji('update', '🔄')} <b>New update available.</b>\n\n"
        f"Current: <code>{(current or 'unknown')[:7]}</code>\n"
        f"Latest: <code>{latest[:7]}</code>\n\n"
        f"{premium_emoji('default', '📝')} <b>{commit_message[:500]}</b>\n"
        f"Release: <code>{release_text[:80]}</code>\n\nInstall this update?",
        reply_markup=keyboard(),
    )


@Client.on_message(filters.command("update") & filters.private)
async def update_command(client, message):
    if not message.from_user or not authorized(message.from_user.id):
        return
    if LOCK.locked():
        await message.reply_text("An update is already running.")
        return
    msg = await message.reply_text(f"{premium_emoji('update', '🔄')} <b>Checking for updates...</b>")
    try:
        await check_message(msg)
    except Exception as e:
        await msg.edit_text(
            f"{premium_emoji('cancel', '❌')} <b>Update check failed.</b>\n\n<code>{str(e)[:900]}</code>",
            reply_markup=keyboard(),
        )


@Client.on_callback_query(filters.regex(r"^yupd:(update|cancel|check|history|rollback)$"))
async def update_callback(client, query):
    if not query.from_user or not authorized(query.from_user.id):
        await query.answer("You are not authorized.", show_alert=True)
        return

    action = query.data.split(":", 1)[1]
    if action == "cancel":
        await query.answer("Cancelled.")
        await query.message.edit_text(f"{premium_emoji('cancel', '❌')} <b>Update cancelled.</b>")
        return

    if action == "history":
        await query.answer()
        await query.message.edit_text(history_text(), reply_markup=keyboard())
        return

    if action == "check":
        await query.answer()
        try:
            await query.message.edit_text(f"{premium_emoji('update', '🔄')} <b>Checking...</b>")
            await check_message(query.message)
        except Exception as e:
            await query.message.edit_text(
                f"{premium_emoji('cancel', '❌')} <b>Check failed.</b>\n\n<code>{str(e)[:900]}</code>",
                reply_markup=keyboard(),
            )
        return

    if action == "rollback":
        if LOCK.locked():
            await query.answer("An update is already running.", show_alert=True)
            return
        backup = latest_backup()
        if not backup:
            await query.answer("No backup available.", show_alert=True)
            return
        await query.answer("Rolling back...")
        try:
            async with LOCK:
                count = await asyncio.to_thread(restore_backup, backup)
            add_history({
                "time": int(time.time()),
                "from": local_commit(),
                "to": "rollback",
                "message": "Manual rollback",
                "backup": str(backup.relative_to(ROOT)),
                "status": "rollback_success",
                "files": [f"Restored {count} files"],
            })
            await query.message.edit_text(
                f"{premium_emoji('confirm', '✅')} <b>Rollback completed.</b>\n\nRestarting Yuki..."
            )
            await asyncio.sleep(2)
            await asyncio.to_thread(restart_bot)
        except Exception as e:
            await query.message.edit_text(
                f"{premium_emoji('cancel', '❌')} <b>Rollback failed.</b>\n\n<code>{str(e)[:900]}</code>",
                reply_markup=keyboard(),
            )
        return

    if LOCK.locked():
        await query.answer("An update is already running.", show_alert=True)
        return

    await query.answer("Updating...")
    try:
        await query.message.edit_text(
            f"{premium_emoji('update', '🔄')} <b>Updating Yuki...</b>\n\n"
            "Backup and syntax validation are running."
        )
        updated, commit, message, files, backup = await update_bot()
        if not updated:
            await query.message.edit_text(
                f"{premium_emoji('confirm', '✅')} <b>Yuki is already up to date.</b>",
                reply_markup=keyboard(),
            )
            return
        changed = "\n".join(f"• <code>{x}</code>" for x in files)
        await query.message.edit_text(
            f"{premium_emoji('confirm', '✅')} <b>Update installed successfully.</b>\n\n"
            f"Version: <code>{commit[:7]}</code>\n"
            f"{premium_emoji('default', '📝')} <b>{message[:400]}</b>\n\n"
            f"<b>Changed files:</b>\n{changed or '• None'}\n\n"
            "Backup created.\nRestarting Yuki..."
        )
        await asyncio.sleep(2)
        await asyncio.to_thread(restart_bot)
    except Exception as e:
        log.exception("Update failed")
        try:
            await query.message.edit_text(
                f"{premium_emoji('cancel', '❌')} <b>Update failed and was rolled back.</b>\n\n"
                f"<code>{str(e)[:900]}</code>",
                reply_markup=keyboard(),
            )
        except Exception:
            pass


@Client.on_message(filters.command("updatehistory") & filters.private)
async def update_history(client, message):
    if message.from_user and authorized(message.from_user.id):
        await message.reply_text(history_text(), reply_markup=keyboard())


async def auto_update_checker(client):
    if not AUTO_CHECK:
        return
    await asyncio.sleep(60)
    while True:
        try:
            if not LOCK.locked():
                latest, commit_message = await asyncio.to_thread(latest_commit)
                current = local_commit()
                if current and current != latest and config.OWNER_ID:
                    state = read_state()
                    if state.get("last_notice_commit") != latest:
                        await client.send_message(
                            config.OWNER_ID,
                            f"{premium_emoji('update', '🔄')} <b>New Yuki update available.</b>\n\n"
                            f"Current: <code>{current[:7]}</code>\n"
                            f"Latest: <code>{latest[:7]}</code>\n\n"
                            f"{commit_message[:500]}",
                            reply_markup=keyboard(),
                        )
                        save_state(last_notice_commit=latest, last_notice_at=int(time.time()))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Automatic update check failed")
        await asyncio.sleep(CHECK_INTERVAL)
