from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from llm_service import ConfigStore, LLMError, LLMProcessor


ROOT = Path(__file__).resolve().parent
STATIC_ROOT = ROOT / "static"
DATA_ROOT = ROOT / ".tagcleaner-data"
APP_VERSION = "0.2.0"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
CONFIG_FILES = {
    "translations": DATA_ROOT / "translations.json",
    "rules": DATA_ROOT / "replacement-rules.json",
    "favorites": DATA_ROOT / "favorites.json",
    "characters": DATA_ROOT / "character-descriptions.json",
}


def ensure_dirs() -> None:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)


def choose_folder_dialog() -> dict:
    picker_script = (
        "import tkinter as tk\n"
        "from tkinter import filedialog\n"
        "import sys\n"
        "sys.stdout.reconfigure(encoding='utf-8')\n"
        "root = tk.Tk()\n"
        "root.withdraw()\n"
        "root.attributes('-topmost', True)\n"
        "path = filedialog.askdirectory(title='选择图片文件夹')\n"
        "root.destroy()\n"
        "print(path or '')\n"
    )
    options = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": 600,
        "check": False,
    }
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run([sys.executable, "-c", picker_script], **options)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"无法打开本机路径选择器: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or "").strip()
        raise ValueError(detail or "本机路径选择器启动失败")
    path = next((line.strip() for line in reversed(result.stdout.splitlines()) if line.strip()), "")
    return {"path": path}


def choose_image_dialog() -> dict:
    picker_script = (
        "import tkinter as tk\n"
        "from tkinter import filedialog\n"
        "import sys\n"
        "sys.stdout.reconfigure(encoding='utf-8')\n"
        "root = tk.Tk()\n"
        "root.withdraw()\n"
        "root.attributes('-topmost', True)\n"
        "path = filedialog.askopenfilename(title='选择角色 A-pose 图片', "
        "filetypes=[('Image files', '*.png *.jpg *.jpeg *.webp *.bmp'), ('All files', '*.*')])\n"
        "root.destroy()\n"
        "print(path or '')\n"
    )
    options = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": 600,
        "check": False,
    }
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run([sys.executable, "-c", picker_script], **options)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"无法打开本机图片选择器: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or "").strip()
        raise ValueError(detail or "本机图片选择器启动失败")
    path = next((line.strip() for line in reversed(result.stdout.splitlines()) if line.strip()), "")
    return {"path": path}


def read_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return fallback


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def clean_token(value) -> str:
    return str(value or "").strip()


def parse_tags(value) -> list[str]:
    if isinstance(value, list):
        raw = value
    else:
        raw = str(value or "").split(",")
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        token = clean_token(item).lstrip("\ufeff")
        if token and token not in seen:
            result.append(token)
            seen.add(token)
    return result


def join_tags(tags: list[str]) -> str:
    return ", ".join(parse_tags(tags))


def parse_prompt_text(value: str) -> tuple[list[str], str]:
    blocks = re.split(r"\r?\n\s*\r?\n", str(value or "").lstrip("\ufeff"), maxsplit=1)
    tags = parse_tags(blocks[0].replace("\r", "").replace("\n", ","))
    description = blocks[1].strip() if len(blocks) > 1 else ""
    return tags, description


def join_prompt_text(tags: list[str], description: str = "") -> str:
    tag_line = join_tags(tags)
    description = clean_token(description)
    return f"{tag_line}\n\n{description}" if description else tag_line


def image_fingerprint(path: Path) -> str:
    try:
        stat = path.stat()
        return f"{stat.st_size}:{stat.st_mtime_ns}"
    except OSError:
        return "missing"


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def mode_matches(tags: list[str], required: list[str], mode: str) -> bool:
    if not required:
        return True
    present = set(tags)
    if clean_token(mode).lower() == "any":
        return any(token in present for token in required)
    return all(token in present for token in required)


def write_matches(original: list[str], required: list[str], mode: str) -> bool:
    if not required:
        return False
    present = set(original)
    missing = [token for token in required if token not in present]
    if clean_token(mode).lower() == "any":
        return bool(missing)
    return len(missing) == len(required)


class LibraryState:
    def __init__(self) -> None:
        ensure_dirs()
        self.lock = threading.RLock()
        self.root: Path | None = None
        self.images: dict[str, dict] = {}
        self.translations: dict[str, str] = read_json(CONFIG_FILES["translations"], {})
        self.rules: list[dict] = read_json(CONFIG_FILES["rules"], [])
        self.favorites: list[dict] = read_json(CONFIG_FILES["favorites"], [])
        self.characters: dict[str, dict] = read_json(CONFIG_FILES["characters"], {})
        if not isinstance(self.translations, dict):
            self.translations = {}
        if not isinstance(self.rules, list):
            self.rules = []
        if not isinstance(self.favorites, list):
            self.favorites = []
        if not isinstance(self.characters, dict):
            self.characters = {}
        if not self.favorites:
            self.favorites = [{"id": "core", "name": "核心标签", "tags": []}]

    def _read_tags(self, image_path: Path) -> tuple[list[str], str, bool]:
        text_path = image_path.with_suffix(".txt")
        try:
            text = text_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return [], "", False
        tags, description = parse_prompt_text(text)
        return tags, description, True

    def _cache_status(self, image_path: Path, relative_path: str, cache_files: dict[Path, dict] | None = None) -> str:
        cache_path = image_path.parent / ".tagcleaner-cache.json"
        if cache_files is not None:
            if cache_path not in cache_files:
                raw = read_json(cache_path, {})
                cache_files[cache_path] = raw if isinstance(raw, dict) else {}
            cache = cache_files[cache_path]
        else:
            cache = read_json(cache_path, {})
        entries = cache.get("entries", {}) if isinstance(cache, dict) else {}
        entry = entries.get(relative_path, {}) if isinstance(entries, dict) else {}
        if entry.get("status") == "completed" and entry.get("fingerprint") == image_fingerprint(image_path):
            return "cached"
        return ""

    def load_folder(self, raw_path: str) -> dict:
        candidate = Path(os.path.expandvars(os.path.expanduser(clean_token(raw_path)))).resolve()
        if not candidate.is_dir():
            raise ValueError("文件夹不存在或不可读取")
        found: dict[str, dict] = {}
        cache_files: dict[Path, dict] = {}
        for image_path in sorted(candidate.rglob("*"), key=lambda p: str(p).lower()):
            if not image_path.is_file() or image_path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            tags, description, has_txt = self._read_tags(image_path)
            image_id = uuid.uuid4().hex
            relative = str(image_path.relative_to(candidate)).replace("\\", "/")
            found[image_id] = {
                "id": image_id,
                "path": image_path,
                "name": image_path.name,
                "relativePath": relative,
                "tags": tags,
                "originalTags": list(tags),
                "description": description,
                "originalDescription": description,
                "hasTxt": has_txt,
                "modified": False,
                "llmStatus": self._cache_status(image_path, relative, cache_files),
            }
        with self.lock:
            self.root = candidate
            self.images = found
        return self.public_state()

    def public_images(self) -> list[dict]:
        return [
            {
                "id": item["id"],
                "name": item["name"],
                "relativePath": item["relativePath"],
                "tags": list(item["tags"]),
                "description": item.get("description", ""),
                "hasTxt": item["hasTxt"],
                "modified": item["modified"],
                "llmStatus": item.get("llmStatus", ""),
                "src": f"/api/media/{item['id']}",
            }
            for item in self.images.values()
        ]

    def public_tags(self) -> list[dict]:
        counts: dict[str, int] = {}
        for image in self.images.values():
            for tag in image["tags"]:
                counts[tag] = counts.get(tag, 0) + 1
        return [
            {
                "tag": tag,
                "translation": self.translations.get(tag, ""),
                "count": counts[tag],
            }
            for tag in sorted(counts, key=lambda value: value.lower())
        ]

    def public_state(self) -> dict:
        with self.lock:
            changed = sum(1 for item in self.images.values() if item["modified"])
            return {
                "loadedPath": str(self.root) if self.root else "",
                "images": self.public_images(),
                "tags": self.public_tags(),
                "translations": dict(self.translations),
                "rules": self.rules,
                "favorites": self.favorites,
                "characters": self.public_characters(),
                "changedCount": changed,
                "llmConfig": LLM_CONFIG.public() if "LLM_CONFIG" in globals() else {},
                "llmJobs": LLM.public_jobs() if "LLM" in globals() else [],
            }

    def public_characters(self) -> list[dict]:
        result = []
        for key, raw in sorted(self.characters.items(), key=lambda item: item[0].lower()):
            if not isinstance(raw, dict):
                continue
            path = clean_token(raw.get("aposePath"))
            result.append({
                "key": key,
                "tag": clean_token(raw.get("tag")),
                "aposePath": path,
                "aposeName": Path(path).name if path else key,
                "description": clean_token(raw.get("description")),
                "src": f"/api/characters/media/{key}",
            })
        return result

    def save_translations(self, translations: dict) -> dict:
        cleaned = {
            clean_token(key): clean_token(value)
            for key, value in translations.items()
            if clean_token(key)
        }
        with self.lock:
            self.translations = cleaned
            write_json(CONFIG_FILES["translations"], self.translations)
        return self.public_state()

    def update_translation(self, tag: str, translation: str) -> dict:
        with self.lock:
            tag = clean_token(tag)
            if tag:
                if clean_token(translation):
                    self.translations[tag] = clean_token(translation)
                else:
                    self.translations.pop(tag, None)
            write_json(CONFIG_FILES["translations"], self.translations)
        return self.public_state()

    def save_rules(self, rules: list) -> dict:
        with self.lock:
            self.rules = rules if isinstance(rules, list) else []
            write_json(CONFIG_FILES["rules"], self.rules)
        return self.public_state()

    def save_favorites(self, favorites: list) -> dict:
        cleaned = []
        for index, group in enumerate(favorites if isinstance(favorites, list) else []):
            if not isinstance(group, dict):
                continue
            tags = parse_tags(group.get("tags", []))
            name = clean_token(group.get("name")) or f"收藏栏 {index + 1:02d}"
            cleaned.append({"id": clean_token(group.get("id")) or uuid.uuid4().hex[:8], "name": name, "tags": tags})
        if not cleaned:
            cleaned = [{"id": "core", "name": "核心标签", "tags": []}]
        with self.lock:
            self.favorites = cleaned
            write_json(CONFIG_FILES["favorites"], self.favorites)
        return self.public_state()

    def save_character(self, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("角色描述内容无效")
        old_key = clean_token(payload.get("oldKey")).casefold()
        tag = clean_token(payload.get("tag"))
        apose_raw = clean_token(payload.get("aposePath"))
        description = clean_token(payload.get("description"))
        if not tag:
            raise ValueError("角色 tag 不能为空")
        if not apose_raw:
            raise ValueError("请选择角色 A-pose 图片")
        apose = Path(os.path.expandvars(os.path.expanduser(apose_raw))).resolve()
        if not apose.is_file() or apose.suffix.lower() not in IMAGE_EXTENSIONS:
            raise ValueError("A-pose 图片不存在或格式不受支持")
        key = apose.name.casefold()
        with self.lock:
            if key in self.characters and key != old_key:
                raise ValueError(f"A-pose 文件名 {apose.name} 已存在，无法创建重复分支")
            for existing_key, entry in self.characters.items():
                if existing_key != old_key and clean_token(entry.get("tag")).casefold() == tag.casefold():
                    raise ValueError(f"角色 tag {tag} 已由另一条 A-pose 数据管理")
            if old_key and old_key != key:
                self.characters.pop(old_key, None)
            self.characters[key] = {
                "tag": tag,
                "aposePath": str(apose),
                "description": description,
            }
            write_json(CONFIG_FILES["characters"], self.characters)
        return self.public_state()

    def delete_character(self, key: str) -> dict:
        key = clean_token(key).casefold()
        with self.lock:
            if key:
                self.characters.pop(key, None)
                write_json(CONFIG_FILES["characters"], self.characters)
        return self.public_state()

    def update_character_description(self, key: str, description: str) -> None:
        key = clean_token(key).casefold()
        with self.lock:
            entry = self.characters.get(key)
            if not entry:
                raise ValueError("角色描述条目不存在")
            entry["description"] = clean_token(description)
            write_json(CONFIG_FILES["characters"], self.characters)

    def apply_llm_result(self, image_id: str, tags: list[str], description: str, status: str) -> None:
        tags = parse_tags(tags)
        if not tags:
            raise ValueError("LLM 结果没有有效标签")
        description = clean_token(description)
        with self.lock:
            image = self.images.get(image_id)
            if not image:
                raise ValueError("图片已从当前会话移除")
            if image["tags"] != tags or image.get("description", "") != description:
                image["tags"] = tags
                image["description"] = description
                image["modified"] = True
            image["llmStatus"] = status

    def _save_image_record(self, image: dict) -> None:
        text_path = image["path"].with_suffix(".txt")
        temp_path = text_path.with_suffix(text_path.suffix + ".tagcleaner.tmp")
        try:
            temp_path.write_text(join_prompt_text(image["tags"], image.get("description", "")), encoding="utf-8")
            temp_path.replace(text_path)
        except OSError:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        image["originalTags"] = list(image["tags"])
        image["originalDescription"] = image.get("description", "")
        image["modified"] = False
        image["hasTxt"] = True

    def save_image(self, image_id: str) -> bool:
        with self.lock:
            image = self.images.get(image_id)
            if not image:
                raise ValueError("图片已从当前会话移除")
            if not image["modified"]:
                return False
            self._save_image_record(image)
            return True

    def add_tags(self, image_ids: list[str], tags: list[str], action: str = "add") -> dict:
        tags = parse_tags(tags)
        selected = image_ids or list(self.images.keys())[:1]
        changed = 0
        with self.lock:
            for image_id in selected:
                image = self.images.get(image_id)
                if not image or not tags:
                    continue
                original = list(image["tags"])
                if action == "remove":
                    image["tags"] = [tag for tag in image["tags"] if tag not in set(tags)]
                else:
                    image["tags"] = image["tags"] + [tag for tag in tags if tag not in image["tags"]]
                if image["tags"] != original:
                    image["modified"] = True
                    changed += 1
        return {**self.public_state(), "changedImages": changed}

    def apply_rules(self, rules: list, image_ids: list[str]) -> dict:
        targets = image_ids or list(self.images.keys())
        changed = 0
        matched = 0
        with self.lock:
            for image_id in targets:
                image = self.images.get(image_id)
                if not image:
                    continue
                original = list(image["tags"])
                current = list(original)
                image_matched = False
                for raw_rule in rules if isinstance(rules, list) else []:
                    if not isinstance(raw_rule, dict):
                        continue
                    delete_tokens = parse_tags(raw_rule.get("delete", ""))
                    write_tokens = parse_tags(raw_rule.get("write", ""))
                    condition_tokens = parse_tags(raw_rule.get("condition", ""))
                    condition_ok = mode_matches(original, condition_tokens, raw_rule.get("conditionMode", "any"))
                    if not condition_ok:
                        continue
                    delete_success = True
                    if delete_tokens:
                        delete_success = mode_matches(original, delete_tokens, raw_rule.get("deleteMode", "any"))
                        if delete_success:
                            current = [tag for tag in current if tag not in set(delete_tokens)]
                            image_matched = True
                    if write_tokens and (not delete_tokens or delete_success):
                        if write_matches(original, write_tokens, raw_rule.get("writeMode", "any")):
                            missing = [tag for tag in write_tokens if tag not in current]
                            current.extend(missing)
                            if missing:
                                image_matched = True
                if image_matched:
                    matched += 1
                if current != original:
                    image["tags"] = current
                    image["modified"] = True
                    changed += 1
        return {**self.public_state(), "matchedImages": matched, "changedImages": changed}

    def save_changed(self) -> dict:
        saved = 0
        failed: list[str] = []
        with self.lock:
            for image in self.images.values():
                if not image["modified"]:
                    continue
                try:
                    self._save_image_record(image)
                    saved += 1
                except OSError as exc:
                    failed.append(f"{image['name']}: {exc}")
        return {**self.public_state(), "saved": saved, "failed": failed}

    def revert_changes(self) -> dict:
        reverted = 0
        with self.lock:
            for image in self.images.values():
                if not image.get("modified"):
                    continue
                image["tags"] = list(image.get("originalTags", image["tags"]))
                image["description"] = image.get("originalDescription", "")
                image["modified"] = False
                reverted += 1
        return {**self.public_state(), "revertedImages": reverted}

    def clear_llm_cache(self) -> dict:
        """Remove current-library cache entries without touching images or prompt files."""
        with self.lock:
            if not self.root or not self.images:
                raise ValueError("请先加载图片目录")

            grouped_keys: dict[Path, set[str]] = {}
            for image in self.images.values():
                cache_path = image["path"].parent / ".tagcleaner-cache.json"
                grouped_keys.setdefault(cache_path, set()).add(image["relativePath"])

            cleared_entries = 0
            removed_files = 0
            updated_files = 0
            for cache_path, keys in grouped_keys.items():
                if not cache_path.exists():
                    continue
                raw = read_json(cache_path, {})
                cache = raw if isinstance(raw, dict) else {}
                raw_entries = cache.get("entries", {})
                entries = dict(raw_entries) if isinstance(raw_entries, dict) else {}
                removed_here = 0
                for key in keys:
                    if key in entries:
                        entries.pop(key, None)
                        removed_here += 1
                cleared_entries += removed_here

                if entries:
                    if removed_here:
                        cache["entries"] = entries
                        write_json(cache_path, cache)
                        updated_files += 1
                else:
                    cache_path.unlink(missing_ok=True)
                    removed_files += 1

            cleared_markers = 0
            for image in self.images.values():
                if image.get("llmStatus"):
                    cleared_markers += 1
                image["llmStatus"] = ""

            return {
                **self.public_state(),
                "clearedEntries": cleared_entries,
                "clearedMarkers": cleared_markers,
                "removedCacheFiles": removed_files,
                "updatedCacheFiles": updated_files,
            }

STATE = LibraryState()
LLM_CONFIG = ConfigStore()
LLM = LLMProcessor(STATE, LLM_CONFIG)


class Handler(BaseHTTPRequestHandler):
    server_version = f"TagCleaner/{APP_VERSION}"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def _headers(self, content_type: str = "application/json; charset=utf-8", length: int | None = None) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        if length is not None:
            self.send_header("Content-Length", str(length))

    def _json(self, payload, status: int = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._headers(length=len(data))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, message: str, status: int = HTTPStatus.BAD_REQUEST) -> None:
        self._json({"error": message}, status)

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            return json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("请求内容不是有效 JSON")

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._headers(length=0)
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/api/health":
            self._json({"ok": True, "service": "TagCleaner", "version": APP_VERSION})
            return
        if path == "/api/state":
            self._json(STATE.public_state())
            return
        if path == "/api/llm/config":
            self._json(LLM_CONFIG.public())
            return
        if path == "/api/llm/models":
            query = parse_qs(urlparse(self.path).query)
            provider = (query.get("provider") or ["volcano"])[0]
            self._json(LLM.list_models(provider))
            return
        if path.startswith("/api/llm/jobs/"):
            job_id = path.rsplit("/", 1)[-1]
            job = LLM.get_job(job_id)
            if not job:
                self._error("LLM 清洗任务不存在", HTTPStatus.NOT_FOUND)
                return
            self._json(job)
            return
        if path == "/api/rules/export":
            payload = json.dumps(STATE.rules, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self._headers("application/json; charset=utf-8", len(payload))
            self.send_header("Content-Disposition", 'attachment; filename="replacement-rules.json"')
            self.end_headers()
            self.wfile.write(payload)
            return
        if path == "/api/translations/export":
            payload = json.dumps(STATE.translations, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self._headers("application/json; charset=utf-8", len(payload))
            self.send_header("Content-Disposition", 'attachment; filename="tag-translations.json"')
            self.end_headers()
            self.wfile.write(payload)
            return
        if path.startswith("/api/media/"):
            image_id = path.rsplit("/", 1)[-1]
            image = STATE.images.get(image_id)
            if not image or not image["path"].is_file():
                self._error("媒体文件不存在", HTTPStatus.NOT_FOUND)
                return
            file_path = image["path"]
            data = file_path.read_bytes()
            content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            self._headers(content_type, len(data))
            self.end_headers()
            self.wfile.write(data)
            return
        if path.startswith("/api/characters/media/"):
            key = path.rsplit("/", 1)[-1].casefold()
            entry = STATE.characters.get(key)
            file_path = Path(clean_token(entry.get("aposePath"))) if isinstance(entry, dict) else None
            if not file_path or not file_path.is_file():
                self._error("角色 A-pose 图片不存在", HTTPStatus.NOT_FOUND)
                return
            data = file_path.read_bytes()
            content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            self._headers(content_type, len(data))
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/" or path == "/index.html":
            self._serve_static("index.html")
            return
        if path.startswith("/static/"):
            self._serve_static(path.removeprefix("/static/"))
            return
        self._error("未找到资源", HTTPStatus.NOT_FOUND)

    def _serve_static(self, relative: str) -> None:
        target = (STATIC_ROOT / relative).resolve()
        if not is_within(target, STATIC_ROOT) or not target.is_file():
            self._error("静态资源不存在", HTTPStatus.NOT_FOUND)
            return
        data = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self._headers(content_type, len(data))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        path = unquote(urlparse(self.path).path)
        try:
            body = self._body()
        except ValueError as exc:
            self._error(str(exc))
            return
        try:
            if path == "/api/library/choose-folder":
                self._json(choose_folder_dialog())
            elif path == "/api/library/choose-image":
                self._json(choose_image_dialog())
            elif path == "/api/library/load":
                self._json(STATE.load_folder(body.get("path", "")))
            elif path == "/api/translations":
                if "translations" in body:
                    self._json(STATE.save_translations(body["translations"]))
                else:
                    self._json(STATE.update_translation(body.get("tag", ""), body.get("translation", "")))
            elif path == "/api/rules":
                self._json(STATE.save_rules(body.get("rules", [])))
            elif path == "/api/favorites":
                self._json(STATE.save_favorites(body.get("favorites", [])))
            elif path == "/api/apply-rules":
                self._json(STATE.apply_rules(body.get("rules", STATE.rules), body.get("imageIds", [])))
            elif path == "/api/tags":
                self._json(STATE.add_tags(body.get("imageIds", []), body.get("tags", []), body.get("action", "add")))
            elif path == "/api/library/save":
                self._json(STATE.save_changed())
            elif path == "/api/library/revert":
                self._json(STATE.revert_changes())
            elif path == "/api/characters":
                self._json(STATE.save_character(body))
            elif path == "/api/characters/delete":
                self._json(STATE.delete_character(body.get("key", "")))
            elif path == "/api/characters/generate":
                description = LLM.generate_character(body.get("key", ""))
                self._json({**STATE.public_state(), "generatedDescription": description})
            elif path == "/api/llm/config":
                self._json(LLM_CONFIG.update(body))
            elif path == "/api/llm/test":
                self._json(LLM.test(body.get("provider", ""), body.get("config")))
            elif path == "/api/llm/models":
                self._json(LLM.list_models(body.get("provider", ""), body.get("config")))
            elif path == "/api/llm/cache/clear":
                self._json(LLM.clear_cache())
            elif path == "/api/llm/clean":
                self._json(LLM.start(body.get("imageIds", []), body.get("options", {})), HTTPStatus.ACCEPTED)
            else:
                self._error("未找到 API", HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self._error(str(exc))
        except OSError as exc:
            self._error(f"文件操作失败: {exc}", HTTPStatus.INTERNAL_SERVER_ERROR)
        except LLMError as exc:
            self._error(str(exc), HTTPStatus.BAD_GATEWAY)
        except Exception as exc:  # Keep the local tool responsive while exposing a useful error.
            self._error(f"服务内部错误: {exc}", HTTPStatus.INTERNAL_SERVER_ERROR)


def main() -> None:
    parser = argparse.ArgumentParser(description="Local TagCleaner workspace")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    ensure_dirs()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"TagCleaner running at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping TagCleaner")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
