#!/bin/bash
# Script principal — corre el monitor y pushea resultados a GitHub

set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="$REPO_DIR/output/run.log"
mkdir -p "$REPO_DIR/output"

echo "==============================" >> "$LOG_FILE"
echo "Inicio: $(date)" >> "$LOG_FILE"

# Verificar VPN (si la API no es alcanzable, abortar)
if ! curl -sS --connect-timeout 5 "https://fpr-cross.melioffice.com" > /dev/null 2>&1; then
    echo "ERROR: VPN no activa. Abortando." >> "$LOG_FILE"
    echo "ERROR: VPN no activa. Abortando."
    exit 1
fi

# Correr el monitor
cd "$REPO_DIR"
python3 src/monitor.py >> "$LOG_FILE" 2>&1

# Push a GitHub
git add output/
git commit -m "Monitor $(date '+%Y-%m-%d')" >> "$LOG_FILE" 2>&1
git push origin main >> "$LOG_FILE" 2>&1

echo "Fin: $(date)" >> "$LOG_FILE"
echo "Listo. Resultados pusheados a GitHub."
