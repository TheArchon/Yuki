"""AI-powered profile safety guard for Yuki.

Checks a sender's first/last name, username, Telegram bio and (when available)
profile photo.  It uses OpenAI's multimodal moderation API and applies a
configurable, conservative punishment policy in groups/supergroups.

The plugin is deliberately fail-open: an API/Telegram error never breaks the
rest of Yuki's message handling.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus, ChatType
from pyrogram.errors import RPCError
from pyrogram.types import ChatPermissions

import config

try:
    from openai import AsyncOpenAI
except ImportError:  # pragma: no cover - dependency is declared in requirements.txt
    AsyncOpenAI = None

logger = logging.getLogger("Yuki.ProfileGuard")


# Strong commercial/adult-service phrases. These are only a supplement to AI
# moderation and are intentionally conservative to reduce false positives.
COMMERCIAL_ADULT_PATTERNS = (
    r"\bcall\s*girl\b",
    r"\bcall\s*boy\b",
    r"\bescort\s*(service|girl|boy)?\b",
    r"\bpaid\s*(sex|hookup|meet|dating|companionship)\b",
    r"\bsex\s*(service|for\s*sale|available)\b",
    r"\bsexual\s*service(s)?\b",
    r"\bprivate\s*(meet|meeting)\s*paid\b",
    r"\bsugar\s*(daddy|mommy)\s*(available|wanted)\b",
    r"\bfull\s*night\s*service\b",
)
COMMERCIAL_ADULT_RE = re.compile("|".join(COMMERCIAL_ADULT_PATTERNS), re.I)

TEXT_CACHE: dict[int, dict[str, Any]] = {}
PHOTO_CACHE: dict[int, dict[str, Any]] = {}
CACHE_LOCK = asyncio.Lock()


@dataclass
class Finding:
    category: str
    score: float
    source: str


@dataclass
class Analysis:
    findings: list[Finding]

    @property
    def unsafe(self) -> bool:
        return bool(self.findings)



def _float_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
        return max(0.0, min(1.0, value))
    except (TypeError, ValueError):
        return default


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
MODERATION_MODEL = os.getenv("PROFILE_GUARD_MODEL", "omni-moderation-latest")
TEXT_CACHE_SECONDS = _int_env("PROFILE_GUARD_CACHE_SECONDS", 900)
PHOTO_CACHE_SECONDS = _int_env("PROFILE_GUARD_PHOTO_CACHE_SECONDS", 3600)
MUTE_SECONDS = _int_env("PROFILE_GUARD_MUTE_SECONDS", 3600)
BAN_AFTER_VIOLATIONS = _int_env("PROFILE_GUARD_BAN_AFTER", 2)
MAX_PHOTO_BYTES = _int_env("PROFILE_GUARD_MAX_PHOTO_BYTES", 8 * 1024 * 1024)

# High thresholds make the system conservative. The moderation model itself
# remains the primary decision maker; these thresholds are a second safety gate.
THRESHOLDS = {
    "harassment": _float_env("PROFILE_GUARD_HARASSMENT_THRESHOLD", 0.90),
    "harassment/threatening": _float_env("PROFILE_GUARD_HARASSMENT_THRESHOLD", 0.90),
    "sexual": _float_env("PROFILE_GUARD_SEXUAL_THRESHOLD", 0.90),
    "sexual/minors": _float_env("PROFILE_GUARD_MINOR_THRESHOLD", 0.80),
    "illicit": _float_env("PROFILE_GUARD_ILLICIT_THRESHOLD", 0.90),
    "illicit/violent": _float_env("PROFILE_GUARD_ILLICIT_THRESHOLD", 0.90),
    "violence": _float_env("PROFILE_GUARD_VIOLENCE_THRESHOLD", 0.95),
    "violence/graphic": _float_env("PROFILE_GUARD_VIOLENCE_THRESHOLD", 0.95),
}

_client: AsyncOpenAI | None = None


def _openai_client() -> AsyncOpenAI | None:
    global _client
    if not API_KEY or AsyncOpenAI is None:
        return None
    if _client is None:
        _client = AsyncOpenAI(api_key=API_KEY, timeout=20.0, max_retries=1)
    return _client


def _category_score(result: Any, category: str) -> float:
    scores = getattr(result, "category_scores", None)
    if scores is None and isinstance(result, dict):
        scores = result.get("category_scores", {})
    if scores is None:
        return 0.0
    if hasattr(scores, "model_dump"):
        scores = scores.model_dump()
    elif hasattr(scores, "__dict__"):
        scores = vars(scores)
    try:
        return float(scores.get(category, 0.0))
    except (AttributeError, TypeError, ValueError):
        return 0.0


def _moderation_result(response: Any) -> Any | None:
    results = getattr(response, "results", None)
    if results is None and isinstance(response, dict):
        results = response.get("results")
    if not results:
        return None
    return results[0]


def _text_findings(text: str, result: Any) -> list[Finding]:
    findings: list[Finding] = []
    if not text:
        return findings

    for category, threshold in THRESHOLDS.items():
        score = _category_score(result, category)
        if score >= threshold:
            public_category = "adult/minor sexual" if category == "sexual/minors" else category
            findings.append(Finding(public_category, score, "openai-moderation"))

    # Paid/adult service promotion is not represented by a single moderation
    # category, so require a strong explicit phrase in the profile text.
    if COMMERCIAL_ADULT_RE.search(text):
        findings.append(Finding("paid/adult service solicitation", 1.0, "profile-rule"))
    return findings


async def moderate_text(text: str) -> Analysis:
    """Analyze profile text with OpenAI moderation."""
    client = _openai_client()
    if client is None or not text.strip():
        return Analysis([])
    try:
        response = await client.moderations.create(
            model=MODERATION_MODEL,
            input=[{"type": "text", "text": text[:8000]}],
        )
        result = _moderation_result(response)
        return Analysis(_text_findings(text, result)) if result else Analysis([])
    except Exception as exc:
        logger.warning("Text profile moderation failed: %s", exc)
        return Analysis([])


async def moderate_image(path: str) -> Analysis:
    """Analyze a downloaded profile photo with OpenAI multimodal moderation."""
    client = _openai_client()
    if client is None:
        return Analysis([])

    try:
        file_path = Path(path)
        if not file_path.exists() or file_path.stat().st_size > MAX_PHOTO_BYTES:
            return Analysis([])
        data = base64.b64encode(file_path.read_bytes()).decode("ascii")
        suffix = file_path.suffix.lower()
        mime = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }.get(suffix, "image/jpeg")
        response = await client.moderations.create(
            model=MODERATION_MODEL,
            input=[{"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}],
        )
        result = _moderation_result(response)
        if not result:
            return Analysis([])
        findings: list[Finding] = []
        for category, threshold in THRESHOLDS.items():
            score = _category_score(result, category)
            if score >= threshold:
                public_category = "adult/minor sexual" if category == "sexual/minors" else category
                findings.append(Finding(public_category, score, "openai-image-moderation"))
        return Analysis(findings)
    except Exception as exc:
        logger.warning("Profile photo moderation failed: %s", exc)
        return Analysis([])


async def _get_profile(client: Client, user_id: int) -> tuple[str, str | None]:
    """Return bio and current profile-photo file id."""
    try:
        chat = await client.get_chat(user_id)
    except RPCError:
        return "", None
    bio = getattr(chat, "bio", None) or ""
    photo = getattr(chat, "photo", None)
    photo_id = getattr(photo, "big_file_id", None) or getattr(photo, "small_file_id", None)
    return str(bio), photo_id


async def _download_photo(client: Client, file_id: str) -> str | None:
    if not file_id:
        return None
    temp_dir = Path(tempfile.gettempdir()) / "yuki-profile-guard"
    temp_dir.mkdir(parents=True, exist_ok=True)
    target = temp_dir / f"profile-{int(time.time() * 1000)}-{os.getpid()}"
    try:
        downloaded = await client.download_media(file_id, file_name=str(target))
        if not downloaded:
            return None
        path = Path(downloaded)
        if path.exists() and path.stat().st_size <= MAX_PHOTO_BYTES:
            return str(path)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    except Exception as exc:
        logger.warning("Profile photo download failed: %s", exc)
    return None


async def _is_exempt(client: Client, message: Any) -> bool:
    if not message.from_user or not message.chat:
        return True
    user_id = message.from_user.id
    if user_id == config.OWNER_ID or user_id in config.SUDO_USERS:
        return True
    try:
        member = await client.get_chat_member(message.chat.id, user_id)
        status = getattr(member, "status", None)
        return status in {ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR, "owner", "administrator"}
    except Exception:
        # If membership lookup fails, do not punish someone based on an
        # uncertain admin state. This is safer than accidentally muting an admin.
        return True


async def _record_violation(client: Client, chat_id: int, user_id: int, findings: list[Finding]) -> int:
    """Persist a simple per-user violation count in MongoDB."""
    try:
        collection = client.db.db.profile_guard_violations
        await collection.update_one(
            {"chat_id": chat_id, "user_id": user_id},
            {"$inc": {"count": 1}, "$set": {"last_at": client.db.now(), "findings": [f.category for f in findings]}},
            upsert=True,
        )
        doc = await collection.find_one({"chat_id": chat_id, "user_id": user_id}, {"count": 1})
        return int((doc or {}).get("count", 1))
    except Exception as exc:
        logger.warning("Could not persist profile-guard violation: %s", exc)
        return 1


async def _punish(client: Client, message: Any, findings: list[Finding]) -> None:
    if not message.chat or not message.from_user:
        return
    chat_id = message.chat.id
    user_id = message.from_user.id
    count = await _record_violation(client, chat_id, user_id, findings)

    try:
        await message.delete()
    except Exception:
        pass

    try:
        if count >= BAN_AFTER_VIOLATIONS:
            await client.ban_chat_member(chat_id, user_id)
            action = "banned"
        else:
            until = int(time.time()) + MUTE_SECONDS
            await client.restrict_chat_member(
                chat_id,
                user_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until,
            )
            action = f"muted for {MUTE_SECONDS // 60} minutes"
    except Exception as exc:
        logger.warning("Punishment failed in %s for %s: %s", chat_id, user_id, exc)
        return

    categories = ", ".join(sorted({f.category for f in findings}))
    try:
        await client.send_message(chat_id,
            f"⚠️ <b>Profile safety action</b>\n\n"
            f"User: <b>{message.from_user.mention}</b>\n"
            f"Reason: <code>{categories}</code>\n"
            f"Action: <b>{action}</b>"
        )
    except Exception:
        pass


async def _analyze_profile(client: Client, message: Any) -> Analysis:
    user = message.from_user
    if not user:
        return Analysis([])

    bio, photo_id = await _get_profile(client, user.id)
    fields = [
        user.first_name or "",
        user.last_name or "",
        f"@{user.username}" if user.username else "",
        bio,
    ]
    text = "\n".join(part.strip() for part in fields if part and part.strip())
    fingerprint = "\n".join(fields)

    now = time.time()
    async with CACHE_LOCK:
        cached = TEXT_CACHE.get(user.id)
        if cached and cached["fingerprint"] == fingerprint and now - cached["at"] < TEXT_CACHE_SECONDS:
            text_analysis = cached["analysis"]
        else:
            text_analysis = None

    if text_analysis is None:
        text_analysis = await moderate_text(text)
        async with CACHE_LOCK:
            TEXT_CACHE[user.id] = {"fingerprint": fingerprint, "at": time.time(), "analysis": text_analysis}

    findings = list(text_analysis.findings)

    if photo_id:
        async with CACHE_LOCK:
            cached_photo = PHOTO_CACHE.get(user.id)
            if cached_photo and cached_photo["file_id"] == photo_id and now - cached_photo["at"] < PHOTO_CACHE_SECONDS:
                photo_analysis = cached_photo["analysis"]
            else:
                photo_analysis = None
        if photo_analysis is None:
            path = await _download_photo(client, photo_id)
            if path:
                try:
                    photo_analysis = await moderate_image(path)
                finally:
                    try:
                        Path(path).unlink(missing_ok=True)
                    except OSError:
                        pass
            else:
                photo_analysis = Analysis([])
            async with CACHE_LOCK:
                PHOTO_CACHE[user.id] = {"file_id": photo_id, "at": time.time(), "analysis": photo_analysis}
        findings.extend(photo_analysis.findings)

    return Analysis(findings)


@Client.on_message(filters.incoming & filters.group)
async def profile_guard_handler(client: Client, message: Any):
    """Inspect every group sender without blocking normal Yuki handlers."""
    if not message.from_user or message.from_user.is_bot:
        return
    if not message.chat or message.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        return
    if _openai_client() is None:
        return

    # Admins/owner/sudo are never automatically punished.
    if await _is_exempt(client, message):
        return

    try:
        analysis = await _analyze_profile(client, message)
        if analysis.unsafe:
            await _punish(client, message, analysis.findings)
    except Exception as exc:
        # Never allow this security plugin to break Yuki's other plugins.
        logger.exception("Profile guard handler failed: %s", exc)
