import pytest
import timed_transcript as T


def test_webvtt_normalises_overlapping_cues_and_builds_timeline():
    transcript = T.parse_timed_transcript(
        """WEBVTT

intro
00:00:01.000 --> 00:00:03.000 align:start
Hello there.

00:02.500 --> 00:04.000
General Kenobi.
""",
        "vtt",
        video_duration_seconds=6,
    )

    assert transcript == [
        {
            "text": "Hello there. General Kenobi.",
            "start": 1.0,
            "end": 4.0,
            "words": [],
        }
    ]
    assert T.transcript_audio_events(transcript, video_duration_seconds=6) == [
        {
            "start": 0.0,
            "end": 1.0,
            "event_type": "silence",
            "confidence": 1.0,
            "transcript": "",
        },
        {
            "start": 1.0,
            "end": 4.0,
            "event_type": "dialogue",
            "confidence": 1.0,
            "transcript": "Hello there. General Kenobi.",
        },
        {
            "start": 4.0,
            "end": 6,
            "event_type": "silence",
            "confidence": 1.0,
            "transcript": "",
        },
    ]


def test_srt_accepts_indices_and_multiline_text():
    transcript = T.parse_timed_transcript(
        """1
00:00:00,500 --> 00:00:01,250
First line
continues

2
00:00:02,000 --> 00:00:03,000
Second
""",
        "srt",
        video_duration_seconds=3,
    )
    assert transcript[0]["text"] == "First line continues"
    assert transcript[1]["start"] == 2.0


@pytest.mark.parametrize(
    ("body", "transcript_format", "message"),
    [
        ("00:00:00,000 --> 00:00:01,000\ntext", "vtt", "header"),
        ("WEBVTT\n\n00:00:02.000 --> 00:00:01.000\ntext", "vtt", "end"),
        ("1\n00:00:00,000 --> 00:00:04,000\ntext", "srt", "duration"),
        ("1\n00:00:00,000 --> 00:00:01,000\n", "srt", "text"),
        ("WEBVTT\n\nSTYLE\n::cue { color: red }", "vtt", "style"),
    ],
)
def test_invalid_transcript_fails_closed(body, transcript_format, message):
    with pytest.raises(T.TimedTranscriptError, match=message):
        T.parse_timed_transcript(body, transcript_format, video_duration_seconds=3)


def test_null_bytes_and_unsupported_formats_are_rejected():
    with pytest.raises(T.TimedTranscriptError, match="null"):
        T.parse_timed_transcript(
            "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhello\x00",
            "vtt",
            video_duration_seconds=3,
        )
    with pytest.raises(T.TimedTranscriptError, match="unsupported"):
        T.parse_timed_transcript("text", "txt", video_duration_seconds=3)  # type: ignore[arg-type]


def test_bytes_contract_rejects_non_utf8_and_oversize(monkeypatch):
    with pytest.raises(T.TimedTranscriptError, match="UTF-8"):
        T.parse_timed_transcript_bytes(b"\xff", "vtt", video_duration_seconds=3)

    monkeypatch.setattr(T, "MAX_TRANSCRIPT_BYTES", 3)
    with pytest.raises(T.TimedTranscriptError, match="10 MiB"):
        T.parse_timed_transcript_bytes(b"four", "vtt", video_duration_seconds=3)


def test_ad_gaps_match_canonical_shape():
    transcript = [{"text": "Hello", "start": 1.0, "end": 2.0, "words": []}]
    assert T.transcript_ad_gaps(transcript, video_duration_seconds=5) == [
        {
            "start": 0.0,
            "end": 1.0,
            "duration_seconds": 1.0,
            "midpoint": 0.5,
            "recommended_ad_start": 0.25,
            "recommended": False,
        },
        {
            "start": 2.0,
            "end": 5.0,
            "duration_seconds": 3.0,
            "midpoint": 3.5,
            "recommended_ad_start": 2.25,
            "recommended": True,
        },
    ]
