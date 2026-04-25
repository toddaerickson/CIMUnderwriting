#!/bin/bash
# Backup the CIM Analyst comp database.
# Usage: ./scripts/backup_db.sh [db_path] [backup_dir]
#
# Defaults:
#   db_path   = $COMP_DB_PATH or data/cim_comps.db
#   backup_dir = /data/backups (Docker) or ./backups (local)

set -e

DB_PATH="${1:-${COMP_DB_PATH:-data/cim_comps.db}}"
BACKUP_DIR="${2:-${CIM_BACKUP_DIR:-/data/backups}}"

# Fall back to local backups/ if /data/backups doesn't exist
if [ ! -d "$BACKUP_DIR" ]; then
    BACKUP_DIR="./backups"
fi

mkdir -p "$BACKUP_DIR"

if [ ! -f "$DB_PATH" ]; then
    echo "Database not found: $DB_PATH"
    exit 1
fi

STAMP=$(date +%Y%m%d_%H%M%S)
DEST="$BACKUP_DIR/cim_comps_${STAMP}.db"

# Use SQLite backup API for consistency (safe even during writes with WAL)
if command -v sqlite3 &>/dev/null; then
    sqlite3 "$DB_PATH" ".backup '$DEST'"
else
    cp "$DB_PATH" "$DEST"
fi

echo "Backup: $DEST ($(du -h "$DEST" | cut -f1))"

# Prune backups older than 30 days
find "$BACKUP_DIR" -name "cim_comps_*.db" -mtime +30 -delete 2>/dev/null || true
echo "Pruned backups older than 30 days."
