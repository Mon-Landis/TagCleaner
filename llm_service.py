from __future__ import annotations

import base64
import ctypes
import hashlib
import io
import json
import math
import mimetypes
import os
import re
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


USER_CONFIG_ROOT = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / ".tagcleaner")) / "TagCleaner"
SETTINGS_PATH = USER_CONFIG_ROOT / "settings.json"
SECRETS_PATH = USER_CONFIG_ROOT / "secrets.json"
PROJECT_ROOT = Path(__file__).resolve().parent
SKILLS_ROOT = PROJECT_ROOT / "skills"
MAX_INLINE_IMAGE_BYTES = 10 * 1024 * 1024
BATCH_CONCURRENCY = 2
MODEL_ALIASES = {
    # Keep the shorthand accepted by earlier builds, but always send the
    # provider's canonical endpoint/model ID.
    "deepseekv4flashga": "deepseek-v4-flash",
}

STYLE_MODE_NON_TRIGGER_TAGS = {
    "masterpiece", "best_quality", "amazing_quality", "great_quality", "good_quality",
    "normal_quality", "low_quality", "worst_quality", "highres", "absurdres",
    "very_aesthetic", "aesthetic", "official_art", "anime_coloring", "anime_screencap",
    "game_cg", "illustration", "digital_art", "digital_painting", "watercolor",
    "oil_painting", "painting", "sketch", "lineart", "monochrome", "grayscale",
}

DEFAULT_VISION_PROMPT = """You are an anime image analyst for dataset cleanup. Inspect the image once and return both Danbooru-style tag candidates and a natural-English visual description. Treat the supplied WD14 tags as hints rather than facts. Preserve recognizable character and copyright tags exactly as supplied when the pixels support them. Do not output artist names, quality tags, aesthetic ratings, generation metadata, or unsupported guesses.

For tags, use concise lowercase snake_case terms where appropriate. Cover visible subjects, identity, appearance, outfit, expression, pose, actions, props, setting, framing, camera angle, lighting facts, spatial relationships, and any watermark with its position. Do not repeat equivalent spellings.

For the description, write one flowing paragraph in natural English. Cover, in order, shot type and framing, camera angle and perspective, character count and spatial positions, setting and background, factual lighting direction and color temperature, then each visible character from left to right or foreground to background. Describe only visible hair, eyes, expression, gaze, clothing from top to bottom, pose, limb placement, body orientation, props, accessories, interactions, overlap, and depth. Qualify unclear or partially visible details and never invent them. Describe depicted content, not art style or rendering technique.

Primary WD14 reference tags:
{{tags}}

Runtime mode policy:
{{dataset_mode_policy}}
Final archive natural-language policy:
{{natural_language_policy}}

Return only one JSON object with exactly two fields: {"tags":["tag_a","tag_b"],"description":"one paragraph"}. Do not use Markdown, labels, or any text outside the JSON object. The description is mandatory internal visual evidence even when the final archive disables natural-language output."""

DEFAULT_CLEAN_PROMPT = """You are a meticulous anime dataset tag editor. Treat the original WD14 tags as the primary tag evidence and the vision-model tags as secondary candidates. Keep the original WD14 spelling and order when two candidates are equivalent. A tag omitted by the vision model is not evidence that the WD14 tag is wrong. Add a vision-only tag only when the visual description clearly supports it. Remove a WD14 tag only when it is contradicted by the visual evidence, is metadata or an unsupported guess, or the dataset mode policy explicitly requires its removal. Preserve supported character and copyright identities. Apply validated character-audit changes as hard constraints.

Remove duplicates and contradictions. Keep tags concise, lowercase where appropriate, and comma-ready. Do not invent a character, outfit, prop, watermark, or action. Apply the dataset mode policy below to every style, technique, and quality term; never invent such terms. The final response contract below is mandatory.

Original WD14 tags (primary evidence):
{{tags}}

Vision-model tags (secondary evidence):
{{vision_tags}}

Merged candidates (WD14 first, then non-duplicate vision tags):
{{merged_tag_candidates}}

Visual description:
{{vision_description}}

Character reference descriptions (stable appearance only):
{{character_descriptions}}

Validated character-audit tags:
{{audited_tags}}

Character-audit report:
{{character_audit}}

Protected dataset activation tags:
{{protected_dataset_tags}}

Natural-language policy:
{{natural_language_policy}}

Dataset mode policy:
{{dataset_mode_policy}}

Return only a JSON object with exactly two fields: tags (an array of strings) and description (a single natural-English paragraph or an empty string). Do not wrap it in Markdown. When natural language is disabled, description must be empty. When enabled, place composition, pose, lighting, and relationships in description while keeping stable identity and copyright information in tags."""

STYLE_MODE_POLICY = """STYLE MODE (mandatory, highest priority): This image belongs to an art-style training set. Do not use or pass any character reference description or character-audit appearance additions. Remove every art-style, artist, medium, rendering, technique, quality, and post-processing descriptor from both the output tags and the optional natural-language description, including terms about linework, brushwork, coloring, shading, texture, effects, or how the image was made. Preserve all visually supported content descriptions: people and recognizable identity/copyright tags, physical appearance, hair and eyes, clothing, accessories, props, action, pose and limb placement, expression and gaze, scene and background, factual lighting conditions, spatial relationships, framing, camera angle, depth, and composition. Dataset activation tokens explicitly listed as protected are not style descriptions and must be preserved exactly. Remove stylistic adjectives without removing the underlying depicted content. Never invent details."""

STANDARD_MODE_POLICY = """STANDARD MODE: Use matching character reference descriptions and validated character-audit appearance tags as stable identity hints when available. Preserve visually supported content and remove guesses or contradictions. Keep any style or quality tags only when they were present in the original tags."""

DEFAULT_CHARACTER_PROMPT = """Analyze this character reference / A-pose image. Return only a comma-separated list of stable appearance tags useful for anime image generation: hair color and style, eye color, skin tone, body features that are clearly visible, signature accessories, and the character's default outfit silhouette. Exclude pose, camera, background, temporary expression, image quality words, artist names, and uncertain details. Do not use JSON, bullets, prose, or explanations.

Runtime mode policy:
{{dataset_mode_policy}}"""

DEFAULT_CONFIG = {
    "volcanoUrl": "https://ark.cn-beijing.volces.com/api/plan/v3",
    "volcanoModel": "doubao-seed-2.1-turbo",
    "volcanoProtocol": "chat",
    "geminiUrl": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
    "geminiModel": "gemini-3.5-flash",
    "geminiProtocol": "openai",
    # Stage targets are independent from supplier credentials. The legacy
    # volcanoModel/geminiModel fields remain above for older config files.
    "tagProvider": "volcano",
    "tagModel": "doubao-seed-2.1-turbo",
    "aposeProvider": "volcano",
    "aposeModel": "doubao-seed-2.1-turbo",
    "cleanProvider": "volcano",
    "cleanModel": "deepseek-v4-flash",
    "autoSaveResults": True,
    "timeoutSeconds": 300,
    "retryCount": 2,
    "retryDelaySeconds": 4,
    "imageDetail": "high",
    "imageMaxPixels": 2000000,
    "visionPrompt": DEFAULT_VISION_PROMPT,
    "cleanPrompt": DEFAULT_CLEAN_PROMPT,
    "characterPrompt": DEFAULT_CHARACTER_PROMPT,
    "enabledSkills": ["prompt-pyramid", "character-tag-auditor"],
    "characterAuditMode": "sparse",
}

BUILTIN_MODEL_CATALOG = {
    "volcano": [
        {
            "id": "doubao-seed-2.1-turbo",
            "provider": "volcano",
            "vision": True,
            "visionKnown": True,
            "ownedBy": "volcengine",
        },
        {
            "id": "deepseek-v4-flash",
            "provider": "volcano",
            "vision": None,
            "visionKnown": False,
            "ownedBy": "volcengine",
        },
    ],
    "gemini": [],
}

SKILL_SPECS = {
    "prompt-pyramid": {
        "displayName": "Prompt Pyramid",
        "stage": "FINAL CLEANUP",
    },
    "character-tag-auditor": {
        "displayName": "Character Tag Auditor",
        "stage": "CHARACTER REVIEW",
    },
}

AUDIT_CATEGORIES = {
    "identity", "hair", "eyes", "face", "body", "clothing", "footwear", "legwear",
    "wearable_accessory", "action", "pose", "expression", "scene", "composition",
    "quality", "object", "other",
}
AUDIT_MUTABLE_CATEGORIES = {
    "hair", "eyes", "face", "body", "clothing", "footwear", "legwear", "wearable_accessory",
}
AUDIT_DECISIONS = {"keep", "delete", "replace", "uncertain"}


class LLMError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, retryable: bool = True) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class SkillRegistry:
    """Loads checked-in skills without requiring a YAML dependency at runtime."""

    def __init__(self, root: Path = SKILLS_ROOT) -> None:
        self.root = Path(root)

    def _load(self, skill_id: str) -> dict:
        spec = SKILL_SPECS[skill_id]
        skill_path = self.root / skill_id / "SKILL.md"
        runtime_path = self.root / skill_id / "runtime-prompt.md"
        record = {
            "id": skill_id,
            "name": skill_id,
            "displayName": spec["displayName"],
            "stage": spec["stage"],
            "description": "",
            "prompt": "",
            "hash": "",
            "valid": False,
            "error": "",
        }
        try:
            skill_bytes = skill_path.read_bytes()
            runtime_bytes = runtime_path.read_bytes()
            skill_text = skill_bytes.decode("utf-8")
            runtime_prompt = runtime_bytes.decode("utf-8").strip()
            frontmatter = re.match(r"\A---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|\Z)", skill_text, flags=re.S)
            if not frontmatter:
                raise ValueError("SKILL.md 缺少有效 frontmatter")
            metadata: dict[str, str] = {}
            for line in frontmatter.group(1).splitlines():
                match = re.match(r"^([A-Za-z][A-Za-z0-9_-]*):\s*(.*?)\s*$", line)
                if match:
                    value = match.group(2)
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'\"', "'"}:
                        value = value[1:-1]
                    metadata[match.group(1)] = value
            if metadata.get("name") != skill_id:
                raise ValueError(f"Skill name 必须为 {skill_id}")
            if not metadata.get("description"):
                raise ValueError("Skill description 不能为空")
            if not runtime_prompt:
                raise ValueError("runtime-prompt.md 不能为空")
            digest = hashlib.sha256()
            digest.update(b"SKILL.md\0")
            digest.update(skill_bytes)
            digest.update(b"\0runtime-prompt.md\0")
            digest.update(runtime_bytes)
            record.update({
                "name": metadata["name"],
                "description": metadata["description"],
                "prompt": runtime_prompt,
                "hash": digest.hexdigest(),
                "valid": True,
            })
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            record["error"] = _clean_text(exc)
        return record

    def records(self) -> dict[str, dict]:
        return {skill_id: self._load(skill_id) for skill_id in SKILL_SPECS}

    def validate_enabled(self, value, *, strict: bool = True) -> list[str]:
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            if strict:
                raise ValueError("enabledSkills 必须是 Skill ID 数组")
            value = list(DEFAULT_CONFIG["enabledSkills"])
        requested = {_clean_text(item) for item in value if _clean_text(item)}
        unknown = sorted(requested - set(SKILL_SPECS))
        if unknown and strict:
            raise ValueError("未知 Skill: " + ", ".join(unknown))
        records = self.records()
        invalid = [skill_id for skill_id in requested if skill_id in records and not records[skill_id]["valid"]]
        if invalid and strict:
            details = "; ".join(f"{skill_id}: {records[skill_id]['error']}" for skill_id in sorted(invalid))
            raise ValueError("无法启用无效 Skill: " + details)
        return [skill_id for skill_id in SKILL_SPECS if skill_id in requested and records[skill_id]["valid"]]

    def public(self, enabled) -> list[dict]:
        enabled_set = set(self.validate_enabled(enabled, strict=False))
        result = []
        for record in self.records().values():
            result.append({
                "id": record["id"],
                "name": record["name"],
                "displayName": record["displayName"],
                "description": record["description"],
                "stage": record["stage"],
                "enabled": record["id"] in enabled_set,
                "valid": record["valid"],
                "hash": record["hash"][:16],
                "error": record["error"],
            })
        return result

    def prompt(self, skill_id: str) -> str:
        if skill_id not in SKILL_SPECS:
            raise ValueError(f"未知 Skill: {skill_id}")
        record = self._load(skill_id)
        if not record["valid"]:
            raise ValueError(f"Skill {skill_id} 无效: {record['error']}")
        return record["prompt"]

    def hashes(self, enabled) -> dict[str, str]:
        enabled_ids = self.validate_enabled(enabled, strict=False)
        records = self.records()
        return {skill_id: records[skill_id]["hash"] for skill_id in enabled_ids}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _elapsed_seconds(started_at: str, finished_at: str = "") -> int:
    try:
        started = datetime.fromisoformat(_clean_text(started_at).replace("Z", "+00:00"))
        finished = (
            datetime.fromisoformat(_clean_text(finished_at).replace("Z", "+00:00"))
            if _clean_text(finished_at)
            else datetime.now(timezone.utc)
        )
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=timezone.utc)
        return max(0, int((finished - started).total_seconds()))
    except (TypeError, ValueError):
        return 0


def _clean_text(value) -> str:
    return str(value or "").strip()


def _canonical_model_id(value) -> str:
    model = _clean_text(value)
    return MODEL_ALIASES.get(model.casefold(), model)


def _parse_tags(value) -> list[str]:
    if isinstance(value, list):
        raw = value
    else:
        raw = re.split(r"[,\n]", str(value or ""))
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        token = _clean_text(item).lstrip("\ufeff")
        if token and token not in seen:
            result.append(token)
            seen.add(token)
    return result


def _join_tags(tags) -> str:
    return ", ".join(_parse_tags(tags))


def _normalise_tag(tag: str) -> str:
    return re.sub(r"[\s-]+", "_", _clean_text(tag).lower())


def _merge_tag_candidates(wd14_tags, vision_tags) -> list[str]:
    """Keep WD14 spelling/order and append only genuinely new vision tags."""
    result: list[str] = []
    seen: set[str] = set()
    for tag in [*_parse_tags(wd14_tags), *_parse_tags(vision_tags)]:
        normalized = _normalise_tag(tag)
        if not normalized or normalized in seen:
            continue
        result.append(tag)
        seen.add(normalized)
    return result


def _detect_dataset_trigger(images) -> list[str]:
    """Detect a high-consensus leading activation token without protecting style terms."""
    leading: list[str] = []
    for image in images:
        tags = _parse_tags(image.get("tags", [])) if isinstance(image, dict) else []
        if tags:
            leading.append(tags[0])
    if len(images) < 2 or len(leading) < 2:
        return []
    counts: dict[str, tuple[int, str]] = {}
    for tag in leading:
        normalized = _normalise_tag(tag)
        count, original = counts.get(normalized, (0, tag))
        counts[normalized] = (count + 1, original)
    normalized, (count, original) = max(counts.items(), key=lambda item: item[1][0])
    required = max(2, (len(images) * 9 + 9) // 10)
    if count < required or normalized in STYLE_MODE_NON_TRIGGER_TAGS:
        return []
    return [original]


def _ensure_protected_tags(tags, protected_tags) -> list[str]:
    protected = _parse_tags(protected_tags)
    protected_normalized = {_normalise_tag(tag) for tag in protected}
    remainder = [tag for tag in _parse_tags(tags) if _normalise_tag(tag) not in protected_normalized]
    return protected + remainder


def _ensure_watermark_evidence(
    tags,
    description: str,
    vision_tags,
    vision_description: str,
    allow_natural_language: bool,
) -> tuple[list[str], str]:
    """Keep a visually confirmed watermark even when the cleanup model drops it."""
    result_tags = _parse_tags(tags)
    result_description = _clean_text(description)
    if "watermark" not in {_normalise_tag(tag) for tag in _parse_tags(vision_tags)}:
        return result_tags, result_description

    if "watermark" not in {_normalise_tag(tag) for tag in result_tags}:
        result_tags.append("watermark")
    if allow_natural_language and "watermark" not in result_description.casefold():
        evidence_sentence = next((
            sentence.strip()
            for sentence in re.split(r"(?<=[.!?])\s+", _clean_text(vision_description))
            if "watermark" in sentence.casefold()
        ), "")
        if evidence_sentence:
            separator = " " if not result_description or result_description.endswith((".", "!", "?")) else ". "
            result_description = (result_description + separator + evidence_sentence).strip()
    return result_tags, result_description


def _render(template: str, values: dict[str, str]) -> str:
    output = str(template or "")
    for key, value in values.items():
        output = output.replace("{{" + key + "}}", value)
    return output


def _style_mode(options: dict | None) -> bool:
    """Return the explicit dataset mode while tolerating older callers."""
    return bool(options.get("styleMode")) if isinstance(options, dict) else False


def _include_character_descriptions(options: dict | None) -> bool:
    """Resolve character-reference behavior for new and legacy option payloads."""
    if not isinstance(options, dict) or _style_mode(options):
        return False
    if "includeCharacterDescriptions" in options:
        return bool(options.get("includeCharacterDescriptions"))
    if "styleMode" in options:
        return True
    # Legacy callers used characterCleaning as an opt-in switch.
    return bool(options.get("characterCleaning"))


def _read_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return fallback


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _dpapi_transform(value: bytes, protect: bool) -> bytes:
    if os.name != "nt":
        raise OSError("Windows DPAPI is unavailable")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    source = ctypes.create_string_buffer(value)
    source_blob = _DATA_BLOB(len(value), ctypes.cast(source, ctypes.POINTER(ctypes.c_byte)))
    target_blob = _DATA_BLOB()
    description = ctypes.create_unicode_buffer("TagCleaner local secret")
    if protect:
        ok = crypt32.CryptProtectData(ctypes.byref(source_blob), description, None, None, None, 0, ctypes.byref(target_blob))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(source_blob), None, None, None, None, 0, ctypes.byref(target_blob))
    if not ok:
        raise OSError(ctypes.get_last_error() or "DPAPI operation failed")
    try:
        return ctypes.string_at(target_blob.pbData, target_blob.cbData)
    finally:
        kernel32.LocalFree(target_blob.pbData)


class ConfigStore:
    """Stores settings outside the repository and encrypts API keys with DPAPI."""

    def __init__(self) -> None:
        USER_CONFIG_ROOT.mkdir(parents=True, exist_ok=True)
        self.skill_registry = SkillRegistry()
        raw = _read_json(SETTINGS_PATH, {})
        self.config = dict(DEFAULT_CONFIG)
        if isinstance(raw, dict):
            for key in DEFAULT_CONFIG:
                if key in raw:
                    self.config[key] = raw[key]
        self.config["enabledSkills"] = self.skill_registry.validate_enabled(
            self.config.get("enabledSkills"), strict=False
        )
        if self.config.get("characterAuditMode") not in {"sparse", "full"}:
            self.config["characterAuditMode"] = DEFAULT_CONFIG["characterAuditMode"]
        # Migrate custom legacy Volcano model settings into the new stage
        # selectors without overwriting explicit stage choices.
        if isinstance(raw, dict):
            if "tagModel" not in raw and _clean_text(raw.get("volcanoModel")):
                self.config["tagModel"] = _canonical_model_id(raw["volcanoModel"])
            if "aposeModel" not in raw and _clean_text(raw.get("volcanoModel")):
                self.config["aposeModel"] = _canonical_model_id(raw["volcanoModel"])
        for model_key in ("volcanoModel", "geminiModel", "tagModel", "aposeModel", "cleanModel"):
            self.config[model_key] = _canonical_model_id(self.config.get(model_key))
        self.lock = threading.RLock()
        self.secrets = self._load_secrets()
        self.model_catalog: dict[str, list[dict]] = {
            provider: [dict(item) for item in models]
            for provider, models in BUILTIN_MODEL_CATALOG.items()
        }

    def _load_secrets(self) -> dict[str, str]:
        raw = _read_json(SECRETS_PATH, {})
        if not isinstance(raw, dict):
            return {}
        decoded: dict[str, str] = {}
        for name, encoded in raw.items():
            try:
                if os.name == "nt":
                    decoded[name] = _dpapi_transform(base64.b64decode(str(encoded)), False).decode("utf-8")
                elif isinstance(encoded, str):
                    decoded[name] = encoded
            except (ValueError, OSError, UnicodeDecodeError):
                continue
        return decoded

    def _save_secrets(self) -> None:
        encoded: dict[str, str] = {}
        for name, value in self.secrets.items():
            if not value:
                continue
            if os.name == "nt":
                encoded[name] = base64.b64encode(_dpapi_transform(value.encode("utf-8"), True)).decode("ascii")
            else:
                encoded[name] = value
        _write_json(SECRETS_PATH, encoded)
        if os.name != "nt":
            try:
                os.chmod(SECRETS_PATH, 0o600)
            except OSError:
                pass

    def public(self) -> dict:
        with self.lock:
            payload = dict(self.config)
            payload["enabledSkills"] = self.enabled_skills()
            payload["skills"] = self.skill_registry.public(payload["enabledSkills"])
            payload["modelCatalog"] = {
                provider: list(models) for provider, models in self.model_catalog.items()
            }
            payload["volcanoKeySet"] = bool(self.get_key("volcano"))
            payload["geminiKeySet"] = bool(self.get_key("gemini"))
            payload["keyStorage"] = "Windows DPAPI" if os.name == "nt" else "local user config directory"
            return payload

    def set_model_catalog(self, provider: str, models: list[dict]) -> None:
        provider = _clean_text(provider).lower()
        if provider not in {"volcano", "gemini"}:
            return
        with self.lock:
            self.model_catalog[provider] = [item for item in models if isinstance(item, dict)]

    def model_vision_capability(self, provider: str, model_id: str) -> bool | None:
        provider = _clean_text(provider).lower()
        model_id = _clean_text(model_id)
        with self.lock:
            record = next((item for item in self.model_catalog.get(provider, []) if item.get("id") == model_id), None)
        return record.get("vision") if isinstance(record, dict) else None

    def enabled_skills(self) -> list[str]:
        return self.skill_registry.validate_enabled(self.config.get("enabledSkills"), strict=False)

    def skill_enabled(self, skill_id: str) -> bool:
        return skill_id in self.enabled_skills()

    def skill_prompt(self, skill_id: str) -> str:
        if not self.skill_enabled(skill_id):
            return ""
        return self.skill_registry.prompt(skill_id)

    def enabled_skill_hashes(self) -> dict[str, str]:
        return self.skill_registry.hashes(self.enabled_skills())

    def validate_skills(self) -> None:
        self.config["enabledSkills"] = self.skill_registry.validate_enabled(
            self.config.get("enabledSkills"), strict=True
        )

    def get_key(self, provider: str) -> str:
        env_name = "TAGCLEANER_VOLCANO_KEY" if provider == "volcano" else "TAGCLEANER_GEMINI_KEY"
        return _clean_text(os.environ.get(env_name) or self.secrets.get(provider, ""))

    def snapshot(self):
        """Freeze settings and effective secrets for one background cleaning job."""
        with self.lock:
            keys = {provider: self.get_key(provider) for provider in ("volcano", "gemini")}
            return ConfigPreview(dict(self.config), keys, self.skill_registry)

    def _coerce_setting(self, key: str, value):
        if key == "enabledSkills":
            return self.skill_registry.validate_enabled(value, strict=True)
        if key == "characterAuditMode":
            value = _clean_text(value).lower()
            if value not in {"sparse", "full"}:
                raise ValueError("characterAuditMode 必须是 sparse 或 full")
            return value
        if key.endswith("Model"):
            return _canonical_model_id(value)
        if key in {"timeoutSeconds", "retryCount", "retryDelaySeconds", "imageMaxPixels"}:
            try:
                number = max(196 if key == "imageMaxPixels" else 0, float(value))
                if key == "imageMaxPixels":
                    number = min(36_000_000, number)
                return int(number) if number.is_integer() else number
            except (TypeError, ValueError):
                raise ValueError(f"配置项 {key} 必须是数字")
        return value

    def preview(self, payload: dict):
        """Build an in-memory request config without persisting settings or secrets."""
        if not isinstance(payload, dict):
            raise ValueError("测试配置内容无效")
        with self.lock:
            config = dict(self.config)
            for key in DEFAULT_CONFIG:
                if key in payload:
                    config[key] = self._coerce_setting(key, payload[key])
            keys: dict[str, str] = {}
            for provider in ("volcano", "gemini"):
                clear_name = "clear" + provider.title() + "Key"
                supplied = _clean_text(payload.get(provider + "Key"))
                keys[provider] = "" if payload.get(clear_name) else (supplied or self.get_key(provider))
            return ConfigPreview(config, keys, self.skill_registry)

    def update(self, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("配置内容无效")
        with self.lock:
            for key in DEFAULT_CONFIG:
                if key not in payload:
                    continue
                self.config[key] = self._coerce_setting(key, payload[key])
            for provider in ("volcano", "gemini"):
                key_name = provider + "Key"
                if key_name in payload and _clean_text(payload[key_name]):
                    self.secrets[provider] = _clean_text(payload[key_name])
                if payload.get("clear" + provider.title() + "Key"):
                    self.secrets.pop(provider, None)
            _write_json(SETTINGS_PATH, self.config)
            self._save_secrets()
            return self.public()


class ConfigPreview:
    """Read-only configuration used by connection tests."""

    def __init__(self, config: dict, keys: dict[str, str], skill_registry: SkillRegistry) -> None:
        self.config = config
        self.keys = keys
        self.skill_registry = skill_registry

    def get_key(self, provider: str) -> str:
        return _clean_text(self.keys.get(provider, ""))

    def enabled_skills(self) -> list[str]:
        return self.skill_registry.validate_enabled(self.config.get("enabledSkills"), strict=False)

    def skill_prompt(self, skill_id: str) -> str:
        return self.skill_registry.prompt(skill_id) if skill_id in self.enabled_skills() else ""

    def enabled_skill_hashes(self) -> dict[str, str]:
        return self.skill_registry.hashes(self.enabled_skills())

    def validate_skills(self) -> None:
        self.config["enabledSkills"] = self.skill_registry.validate_enabled(
            self.config.get("enabledSkills"), strict=True
        )


def _image_retry_profile(max_pixels: int, compression_level: int) -> tuple[int, int, bool]:
    level = max(0, min(2, int(compression_level)))
    if level == 0:
        return max(0, int(max_pixels)), 92, False
    retry_limit = 1_600_000 if level == 1 else 1_000_000
    effective_limit = min(int(max_pixels), retry_limit) if max_pixels > 0 else retry_limit
    return max(1, effective_limit), (88 if level == 1 else 84), True


def _image_data_url(
    path: Path,
    max_pixels: int = 0,
    *,
    force_jpeg: bool = False,
    jpeg_quality: int = 92,
) -> str:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise LLMError(f"无法读取图片 {path.name}: {exc}", retryable=False) from exc
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    inspect_image = force_jpeg or max_pixels > 0 or len(data) >= MAX_INLINE_IMAGE_BYTES
    if inspect_image:
        try:
            from PIL import Image, ImageOps

            with Image.open(io.BytesIO(data)) as opened:
                image = ImageOps.exif_transpose(opened)
                resize_needed = max_pixels > 0 and image.width * image.height > max_pixels
                encode_needed = force_jpeg or resize_needed or len(data) >= MAX_INLINE_IMAGE_BYTES
                if resize_needed:
                    scale = math.sqrt(max_pixels / (image.width * image.height))
                    size = (max(1, math.floor(image.width * scale)), max(1, math.floor(image.height * scale)))
                    image = image.resize(size, Image.Resampling.LANCZOS)
                if encode_needed:
                    if image.mode not in {"RGB", "L"}:
                        rgba = image.convert("RGBA")
                        background = Image.new("RGB", rgba.size, "white")
                        background.paste(rgba, mask=rgba.getchannel("A"))
                        image = background
                    elif image.mode == "L":
                        image = image.convert("RGB")
                    encoded = io.BytesIO()
                    image.save(
                        encoded,
                        format="JPEG",
                        quality=max(72, min(95, int(jpeg_quality))),
                        optimize=True,
                    )
                    data = encoded.getvalue()
                    mime = "image/jpeg"
        except (ImportError, OSError, ValueError) as exc:
            raise LLMError(
                f"无法为图片 {path.name} 创建请求用缩放副本: {exc}",
                retryable=False,
            ) from exc
    if len(data) >= MAX_INLINE_IMAGE_BYTES:
        raise LLMError(f"图片 {path.name} 的请求用副本仍大于 10 MB", retryable=False)
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _request_json_method(
    method: str,
    url: str,
    payload: dict | None,
    headers: dict[str, str],
    timeout: float,
) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = Request(url, data=body, headers={"Content-Type": "application/json", **headers}, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = getattr(response, "status", 200)
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        retryable = exc.code in {408, 409, 425, 429} or exc.code >= 500
        raise LLMError(f"远端 API 返回 HTTP {exc.code}: {raw[:500]}", exc.code, retryable) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise LLMError(f"远端 API 请求失败: {exc}", retryable=True) from exc
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LLMError(f"远端 API 返回了非 JSON 内容 (HTTP {status})", status, True) from exc
    if not isinstance(parsed, dict):
        raise LLMError("远端 API 返回格式不是对象", status, True)
    return parsed


def _request_json(url: str, payload: dict, headers: dict[str, str], timeout: float) -> dict:
    return _request_json_method("POST", url, payload, headers, timeout)


def _request_json_get(url: str, headers: dict[str, str], timeout: float) -> dict:
    return _request_json_method("GET", url, None, headers, timeout)


def _extract_text(payload: dict) -> str:
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = [item["text"] for item in content if isinstance(item, dict) and isinstance(item.get("text"), str)]
            if parts:
                return "".join(parts).strip()
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"].strip()
    output = payload.get("output")
    if isinstance(output, list):
        parts = []
        for item in output:
            for content in (item.get("content", []) if isinstance(item, dict) else []):
                if isinstance(content, dict) and isinstance(content.get("text"), str):
                    parts.append(content["text"])
        if parts:
            return "".join(parts).strip()
    candidates = payload.get("candidates")
    if isinstance(candidates, list) and candidates:
        parts = candidates[0].get("content", {}).get("parts", []) if isinstance(candidates[0], dict) else []
        text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
        if text.strip():
            return text.strip()
    raise LLMError("远端 API 响应中没有可读文本", retryable=True)


def _retry(call, config: ConfigStore, label: str, event_callback=None) -> str:
    retries = max(0, int(config.config.get("retryCount", 2)))
    delay = max(0.2, float(config.config.get("retryDelaySeconds", 4)))
    last: Exception | None = None

    def emit(**event) -> None:
        if not callable(event_callback):
            return
        try:
            event_callback(event)
        except Exception:
            # Progress reporting must never turn a recoverable request into
            # a failed cleanup job.
            pass

    for attempt in range(retries + 1):
        emit(phase="request", attempt=attempt + 1, maxAttempts=retries + 1)
        try:
            result = call()
            emit(phase="response", attempt=attempt + 1, maxAttempts=retries + 1)
            return result
        except LLMError as exc:
            last = exc
            if not exc.retryable or attempt >= retries:
                # Once the configured attempts are exhausted, make the
                # failure terminal.  Stage wrappers should not restart the
                # whole request loop after the network layer has already
                # retried it, and the stage label is needed by the UI.
                if exc.retryable and attempt >= retries:
                    raise LLMError(f"{label} 失败（已重试 {retries} 次）: {exc}", exc.status, retryable=False) from exc
                raise
            wait_seconds = delay * (2**attempt)
            reason = f"http_{exc.status}" if exc.status else ("timeout" if "timed out" in str(exc).lower() else "network")
            emit(
                phase="retry_wait",
                attempt=attempt + 2,
                maxAttempts=retries + 1,
                retryInSeconds=wait_seconds,
                reason=reason,
            )
            time.sleep(wait_seconds)
        except Exception as exc:
            last = exc
            if attempt >= retries:
                raise LLMError(f"{label} 失败: {exc}", retryable=False) from exc
            wait_seconds = delay * (2**attempt)
            emit(
                phase="retry_wait",
                attempt=attempt + 2,
                maxAttempts=retries + 1,
                retryInSeconds=wait_seconds,
                reason="network",
            )
            time.sleep(wait_seconds)
    raise LLMError(f"{label} 失败: {last}", retryable=False)


def _native_gemini_url(base_url: str, model: str) -> str:
    url = base_url.rstrip("/")
    if "/openai/" in url:
        url = url.split("/openai/", 1)[0]
    elif url.endswith("/openai"):
        url = url[: -len("/openai")]
    if ":generateContent" in url:
        return url.replace("{model}", model)
    if "/models/" in url:
        return url + ("" if url.endswith(":generateContent") else ":generateContent")
    return f"{url}/models/{model}:generateContent"


def _volcano_url(base_url: str, protocol: str) -> str:
    url = base_url.rstrip("/")
    if protocol == "responses" and url.endswith("/chat/completions"):
        return url[: -len("/chat/completions")] + "/responses"
    if protocol != "responses" and url.endswith("/responses"):
        return url[: -len("/responses")] + "/chat/completions"
    if not url.endswith(("/chat/completions", "/responses")):
        return url + ("/responses" if protocol == "responses" else "/chat/completions")
    return url


def _provider_base_url(base_url: str) -> str:
    """Strip an inference operation suffix before requesting provider metadata."""
    url = _clean_text(base_url).rstrip("/")
    for suffix in ("/chat/completions", "/responses"):
        if url.endswith(suffix):
            return url[: -len(suffix)]
    return url


def _model_list_urls(provider: str, base_url: str) -> list[str]:
    base = _provider_base_url(base_url)
    if not base:
        return []
    parsed = urlsplit(base)
    if provider == "gemini" and "/openai" in parsed.path:
        parsed = parsed._replace(path=parsed.path.split("/openai", 1)[0].rstrip("/"))
    candidates = [urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/") + "/models", "", ""))]
    if provider == "volcano":
        # API Plan and the public Ark v3 path expose the same OpenAI-style
        # model collection on different deployments.
        for source, target in (("/api/plan/v3", "/api/v3"), ("/api/v3", "/api/plan/v3")):
            if source in parsed.path:
                alternate_path = parsed.path.replace(source, target).rstrip("/") + "/models"
                candidates.append(urlunsplit((parsed.scheme, parsed.netloc, alternate_path, "", "")))
    return list(dict.fromkeys(candidates))


def _vision_capability(item: dict) -> bool | None:
    """Extract a conservative vision flag from provider-specific model metadata."""
    explicit_keys = ("vision", "supports_vision", "vision_capable", "image_input", "supportsImageInput")
    for key in explicit_keys:
        value = item.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
            return value.strip().lower() == "true"
    values: list[str] = []

    def collect(value) -> None:
        if isinstance(value, str):
            values.append(value.lower())
        elif isinstance(value, list):
            for child in value:
                collect(child)
        elif isinstance(value, dict):
            for child in value.values():
                collect(child)

    for key in ("modalities", "input_modalities", "output_modalities", "capabilities", "supported_inputs"):
        collect(item.get(key))
    joined = " ".join(values)
    if any(token in joined for token in ("vision", "image", "multimodal", "视觉", "图片")):
        return True
    model_id = _clean_text(item.get("id") or item.get("name") or item.get("model"))
    if re.search(r"(^|[-_])(vision|vl|multimodal)([-_]|$)", model_id.lower()):
        return True
    if values:
        return False
    return None


def _normalise_model_catalog(provider: str, payload: dict) -> list[dict]:
    raw = payload.get("data") if isinstance(payload.get("data"), list) else payload.get("models")
    if not isinstance(raw, list):
        raw = []
    result = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            item = {"id": item}
        if not isinstance(item, dict):
            continue
        model_id = _clean_text(item.get("id") or item.get("name") or item.get("model"))
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        vision = _vision_capability(item)
        result.append({
            "id": model_id,
            "provider": provider,
            "vision": vision,
            "visionKnown": vision is not None,
            "ownedBy": _clean_text(item.get("owned_by") or item.get("ownedBy")),
        })
    return result


def _parse_clean_output(text: str) -> tuple[list[str], str]:
    cleaned = text.strip()
    fence = chr(96) * 3
    if cleaned.startswith(fence):
        cleaned = re.sub("^" + re.escape(fence) + r"(?:json|text)?\s*", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\s*" + re.escape(fence) + "$", "", cleaned).strip()
    candidate = cleaned
    if not candidate.startswith("{"):
        start = candidate.find("{")
        if start >= 0:
            candidate = candidate[start:]
    try:
        parsed, _ = json.JSONDecoder().raw_decode(candidate)
        if isinstance(parsed, dict) and isinstance(parsed.get("tags"), (list, str)):
            tags = _parse_tags(parsed.get("tags"))
            description = _clean_text(parsed.get("description", ""))
            if tags:
                return tags, description
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    blocks = re.split(r"\r?\n\s*\r?\n", cleaned, maxsplit=1)
    tags = _parse_tags(blocks[0])
    description = _clean_text(blocks[1]) if len(blocks) > 1 else ""
    if not tags or (len(tags) == 1 and len(tags[0].split()) > 5):
        raise LLMError("Gemini 清洗结果无法解析为标签", retryable=True)
    return tags, description


def _parse_vision_output(text: str) -> tuple[list[str], str]:
    parsed = _parse_json_object(text, "视觉反推")
    raw_tags = parsed.get("tags")
    raw_description = parsed.get("description")
    if not isinstance(raw_tags, list) or any(not isinstance(tag, str) for tag in raw_tags):
        raise LLMError("视觉反推返回的 tags 必须是字符串数组", retryable=True)
    tags = _parse_tags(raw_tags)
    description = _clean_text(raw_description) if isinstance(raw_description, str) else ""
    if not tags:
        raise LLMError("视觉反推没有返回可用 tags", retryable=True)
    if not description:
        raise LLMError("视觉反推没有返回自然语言描述", retryable=True)
    return tags, description


def _parse_character_output(text: str) -> str:
    cleaned = _clean_text(text)
    fence = chr(96) * 3
    if cleaned.startswith(fence):
        cleaned = re.sub("^" + re.escape(fence) + r"(?:text)?\s*", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\s*" + re.escape(fence) + "$", "", cleaned).strip()
    tags = _parse_tags(cleaned)
    if not tags or (len(tags) == 1 and len(tags[0].split()) > 5):
        raise LLMError("Gemini 角色描述结果无法解析为 tags", retryable=True)
    return _join_tags(tags)


def _parse_json_object(text: str, label: str) -> dict:
    cleaned = _clean_text(text)
    fence = chr(96) * 3
    if cleaned.startswith(fence):
        cleaned = re.sub("^" + re.escape(fence) + r"(?:json|text)?\s*", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\s*" + re.escape(fence) + "$", "", cleaned).strip()
    start = cleaned.find("{")
    if start < 0:
        raise LLMError(f"{label}没有返回 JSON 对象", retryable=True)
    try:
        parsed, _ = json.JSONDecoder().raw_decode(cleaned[start:])
    except json.JSONDecodeError as exc:
        raise LLMError(f"{label}返回的 JSON 无法解析", retryable=True) from exc
    if not isinstance(parsed, dict):
        raise LLMError(f"{label}返回格式不是对象", retryable=True)
    return parsed


def _parse_character_audit_output(text: str, original_tags: list[str], locked_trigger: str) -> dict:
    original_tags = _parse_tags(original_tags)
    locked_trigger = _clean_text(locked_trigger)
    if locked_trigger not in original_tags:
        raise LLMError("角色审计的 locked trigger 不在原始 tags 中", retryable=False)
    parsed = _parse_json_object(text, "Gemini 角色审计")
    raw_decisions = parsed.get("decisions")
    raw_prompt = parsed.get("character_prompt")
    if not isinstance(raw_decisions, list) or not isinstance(raw_prompt, list):
        raise LLMError("Gemini 角色审计缺少 decisions 或 character_prompt 数组", retryable=True)

    original_set = set(original_tags)
    seen_sources: set[str] = set()
    decisions: list[dict] = []
    replacement_sources: set[str] = set()
    for index, raw in enumerate(raw_decisions):
        if not isinstance(raw, dict):
            raise LLMError(f"角色审计 decision #{index + 1} 不是对象", retryable=True)
        tag = raw.get("tag")
        if not isinstance(tag, str) or tag not in original_set:
            raise LLMError(f"角色审计包含未知或被改写的 tag: {_clean_text(tag)}", retryable=True)
        if tag in seen_sources:
            raise LLMError(f"角色审计重复返回 tag: {tag}", retryable=True)
        seen_sources.add(tag)
        category = _clean_text(raw.get("category")).lower()
        decision = _clean_text(raw.get("decision")).lower()
        replacement = _clean_text(raw.get("replacement_tag"))
        include = raw.get("include_in_prompt")
        prompt_order = raw.get("prompt_order")
        reason = _clean_text(raw.get("reason"))
        if category not in AUDIT_CATEGORIES:
            raise LLMError(f"角色审计 category 无效: {category or '(empty)'}", retryable=True)
        if decision not in AUDIT_DECISIONS:
            raise LLMError(f"角色审计 decision 无效: {decision or '(empty)'}", retryable=True)
        if not isinstance(include, bool):
            raise LLMError(f"角色审计 tag {tag} 的 include_in_prompt 必须是布尔值", retryable=True)
        if not reason:
            raise LLMError(f"角色审计 tag {tag} 缺少 reason", retryable=True)
        if decision in {"delete", "replace"} and category not in AUDIT_MUTABLE_CATEGORIES:
            raise LLMError(f"角色审计试图修改受保护 tag: {tag}", retryable=True)
        if decision == "replace":
            if not replacement or replacement == tag:
                raise LLMError(f"角色审计 tag {tag} 的替换目标无效", retryable=True)
            if re.search(r"[,\r\n\x00-\x1f\x7f]", replacement):
                raise LLMError(f"角色审计 tag {tag} 的替换目标不是单一 tag", retryable=True)
            if re.search(r"\([^)]*:\s*\d+(?:\.\d+)?\)", replacement):
                raise LLMError(f"角色审计 tag {tag} 的替换目标包含权重", retryable=True)
            replacement_sources.add(_normalise_tag(tag))
        elif replacement:
            raise LLMError(f"角色审计 tag {tag} 不是 replace 却提供了 replacement_tag", retryable=True)
        if decision in {"delete", "uncertain"} and include:
            raise LLMError(f"角色审计 tag {tag} 不应进入角色 prompt", retryable=True)
        if include:
            if isinstance(prompt_order, bool) or not isinstance(prompt_order, int) or prompt_order < 0:
                raise LLMError(f"角色审计 tag {tag} 缺少有效 prompt_order", retryable=True)
        elif prompt_order is not None and (isinstance(prompt_order, bool) or not isinstance(prompt_order, int)):
            raise LLMError(f"角色审计 tag {tag} 的 prompt_order 无效", retryable=True)
        decisions.append({
            "tag": tag,
            "category": category,
            "decision": decision,
            "replacementTag": replacement,
            "includeInPrompt": include,
            "promptOrder": prompt_order,
            "reason": reason,
            "sourceIndex": original_tags.index(tag),
        })

    missing = [tag for tag in original_tags if tag not in seen_sources]
    if missing or len(decisions) != len(original_tags):
        raise LLMError("角色审计遗漏原始 tags: " + ", ".join(missing), retryable=True)
    by_tag = {item["tag"]: item for item in decisions}
    trigger = by_tag[locked_trigger]
    if trigger["decision"] != "keep" or trigger["replacementTag"] or not trigger["includeInPrompt"] or trigger["promptOrder"] != 0:
        raise LLMError("角色审计没有正确锁定角色 trigger", retryable=True)
    for item in decisions:
        target = item["replacementTag"]
        if target and _normalise_tag(target) in replacement_sources:
            raise LLMError(f"角色审计形成 replacement chain: {item['tag']} -> {target}", retryable=True)

    ordered = sorted(
        (item for item in decisions if item["includeInPrompt"]),
        key=lambda item: (item["promptOrder"], item["sourceIndex"]),
    )
    expected_prompt: list[str] = []
    prompt_seen: set[str] = set()
    for item in ordered:
        canonical = item["replacementTag"] if item["decision"] == "replace" else item["tag"]
        normalized = _normalise_tag(canonical)
        if normalized not in prompt_seen:
            expected_prompt.append(canonical)
            prompt_seen.add(normalized)
    character_prompt = []
    for raw_tag in raw_prompt:
        if not isinstance(raw_tag, str):
            raise LLMError("角色审计 character_prompt 包含非字符串项", retryable=True)
        tag = _clean_text(raw_tag)
        if not tag or re.search(r"[,\r\n\x00-\x1f\x7f]", tag):
            raise LLMError("角色审计 character_prompt 包含无效 tag", retryable=True)
        character_prompt.append(tag)
    if character_prompt != expected_prompt:
        raise LLMError("角色审计 character_prompt 与 decisions 的选择或顺序不一致", retryable=True)
    if not character_prompt or character_prompt[0] != locked_trigger:
        raise LLMError("角色审计 character_prompt 未以 locked trigger 开头", retryable=True)
    return {
        "lockedTrigger": locked_trigger,
        "decisions": decisions,
        "characterPrompt": character_prompt,
    }


def _reconcile_character_audits(original_tags: list[str], audits: list[dict]) -> tuple[list[str], list[str], dict]:
    original_tags = _parse_tags(original_tags)
    decisions_by_audit = [
        {decision["tag"]: decision for decision in audit.get("decisions", [])}
        for audit in audits
    ]
    warnings: list[str] = []
    mutations: dict[str, tuple[str, str]] = {}
    for tag in original_tags:
        candidates = []
        for decisions in decisions_by_audit:
            item = decisions.get(tag)
            if item and item["decision"] in {"delete", "replace"}:
                candidates.append((item["decision"], item["replacementTag"]))
        unique = []
        seen = set()
        for decision, replacement in candidates:
            signature = (decision, _normalise_tag(replacement) if replacement else "")
            if signature not in seen:
                unique.append((decision, replacement))
                seen.add(signature)
        if len(unique) == 1:
            mutations[tag] = unique[0]
        elif len(unique) > 1:
            warnings.append(f"角色审计对 tag {tag} 给出冲突修改，已保留原值")

    result: list[str] = []
    result_seen: set[str] = set()
    for tag in original_tags:
        decision, replacement = mutations.get(tag, ("keep", ""))
        if decision == "delete":
            continue
        output = replacement if decision == "replace" else tag
        normalized = _normalise_tag(output)
        if normalized not in result_seen:
            result.append(output)
            result_seen.add(normalized)

    report = {
        "characters": [
            {
                "lockedTrigger": audit.get("lockedTrigger", ""),
                "characterPrompt": audit.get("characterPrompt", []),
                "changes": [
                    {
                        "tag": item["tag"],
                        "decision": item["decision"],
                        "replacementTag": item["replacementTag"],
                    }
                    for item in audit.get("decisions", [])
                    if item.get("decision") in {"delete", "replace"}
                ],
            }
            for audit in audits
        ]
    }
    return result, warnings, report


class LLMProcessor:
    def __init__(self, state, config: ConfigStore, progress_callback=None) -> None:
        self.state = state
        self.config = config
        self.progress_callback = progress_callback
        self._progress_context = threading.local()
        self.jobs: dict[str, dict] = {}
        self.lock = threading.RLock()
        self._cache_clear_in_progress = False

    def public_jobs(self) -> list[dict]:
        with self.lock:
            return [self._public_job(job) for job in self.jobs.values() if job["status"] in {"running", "queued"}]

    def _public_job(self, job: dict) -> dict:
        active_stages = []
        for image_name, record in sorted(job.get("activeStages", {}).items()):
            if not isinstance(record, dict):
                continue
            public_record = {
                "image": image_name,
                "stage": record.get("stage", "cache"),
                "startedAt": record.get("startedAt", ""),
                "updatedAt": record.get("updatedAt", ""),
            }
            for key in (
                "phase", "attempt", "maxAttempts", "retryInSeconds", "reason",
                "provider", "model", "timeoutSeconds", "imageCompressionLevel",
                "requestImageMaxPixels", "requestImageQuality",
            ):
                if key in record:
                    public_record[key] = record[key]
            active_stages.append(public_record)
        finished_at = job.get("finishedAt", "")
        return {
            "id": job["id"],
            "status": job["status"],
            "total": job["total"],
            "done": job["done"],
            "cached": job["cached"],
            "failed": len(job["failed"]),
            "warnings": job["warnings"][-20:],
            "errors": job["failed"][-20:],
            "current": job.get("current", ""),
            "activeStages": active_stages,
            "recentStages": [
                {
                    "image": _clean_text(record.get("image")),
                    "status": record.get("status", "completed"),
                    "finishedAt": record.get("finishedAt", ""),
                }
                for record in job.get("recentStages", [])[:3]
                if isinstance(record, dict) and _clean_text(record.get("image"))
            ],
            "startedAt": job["startedAt"],
            "finishedAt": finished_at,
            "elapsedSeconds": _elapsed_seconds(job.get("startedAt", ""), finished_at),
        }

    def get_job(self, job_id: str) -> dict | None:
        with self.lock:
            job = self.jobs.get(job_id)
            return self._public_job(job) if job else None

    def _enabled_skills(self) -> list[str]:
        getter = getattr(self.config, "enabled_skills", None)
        if callable(getter):
            return list(getter())
        value = self.config.config.get("enabledSkills", [])
        return [skill_id for skill_id in SKILL_SPECS if skill_id in value] if isinstance(value, list) else []

    def _skill_enabled(self, skill_id: str) -> bool:
        return skill_id in self._enabled_skills()

    def _skill_prompt(self, skill_id: str) -> str:
        getter = getattr(self.config, "skill_prompt", None)
        if callable(getter):
            return _clean_text(getter(skill_id))
        return _clean_text(SkillRegistry().prompt(skill_id)) if self._skill_enabled(skill_id) else ""

    def _skill_hashes(self) -> dict[str, str]:
        getter = getattr(self.config, "enabled_skill_hashes", None)
        if callable(getter):
            return dict(getter())
        return SkillRegistry().hashes(self._enabled_skills())

    def _validate(self, options: dict, image_ids: list[str] | None = None) -> None:
        validator = getattr(self.config, "validate_skills", None)
        if callable(validator):
            validator()
        required_stages = ["tag", "clean"]
        if _include_character_descriptions(options) and image_ids:
            character_tags = {
                _normalise_tag(entry.get("tag"))
                for entry in getattr(self.state, "characters", {}).values()
                if isinstance(entry, dict) and _clean_text(entry.get("tag"))
            }
            if any(
                any(_normalise_tag(tag) in character_tags for tag in self.state.images[image_id].get("tags", []))
                for image_id in image_ids
                if image_id in self.state.images
            ):
                required_stages.append("apose")
        checked: set[str] = set()
        for stage in required_stages:
            provider, model = self._stage_target(stage)
            if not model:
                raise ValueError(f"{stage} 阶段未选择模型")
            if stage in {"tag", "apose"}:
                capability_getter = getattr(self.config, "model_vision_capability", None)
                capability = capability_getter(provider, model) if callable(capability_getter) else None
                if capability is False:
                    raise ValueError(f"{stage} 阶段模型 {model} 不支持图像输入")
            if provider in checked:
                continue
            checked.add(provider)
            url_key = "geminiUrl" if provider == "gemini" else "volcanoUrl"
            if not _clean_text(self.config.config.get(url_key)):
                raise ValueError(f"{stage} 阶段未配置 {provider} API 地址")
            if not self._provider_key(provider):
                provider_name = "Gemini" if provider == "gemini" else "火山方舟"
                raise ValueError(f"{stage} 阶段需要配置 {provider_name} API Key")

    def start(self, image_ids: list[str], options: dict) -> dict:
        ids = [item for item in image_ids if item in self.state.images]
        if not ids:
            raise ValueError("没有可处理的图片")
        raw_options = options if isinstance(options, dict) else {}
        if "styleMode" in raw_options:
            style_mode = bool(raw_options.get("styleMode"))
            include_character_descriptions = not style_mode
        else:
            # Keep requests from older frontends deterministic during the transition.
            style_mode = False
            include_character_descriptions = bool(raw_options.get("characterCleaning")) if "characterCleaning" in raw_options else True
        options = {
            "styleMode": style_mode,
            "includeCharacterDescriptions": include_character_descriptions,
            "allowNaturalLanguage": bool(raw_options.get("allowNaturalLanguage")),
            "autoSaveResults": bool(raw_options.get(
                "autoSaveResults",
                self.config.config.get("autoSaveResults", True),
            )),
        }
        options["protectedDatasetTags"] = (
            _detect_dataset_trigger(list(self.state.images.values()))
            if style_mode
            else []
        )
        self._validate(options, ids)
        with self.lock:
            if self._cache_clear_in_progress:
                raise ValueError("LLM 缓存正在清除，请稍后再执行")
            active = next((job for job in self.jobs.values() if job["status"] in {"queued", "running"}), None)
            if active:
                raise ValueError("已有 LLM 清洗任务正在运行")
            job_id = uuid.uuid4().hex
            job = {
                "id": job_id,
                "status": "queued",
                "imageIds": ids,
                "options": options,
                "total": len(ids),
                "done": 0,
                "cached": 0,
                "failed": [],
                "warnings": [],
                "current": "",
                "activeStages": {},
                "recentStages": [],
                "startedAt": _now(),
                "finishedAt": "",
            }
            self.jobs[job_id] = job
            threading.Thread(target=self._run, args=(job,), daemon=True, name=f"tagcleaner-llm-{job_id[:8]}").start()
            return self._public_job(job)

    def clear_cache(self) -> dict:
        """Serialize cache clearing against job creation without reversing lock order."""
        with self.lock:
            active = next((job for job in self.jobs.values() if job["status"] in {"queued", "running"}), None)
            if active:
                raise ValueError("LLM 清洗任务运行中，不能清除缓存")
            if self._cache_clear_in_progress:
                raise ValueError("LLM 缓存正在清除")
            self._cache_clear_in_progress = True
        try:
            clearer = getattr(self.state, "clear_llm_cache", None)
            if not callable(clearer):
                raise ValueError("当前数据状态不支持清除 LLM 缓存")
            return clearer()
        finally:
            with self.lock:
                self._cache_clear_in_progress = False

    def _set_job(self, job: dict, **changes) -> None:
        with self.lock:
            job.update(changes)

    def _run(self, job: dict) -> None:
        self._set_job(job, status="running")
        snapshot = getattr(self.config, "snapshot", None)
        active_names: set[str] = set()

        def report_stage(image: dict, stage: str, activity: dict | None = None) -> None:
            image_name = image.get("name", "")
            if not image_name:
                return
            with self.lock:
                previous = job.setdefault("activeStages", {}).get(image_name, {})
                started_at = previous.get("startedAt") if previous.get("stage") == stage else _now()
                record = dict(previous) if previous.get("stage") == stage else {}
                record.update({"stage": stage, "startedAt": started_at, "updatedAt": _now()})
                if isinstance(activity, dict):
                    for key in (
                        "phase", "attempt", "maxAttempts", "retryInSeconds", "reason",
                        "provider", "model", "timeoutSeconds", "imageCompressionLevel",
                        "requestImageMaxPixels", "requestImageQuality",
                    ):
                        if key in activity:
                            record[key] = activity[key]
                    if activity.get("phase") != "retry_wait":
                        record.pop("retryInSeconds", None)
                        record.pop("reason", None)
                job["activeStages"][image_name] = record

        worker = LLMProcessor(
            self.state,
            snapshot() if callable(snapshot) else self.config,
            progress_callback=report_stage,
        )

        def process_one(image_id: str):
            image = self.state.images.get(image_id)
            if not image:
                return None, None, None
            with self.lock:
                active_names.add(image["name"])
                job["current"] = " · ".join(sorted(active_names))
                job.setdefault("activeStages", {})[image["name"]] = {
                    "stage": "cache",
                    "startedAt": _now(),
                    "updatedAt": _now(),
                }
            try:
                outcome = worker._process_image(image, job["options"])
                return image, outcome, None
            except Exception as exc:
                return image, None, exc
            finally:
                with self.lock:
                    active_names.discard(image["name"])
                    job.setdefault("activeStages", {}).pop(image["name"], None)
                    job["current"] = " · ".join(sorted(active_names))

        max_workers = min(BATCH_CONCURRENCY, len(job["imageIds"]))
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="tagcleaner-stage") as pool:
            futures = [pool.submit(process_one, image_id) for image_id in job["imageIds"]]
            for future in as_completed(futures):
                image, outcome, error = future.result()
                with self.lock:
                    if outcome and outcome.get("cached"):
                        job["cached"] += 1
                    if outcome:
                        job["warnings"].extend(outcome.get("warnings", []))
                    if error is not None and image is not None:
                        job["failed"].append(f"{image['name']}: {_clean_text(error)}")
                    if image is not None:
                        recent_status = "failed" if error is not None else ("cached" if outcome and outcome.get("cached") else "completed")
                        job.setdefault("recentStages", []).insert(0, {
                            "image": image["name"],
                            "status": recent_status,
                            "finishedAt": _now(),
                        })
                        del job["recentStages"][3:]
                    job["done"] += 1
        self._set_job(job, status="completed" if not job["failed"] else "completed_with_errors", current="", finishedAt=_now())

    def _cache_path(self, image: dict) -> Path:
        return image["path"].parent / ".tagcleaner-cache.json"

    def _read_cache(self, image: dict) -> dict:
        raw = _read_json(self._cache_path(image), {})
        return raw if isinstance(raw, dict) else {}

    def _write_cache(self, image: dict, cache: dict) -> None:
        with self.state.lock:
            path = self._cache_path(image)
            current = _read_json(path, {})
            merged = current if isinstance(current, dict) else {}
            incoming_entries = cache.get("entries", {}) if isinstance(cache, dict) else {}
            merged_entries = merged.get("entries", {}) if isinstance(merged.get("entries"), dict) else {}
            if isinstance(incoming_entries, dict):
                merged_entries.update(incoming_entries)
            merged["entries"] = merged_entries
            _write_json(path, merged)

    def _fingerprint(self, image: dict) -> str:
        try:
            stat = image["path"].stat()
            return f"{stat.st_size}:{stat.st_mtime_ns}"
        except OSError:
            return "missing"

    def _config_hash(self, options: dict) -> str:
        relevant = {
            key: self.config.config.get(key)
            for key in (
                "volcanoUrl", "volcanoModel", "volcanoProtocol", "geminiUrl", "geminiModel",
                "geminiProtocol", "imageDetail", "imageMaxPixels", "visionPrompt", "cleanPrompt", "characterPrompt",
                "characterAuditMode", "tagProvider", "tagModel", "aposeProvider", "aposeModel",
                "cleanProvider", "cleanModel",
            )
        }
        relevant["enabledSkills"] = self._enabled_skills()
        relevant["skillHashes"] = self._skill_hashes()
        relevant["callRoutes"] = self._stage_routes()
        relevant["pipelineVersion"] = 7
        relevant.update({key: value for key, value in options.items() if key != "autoSaveResults"})
        return hashlib.sha256(json.dumps(relevant, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    def _report_progress(self, image: dict, stage: str, activity: dict | None = None) -> None:
        if callable(self.progress_callback):
            self.progress_callback(image, stage, activity or {})

    def _report_request_event(self, stage: str, event: dict) -> None:
        image = getattr(self._progress_context, "image", None)
        if isinstance(image, dict):
            self._report_progress(image, stage, event)

    def _auto_save_result(self, image: dict, options: dict, warnings: list[str]) -> None:
        if not options.get("autoSaveResults"):
            return
        saver = getattr(self.state, "save_image", None)
        if not callable(saver):
            return
        try:
            saver(image["id"])
        except (OSError, ValueError) as exc:
            warnings.append(f"{image['name']}: LLM 结果自动保存失败，已保留为未保存改动: {_clean_text(exc)}")

    def _character_context(self, tags: list[str], options: dict) -> dict:
        context = {
            "descriptions": "",
            "auditedTags": list(tags),
            "auditReport": {"characters": []},
            "warnings": [],
        }
        if not _include_character_descriptions(options):
            return context
        normalized = {_normalise_tag(tag) for tag in tags}
        excluded = {"alternate_costume", "official_alternate_costume", "alternate_outfit", "official_alternate_outfit"}
        if normalized & excluded:
            context["warnings"].append("检测到 alternate costume / official alternate costume，已跳过角色描述与审计")
            return context
        apose_provider, _ = self._stage_target("apose")
        if not self._provider_key(apose_provider):
            context["warnings"].append(f"A-pose 识别供应商 {apose_provider} 未配置 Key，已跳过角色描述与审计")
            return context
        descriptions: list[str] = []
        audits: list[dict] = []
        audit_mode = _clean_text(self.config.config.get("characterAuditMode", "sparse")).lower()
        for key, entry in list(self.state.characters.items()):
            if not isinstance(entry, dict):
                continue
            character_tag = _clean_text(entry.get("tag"))
            if not character_tag or _normalise_tag(character_tag) not in normalized:
                continue
            locked_trigger = next(tag for tag in tags if _normalise_tag(tag) == _normalise_tag(character_tag))
            apose = Path(os.path.expandvars(os.path.expanduser(_clean_text(entry.get("aposePath")))))
            if not apose.is_file():
                context["warnings"].append(f"角色 {character_tag} 的 A-pose 图片不存在，已跳过")
                continue
            description = _clean_text(entry.get("description"))
            if not description:
                description = self._gemini_character(apose)
                self.state.update_character_description(key, description)
            if description:
                descriptions.append(f"{character_tag}: {description}")
            if self._skill_enabled("character-tag-auditor"):
                audits.append(self._gemini_character_audit(
                    apose,
                    tags,
                    locked_trigger,
                    description,
                    audit_mode,
                ))
        if audits:
            audited_tags, reconcile_warnings, report = _reconcile_character_audits(tags, audits)
            context["auditedTags"] = audited_tags
            context["auditReport"] = {"mode": audit_mode, **report}
            context["warnings"].extend(reconcile_warnings)
        context["descriptions"] = "\n".join(descriptions)
        return context

    def _stage_target(self, stage: str) -> tuple[str, str]:
        defaults = {
            "tag": ("volcano", self.config.config.get("volcanoModel", "doubao-seed-2.1-turbo")),
            "apose": ("volcano", self.config.config.get("volcanoModel", "doubao-seed-2.1-turbo")),
            "clean": ("gemini", self.config.config.get("geminiModel", "gemini-3.5-flash")),
        }
        default_provider, default_model = defaults.get(stage, ("volcano", ""))
        provider = _clean_text(self.config.config.get(stage + "Provider", default_provider)).lower()
        if provider in {"ark", "volcengine", "volcano"}:
            provider = "volcano"
        elif provider not in {"gemini", "volcano"}:
            provider = default_provider
        model = _canonical_model_id(self.config.config.get(stage + "Model", "")) or _canonical_model_id(default_model)
        return provider, model

    def _stage_routes(self) -> dict[str, dict[str, str]]:
        return {
            stage: {"provider": provider, "model": model}
            for stage in ("tag", "apose", "clean")
            for provider, model in [self._stage_target(stage)]
        }

    def _provider_key(self, provider: str) -> str:
        return self.config.get_key("gemini" if provider == "gemini" else "volcano")

    def _provider_request(
        self,
        provider: str,
        model: str,
        prompt: str,
        image: Path | None,
        label: str,
        progress_stage: str = "",
    ) -> str:
        provider = "gemini" if provider == "gemini" else "volcano"
        model = _canonical_model_id(model)
        if not model:
            raise LLMError(f"{label} 未选择模型", retryable=False)
        url = _clean_text(self.config.config.get("geminiUrl" if provider == "gemini" else "volcanoUrl"))
        key = self._provider_key(provider)
        if not url:
            raise LLMError(f"{label} 未配置 API 地址", retryable=False)
        if not key:
            raise LLMError(f"{label} 未配置供应商 API Key", retryable=False)
        try:
            max_pixels = int(float(self.config.config.get("imageMaxPixels", 0)))
        except (TypeError, ValueError):
            max_pixels = 0
        timeout = float(self.config.config.get("timeoutSeconds", 300))
        compression_level = 0

        def call() -> str:
            effective_max_pixels, jpeg_quality, force_jpeg = _image_retry_profile(max_pixels, compression_level)
            data_url = _image_data_url(
                image,
                effective_max_pixels,
                force_jpeg=force_jpeg,
                jpeg_quality=jpeg_quality,
            ) if image else None
            if provider == "volcano":
                protocol = _clean_text(self.config.config.get("volcanoProtocol", "chat")).lower()
                endpoint = _volcano_url(url, protocol)
                detail = _clean_text(self.config.config.get("imageDetail", "high")).lower() or "high"
                # `min_pixels` is optional and some API Plan Responses
                # deployments reject an explicit minimum for Base64 input.
                image_limit = {"max_pixels": effective_max_pixels} if effective_max_pixels > 0 else None
                if protocol == "responses" or endpoint.rstrip("/").endswith("/responses"):
                    content = []
                    if data_url:
                        image_part = {"type": "input_image", "image_url": data_url, "detail": detail}
                        if image_limit:
                            image_part["image_pixel_limit"] = image_limit
                        content.append(image_part)
                    content.append({"type": "input_text", "text": prompt})
                    payload = {"model": model, "input": [{"role": "user", "content": content}]}
                elif data_url:
                    image_url = {"url": data_url, "detail": detail}
                    if image_limit:
                        image_url["image_pixel_limit"] = image_limit
                    payload = {
                        "model": model,
                        "messages": [{"role": "user", "content": [
                            {"type": "image_url", "image_url": image_url},
                            {"type": "text", "text": prompt},
                        ]}],
                        "max_tokens": 1600,
                        "temperature": 0.2,
                    }
                else:
                    payload = {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 1600, "temperature": 0.15}
                response = _request_json(endpoint, payload, {"Authorization": f"Bearer {key}"}, timeout)
            else:
                protocol = _clean_text(self.config.config.get("geminiProtocol", "openai")).lower()
                if protocol in {"native", "gemini"}:
                    endpoint = _native_gemini_url(url, model)
                    parts = []
                    if data_url:
                        parts.append({"inline_data": {"mime_type": data_url.split(";", 1)[0].split(":", 1)[1], "data": data_url.split(",", 1)[1]}})
                    parts.append({"text": prompt})
                    response = _request_json(endpoint, {"contents": [{"role": "user", "parts": parts}]}, {"x-goog-api-key": key}, timeout)
                else:
                    content = []
                    if data_url:
                        content.append({"type": "image_url", "image_url": {"url": data_url, "detail": self.config.config.get("imageDetail", "high")}})
                    content.append({"type": "text", "text": prompt})
                    response = _request_json(url, {"model": model, "messages": [{"role": "user", "content": content}], "temperature": 0.15}, {"Authorization": f"Bearer {key}"}, timeout)
            return _extract_text(response)

        def report_request(event: dict) -> None:
            nonlocal compression_level
            if image and event.get("phase") == "retry_wait" and event.get("reason") == "timeout":
                compression_level = min(2, compression_level + 1)
            if not progress_stage:
                return
            effective_max_pixels, jpeg_quality, force_jpeg = _image_retry_profile(max_pixels, compression_level)
            self._report_request_event(progress_stage, {
                **event,
                "provider": provider,
                "model": model,
                "timeoutSeconds": timeout,
                "imageCompressionLevel": compression_level if image else 0,
                "requestImageMaxPixels": effective_max_pixels if image else 0,
                "requestImageQuality": jpeg_quality if image and force_jpeg else 0,
            })

        return _retry(call, self.config, label, report_request)

    def _stage_request(self, stage: str, prompt: str, image: Path | None = None) -> str:
        provider, model = self._stage_target(stage)
        labels = {"tag": "标签识别", "apose": "A-pose 识别", "clean": "数据清洗"}
        progress_stage = {"tag": "vision", "apose": "apose", "clean": "clean"}.get(stage, stage)
        return self._provider_request(provider, model, prompt, image, labels.get(stage, stage), progress_stage)

    def _volcano(self, image: Path, prompt: str) -> str:
        model = _canonical_model_id(self.config.config.get("tagModel")) or _canonical_model_id(self.config.config.get("volcanoModel"))
        return self._provider_request("volcano", model, prompt, image, "火山视觉分析")

    def _gemini_text(self, prompt: str) -> str:
        return self._provider_request("gemini", _clean_text(self.config.config.get("geminiModel")), prompt, None, "Gemini 请求")

    def _gemini_image(self, image: Path, prompt: str) -> str:
        return self._provider_request("gemini", _clean_text(self.config.config.get("geminiModel")), prompt, image, "Gemini 请求")

    def _gemini_character(self, image: Path) -> str:
        retries = max(0, int(self.config.config.get("retryCount", 2)))
        delay = max(0.2, float(self.config.config.get("retryDelaySeconds", 4)))
        prompt_template = self.config.config.get("characterPrompt", DEFAULT_CHARACTER_PROMPT)
        prompt = _render(prompt_template, {
            "dataset_mode_policy": STANDARD_MODE_POLICY,
            "natural_language_policy": "disabled: return appearance tags only",
        })
        if "{{dataset_mode_policy}}" not in str(prompt_template):
            prompt += "\n\nRuntime character mode: standard/person mode; return stable appearance only."
        last: LLMError | None = None
        for attempt in range(retries + 1):
            try:
                return _parse_character_output(self._stage_request("apose", prompt, image))
            except LLMError as exc:
                last = exc
                if not exc.retryable or attempt >= retries:
                    raise
                time.sleep(delay * (2**attempt))
        raise last or LLMError("Gemini 角色描述结果无法解析", retryable=False)

    def _gemini_character_audit(
        self,
        image: Path,
        original_tags: list[str],
        locked_trigger: str,
        reference_description: str,
        audit_mode: str,
    ) -> dict:
        retries = max(0, int(self.config.config.get("retryCount", 2)))
        delay = max(0.2, float(self.config.config.get("retryDelaySeconds", 4)))
        runtime_prompt = self._skill_prompt("character-tag-auditor")
        if not runtime_prompt:
            raise LLMError("Character Tag Auditor Skill 未启用或运行提示词为空", retryable=False)
        prompt = _render(runtime_prompt, {
            "locked_trigger": locked_trigger,
            "audit_mode": audit_mode,
            "reference_description": reference_description or "(not available)",
            "original_tags": json.dumps(list(original_tags), ensure_ascii=False),
        })
        last: LLMError | None = None
        for attempt in range(retries + 1):
            try:
                response = self._stage_request("apose", prompt, image)
                return _parse_character_audit_output(response, original_tags, locked_trigger)
            except LLMError as exc:
                last = exc
                if not exc.retryable or attempt >= retries:
                    raise
                time.sleep(delay * (2**attempt))
        raise last or LLMError("Gemini 角色审计结果无法解析", retryable=False)

    def _gemini_request(self, prompt: str, image: Path | None) -> str:
        return self._provider_request("gemini", _clean_text(self.config.config.get("geminiModel")), prompt, image, "Gemini 请求")

    def _gemini_clean(self, prompt: str) -> tuple[list[str], str]:
        retries = max(0, int(self.config.config.get("retryCount", 2)))
        delay = max(0.2, float(self.config.config.get("retryDelaySeconds", 4)))
        last: LLMError | None = None
        for attempt in range(retries + 1):
            try:
                return _parse_clean_output(self._stage_request("clean", prompt))
            except LLMError as exc:
                last = exc
                if not exc.retryable or attempt >= retries:
                    raise
                time.sleep(delay * (2**attempt))
        raise last or LLMError("Gemini 清洗结果无法解析", retryable=False)

    def _vision_analyze(self, prompt: str, image: Path) -> tuple[list[str], str]:
        retries = max(0, int(self.config.config.get("retryCount", 2)))
        delay = max(0.2, float(self.config.config.get("retryDelaySeconds", 4)))
        last: LLMError | None = None
        for attempt in range(retries + 1):
            try:
                return _parse_vision_output(self._stage_request("tag", prompt, image))
            except LLMError as exc:
                last = exc
                if not exc.retryable or attempt >= retries:
                    raise
                time.sleep(delay * (2**attempt))
        raise last or LLMError("视觉反推结果无法解析", retryable=False)

    def _process_image(self, image: dict, options: dict) -> dict:
        self._progress_context.image = image
        self._report_progress(image, "cache")
        source_tags = list(image.get("tags", []))
        source_description = _clean_text(image.get("description", ""))
        config_hash = self._config_hash(options)
        cache = self._read_cache(image)
        key = image["relativePath"]
        cached = cache.get("entries", {}).get(key, {}) if isinstance(cache.get("entries"), dict) else {}
        fingerprint = self._fingerprint(image)
        if cached.get("fingerprint") == fingerprint and cached.get("configHash") == config_hash and cached.get("status") == "completed":
            warnings: list[str] = []
            cached_result_tags = _parse_tags(cached.get("resultTags", []))
            cached_result_description = _clean_text(cached.get("resultDescription", ""))
            result_tags, result_description = _ensure_watermark_evidence(
                cached_result_tags,
                cached_result_description,
                cached.get("visionTags", []),
                cached.get("visionDescription", ""),
                bool(options.get("allowNaturalLanguage")),
            )
            repaired = result_tags != cached_result_tags or result_description != cached_result_description
            if repaired:
                cached["resultTags"] = result_tags
                cached["resultDescription"] = result_description
                cached["updatedAt"] = _now()
                cache.setdefault("entries", {})[key] = cached
                self._write_cache(image, cache)
            source = cached.get("source", {})
            if source.get("tags") == source_tags and _clean_text(source.get("description", "")) == source_description:
                self.state.apply_llm_result(image["id"], result_tags, result_description, "cached")
                self._auto_save_result(image, options, warnings)
                return {"cached": True, "warnings": warnings}
            if cached_result_tags == source_tags and cached_result_description == source_description:
                if repaired:
                    self.state.apply_llm_result(image["id"], result_tags, result_description, "cached")
                    self._auto_save_result(image, options, warnings)
                return {"cached": True, "warnings": warnings}

        style_mode = _style_mode(options)
        source_normalized = {_normalise_tag(source_tag) for source_tag in source_tags}
        protected_dataset_tags = [
            tag
            for tag in _parse_tags(options.get("protectedDatasetTags", []))
            if _normalise_tag(tag) in source_normalized
        ]
        if _include_character_descriptions(options):
            self._report_progress(image, "apose")
        character_context = self._character_context(source_tags, options)
        warnings = list(character_context["warnings"])
        audited_tags = list(character_context["auditedTags"])
        audit_report_text = json.dumps(character_context["auditReport"], ensure_ascii=False, separators=(",", ":"))
        natural_policy = "enabled: return a useful paragraph for composition, pose, lighting, and relationships" if options.get("allowNaturalLanguage") else "disabled: return an empty description in the final archive"
        vision_natural_policy = (
            natural_policy
            + "; the vision-stage description remains mandatory internal evidence and must still be returned"
        )
        mode_policy = STYLE_MODE_POLICY if style_mode else STANDARD_MODE_POLICY
        vision_template = self.config.config.get("visionPrompt", DEFAULT_VISION_PROMPT)
        vision_values = {
            "tags": _join_tags(source_tags),
            "dataset_mode_policy": mode_policy,
            "natural_language_policy": vision_natural_policy,
        }
        vision_prompt = _render(vision_template, vision_values)
        vision_runtime = (
            "STYLE MODE is active for the final cleanup: identify factual depicted content clearly, "
            "but treat art-style and technique wording as removable."
            if style_mode
            else "STANDARD MODE is active: stable character appearance may be checked against the A-pose reference."
        )
        missing_vision_fields = []
        for field, value in (
            ("tags", _join_tags(source_tags)),
            ("dataset_mode_policy", mode_policy),
            ("natural_language_policy", vision_natural_policy),
        ):
            if "{{" + field + "}}" not in str(vision_template):
                missing_vision_fields.append(f"{field}: {value}")
        if missing_vision_fields:
            vision_prompt += "\n\nRuntime stage policy:\n" + "\n".join(missing_vision_fields) + "\n" + vision_runtime
        vision_prompt += (
            "\n\nRuntime vision response contract (mandatory; overrides every conflicting earlier instruction): "
            "return only one JSON object with exactly {\"tags\":[\"tag_a\",\"tag_b\"],"
            "\"description\":\"one natural-English paragraph\"}. tags must be a JSON string array and "
            "description must be a non-empty paragraph used as internal visual evidence. Do not output Markdown, "
            "a paragraph by itself, labels, or text outside the JSON object."
        )
        self._report_progress(image, "vision")
        vision_tags, vision_description = self._vision_analyze(vision_prompt, image["path"])
        merged_tag_candidates = _merge_tag_candidates(source_tags, vision_tags)
        clean_template = self.config.config.get("cleanPrompt", DEFAULT_CLEAN_PROMPT)
        character_descriptions = character_context["descriptions"]
        if style_mode:
            character_descriptions = "(not provided: style mode does not use character reference descriptions)"
        clean_values = {
            "tags": _join_tags(source_tags),
            "vision_tags": _join_tags(vision_tags),
            "merged_tag_candidates": _join_tags(merged_tag_candidates),
            "vision_description": vision_description,
            "character_descriptions": character_descriptions or "(none)",
            "audited_tags": _join_tags(audited_tags),
            "character_audit": audit_report_text,
            "protected_dataset_tags": _join_tags(protected_dataset_tags) or "(none)",
            "natural_language_policy": natural_policy,
            "dataset_mode_policy": mode_policy,
        }
        clean_prompt = _render(clean_template, clean_values)
        runtime_inputs = []
        for field, label in (
            ("tags", "Original WD14 tags (primary evidence)"),
            ("vision_tags", "Vision-model tags (secondary evidence)"),
            ("merged_tag_candidates", "Merged tag candidates (WD14 first)"),
            ("vision_description", "Visual description"),
            ("audited_tags", "Validated character-audit tags"),
            ("character_audit", "Character-audit report"),
            ("protected_dataset_tags", "Protected dataset activation tags"),
            ("character_descriptions", "Character reference descriptions"),
            ("natural_language_policy", "Natural-language policy"),
            ("dataset_mode_policy", "Dataset mode policy"),
        ):
            if "{{" + field + "}}" not in str(clean_template):
                runtime_inputs.append(f"{label}:\n{clean_values[field]}")
        if runtime_inputs:
            clean_prompt += "\n\nRuntime inputs:\n\n" + "\n\n".join(runtime_inputs)
        clean_prompt += (
            "\n\nRuntime tag-evidence policy (mandatory; overrides conflicting instructions): "
            "WD14 tags are primary and vision-model tags are secondary. Preserve WD14 spelling and order for "
            "equivalent tags. A vision omission never justifies deleting a WD14 tag. Add a vision-only tag only "
            "when the visual description clearly supports it. A WD14 tag may be removed only when contradicted "
            "by visual evidence, when it is metadata or an unsupported guess, or when the dataset mode policy "
            "requires removal. Never turn prose into a tag."
        )
        if self._skill_enabled("prompt-pyramid"):
            clean_prompt += (
                "\n\nEnabled runtime Skill: prompt-pyramid\n"
                + self._skill_prompt("prompt-pyramid")
            )
        if style_mode:
            clean_prompt += (
                "\n\nFinal STYLE MODE override (mandatory; overrides conflicting instructions):\n"
                + STYLE_MODE_POLICY
            )
        if protected_dataset_tags:
            clean_prompt += (
                "\n\nFinal protected dataset activation tags (mandatory; overrides conflicting instructions): "
                + _join_tags(protected_dataset_tags)
                + ". These opaque source tokens are training activation tags, not style descriptions. Preserve "
                "them exactly in the final tags even if the vision model does not repeat them."
            )
        description_contract = (
            "description must be one natural-English paragraph when useful"
            if options.get("allowNaturalLanguage")
            else "description must be an empty string"
        )
        clean_prompt += (
            "\n\nRuntime response contract (mandatory): return only one JSON object with "
            "exactly {\"tags\":[\"tag_a\",\"tag_b\"],\"description\":\"...\"}; "
            + description_contract
            + ". Do not use Markdown or add any other text."
        )
        self._report_progress(image, "clean")
        result_tags, result_description = self._gemini_clean(clean_prompt)
        result_tags = _ensure_protected_tags(result_tags, protected_dataset_tags)
        if not options.get("allowNaturalLanguage"):
            result_description = ""
        result_tags, result_description = _ensure_watermark_evidence(
            result_tags,
            result_description,
            vision_tags,
            vision_description,
            bool(options.get("allowNaturalLanguage")),
        )
        self._report_progress(image, "write")
        self.state.apply_llm_result(image["id"], result_tags, result_description, "completed")
        cache.setdefault("entries", {})[key] = {
            "status": "completed",
            "fingerprint": fingerprint,
            "configHash": config_hash,
            "source": {"tags": source_tags, "description": source_description},
            "wd14Tags": source_tags,
            "visionTags": vision_tags,
            "mergedTagCandidates": merged_tag_candidates,
            "protectedDatasetTags": protected_dataset_tags,
            "visionDescription": vision_description,
            "auditedTags": audited_tags,
            "characterAudit": character_context["auditReport"],
            "callRoutes": self._stage_routes(),
            "styleMode": style_mode,
            "characterDescriptionsIncluded": bool(character_context["descriptions"]),
            "enabledSkills": self._enabled_skills(),
            "skillHashes": self._skill_hashes(),
            "resultTags": result_tags,
            "resultDescription": result_description,
            "updatedAt": _now(),
        }
        self._write_cache(image, cache)
        self._auto_save_result(image, options, warnings)
        return {"cached": False, "warnings": warnings}

    def list_models(self, provider: str, draft: dict | None = None) -> dict:
        """Fetch a non-secret model catalog for a supplier without persisting draft settings."""
        if isinstance(draft, dict):
            preview = getattr(self.config, "preview", None)
            if callable(preview):
                return LLMProcessor(self.state, preview(draft)).list_models(provider)
        provider = "gemini" if _clean_text(provider).lower() == "gemini" else "volcano"
        url_key = "geminiUrl" if provider == "gemini" else "volcanoUrl"
        base_url = _clean_text(self.config.config.get(url_key))
        key = self._provider_key(provider)
        if not base_url:
            raise ValueError(f"{provider} 未配置 API 地址")
        if not key:
            raise ValueError(f"{provider} 未配置 API Key")
        headers = {"Authorization": f"Bearer {key}"}
        if provider == "gemini" and _clean_text(self.config.config.get("geminiProtocol", "openai")).lower() in {"native", "gemini"}:
            headers = {"x-goog-api-key": key}
        errors = []
        for endpoint in _model_list_urls(provider, base_url):
            try:
                payload = _request_json_get(endpoint, headers, float(self.config.config.get("timeoutSeconds", 300)))
                models = _normalise_model_catalog(provider, payload)
                if models:
                    setter = getattr(self.config, "set_model_catalog", None)
                    if callable(setter):
                        setter(provider, models)
                    return {"provider": provider, "models": models, "endpoint": endpoint}
                errors.append(f"{endpoint}: 未返回模型列表")
            except LLMError as exc:
                errors.append(f"{endpoint}: {exc}")
        detail = "; ".join(errors[-3:])
        cached_catalog = getattr(self.config, "model_catalog", {}).get(provider, [])
        fallback = [dict(item) for item in (cached_catalog or BUILTIN_MODEL_CATALOG.get(provider, []))]
        if fallback:
            return {
                "provider": provider,
                "models": fallback,
                "endpoint": "",
                "partial": True,
                "warning": "供应商未开放可用的模型目录接口；仅显示本地已知模型。" + (f" {detail}" if detail else ""),
            }
        raise LLMError(f"无法获取 {provider} 模型列表" + (f"：{detail}" if detail else ""), retryable=False)

    def test(self, provider: str, draft: dict | None = None) -> dict:
        if isinstance(draft, dict):
            preview = getattr(self.config, "preview", None)
            if callable(preview):
                return LLMProcessor(self.state, preview(draft)).test(provider)
        provider = "gemini" if provider == "gemini" else "volcano"
        if not self.config.get_key(provider):
            raise ValueError(f"尚未配置 {provider} API Key")
        text = self._gemini_text("Reply with the single word OK.") if provider == "gemini" else self._volcano_text_test()
        return {"provider": provider, "ok": bool(text), "preview": text[:120]}

    def generate_character(self, key: str) -> str:
        key = _clean_text(key).casefold()
        entry = self.state.characters.get(key)
        if not entry:
            raise ValueError("角色描述条目不存在")
        apose_provider, _ = self._stage_target("apose")
        if not self._provider_key(apose_provider):
            provider_name = "Gemini" if apose_provider == "gemini" else "火山方舟"
            raise ValueError(f"尚未配置 {provider_name} API Key")
        apose = Path(os.path.expandvars(os.path.expanduser(_clean_text(entry.get("aposePath")))))
        if not apose.is_file():
            raise ValueError("角色 A-pose 图片不存在")
        description = self._gemini_character(apose)
        self.state.update_character_description(key, description)
        return description

    def _volcano_text_test(self) -> str:
        model = _clean_text(self.config.config.get("tagModel")) or _clean_text(self.config.config.get("volcanoModel"))
        return self._provider_request("volcano", model, "Reply with the single word OK.", None, "火山文本测试")


def traceback_hint(exc: Exception) -> str:
    return _clean_text(str(exc)) or traceback.format_exc(limit=1).strip()
