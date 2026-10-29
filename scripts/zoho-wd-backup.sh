#!/usr/bin/env bash

# Step 1: Use Bash because this script uses Bash arrays.

# Step 2: Stop on errors, unset variables, and failed pipelines.
set -euo pipefail

# Message colours.
RED=$'\033[31m'
GREEN=$'\033[32m'
RESET=$'\033[0m'

# Step 3: Define the TrueSync mount and backup destination.
zoho_root="$HOME/ZohoWorkDrive"
zoho_dest="$zoho_root/My Folders/HP-EndeavourOS/NK"

# Diagnostic run: back up only Docs. Restore the other directories after testing.
dirs_to_backup=(
  "NK/Docs"
)

# Step 4: Verify that TrueSync is mounted and responsive.
if ! findmnt -T "$zoho_root" -n -o FSTYPE 2>/dev/null |
  grep -Eq '^fuse'; then
  printf '%sERROR: TrueSync is not mounted.%s\n' "$RED" "$RESET" >&2
  exit 1
fi

# Step 5: Verify that the destination folder exists and is accessible.
if ! ls -d -- "$zoho_dest" >/dev/null 2>&1; then
  printf '%sERROR: Destination is missing or unavailable:%s\n%s\n' \
    "$RED" "$RESET" "$zoho_dest" >&2
  exit 1
fi

# Step 6 and 7: Verify sources and collect symlinks to skip.
skipped_symlinks=()

for dir in "${dirs_to_backup[@]}"; do
  source="$HOME/$dir"

  if [[ ! -d "$source" ]]; then
    printf '%sERROR: Source directory is missing:%s\n%s\n' \
      "$RED" "$RESET" "$source" >&2
    exit 1
  fi

  while IFS= read -r -d '' symlink; do
    skipped_symlinks+=("$symlink")
  done < <(find "$source" -type l -print0)
done

# Step 8: Synchronize each source directory with rsync.
# --no-links skips symlinks.
# --delete removes destination files that no longer exist on the SSD.
# Count transferred regular files without displaying each file.
# Add --dry-run before the first real run to preview changes.
transferred_files=0
SECONDS=0

for dir in "${dirs_to_backup[@]}"; do
  source="$HOME/$dir"
  target="$zoho_dest/${dir#NK/}"

  printf 'Backing up: %s%s%s\n' \
    "$(tput bold)$(tput setaf 2)" "$dir" "$(tput sgr0)"

  transferred_in_dir=$(
    rsync -a --no-links --delete --out-format='%i %n%L' \
      "$source/" "$target/" |
      awk '$0 ~ /^>f/ {count++} {print} END {print "__TRANSFERRED_COUNT__ " count+0}' |
      tee /dev/stderr |
      awk '$1 == "__TRANSFERRED_COUNT__" {print $2}'
  )
  transferred_files=$((transferred_files + transferred_in_dir))
done

# Step 9: Report skipped symlinks.
if ((${#skipped_symlinks[@]} > 0)); then
  printf '\n%sWARNING: These symlinks were skipped:%s\n' \
    "$RED" "$RESET"
  printf '  %s\n' "${skipped_symlinks[@]}"
fi

# Step 10: Keep the destination folder set to Online Only in TrueSync.

# Step 11: Report successful completion, transfer statistics, and elapsed time.
elapsed_seconds=$SECONDS
hours=$((elapsed_seconds / 3600))
minutes=$(((elapsed_seconds % 3600) / 60))
seconds=$((elapsed_seconds % 60))

printf '%s%d files backed up in %02d:%02d:%02d.%s\n' \
  "$GREEN" "$transferred_files" "$hours" "$minutes" "$seconds" "$RESET"
printf '%sKāryaṃ samāptam. Bhayasya kāraṇaṃ nāsti 🙏%s\n' "$GREEN" "$RESET"
