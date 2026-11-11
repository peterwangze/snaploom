#!/bin/sh
set -eu

application_id=io.github.peterwangze.snaploom
data_home=${XDG_DATA_HOME:-"$HOME/.local/share"}
app_home="$data_home/snaploom"
app_directory="$app_home/app"
executable="$app_directory/Snaploom"
menu_file="$data_home/applications/$application_id.desktop"
icon_file="$data_home/icons/hicolor/256x256/apps/$application_id.png"
bin_directory="$HOME/.local/bin"
bin_link="$bin_directory/snaploom"

if [ "${1:-}" = "--uninstall" ]; then
    if [ -f "$app_directory/.snaploom-installed" ]; then
        rm -rf -- "$app_directory"
        rm -f -- "$menu_file" "$icon_file"
        if [ -L "$bin_link" ] && [ "$(readlink "$bin_link")" = "$executable" ]; then
            rm -f -- "$bin_link"
        fi
        printf '%s\n' 'Snaploom was removed. Your preferences and captures were kept.'
    fi
    exit 0
fi

if [ "$#" -ne 0 ]; then
    printf '%s\n' 'Usage: install.sh [--uninstall]' >&2
    exit 2
fi

source_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
test -x "$source_directory/Snaploom/Snaploom"
test -f "$source_directory/snaploom.png"

if [ -e "$app_directory" ] && [ ! -f "$app_directory/.snaploom-installed" ]; then
    printf 'The destination already exists and belongs to another installation: %s\n' "$app_directory" >&2
    exit 1
fi

mkdir -p -- "$app_home" "$(dirname -- "$menu_file")" "$(dirname -- "$icon_file")" "$bin_directory"
temporary=$(mktemp -d "$app_home/.install.XXXXXXXX")
trap 'if [ -d "$temporary/previous" ] && [ ! -e "$app_directory" ]; then mv -- "$temporary/previous" "$app_directory"; fi; rm -rf -- "$temporary"' EXIT
trap 'exit 1' INT HUP TERM

cp -a -- "$source_directory/Snaploom" "$temporary/app"
cp -- "$source_directory/install.sh" "$temporary/app/uninstall.sh"
chmod +x -- "$temporary/app/uninstall.sh"
touch -- "$temporary/app/.snaploom-installed"

if [ -d "$app_directory" ]; then
    mv -- "$app_directory" "$temporary/previous"
fi
mv -- "$temporary/app" "$app_directory"

# Exec field quoting covers spaces, percent codes, and desktop-entry escapes.
desktop_executable=$(printf '%s' "$executable" | sed -e 's/["`$]/\\\\&/g' -e 's/%/%%/g')
cat > "$menu_file" <<EOF
[Desktop Entry]
Type=Application
Name=Snaploom
Comment=Capture, annotate, and pin screenshots
Exec="$desktop_executable"
Icon=$application_id
Categories=Graphics;Utility;
Terminal=false
StartupNotify=true
EOF
cp -- "$source_directory/snaploom.png" "$icon_file"
if [ ! -e "$bin_link" ] && [ ! -L "$bin_link" ]; then
    ln -s -- "$executable" "$bin_link"
fi
printf 'Installed Snaploom to %s\n' "$app_directory"
printf 'Launch from the application menu or run "%s".\n' "$executable"
printf 'To uninstall, run "%s/uninstall.sh" --uninstall.\n' "$app_directory"
