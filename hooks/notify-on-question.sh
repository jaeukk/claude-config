#!/usr/bin/env bash
# Stop hook: play the notification sound ONLY when Claude's final message to the
# user is asking for a decision or their attention (a question, a choice, or an
# explicit "let me know" style prompt). Stays silent on purely informational
# turns so the alarm means "your input is needed". Best-effort; never fails.

dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
input="$(cat)"  # Stop-hook JSON on stdin (includes transcript_path)

# A tiny Python classifier: pull the last assistant text from the transcript and
# decide whether it's soliciting the user. Exit 0 = ask/attention, 1 = silent.
python3 - "$input" <<'PY'
import sys, json, re

try:
    data = json.loads(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].strip() else {}
except Exception:
    data = {}

path = data.get("transcript_path")
if not path:
    sys.exit(1)

# Grab the text of the most recent assistant message that actually spoke.
last_text = None
try:
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("type") != "assistant":
                continue
            content = obj.get("message", {}).get("content")
            parts = []
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "text":
                        parts.append(b.get("text", ""))
            text = "".join(parts).strip()
            if text:           # remember the last turn that had spoken text
                last_text = text
except Exception:
    sys.exit(1)

if not last_text:
    sys.exit(1)

# Look at the tail of the message — that's where a question/CTA lands.
tail = last_text[-600:].lower()
last_line = next((ln.strip() for ln in reversed(last_text.splitlines()) if ln.strip()), "")

asks = last_line.endswith("?")
if not asks:
    cues = [
        "let me know", "would you like", "do you want", "want me to",
        "should i ", "shall i ", "which would", "which one", "your call",
        "up to you", "confirm", "please choose", "pick one", "go ahead?",
        "proceed?", "ok to ", "okay to ", "?\n", "? ",
    ]
    asks = any(c in tail for c in cues)

sys.exit(0 if asks else 1)
PY

# If the classifier says "asking", fire the existing cross-platform sound.
if [ $? -eq 0 ]; then
    "$dir/notify-sound.sh" >/dev/null 2>&1 || true
fi

exit 0
