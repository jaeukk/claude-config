#!/usr/bin/env bash
# Notification hook: play an alarming sound when Claude Code needs the user's
# answer or permission (the Notification event covers permission prompts and
# idle "waiting for input" notices). Best-effort + cross-platform; never fails.

play_wsl() {
  command -v powershell.exe >/dev/null 2>&1 || return 1
  # [console]::beep is synchronous, so the process won't exit before the tones
  # finish. A rising 3-tone pattern reads as an alert rather than a single ding.
  powershell.exe -NoProfile -Command \
    "[console]::beep(880,250);[console]::beep(1175,250);[console]::beep(1568,400)" \
    >/dev/null 2>&1
}

play_mac() {
  command -v afplay >/dev/null 2>&1 || return 1
  for f in /System/Library/Sounds/Sosumi.aiff /System/Library/Sounds/Glass.aiff; do
    [ -f "$f" ] && { afplay "$f" >/dev/null 2>&1 && return 0; }
  done
  return 1
}

play_linux() {
  for p in paplay aplay; do
    command -v "$p" >/dev/null 2>&1 || continue
    for f in /usr/share/sounds/freedesktop/stereo/dialog-warning.oga \
             /usr/share/sounds/freedesktop/stereo/bell.oga \
             /usr/share/sounds/alsa/Front_Center.wav; do
      [ -f "$f" ] && { "$p" "$f" >/dev/null 2>&1 && return 0; }
    done
  done
  command -v canberra-gtk-play >/dev/null 2>&1 && canberra-gtk-play -i bell >/dev/null 2>&1 && return 0
  return 1
}

play_wsl || play_mac || play_linux || printf '\a\a\a' > /dev/tty 2>/dev/null || printf '\a\a\a'

exit 0
