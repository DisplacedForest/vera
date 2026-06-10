#!/bin/sh
# install-launchd — render a *.plist.template (@HOME@ -> $HOME) into ~/Library/LaunchAgents
# and bootstrap it. launchd plists can't read env vars in paths, so templates carry a
# placeholder and this script does the substitution per machine.
#
#   usage: scripts/install-launchd.sh <template.plist.template> [more templates...]
set -eu

DEST="$HOME/Library/LaunchAgents"
mkdir -p "$DEST"

for tpl in "$@"; do
  [ -f "$tpl" ] || { echo "no such template: $tpl" >&2; exit 1; }
  label="$(basename "$tpl" .plist.template)"
  # The launchd Label inside the file is authoritative; read it for the bootstrap call.
  plist_label="$(sed -n 's:.*<key>Label</key><string>\(.*\)</string>.*:\1:p' "$tpl" | head -1)"
  out="$DEST/${plist_label:-$label}.plist"
  sed "s:@HOME@:$HOME:g" "$tpl" > "$out"
  launchctl bootout "gui/$(id -u)/${plist_label}" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$out"
  echo "installed + bootstrapped ${plist_label} -> $out"
done
