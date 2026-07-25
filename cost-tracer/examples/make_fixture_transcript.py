"""Generate a small, clearly-synthetic transcript that matches the real
Claude Code JSONL schema, so trace.py has something safe and reproducible to
run against. No real session data is ever shipped.

    python -m examples.make_fixture_transcript            # -> examples/fixture_transcript.jsonl
    python -m examples.make_fixture_transcript --out X.jsonl

The fixture deliberately includes:
  * assistant turns with a full `usage` block,
  * tool_use / tool_result pairs of varying result sizes,
  * unknown line types (mode, attachment, system) that must be skipped,
  * ONE assistant line with a missing `usage` block, to exercise the
    honest-degradation path (counted as "no usage data", not zero-filled-hidden).

Hand-computable totals (see README / SKILL verification section):
  input = 100 + 200 + 50          = 350
  output = 40 + 300 + 20          = 360
  cache_creation = 500 + 0 + 0    = 500
  cache_read = 0 + 1000 + 1000    = 2000
  grand total                     = 3210
  (the 4th assistant turn has NO usage -> contributes 0, counted as 1 missing)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

TS = "2026-07-25T12:00:0{}Z"

LINES = [
    {"type": "mode", "mode": "default"},  # unknown type -> skipped
    {"type": "attachment", "timestamp": TS.format(0), "data": "irrelevant"},  # skipped

    # turn 1: assistant reads a file (tool_use "Read")
    {"type": "assistant", "timestamp": TS.format(1), "message": {
        "usage": {"input_tokens": 100, "output_tokens": 40,
                  "cache_creation_input_tokens": 500, "cache_read_input_tokens": 0,
                  "service_tier": "standard"},
        "content": [
            {"type": "thinking", "thinking": "I'll read the config."},
            {"type": "tool_use", "id": "tu_read_1", "name": "Read",
             "input": {"file_path": "/app/config.yaml"}},
        ],
    }},
    # tool_result for the Read — small payload
    {"type": "user", "timestamp": TS.format(2), "message": {"content": [
        {"type": "tool_result", "tool_use_id": "tu_read_1",
         "content": "port: 8080\nhost: localhost\n"},
    ]}},

    # turn 2: assistant runs a Bash command that returns a BIG result
    {"type": "assistant", "timestamp": TS.format(3), "message": {
        "usage": {"input_tokens": 200, "output_tokens": 300,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 1000},
        "content": [
            {"type": "text", "text": "Listing everything."},
            {"type": "tool_use", "id": "tu_bash_1", "name": "Bash",
             "input": {"command": "find / -type f"}},
        ],
    }},
    # big tool_result (list-shaped content, like a real multi-block result)
    {"type": "user", "timestamp": TS.format(4), "message": {"content": [
        {"type": "tool_result", "tool_use_id": "tu_bash_1", "content": [
            {"type": "text", "text": "PATH LISTING\n" + ("/some/very/long/path/file.txt\n" * 400)},
        ]},
    ]}},

    {"type": "system", "timestamp": TS.format(5), "subtype": "info"},  # skipped

    # turn 3: assistant runs a Bash command that ERRORS (small result, is_error)
    {"type": "assistant", "timestamp": TS.format(6), "message": {
        "usage": {"input_tokens": 50, "output_tokens": 20,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 1000},
        "content": [
            {"type": "tool_use", "id": "tu_bash_2", "name": "Bash",
             "input": {"command": "cat /nope"}},
        ],
    }},
    {"type": "user", "timestamp": TS.format(7), "message": {"content": [
        {"type": "tool_result", "tool_use_id": "tu_bash_2", "is_error": True,
         "content": "cat: /nope: No such file or directory"},
    ]}},

    # turn 4: assistant line with NO usage block -> honest degradation path
    {"type": "assistant", "timestamp": TS.format(8), "message": {
        "content": [{"type": "text", "text": "Done."}],
    }},
]


def main() -> None:
    p = argparse.ArgumentParser()
    default = str(Path(__file__).with_name("fixture_transcript.jsonl"))
    p.add_argument("--out", default=default)
    args = p.parse_args()
    text = "\n".join(json.dumps(o, ensure_ascii=False) for o in LINES) + "\n"
    Path(args.out).write_text(text, encoding="utf-8")
    print(f"Wrote fixture transcript: {Path(args.out).resolve()} ({len(LINES)} lines)")


if __name__ == "__main__":
    main()
