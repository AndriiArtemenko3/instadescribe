"""Unit tests for the caption-templating + pronoun-grammar engine."""

import json
import logging
import math

import normalisation as N


def test_get_pronoun_set_known_and_fallback():
    assert N.get_pronoun_set("he") == {"subj": "he", "obj": "him", "poss": "his"}
    assert N.get_pronoun_set("THEY")["obj"] == "them"
    # Unknown or missing pronouns fall back to neutral "it".
    assert N.get_pronoun_set("dragon") == N.PRONOUN_FORMS["it"]
    assert N.get_pronoun_set(None) == N.PRONOUN_FORMS["it"]


def test_capitalize_first():
    assert N.capitalize_first("hello") == "Hello"
    assert N.capitalize_first("") == ""
    assert N.capitalize_first("a") == "A"


def test_get_first_reference_prefers_user_rename():
    renamed = {"user_renamed": True, "name": "Indy", "first_mention_label": "a man"}
    assert N.get_first_reference(renamed) == "Indy"
    auto = {"user_renamed": False, "name": "Indy", "first_mention_label": "a man"}
    assert N.get_first_reference(auto) == "a man"
    assert N.get_first_reference({}) == "someone"


def test_render_caption_template_substitutes_tokens():
    entities = {
        "char_1": {"first_mention_label": "a man", "name": "a man", "pronoun": "he"},
    }
    template = "{char_1_first} lifts {char_1_poss} hat. {char_1_subj_cap} smiles."
    assert N.render_caption_template(template, entities) == "a man lifts his hat. He smiles."


def test_render_caption_template_leaves_unknown_tokens_intact():
    template = "{char_9_first} waves and {not_a_token} stays."
    assert (
        N.render_caption_template(template, {}) == "{char_9_first} waves and {not_a_token} stays."
    )


def test_build_caption_template_replaces_longest_match_first():
    entities = {
        "char_1": {
            "first_mention_label": "older man",
            "name": "older man",
            "aliases": ["older man in a fedora"],
        },
    }
    out = N.build_caption_template("the older man in a fedora nods", ["char_1"], entities)
    assert "{char_1_first}" in out
    assert "fedora" not in out  # the longer alias was consumed whole
    assert out == "the {char_1_first} nods"


def test_apply_manual_character_rename_tracks_history():
    entities = [
        {"id": "char_1", "name": "a man", "name_history": []},
        {"id": "char_2", "name": "a woman", "name_history": []},
    ]
    out = N.apply_manual_character_rename(entities, "char_1", "Indiana")
    renamed = next(e for e in out if e["id"] == "char_1")
    assert renamed["name"] == "Indiana"
    assert renamed["user_renamed"] is True
    assert "a man" in renamed["name_history"]
    # Other entities are untouched, and the input is not mutated in place.
    assert next(e for e in out if e["id"] == "char_2")["name"] == "a woman"
    assert entities[0]["name"] == "a man"


def test_rerender_scenes_respects_locked():
    entities = [
        {"id": "char_1", "name": "Indiana", "pronoun": "he", "first_mention_label": "Indiana"}
    ]
    scenes = [
        {"caption_template": "{char_1_first} runs.", "caption": "old", "locked": False},
        {"caption_template": "{char_1_first} runs.", "caption": "old", "locked": True},
    ]
    out = N.rerender_scenes_with_updated_entities(scenes, entities)
    assert out[0]["caption"] == "Indiana runs."
    assert out[1]["caption"] == "old"  # locked scene is left alone


def _scene(start, end, *, frame_indices=None, ad="description"):
    return {
        "start": start,
        "end": end,
        "frame_indices": frame_indices or [],
        "character_ids": [],
        "ad": ad,
    }


def test_export_scenes_drops_zero_duration_and_renumbers_retained_scenes(caplog):
    memory = {
        "scene_history": [
            _scene(0.0, 10.0, ad="first"),
            _scene(10.0, 10.0, ad="zero tail"),
            _scene(10.0, 20.0, ad="third input"),
        ]
    }

    with caplog.at_level(logging.WARNING, logger=N.__name__):
        scenes = N.export_scenes(memory, [])

    assert [scene["scene_id"] for scene in scenes] == ["scene_1", "scene_2"]
    assert [(scene["start"], scene["end"]) for scene in scenes] == [
        (0.0, 10.0),
        (10.0, 20.0),
    ]
    assert caplog.messages == ["Dropped zero-duration scene during app-state export"]


def test_export_scenes_drops_one_frame_tail_shape():
    """A 61-frame / 60-frame-chunk response can leave one frame at t=60."""
    memory = {
        "scene_history": [
            _scene(0.0, 60.0, frame_indices=list(range(60))),
            _scene(60.0, 60.0, frame_indices=[60], ad="single-frame tail"),
        ]
    }

    assert N.export_scenes(memory, []) == [
        {
            "scene_id": "scene_1",
            "start": 0.0,
            "end": 60.0,
            "frame_indices": list(range(60)),
            "character_ids": [],
            "caption_template": "description",
            "caption": "description",
            "render_mode": "auto",
            "locked": False,
            "needs_review": False,
        }
    ]


def test_export_scenes_retains_negative_and_malformed_bounds_for_strict_validation():
    nan = math.nan
    memory = {
        "scene_history": [
            _scene(5.0, 4.0, ad="negative duration"),
            _scene("6", "6", ad="string bounds"),
            _scene(True, True, ad="boolean bounds"),
            _scene(nan, nan, ad="non-finite bounds"),
            {"end": 8.0, "character_ids": [], "ad": "missing start"},
        ]
    }

    scenes = N.export_scenes(memory, [])

    assert [scene["scene_id"] for scene in scenes] == [
        "scene_1",
        "scene_2",
        "scene_3",
        "scene_4",
        "scene_5",
    ]
    assert (scenes[0]["start"], scenes[0]["end"]) == (5.0, 4.0)
    assert (scenes[1]["start"], scenes[1]["end"]) == ("6", "6")
    assert (scenes[2]["start"], scenes[2]["end"]) == (True, True)
    assert math.isnan(scenes[3]["start"]) and math.isnan(scenes[3]["end"])
    assert scenes[4]["start"] is None and scenes[4]["end"] == 8.0


def test_export_app_state_counts_only_retained_scenes(tmp_path):
    memory = {
        "characters": [],
        "scene_history": [
            _scene(0.0, 4.0),
            _scene(4.0, 4.0, ad="zero"),
            _scene(4.0, 8.0),
        ],
    }
    summaries = [
        {
            "num_chunks": 1,
            "total_usage": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
            "chunks": [],
        }
    ]

    N.export_app_state(
        memory=memory,
        summaries=summaries,
        out_dir=tmp_path,
        video_id="job",
        model="gpt-4.1",
        image_detail="low",
        chunk_sizes=[60],
        num_frames=61,
    )

    scenes = json.loads((tmp_path / "scenes.json").read_text())
    system_info = json.loads((tmp_path / "system_info.json").read_text())
    assert [scene["scene_id"] for scene in scenes] == ["scene_1", "scene_2"]
    assert system_info["output"]["num_scenes"] == len(scenes) == 2
    assert system_info["tokens"] == {
        "input_tokens": 11,
        "output_tokens": 7,
        "total_tokens": 18,
    }
