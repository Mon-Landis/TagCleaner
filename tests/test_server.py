from __future__ import annotations

import json
import os
import base64
import io
import shutil
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import llm_service
import server
from llm_service import (
    ConfigStore,
    DEFAULT_CONFIG,
    SKILLS_ROOT,
    LLMError,
    LLMProcessor,
    SkillRegistry,
    _dpapi_transform,
    _native_gemini_url,
    _merge_tag_candidates,
    _parse_character_audit_output,
    _parse_clean_output,
    _parse_vision_output,
    _reconcile_character_audits,
    _model_list_urls,
    _normalise_model_catalog,
    _canonical_model_id,
    _detect_dataset_trigger,
    _ensure_protected_tags,
    _ensure_watermark_evidence,
    _image_data_url,
    _volcano_url,
)


def _save_test_png(path: Path, color=(80, 120, 160), size=(32, 32)) -> None:
    try:
        from PIL import Image
    except ImportError as exc:
        raise unittest.SkipTest("Pillow is unavailable") from exc
    Image.new("RGB", size, color).save(path, format="PNG")


class PromptTextTests(unittest.TestCase):
    def test_model_alias_is_canonicalised(self) -> None:
        self.assertEqual(_canonical_model_id("deepseekv4flashga"), "deepseek-v4-flash")
        self.assertEqual(_canonical_model_id("deepseek-v4-flash"), "deepseek-v4-flash")

    def test_retry_marks_exhausted_network_error_with_stage_label(self) -> None:
        config = _LocalConfig("http://127.0.0.1:1")
        config.config["retryCount"] = 2
        config.config["retryDelaySeconds"] = 0
        calls = []
        events = []

        def fail() -> str:
            calls.append(True)
            raise LLMError("The read operation timed out", retryable=True)

        with self.assertRaises(LLMError) as caught:
            llm_service._retry(fail, config, "标签识别", events.append)
        self.assertEqual(len(calls), 3)
        self.assertEqual(
            [event["phase"] for event in events],
            ["request", "retry_wait", "request", "retry_wait", "request"],
        )
        self.assertEqual(events[1]["attempt"], 2)
        self.assertEqual(events[1]["maxAttempts"], 3)
        self.assertEqual(events[1]["reason"], "timeout")
        self.assertIn("标签识别", str(caught.exception))
        self.assertIn("已重试 2 次", str(caught.exception))
        self.assertFalse(caught.exception.retryable)

    def test_elapsed_seconds_uses_finished_time_when_present(self) -> None:
        self.assertEqual(
            llm_service._elapsed_seconds("2026-08-05T00:00:00+00:00", "2026-08-05T01:02:03+00:00"),
            3723,
        )

    def test_parse_and_join_mixed_prompt(self) -> None:
        tags, description = server.parse_prompt_text("character_name, blue_hair\n\nA quiet close-up with cool light.")
        self.assertEqual(tags, ["character_name", "blue_hair"])
        self.assertEqual(description, "A quiet close-up with cool light.")
        self.assertEqual(
            server.join_prompt_text(tags, description),
            "character_name, blue_hair\n\nA quiet close-up with cool light.",
        )

    def test_legacy_multiline_tags_remain_tags(self) -> None:
        tags, description = server.parse_prompt_text("tag_a, tag_b\ntag_c")
        self.assertEqual(tags, ["tag_a", "tag_b", "tag_c"])
        self.assertEqual(description, "")


    def test_clean_result_json_and_text_fallback(self) -> None:
        tags, description = _parse_clean_output('{"tags":["1girl","red_hair"],"description":"Warm rim light."}')
        self.assertEqual(tags, ["1girl", "red_hair"])
        self.assertEqual(description, "Warm rim light.")

    def test_vision_result_requires_tags_and_description_json(self) -> None:
        tags, description = _parse_vision_output(
            '```json\n{"tags":["1girl","blue_hair"],"description":"A close portrait in cool side light."}\n```'
        )
        self.assertEqual(tags, ["1girl", "blue_hair"])
        self.assertEqual(description, "A close portrait in cool side light.")
        with self.assertRaises(LLMError):
            _parse_vision_output("A paragraph without the required tag array.")
        with self.assertRaises(LLMError):
            _parse_vision_output('{"tags":["1girl"],"description":""}')

    def test_merge_tag_candidates_keeps_wd14_spelling_and_order(self) -> None:
        merged = _merge_tag_candidates(
            ["1girl", "blue_hair", "looking_at_viewer"],
            ["blue hair", "black-jacket", "1girl", "watermark"],
        )
        self.assertEqual(
            merged,
            ["1girl", "blue_hair", "looking_at_viewer", "black-jacket", "watermark"],
        )

    def test_watermark_evidence_survives_cleanup_with_its_position(self) -> None:
        tags, description = _ensure_watermark_evidence(
            ["1girl", "blue_hair"],
            "A waist-up portrait uses cool side light.",
            ["1girl", "watermark"],
            "A waist-up character is centered. A small watermark is visible at the bottom right.",
            True,
        )
        self.assertEqual(tags, ["1girl", "blue_hair", "watermark"])
        self.assertIn("watermark is visible at the bottom right", description)

        tags_only, disabled_description = _ensure_watermark_evidence(
            ["1girl"], "", ["watermark"], "A watermark is visible at top left.", False
        )
        self.assertEqual(tags_only, ["1girl", "watermark"])
        self.assertEqual(disabled_description, "")

    def test_shared_leading_dataset_trigger_is_protected_but_style_term_is_not(self) -> None:
        images = [
            {"tags": ["ktks", "1girl", "red_hair"]},
            {"tags": ["ktks", "1boy", "blue_hair"]},
        ]
        self.assertEqual(_detect_dataset_trigger(images), ["ktks"])
        self.assertEqual(
            _detect_dataset_trigger([{"tags": ["watercolor", "1girl"]}, {"tags": ["watercolor", "1boy"]}]),
            [],
        )
        self.assertEqual(_detect_dataset_trigger([images[0], {"tags": ["other", "1boy"]}]), [])
        mostly_shared = [
            *[{"tags": ["ktks", "1girl"]} for _ in range(9)],
            {"tags": ["other", "1boy"]},
        ]
        self.assertEqual(_detect_dataset_trigger(mostly_shared), ["ktks"])
        self.assertEqual(_ensure_protected_tags(["1girl", "red hair"], ["ktks"]), ["ktks", "1girl", "red hair"])

    def test_single_image_retry_inherits_trigger_from_loaded_dataset(self) -> None:
        images = {
            **{f"shared-{index}": {"tags": ["ktks", "1girl"]} for index in range(9)},
            "retry": {"tags": ["other", "ktks", "1boy"]},
        }
        state = type("State", (), {"images": images, "characters": {}})()
        processor = LLMProcessor(state, _LocalConfig("http://127.0.0.1:1"))
        with patch("llm_service.threading.Thread"):
            public_job = processor.start(["retry"], {"styleMode": True})
        self.assertEqual(processor.jobs[public_job["id"]]["options"]["protectedDatasetTags"], ["ktks"])

    def test_protocol_switch_normalises_default_urls(self) -> None:
        self.assertEqual(
            _volcano_url("https://ark.cn-beijing.volces.com/api/v3/chat/completions", "responses"),
            "https://ark.cn-beijing.volces.com/api/v3/responses",
        )
        self.assertEqual(
            _volcano_url("https://ark.cn-beijing.volces.com/api/plan/v3", "chat"),
            "https://ark.cn-beijing.volces.com/api/plan/v3/chat/completions",
        )
        self.assertEqual(
            _volcano_url("https://ark.cn-beijing.volces.com/api/plan/v3/", "responses"),
            "https://ark.cn-beijing.volces.com/api/plan/v3/responses",
        )
        self.assertEqual(
            _native_gemini_url(
                "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                "gemini-3.5-flash",
            ),
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent",
        )
        tags, description = _parse_clean_output("1girl, red_hair\n\nWarm rim light.")
        self.assertEqual(tags, ["1girl", "red_hair"])
        self.assertEqual(description, "Warm rim light.")
        tags, description = _parse_clean_output(
            '{"tags":["solo"],"description":""}\nThis trailing text should be ignored.'
        )
        self.assertEqual(tags, ["solo"])
        self.assertEqual(description, "")


class ModelCatalogTests(unittest.TestCase):
    def test_model_catalog_normalises_vision_metadata(self) -> None:
        models = _normalise_model_catalog("volcano", {
            "data": [
                {"id": "vision-model", "modalities": ["text", "image"]},
                {"id": "text-model", "modalities": ["text"]},
                {"id": "unknown-model"},
            ]
        })
        self.assertEqual([item["id"] for item in models], ["vision-model", "text-model", "unknown-model"])
        self.assertTrue(models[0]["vision"])
        self.assertFalse(models[1]["vision"])
        self.assertIsNone(models[2]["vision"])

    def test_model_list_urls_include_api_plan_fallback(self) -> None:
        urls = _model_list_urls("volcano", "https://ark.cn-beijing.volces.com/api/plan/v3")
        self.assertIn("https://ark.cn-beijing.volces.com/api/plan/v3/models", urls)
        self.assertIn("https://ark.cn-beijing.volces.com/api/v3/models", urls)


class LibraryStateTests(unittest.TestCase):
    def test_load_modify_save_and_cache_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image = root / "frame.png"
            image.write_bytes(b"not-a-real-png")
            text = root / "frame.txt"
            text.write_text("old_tag\n\nOriginal description.", encoding="utf-8")
            fingerprint = server.image_fingerprint(image)
            (root / ".tagcleaner-cache.json").write_text(
                json.dumps({
                    "entries": {
                        "frame.png": {
                            "status": "completed",
                            "fingerprint": fingerprint,
                        }
                    }
                }),
                encoding="utf-8",
            )

            state = server.LibraryState()
            payload = state.load_folder(str(root))
            self.assertEqual(len(payload["images"]), 1)
            public_image = payload["images"][0]
            self.assertEqual(public_image["description"], "Original description.")
            self.assertEqual(public_image["llmStatus"], "cached")

            state.apply_llm_result(public_image["id"], ["new_tag", "blue_eyes"], "New description.", "completed")
            saved = state.save_changed()
            self.assertEqual(saved["saved"], 1)
            self.assertEqual(text.read_text(encoding="utf-8"), "new_tag, blue_eyes\n\nNew description.")

    def test_save_image_writes_only_the_completed_llm_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "a.png").write_bytes(b"a")
            (root / "b.png").write_bytes(b"b")
            (root / "a.txt").write_text("old_a", encoding="utf-8")
            (root / "b.txt").write_text("old_b", encoding="utf-8")
            state = server.LibraryState()
            images = state.load_folder(str(root))["images"]
            a = next(image for image in images if image["name"] == "a.png")
            b = next(image for image in images if image["name"] == "b.png")
            state.apply_llm_result(a["id"], ["new_a"], "Saved A.", "completed")
            state.apply_llm_result(b["id"], ["new_b"], "Unsaved B.", "completed")

            self.assertTrue(state.save_image(a["id"]))
            self.assertEqual((root / "a.txt").read_text(encoding="utf-8"), "new_a\n\nSaved A.")
            self.assertEqual((root / "b.txt").read_text(encoding="utf-8"), "old_b")
            current = {image["name"]: image for image in state.public_images()}
            self.assertFalse(current["a.png"]["modified"])
            self.assertTrue(current["b.png"]["modified"])
            self.assertFalse((root / "a.txt.tagcleaner.tmp").exists())

    def test_revert_restores_description(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "frame.jpg").write_bytes(b"jpeg")
            (root / "frame.txt").write_text("tag_a\n\nBefore.", encoding="utf-8")
            state = server.LibraryState()
            image = state.load_folder(str(root))["images"][0]
            state.apply_llm_result(image["id"], ["tag_b"], "After.", "completed")
            reverted = state.revert_changes()
            current = reverted["images"][0]
            self.assertEqual(current["tags"], ["tag_a"])
            self.assertEqual(current["description"], "Before.")

    def test_clear_llm_cache_preserves_dataset_and_unloaded_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_path = root / "frame.png"
            text_path = root / "frame.txt"
            image_bytes = b"unchanged-image"
            text = "tag_a\n\nUnchanged description."
            image_path.write_bytes(image_bytes)
            text_path.write_text(text, encoding="utf-8")
            cache_path = root / ".tagcleaner-cache.json"
            cache_path.write_text(json.dumps({
                "entries": {
                    "frame.png": {"status": "completed", "fingerprint": server.image_fingerprint(image_path)},
                    "unloaded.png": {"status": "completed", "fingerprint": "other"},
                }
            }), encoding="utf-8")

            state = server.LibraryState()
            loaded = state.load_folder(str(root))["images"][0]
            self.assertEqual(loaded["llmStatus"], "cached")
            payload = state.clear_llm_cache()

            self.assertEqual(payload["clearedEntries"], 1)
            self.assertEqual(payload["clearedMarkers"], 1)
            self.assertEqual(payload["updatedCacheFiles"], 1)
            self.assertEqual(payload["removedCacheFiles"], 0)
            self.assertEqual(payload["images"][0]["llmStatus"], "")
            self.assertEqual(image_path.read_bytes(), image_bytes)
            self.assertEqual(text_path.read_text(encoding="utf-8"), text)
            remaining = json.loads(cache_path.read_text(encoding="utf-8"))["entries"]
            self.assertEqual(set(remaining), {"unloaded.png"})

    def test_clear_llm_cache_removes_empty_cache_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_path = root / "frame.png"
            image_path.write_bytes(b"image")
            cache_path = root / ".tagcleaner-cache.json"
            cache_path.write_text(json.dumps({
                "entries": {
                    "frame.png": {"status": "completed", "fingerprint": server.image_fingerprint(image_path)},
                }
            }), encoding="utf-8")

            state = server.LibraryState()
            state.load_folder(str(root))
            payload = state.clear_llm_cache()

            self.assertEqual(payload["clearedEntries"], 1)
            self.assertEqual(payload["removedCacheFiles"], 1)
            self.assertFalse(cache_path.exists())

    def test_character_dictionary_uses_apose_filename_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            apose = root / "Hero_APose.PNG"
            apose.write_bytes(b"image")
            original_path = server.CONFIG_FILES["characters"]
            server.CONFIG_FILES["characters"] = root / "characters.json"
            try:
                state = server.LibraryState()
                state.characters = {}
                payload = state.save_character({
                    "tag": "hero_(series)",
                    "aposePath": str(apose),
                    "description": "blue hair, green eyes",
                })
                self.assertEqual(payload["characters"][0]["key"], "hero_apose.png")
                stored = json.loads((root / "characters.json").read_text(encoding="utf-8"))
                self.assertIn("hero_apose.png", stored)
            finally:
                server.CONFIG_FILES["characters"] = original_path


class CharacterContextTests(unittest.TestCase):
    def test_alternate_costume_skips_reference_without_api_call(self) -> None:
        fake_state = type("FakeState", (), {"characters": {"hero.png": {"tag": "hero_(series)"}}})()
        processor = LLMProcessor(fake_state, server.LLM_CONFIG)
        context = processor._character_context(
            ["hero_(series)", "official_alternate_costume"],
            {"styleMode": False},
        )
        self.assertEqual(context["descriptions"], "")
        self.assertEqual(context["auditedTags"], ["hero_(series)", "official_alternate_costume"])
        self.assertTrue(context["warnings"])

    def test_style_mode_skips_character_reference_without_api_call(self) -> None:
        fake_state = type("FakeState", (), {
            "characters": {
                "hero.png": {
                    "tag": "hero_(series)",
                    "aposePath": "missing.png",
                    "description": "blue hair, green eyes",
                }
            }
        })()
        processor = LLMProcessor(fake_state, server.LLM_CONFIG)
        context = processor._character_context(["hero_(series)", "1girl"], {"styleMode": True})
        self.assertEqual(context["descriptions"], "")
        self.assertEqual(context["auditedTags"], ["hero_(series)", "1girl"])
        self.assertEqual(context["auditReport"], {"characters": []})
        self.assertEqual(context["warnings"], [])


class ConfigPreviewTests(unittest.TestCase):
    def test_connection_test_draft_does_not_persist_settings_or_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "user-config"
            original_paths = (
                llm_service.USER_CONFIG_ROOT,
                llm_service.SETTINGS_PATH,
                llm_service.SECRETS_PATH,
            )
            llm_service.USER_CONFIG_ROOT = root
            llm_service.SETTINGS_PATH = root / "settings.json"
            llm_service.SECRETS_PATH = root / "secrets.json"
            try:
                store = ConfigStore()
                store.update({
                    "volcanoUrl": "https://saved.invalid/v3",
                    "volcanoKey": "saved-volcano-key",
                })
                config_before = dict(store.config)
                secrets_before = dict(store.secrets)
                settings_before = llm_service.SETTINGS_PATH.read_bytes()
                secret_file_before = llm_service.SECRETS_PATH.read_bytes()

                preview = store.preview({
                    "volcanoUrl": "http://127.0.0.1:39001/mock",
                    "volcanoModel": "draft-model",
                    "volcanoKey": "draft-volcano-key",
                })
                self.assertEqual(preview.config["volcanoModel"], "draft-model")
                self.assertEqual(preview.get_key("volcano"), "draft-volcano-key")
                self.assertEqual(store.config, config_before)
                self.assertEqual(store.secrets, secrets_before)
                self.assertEqual(llm_service.SETTINGS_PATH.read_bytes(), settings_before)
                self.assertEqual(llm_service.SECRETS_PATH.read_bytes(), secret_file_before)

                cleared = store.preview({"clearVolcanoKey": True})
                self.assertEqual(cleared.get_key("volcano"), "")
                self.assertEqual(store.secrets, secrets_before)
                self.assertEqual(llm_service.SECRETS_PATH.read_bytes(), secret_file_before)
            finally:
                (
                    llm_service.USER_CONFIG_ROOT,
                    llm_service.SETTINGS_PATH,
                    llm_service.SECRETS_PATH,
                ) = original_paths


def valid_audit_payload() -> dict:
    return {
        "decisions": [
            {"tag": "hero_(series)", "category": "identity", "decision": "keep", "replacement_tag": "", "include_in_prompt": True, "prompt_order": 0, "reason": "locked trigger"},
            {"tag": "1girl", "category": "identity", "decision": "keep", "replacement_tag": "", "include_in_prompt": True, "prompt_order": 1, "reason": "subject identity"},
            {"tag": "blue_hair", "category": "hair", "decision": "keep", "replacement_tag": "", "include_in_prompt": True, "prompt_order": 2, "reason": "blue hair is visible"},
            {"tag": "jacket", "category": "clothing", "decision": "replace", "replacement_tag": "black_jacket", "include_in_prompt": True, "prompt_order": 3, "reason": "the jacket is black"},
            {"tag": "looking_at_viewer", "category": "action", "decision": "keep", "replacement_tag": "", "include_in_prompt": False, "prompt_order": None, "reason": "protected action tag"},
        ],
        "character_prompt": ["hero_(series)", "1girl", "blue_hair", "black_jacket"],
    }


class SkillIntegrationTests(unittest.TestCase):
    def test_registry_loads_runtime_prompts_and_hash_tracks_content(self) -> None:
        registry = SkillRegistry()
        records = registry.records()
        self.assertEqual(set(records), {"prompt-pyramid", "character-tag-auditor"})
        self.assertTrue(all(record["valid"] for record in records.values()))
        self.assertIn("Prompt Pyramid Runtime", registry.prompt("prompt-pyramid"))
        with tempfile.TemporaryDirectory() as temp_dir:
            copied = Path(temp_dir) / "skills"
            shutil.copytree(SKILLS_ROOT, copied)
            local = SkillRegistry(copied)
            before = local.hashes(["prompt-pyramid"])["prompt-pyramid"]
            runtime = copied / "prompt-pyramid" / "runtime-prompt.md"
            runtime.write_text(runtime.read_text(encoding="utf-8") + "\nTest-only mutation.\n", encoding="utf-8")
            after = local.hashes(["prompt-pyramid"])["prompt-pyramid"]
            self.assertNotEqual(before, after)

    def test_audit_validation_and_reconciliation(self) -> None:
        tags = ["hero_(series)", "1girl", "blue_hair", "jacket", "looking_at_viewer"]
        audit = _parse_character_audit_output(json.dumps(valid_audit_payload()), tags, "hero_(series)")
        cleaned, warnings, report = _reconcile_character_audits(tags, [audit])
        self.assertEqual(cleaned, ["hero_(series)", "1girl", "blue_hair", "black_jacket", "looking_at_viewer"])
        self.assertEqual(warnings, [])
        self.assertEqual(report["characters"][0]["characterPrompt"][-1], "black_jacket")

        invalid = valid_audit_payload()
        invalid["decisions"][-1] = {**invalid["decisions"][-1], "decision": "delete"}
        with self.assertRaises(LLMError):
            _parse_character_audit_output(json.dumps(invalid), tags, "hero_(series)")

    def test_cache_hash_changes_with_skill_settings(self) -> None:
        config = _LocalConfig("http://127.0.0.1:1")
        processor = LLMProcessor(type("State", (), {})(), config)
        options = {"styleMode": False, "allowNaturalLanguage": False}
        sparse_hash = processor._config_hash(options)
        auto_save_hash = processor._config_hash({**options, "autoSaveResults": True})
        manual_save_hash = processor._config_hash({**options, "autoSaveResults": False})
        self.assertEqual(auto_save_hash, manual_save_hash)
        self.assertEqual(sparse_hash, auto_save_hash)
        config.config["characterAuditMode"] = "full"
        full_hash = processor._config_hash(options)
        config.config["enabledSkills"] = ["prompt-pyramid"]
        reduced_hash = processor._config_hash(options)
        self.assertNotEqual(sparse_hash, full_hash)
        self.assertNotEqual(full_hash, reduced_hash)


class _PacketHandler(BaseHTTPRequestHandler):
    captures: list[dict] = []

    def log_message(self, _format: str, *_args) -> None:
        pass

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        self.__class__.captures.append({
            "path": self.path,
            "headers": dict(self.headers.items()),
            "payload": payload,
        })
        if self.path == "/volcano/chat/completions":
            request_content = payload.get("messages", [{}])[0].get("content") if isinstance(payload.get("messages"), list) else None
            has_image = isinstance(request_content, list) and any(item.get("type") == "image_url" for item in request_content if isinstance(item, dict))
            content = (
                json.dumps({
                    "tags": ["1girl", "blue hair", "black_jacket", "watermark"],
                    "description": "A waist-up anime character stands in cool side light with a watermark at bottom right.",
                })
                if has_image
                else json.dumps({
                    "tags": ["hero (series)", "1girl", "blue hair", "black jacket", "looking at viewer"],
                    "description": "A waist-up portrait uses cool side light.",
                })
            )
        else:
            parts = payload["messages"][0]["content"]
            prompt = "".join(
                item.get("text", "")
                for item in parts
                if isinstance(item, dict) and item.get("type") == "text"
            )
            if "Character Tag Auditor Runtime" in prompt:
                content = json.dumps(valid_audit_payload())
            else:
                content = json.dumps({
                    "tags": ["hero (series)", "1girl", "blue hair", "black jacket", "looking at viewer"],
                    "description": "A waist-up portrait uses cool side light.",
                })
        raw = json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class _LocalConfig:
    def __init__(self, base_url: str) -> None:
        self.skill_registry = SkillRegistry()
        self.config = {
            **DEFAULT_CONFIG,
            "volcanoUrl": base_url + "/volcano",
            "geminiUrl": base_url + "/gemini",
            "volcanoModel": "packet-volcano",
            "geminiModel": "packet-gemini",
            "tagProvider": "volcano",
            "tagModel": "packet-volcano",
            "aposeProvider": "gemini",
            "aposeModel": "packet-gemini",
            "cleanProvider": "gemini",
            "cleanModel": "packet-gemini",
            "retryCount": 0,
            "retryDelaySeconds": 0,
            "timeoutSeconds": 5,
            "enabledSkills": ["prompt-pyramid", "character-tag-auditor"],
            "characterAuditMode": "sparse",
        }

    def get_key(self, provider: str) -> str:
        return "packet-volcano-key" if provider == "volcano" else "packet-gemini-key"

    def enabled_skills(self) -> list[str]:
        return list(self.config["enabledSkills"])

    def skill_prompt(self, skill_id: str) -> str:
        return self.skill_registry.prompt(skill_id)

    def enabled_skill_hashes(self) -> dict[str, str]:
        return self.skill_registry.hashes(self.enabled_skills())

    def validate_skills(self) -> None:
        self.config["enabledSkills"] = self.skill_registry.validate_enabled(self.config["enabledSkills"])


class _LocalState:
    def __init__(self, apose: Path) -> None:
        self.lock = threading.RLock()
        self.characters = {
            apose.name.casefold(): {
                "tag": "hero_(series)",
                "aposePath": str(apose),
                "description": "blue hair, green eyes, black jacket",
            }
        }
        self.result = None
        self.saved_image_id = None

    def update_character_description(self, key: str, description: str) -> None:
        self.characters[key]["description"] = description

    def apply_llm_result(self, image_id: str, tags: list[str], description: str, status: str) -> None:
        self.result = {"imageId": image_id, "tags": tags, "description": description, "status": status}

    def save_image(self, image_id: str) -> bool:
        self.saved_image_id = image_id
        return True


class LocalPacketCaptureTests(unittest.TestCase):
    def test_oversized_image_uses_resized_jpeg_request_copy(self) -> None:
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is unavailable")
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "large.bmp"
            Image.new("RGB", (300, 300), (80, 120, 160)).save(image_path, format="BMP")
            with patch.object(llm_service, "MAX_INLINE_IMAGE_BYTES", 10_000):
                data_url = _image_data_url(image_path, 20_000)
            self.assertTrue(data_url.startswith("data:image/jpeg;base64,"))
            encoded = base64.b64decode(data_url.split(",", 1)[1])
            with Image.open(io.BytesIO(encoded)) as prepared:
                self.assertLessEqual(prepared.width * prepared.height, 20_000)

    def test_image_timeout_retry_uses_a_smaller_but_readable_request_copy(self) -> None:
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is unavailable")
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "timeout.png"
            Image.new("RGB", (1800, 1800), (80, 120, 160)).save(image_path, format="PNG")
            config = _LocalConfig("http://127.0.0.1:1")
            config.config.update({"retryCount": 1, "retryDelaySeconds": 0, "imageMaxPixels": 2_000_000})
            events = []
            payloads = []
            processor = LLMProcessor(
                type("State", (), {})(),
                config,
                progress_callback=lambda image, stage, activity: events.append((stage, activity)),
            )
            processor._progress_context.image = {"name": image_path.name}

            def request(_url, payload, _headers, _timeout):
                payloads.append(payload)
                if len(payloads) == 1:
                    raise LLMError("The read operation timed out", retryable=True)
                return {"choices": [{"message": {"content": "ok"}}]}

            with patch.object(llm_service, "_request_json", side_effect=request):
                result = processor._provider_request(
                    "volcano", "packet-volcano", "Inspect.", image_path, "标签识别", "vision"
                )
            self.assertEqual(result, "ok")
            self.assertEqual(len(payloads), 2)

            pixel_counts = []
            for payload in payloads:
                image_part = next(
                    item for item in payload["messages"][0]["content"]
                    if item.get("type") == "image_url"
                )
                encoded = base64.b64decode(image_part["image_url"]["url"].split(",", 1)[1])
                with Image.open(io.BytesIO(encoded)) as prepared:
                    pixel_counts.append(prepared.width * prepared.height)
            self.assertLessEqual(pixel_counts[0], 2_000_000)
            self.assertLessEqual(pixel_counts[1], 1_600_000)
            self.assertGreaterEqual(pixel_counts[1], 1_500_000)
            self.assertLess(pixel_counts[1], pixel_counts[0])

            retry_event = next(activity for _stage, activity in events if activity.get("phase") == "retry_wait")
            self.assertEqual(retry_event["imageCompressionLevel"], 1)
            self.assertEqual(retry_event["requestImageMaxPixels"], 1_600_000)
            self.assertEqual(retry_event["requestImageQuality"], 88)

    def test_cache_writes_merge_entries_from_stale_worker_views(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state = type("State", (), {"lock": threading.RLock()})()
            processor = LLMProcessor(state, _LocalConfig("http://127.0.0.1:1"))
            image = {"path": root / "a.png"}
            processor._write_cache(image, {"entries": {"a.png": {"status": "completed"}}})
            processor._write_cache(image, {"entries": {"b.png": {"status": "completed"}}})
            cache = json.loads((root / ".tagcleaner-cache.json").read_text(encoding="utf-8"))
            self.assertEqual(set(cache["entries"]), {"a.png", "b.png"})

    def test_cache_clear_is_rejected_while_llm_job_is_active(self) -> None:
        class State:
            def __init__(self) -> None:
                self.cleared = False

            def clear_llm_cache(self) -> dict:
                self.cleared = True
                return {}

        state = State()
        processor = LLMProcessor(state, _LocalConfig("http://127.0.0.1:1"))
        processor.jobs["active"] = {"status": "running"}

        with self.assertRaisesRegex(ValueError, "任务运行中"):
            processor.clear_cache()
        self.assertFalse(state.cleared)

    def test_cached_result_repairs_dropped_watermark_without_api_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_path = root / "frame.png"
            _save_test_png(image_path)
            (root / "frame.txt").write_text(
                "ktks, 1girl\n\nA waist-up portrait uses cool side light.", encoding="utf-8"
            )
            state = server.LibraryState()
            loaded = state.load_folder(str(root))["images"][0]
            image = state.images[loaded["id"]]
            processor = LLMProcessor(state, _LocalConfig("http://127.0.0.1:1"))
            options = {
                "styleMode": True,
                "includeCharacterDescriptions": False,
                "allowNaturalLanguage": True,
                "autoSaveResults": True,
                "protectedDatasetTags": ["ktks"],
            }
            cache = {
                "entries": {
                    "frame.png": {
                        "status": "completed",
                        "fingerprint": processor._fingerprint(image),
                        "configHash": processor._config_hash(options),
                        "source": {"tags": ["ktks", "raw_tag"], "description": ""},
                        "visionTags": ["1girl", "watermark"],
                        "visionDescription": "A character is centered. A watermark is visible at bottom right.",
                        "resultTags": ["ktks", "1girl"],
                        "resultDescription": "A waist-up portrait uses cool side light.",
                    }
                }
            }
            processor._write_cache(image, cache)

            with patch.object(processor, "_vision_analyze", side_effect=AssertionError("unexpected API call")), \
                    patch.object(processor, "_gemini_clean", side_effect=AssertionError("unexpected API call")):
                outcome = processor._process_image(image, options)

            self.assertTrue(outcome["cached"])
            self.assertIn("watermark", state.images[image["id"]]["tags"])
            self.assertIn("watermark is visible at bottom right", state.images[image["id"]]["description"])
            saved = (root / "frame.txt").read_text(encoding="utf-8")
            self.assertIn("ktks, 1girl, watermark", saved)
            repaired = json.loads((root / ".tagcleaner-cache.json").read_text(encoding="utf-8"))["entries"]["frame.png"]
            self.assertIn("watermark", repaired["resultTags"])
            self.assertIn("watermark is visible at bottom right", repaired["resultDescription"])

    def test_batch_runner_processes_up_to_two_images_concurrently(self) -> None:
        state = type("State", (), {
            "images": {
                str(index): {"id": str(index), "name": f"{index}.png"}
                for index in range(5)
            }
        })()
        processor = LLMProcessor(state, _LocalConfig("http://127.0.0.1:1"))
        job = {
            "id": "parallel-test",
            "status": "queued",
            "imageIds": list(state.images),
            "options": {},
            "total": 5,
            "done": 0,
            "cached": 0,
            "failed": [],
            "warnings": [],
            "current": "",
            "startedAt": "",
            "finishedAt": "",
        }
        guard = threading.Lock()
        active = 0
        peak = 0

        def fake_process(_image, _options):
            nonlocal active, peak
            with guard:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with guard:
                active -= 1
            return {"cached": False, "warnings": []}

        with patch.object(LLMProcessor, "_process_image", side_effect=fake_process):
            processor._run(job)
        self.assertEqual(peak, 2)
        self.assertEqual(job["done"], 5)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(len(job["recentStages"]), 3)
        self.assertTrue(all(item["status"] == "completed" for item in job["recentStages"]))
        job["activeStages"] = {"frame.png": {
            "stage": "vision",
            "startedAt": "2026-08-05T00:00:00+00:00",
            "updatedAt": "2026-08-05T00:00:05+00:00",
            "phase": "retry_wait",
            "attempt": 2,
            "maxAttempts": 3,
            "retryInSeconds": 4,
            "provider": "volcano",
            "model": "doubao-seed-2.1-turbo",
            "timeoutSeconds": 120,
        }}
        self.assertEqual(processor._public_job(job)["activeStages"], [{
            "image": "frame.png",
            "stage": "vision",
            "startedAt": "2026-08-05T00:00:00+00:00",
            "updatedAt": "2026-08-05T00:00:05+00:00",
            "phase": "retry_wait",
            "attempt": 2,
            "maxAttempts": 3,
            "retryInSeconds": 4,
            "provider": "volcano",
            "model": "doubao-seed-2.1-turbo",
            "timeoutSeconds": 120,
        }])

    def test_responses_image_packet_uses_optional_pixel_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "frame.png"
            _save_test_png(image)
            config = _LocalConfig("http://127.0.0.1:1")
            config.config.update({"volcanoProtocol": "responses", "imageMaxPixels": 2000000})
            processor = LLMProcessor(type("State", (), {})(), config)
            with patch.object(llm_service, "_request_json", return_value={"output_text": "ok"}) as request:
                self.assertEqual(
                    processor._provider_request("volcano", "doubao-seed-2.1-turbo", "Look.", image, "标签识别"),
                    "ok",
                )
            payload = request.call_args.args[1]
            image_part = next(part for part in payload["input"][0]["content"] if part.get("type") == "input_image")
            self.assertEqual(image_part["image_pixel_limit"], {"max_pixels": 2000000})
            self.assertNotIn("min_pixels", image_part["image_pixel_limit"])

    def test_complete_pipeline_packets_against_local_listener(self) -> None:
        _PacketHandler.captures = []
        listener = ThreadingHTTPServer(("127.0.0.1", 0), _PacketHandler)
        thread = threading.Thread(target=listener.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                image_path = root / "frame.png"
                apose_path = root / "hero-apose.png"
                _save_test_png(image_path)
                _save_test_png(apose_path, color=(110, 90, 150))
                state = _LocalState(apose_path)
                base_url = f"http://127.0.0.1:{listener.server_address[1]}"
                config = _LocalConfig(base_url)
                config.config["visionPrompt"] = "Describe the image as one prose paragraph. Reference: {{tags}}"
                config.config["cleanPrompt"] = "Clean the WD14 tags: {{tags}}\nVisual: {{vision_description}}"
                processor = LLMProcessor(state, config)
                image = {
                    "id": "frame-id",
                    "path": image_path,
                    "name": image_path.name,
                    "relativePath": image_path.name,
                    "tags": ["hero_(series)", "1girl", "blue_hair", "jacket", "looking_at_viewer"],
                    "description": "",
                }
                outcome = processor._process_image(image, {"styleMode": False, "allowNaturalLanguage": True})
                self.assertFalse(outcome["cached"])
                self.assertEqual(state.result["status"], "completed")
                self.assertIn("watermark", state.result["tags"])
                self.assertIn("watermark at bottom right", state.result["description"])

                captures = _PacketHandler.captures
                self.assertEqual([item["path"] for item in captures], ["/gemini", "/volcano/chat/completions", "/gemini"])
                audit, volcano, final = captures
                self.assertEqual(audit["headers"].get("Authorization"), "Bearer packet-gemini-key")
                self.assertEqual(volcano["headers"].get("Authorization"), "Bearer packet-volcano-key")
                self.assertTrue(all(item["headers"].get("Content-Type") == "application/json" for item in captures))

                audit_content = audit["payload"]["messages"][0]["content"]
                audit_image = next(item for item in audit_content if item.get("type") == "image_url")
                audit_prompt = next(item["text"] for item in audit_content if item.get("type") == "text")
                self.assertTrue(audit_image["image_url"]["url"].startswith("data:image/png;base64,"))
                self.assertIn("Character Tag Auditor Runtime", audit_prompt)
                self.assertIn("Audit mode: sparse", audit_prompt)

                volcano_content = volcano["payload"]["messages"][0]["content"]
                volcano_image = next(item for item in volcano_content if item.get("type") == "image_url")
                volcano_prompt = next(item["text"] for item in volcano_content if item.get("type") == "text")
                self.assertTrue(volcano_image["image_url"]["url"].startswith("data:image/png;base64,"))
                self.assertEqual(
                    volcano_image["image_url"]["image_pixel_limit"]["max_pixels"],
                    DEFAULT_CONFIG["imageMaxPixels"],
                )
                self.assertNotEqual(volcano_image["image_url"]["url"], audit_image["image_url"]["url"])
                self.assertIn("Runtime vision response contract", volcano_prompt)
                self.assertIn('"tags":["tag_a","tag_b"]', volcano_prompt)
                self.assertIn("description must be a non-empty paragraph", volcano_prompt)

                final_content = final["payload"]["messages"][0]["content"]
                self.assertFalse(any(item.get("type") == "image_url" for item in final_content))
                final_prompt = next(item["text"] for item in final_content if item.get("type") == "text")
                self.assertIn("Prompt Pyramid Runtime", final_prompt)
                self.assertIn("black_jacket", final_prompt)
                self.assertIn("Vision-model tags (secondary evidence)", final_prompt)
                self.assertIn("Merged tag candidates (WD14 first)", final_prompt)
                self.assertIn("Runtime tag-evidence policy", final_prompt)
                self.assertIn("watermark", final_prompt)
                self.assertIn("hero_(series): blue hair, green eyes, black jacket", final_prompt)

                cache = json.loads((root / ".tagcleaner-cache.json").read_text(encoding="utf-8"))
                entry = cache["entries"]["frame.png"]
                self.assertEqual(entry["enabledSkills"], ["prompt-pyramid", "character-tag-auditor"])
                self.assertIn("character-tag-auditor", entry["skillHashes"])
                self.assertEqual(entry["auditedTags"][3], "black_jacket")
                self.assertEqual(entry["wd14Tags"], image["tags"])
                self.assertEqual(entry["visionTags"], ["1girl", "blue hair", "black_jacket", "watermark"])
                self.assertEqual(
                    entry["mergedTagCandidates"],
                    ["hero_(series)", "1girl", "blue_hair", "jacket", "looking_at_viewer", "black_jacket", "watermark"],
                )
                self.assertIn("cool side light", entry["visionDescription"])
                self.assertIn("watermark", entry["resultTags"])
                self.assertIn("watermark at bottom right", entry["resultDescription"])

                if os.environ.get("TAGCLEANER_PACKET_TRACE") == "1":
                    summaries = []
                    for item in captures:
                        content = item["payload"]["messages"][0]["content"]
                        images = [part for part in content if isinstance(part, dict) and part.get("type") == "image_url"] if isinstance(content, list) else []
                        data_url = images[0]["image_url"]["url"] if images else ""
                        summaries.append({
                            "path": item["path"],
                            "authorization": "Bearer ***",
                            "contentType": item["headers"].get("Content-Type"),
                            "model": item["payload"].get("model"),
                            "hasImage": bool(data_url),
                            "base64Chars": len(data_url.split(",", 1)[1]) if data_url else 0,
                        })
                    print("PACKET_TRACE " + json.dumps({"port": listener.server_address[1], "requests": summaries}))
        finally:
            listener.shutdown()
            listener.server_close()
            thread.join(timeout=5)

    def test_style_mode_skips_character_packet_and_enforces_content_only_prompt(self) -> None:
        _PacketHandler.captures = []
        listener = ThreadingHTTPServer(("127.0.0.1", 0), _PacketHandler)
        thread = threading.Thread(target=listener.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                image_path = root / "style-frame.png"
                apose_path = root / "hero-apose.png"
                _save_test_png(image_path)
                _save_test_png(apose_path, color=(110, 90, 150))
                state = _LocalState(apose_path)
                base_url = f"http://127.0.0.1:{listener.server_address[1]}"
                reported_stages = []
                processor = LLMProcessor(
                    state,
                    _LocalConfig(base_url),
                    progress_callback=lambda image, stage, activity: (
                        reported_stages.append((image["name"], stage)) if not activity else None
                    ),
                )
                source_tags = [
                    "ktks", "hero_(series)", "1girl", "blue_hair", "jacket",
                    "looking_at_viewer", "watercolor", "painterly",
                ]
                image = {
                    "id": "style-frame-id",
                    "path": image_path,
                    "name": image_path.name,
                    "relativePath": image_path.name,
                    "tags": source_tags,
                    "description": "",
                }

                outcome = processor._process_image(image, {
                    "styleMode": True,
                    "allowNaturalLanguage": True,
                    "autoSaveResults": True,
                    "protectedDatasetTags": ["ktks"],
                })

                self.assertFalse(outcome["cached"])
                self.assertEqual(state.result["status"], "completed")
                self.assertEqual(state.result["tags"][0], "ktks")
                self.assertEqual(state.saved_image_id, image["id"])
                self.assertEqual(
                    [stage for _name, stage in reported_stages],
                    ["cache", "vision", "clean", "write"],
                )
                captures = _PacketHandler.captures
                self.assertEqual([item["path"] for item in captures], ["/volcano/chat/completions", "/gemini"])
                final_content = captures[-1]["payload"]["messages"][0]["content"]
                self.assertFalse(any(item.get("type") == "image_url" for item in final_content))
                final_prompt = next(item["text"] for item in final_content if item.get("type") == "text")
                self.assertIn("Final STYLE MODE override", final_prompt)
                self.assertIn("Final protected dataset activation tags", final_prompt)
                self.assertIn("ktks", final_prompt)
                self.assertIn("Remove every art-style, artist, medium, rendering, technique", final_prompt)
                self.assertIn("Preserve all visually supported content descriptions", final_prompt)
                self.assertIn("hero_(series), 1girl, blue_hair, jacket", final_prompt)
                self.assertNotIn("hero_(series): blue hair, green eyes, black jacket", final_prompt)

                cache = json.loads((root / ".tagcleaner-cache.json").read_text(encoding="utf-8"))
                entry = cache["entries"][image_path.name]
                self.assertTrue(entry["styleMode"])
                self.assertEqual(entry["protectedDatasetTags"], ["ktks"])
                self.assertFalse(entry["characterDescriptionsIncluded"])
                self.assertEqual(entry["auditedTags"], source_tags)
        finally:
            listener.shutdown()
            listener.server_close()
            thread.join(timeout=5)


@unittest.skipUnless(os.name == "nt", "Windows DPAPI test")
class SecretStorageTests(unittest.TestCase):
    def test_dpapi_round_trip(self) -> None:
        value = b"tagcleaner-test-secret"
        encrypted = _dpapi_transform(value, True)
        self.assertNotEqual(encrypted, value)
        self.assertEqual(_dpapi_transform(encrypted, False), value)

    def test_public_config_never_contains_key_values(self) -> None:
        public = server.LLM_CONFIG.public()
        self.assertNotIn("volcanoKey", public)
        self.assertNotIn("geminiKey", public)


if __name__ == "__main__":
    unittest.main()
