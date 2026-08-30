import json
import logging
import math
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PRONOUN_FORMS = {
    "he": {"subj": "he", "obj": "him", "poss": "his"},
    "she": {"subj": "she", "obj": "her", "poss": "her"},
    "they": {"subj": "they", "obj": "them", "poss": "their"},
    "it": {"subj": "it", "obj": "it", "poss": "its"},
}


def safe_json_dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))


def get_pronoun_set(pronoun: str) -> dict[str, str]:
    return PRONOUN_FORMS.get((pronoun or "it").lower(), PRONOUN_FORMS["it"])


def capitalize_first(text: str) -> str:
    if not text:
        return text
    return text[0].upper() + text[1:]


def get_first_reference(entity: dict[str, Any]) -> str:
    if entity.get("user_renamed") and entity.get("name"):
        return entity["name"]
    return entity.get("first_mention_label") or entity.get("name") or "someone"


def get_name_reference(entity: dict[str, Any]) -> str:
    return entity.get("name") or "someone"


def render_caption_template(template: str, entities_by_id: dict[str, dict[str, Any]]) -> str:
    """
    Replaces tokens like:
    {char_1_first}
    {char_1_name}
    {char_1_subj}
    {char_1_obj}
    {char_1_poss}
    {char_1_subj_cap}
    {char_1_obj_cap}
    {char_1_poss_cap}
    """

    KNOWN_FIELDS = ["subj_cap", "obj_cap", "poss_cap", "first", "name", "subj", "obj", "poss"]

    def replacer(match: re.Match) -> str:
        token = match.group(1)

        entity_id = None
        field = None
        for f in KNOWN_FIELDS:
            suffix = "_" + f
            if token.endswith(suffix):
                entity_id = token[: -len(suffix)]
                field = f
                break

        if entity_id is None:
            return match.group(0)

        entity = entities_by_id.get(entity_id)
        if not entity:
            return match.group(0)

        pronouns = get_pronoun_set(entity.get("pronoun", "it"))

        if field == "first":
            return get_first_reference(entity)
        if field == "name":
            return get_name_reference(entity)
        if field == "subj":
            return pronouns["subj"]
        if field == "obj":
            return pronouns["obj"]
        if field == "poss":
            return pronouns["poss"]
        if field == "subj_cap":
            return capitalize_first(pronouns["subj"])
        if field == "obj_cap":
            return capitalize_first(pronouns["obj"])
        if field == "poss_cap":
            return capitalize_first(pronouns["poss"])

        return match.group(0)

    return re.sub(r"\{([^{}]+)\}", replacer, template)


def build_caption_template(
    raw_caption: str, scene_character_ids: list[str], entities_by_id: dict[str, dict[str, Any]]
) -> str:
    """
    Replaces character references in raw_caption with template tokens.
    Matches: first_mention_label, canonical name, and all aliases.
    Matching is case-insensitive; longer strings are replaced first to avoid
    partial matches (e.g. "the older man in a fedora" before "the older man").
    """
    template = raw_caption
    replacements: list[tuple[str, str]] = []

    for char_id in scene_character_ids:
        entity = entities_by_id.get(char_id)
        if not entity:
            continue

        first_label = (entity.get("first_mention_label") or "").strip()
        name = (entity.get("name") or "").strip()
        aliases = [a.strip() for a in entity.get("aliases", []) if a.strip()]

        candidates = []
        if first_label:
            candidates.append((first_label, f"{{{char_id}_first}}"))
        for alias in aliases:
            if alias.lower() != first_label.lower():
                candidates.append((alias, f"{{{char_id}_first}}"))
        if name and name.lower() not in {first_label.lower()} | {a.lower() for a in aliases}:
            candidates.append((name, f"{{{char_id}_name}}"))

        replacements.extend(candidates)

    # Longest match first to prevent partial replacements
    replacements.sort(key=lambda x: len(x[0]), reverse=True)

    for source_text, token in replacements:
        template = re.sub(re.escape(source_text), token, template, flags=re.IGNORECASE)

    return template


def export_entities(memory: dict[str, Any]) -> list[dict[str, Any]]:
    entities = []

    for ch in memory.get("characters", []):
        entities.append(
            {
                "id": ch["id"],
                "name": ch["name"],
                "first_mention_label": ch.get("first_mention_label", ch["name"]),
                "pronoun": ch.get("pronoun", "it"),
                "aliases": ch.get("aliases", []),
                "name_history": ch.get("name_history", []),
                "user_renamed": ch.get("user_renamed", False),
            }
        )

    return entities


def export_scenes(memory: dict[str, Any], entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scenes = []
    entities_by_id = {e["id"]: e for e in entities}

    for scene in memory.get("scene_history", []):
        start = scene.get("start")
        end = scene.get("end")
        bounds_are_finite_numbers = all(
            not isinstance(bound, bool) and isinstance(bound, int | float) and math.isfinite(bound)
            for bound in (start, end)
        )
        if bounds_are_finite_numbers and end == start:
            # A final one-frame model chunk can produce a zero-width tail.
            # It carries no editable interval, so exclude it here before the
            # app-state and result scene counts are derived. Do not broaden
            # this to malformed or negative-duration scenes: retaining those
            # lets the worker's strict artifact boundary reject them.
            logger.warning("Dropped zero-duration scene during app-state export")
            continue

        scene_number = len(scenes) + 1
        scene_character_ids = scene.get("character_ids", [])
        raw_caption = scene.get("ad", "")

        # Prefer the model-generated template (Strategy 3); fall back to
        # post-hoc string matching (Strategies 1+2) if it is absent or empty.
        model_template = (scene.get("ad_template") or "").strip()
        if model_template:
            caption_template = model_template
        else:
            caption_template = build_caption_template(
                raw_caption=raw_caption,
                scene_character_ids=scene_character_ids,
                entities_by_id=entities_by_id,
            )

        caption = render_caption_template(caption_template, entities_by_id)

        scenes.append(
            {
                "scene_id": f"scene_{scene_number}",
                # Preserve malformed bounds as invalid data rather than
                # silently manufacturing valid-looking defaults.
                "start": start,
                "end": end,
                "frame_indices": scene.get("frame_indices", []),
                "character_ids": scene_character_ids,
                "caption_template": caption_template,
                "caption": caption,
                "render_mode": "auto",
                "locked": False,
                "needs_review": False,
            }
        )

    return scenes


def build_system_info(
    video_id: str,
    model: str,
    image_detail: str,
    chunk_sizes: list[int],
    num_frames: int,
    summaries: list[dict[str, Any]],
    entities: list[dict[str, Any]],
    scenes: list[dict[str, Any]],
) -> dict[str, Any]:
    total_input_tokens = 0
    total_output_tokens = 0
    total_tokens = 0
    total_chunks = 0

    avg_ad_words_values = []
    duplicate_ad_values = []

    for summary in summaries:
        total_chunks += summary.get("num_chunks", 0)

        usage = summary.get("total_usage", {})
        total_input_tokens += usage.get("input_tokens", 0) or 0
        total_output_tokens += usage.get("output_tokens", 0) or 0
        total_tokens += usage.get("total_tokens", 0) or 0

        for chunk in summary.get("chunks", []):
            qp = chunk.get("quality_proxy", {})
            avg_ad_words_values.append(qp.get("avg_ad_words", 0) or 0)
            duplicate_ad_values.append(qp.get("duplicate_ad_count", 0) or 0)

    avg_ad_words = (
        sum(avg_ad_words_values) / len(avg_ad_words_values) if avg_ad_words_values else 0.0
    )
    avg_duplicate_ads = (
        sum(duplicate_ad_values) / len(duplicate_ad_values) if duplicate_ad_values else 0.0
    )

    return {
        "video_id": video_id,
        "processing": {
            "model": model,
            "image_detail": image_detail,
            "chunk_sizes": chunk_sizes,
        },
        "input": {
            "num_frames": num_frames,
        },
        "output": {
            "num_entities": len(entities),
            "num_scenes": len(scenes),
            "num_chunks": total_chunks,
        },
        "tokens": {
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "total_tokens": total_tokens,
        },
        "quality": {
            "avg_ad_words": round(avg_ad_words, 2),
            "avg_duplicate_ads": round(avg_duplicate_ads, 2),
        },
        "status": "completed",
    }


def export_app_state(
    memory: dict[str, Any],
    summaries: list[dict[str, Any]],
    out_dir: Path,
    video_id: str,
    model: str,
    image_detail: str,
    chunk_sizes: list[int],
    num_frames: int,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    entities = export_entities(memory)
    scenes = export_scenes(memory, entities)
    system_info = build_system_info(
        video_id=video_id,
        model=model,
        image_detail=image_detail,
        chunk_sizes=chunk_sizes,
        num_frames=num_frames,
        summaries=summaries,
        entities=entities,
        scenes=scenes,
    )

    safe_json_dump(out_dir / "entities.json", entities)
    safe_json_dump(out_dir / "scenes.json", scenes)
    safe_json_dump(out_dir / "system_info.json", system_info)


def apply_manual_character_rename(
    entities: list[dict[str, Any]], character_id: str, new_name: str
) -> list[dict[str, Any]]:
    updated_entities = []

    for entity in entities:
        entity_copy = dict(entity)

        if entity_copy["id"] == character_id:
            old_name = entity_copy.get("name", "")
            if old_name and old_name != new_name:
                history = entity_copy.get("name_history", [])
                if old_name not in history:
                    history = history + [old_name]
                entity_copy["name_history"] = history

            entity_copy["name"] = new_name
            entity_copy["user_renamed"] = True

        updated_entities.append(entity_copy)

    return updated_entities


def rerender_scenes_with_updated_entities(
    scenes: list[dict[str, Any]], entities: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    entities_by_id = {e["id"]: e for e in entities}
    updated_scenes = []

    for scene in scenes:
        scene_copy = dict(scene)

        if not scene_copy.get("locked", False):
            scene_copy["caption"] = render_caption_template(
                scene_copy.get("caption_template", ""), entities_by_id
            )

        updated_scenes.append(scene_copy)

    return updated_scenes


def demo_manual_override(memory: dict[str, Any], out_dir: Path) -> None:
    """
    Demo:
    - export initial entities/scenes
    - rename char_1 to Indiana Jones
    - rerender scenes
    - save demo files
    """

    entities = export_entities(memory)
    scenes = export_scenes(memory, entities)

    safe_json_dump(out_dir / "demo_entities_before.json", entities)
    safe_json_dump(out_dir / "demo_scenes_before.json", scenes)

    updated_entities = apply_manual_character_rename(
        entities=entities, character_id="char_1", new_name="Indiana Jones"
    )

    updated_scenes = rerender_scenes_with_updated_entities(scenes=scenes, entities=updated_entities)

    safe_json_dump(out_dir / "demo_entities_after_rename.json", updated_entities)
    safe_json_dump(out_dir / "demo_scenes_after_rename.json", updated_scenes)
