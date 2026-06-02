#!/bin/bash
# Script principal — corre el monitor y pushea resultados a GitHub

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="$REPO_DIR/output/run.log"
MAIL_TO="marielena.tejera@mercadolibre.com"
mkdir -p "$REPO_DIR/output"

send_alert() {
    local motivo=$1
    /usr/sbin/sendmail -t <<EOF
To: $MAIL_TO
Subject: [Monitor STO MLA] Error en la corrida del $(date '+%Y-%m-%d')
Content-Type: text/plain; charset=UTF-8

Hola,

El monitor de STO MLA no pudo completar su ejecución del $(date '+%A %d/%m/%Y a las %H:%M').

Motivo: $motivo

Revisá el log completo en: $LOG_FILE

Saludos,
Monitor STO MLA
EOF
    echo "Alerta enviada a $MAIL_TO" >> "$LOG_FILE"
}

echo "==============================" >> "$LOG_FILE"
echo "Inicio: $(date)" >> "$LOG_FILE"

# Verificar VPN
if ! curl -sS --connect-timeout 5 "https://fpr-cross.melioffice.com" > /dev/null 2>&1; then
    echo "ERROR: VPN no activa." >> "$LOG_FILE"
    send_alert "VPN corporativa no estaba activa al momento de la ejecución."
    exit 1
fi

# Correr el monitor
cd "$REPO_DIR"
if ! python3 src/monitor.py >> "$LOG_FILE" 2>&1; then
    echo "ERROR: El script Python falló." >> "$LOG_FILE"
    send_alert "El script Python falló durante la ejecución. Revisá el log para más detalles."
    exit 1
fi

# Push a GitHub
if ! git add output/ && git -c core.hooksPath=/dev/null commit -m "Monitor $(date '+%Y-%m-%d')" >> "$LOG_FILE" 2>&1; then
    echo "ERROR: Falló el commit a GitHub." >> "$LOG_FILE"
    send_alert "El monitor corrió correctamente pero falló al subir los resultados a GitHub."
    exit 1
fi

if ! git push origin main >> "$LOG_FILE" 2>&1; then
    echo "ERROR: Falló el push a GitHub." >> "$LOG_FILE"
    send_alert "El monitor corrió correctamente pero falló al hacer push a GitHub."
    exit 1
fi

echo "Fin: $(date)" >> "$LOG_FILE"
echo "Listo. Resultados pusheados a GitHub."
