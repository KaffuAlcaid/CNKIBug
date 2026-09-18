#!/usr/bin/env bash
set -euo pipefail

project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$project_dir"
if [[ $(uname -s) != Linux || $(uname -m) != x86_64 ]]; then
    printf '%s\n' 'Build this AppImage on Linux x86-64.' >&2
    exit 1
fi
: "${APPIMAGETOOL:?Set APPIMAGETOOL to the appimagetool executable.}"
mkdir -p build dist
staging=$(mktemp -d "$project_dir/build/appimage.XXXXXX")
trap 'rm -rf -- "$staging"' EXIT

python -m PyInstaller --noconfirm --clean --onedir --windowed --noupx --strip \
    --name CNKIBug-GUI --copy-metadata cnkibug --copy-metadata ttkbootstrap \
    --copy-metadata playwright --collect-all ttkbootstrap \
    --add-data "$project_dir/icon.ico:." --add-data "$project_dir/pyproject.toml:." \
    --add-data "$project_dir/cnkibug/gui/apply_update.sh:cnkibug/gui" \
    --distpath "$staging/dist" --workpath "$staging/work" --specpath "$staging" \
    run_gui.py

app_dir="$staging/CNKIBug.AppDir"
mkdir -p "$app_dir/usr/bin" "$app_dir/usr/share/doc/cnkibug"
cp -a "$staging/dist/CNKIBug-GUI/." "$app_dir/usr/bin/"
cp packaging/appimage/AppRun packaging/appimage/cnkibug.desktop "$app_dir/"
cp logo.png "$app_dir/cnkibug.png"
cp LICENSE "$app_dir/usr/share/doc/cnkibug/LICENSE"
ln -s cnkibug.png "$app_dir/.DirIcon"
chmod +x "$app_dir/AppRun" "$app_dir/usr/bin/CNKIBug-GUI"

# Browser binaries are installed into the user's Playwright cache on demand.
if find "$app_dir" -type d \( -name .local-browsers -o -name ms-playwright \) -print -quit | grep -q .; then
    printf '%s\n' 'The AppDir unexpectedly contains a browser cache.' >&2
    exit 1
fi
du -ah "$app_dir/usr/bin" | sort -h | tail -20
output="$project_dir/dist/CNKIBug-GUI-x86_64.AppImage"
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$APPIMAGETOOL" --comp xz "$app_dir" "$output"
chmod +x "$output"
size=$(stat -c %s "$output")
printf 'AppImage size: %s bytes\n' "$size"
if (( size > 100 * 1024 * 1024 )); then
    printf '%s\n' '::warning::The AppImage exceeds the 100 MiB size target.'
fi
