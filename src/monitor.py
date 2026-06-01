import csv
import json
import re
import time
import requests
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

from google.cloud import bigquery

# ── Configuración ────────────────────────────────────────────────────────────

BQ_PROJECT  = "meli-bi-data"
API_BASE    = "https://fpr-cross.melioffice.com/attachments"
API_HEADERS = {"X-Scope": "fraud", "X-External-System": "FPR_REPATRIATION"}
USER_PROMPT = (
    "Analizá el documento. Indicá: 1) Si es una denuncia policial (sí/no). "
    "2) Si es denuncia, resumí el motivo y el tipo de estafa. "
    "3) Listá montos, fechas e instituciones involucradas."
)

OUTPUT_DIR = Path(__file__).parent.parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# ── Categorías de fraude ─────────────────────────────────────────────────────

CATEGORIAS = {
    "Llamada entrante del estafador": [
        "recibió una llamada", "recibió llamadas", "recibió un llamado",
        "lo llamaron", "la llamaron", "le llamaron", "llamada telefónica",
        "llamada de un", "llamada de una", "llamada insistente",
        "fue contactada telefónicamente", "fue contactado telefónicamente",
        "llamada de personas que", "recibió una comunicación telefónica",
        "llamó al número que figuraba", "llamó al número de atención",
    ],
    "Búsqueda activa de contacto": [
        "buscó en google", "buscó el número", "buscó en internet",
        "al buscar", "ingresó a un enlace", "encontró en google",
        "buscando en internet", "encontró el número", "buscar en google",
        "buscó por internet", "accedió a un link", "accedió a un enlace",
        "buscó asesoramiento", "buscó asistencia", "buscó ayuda",
        "contactó por instagram", "contactó por facebook", "encontró una cuenta",
    ],
    "Acceso remoto (app instalada)": [
        "acceso remoto", "descargó una aplicación", "instalar una app",
        "instalaron una aplicación", "ultra vnc", "anydesk", "teamviewer",
        "tomaron control", "control de su teléfono", "control de su celular",
        "control remoto", "su celular se bloqueó", "bloquearon el celular",
        "descargó el link", "descargó el enlace",
    ],
    "Préstamo fraudulento": [
        "préstamo a su nombre", "préstamo sin su consentimiento",
        "préstamo no autorizado", "solicitaron un préstamo",
        "tramitaron un préstamo", "aprobaron un préstamo",
        "préstamo que no solicitó", "crédito a su nombre",
        "crédito sin su consentimiento", "adelanto de haberes",
        "descubrió un préstamo",
    ],
    "Estafa en redes sociales": [
        "publicidad en facebook", "publicidad en instagram", "publicidad engañosa",
        "cuenta de instagram", "cuenta de facebook", "perfil falso",
        "anuncio en facebook", "anuncio en instagram", "redes sociales",
        "página de facebook", "grupo de facebook",
    ],
    "Cuento del tío digital": [
        "se hizo pasar por su", "haciéndose pasar por su",
        "suplantó la identidad", "dijo ser su", "fingió ser su",
        "pasó por su hijo", "pasó por su hija", "cambió de número",
        "necesitaba dinero urgente", "dinero urgente para",
    ],
}

KW_DENUNCIA = [
    "denuncia", "constancia policial", "actuaciones policiales",
    "certificado de actuaciones", "denunciante", "damnificad"
]

# ── BigQuery ─────────────────────────────────────────────────────────────────

def get_case_ids():
    # Ventana: últimos 30 días
    end   = datetime.today().replace(hour=23, minute=59, second=59)
    start = (end - timedelta(days=30)).replace(hour=0, minute=0, second=0)

    query = f"""
    SELECT DISTINCT
      a.REPATRI_CASE_ID,
      a.SITE,
      a.CREATED_AT_DATETIME,
      EXTRACT(MONTH FROM a.CREATED_AT_DATETIME) AS MES,
      a.CASE_RESULT_REASON,
      inv.CUST_ID        AS CUST_CUST_ID,
      txn.TRANSACTION_ID AS PAY_PAYMENT_ID,
      txn.AMOUNT         AS MONTO_ARS,
      txn.PRODUCT_TYPE   AS PRODUCT_TYPE,
      txn.PRODUCT_METHOD AS PRODUCT_METHOD,
      txn.ORIGIN_INSTITUTION AS BANCO_ORIGEN
    FROM `{BQ_PROJECT}.WHOWNER.LK_FPR_REPATRI_CASE` a
    CROSS JOIN UNNEST(INVOLVED_CUSTS)         AS inv
    CROSS JOIN UNNEST(CONTESTED_TRANSACTIONS) AS txn
    WHERE a.CREATED_AT_DATETIME BETWEEN '{start.strftime("%Y-%m-%d")}' AND '{end.strftime("%Y-%m-%d")}'
      AND a.FLOW_TYPE       = 'REFUND'
      AND a.CURRENT_STATUS  = 'CLOSED'
      AND a.SITE            = 'MLA'
      AND txn.PRODUCT_TYPE   = 'bank_transfer'
      AND txn.PRODUCT_METHOD = 'cvu'
    """

    client = bigquery.Client(project=BQ_PROJECT)
    print(f"Corriendo query BQ ({start.date()} → {end.date()})...")
    rows = list(client.query(query).result())
    print(f"  {len(rows)} filas obtenidas")
    return rows

# ── API Attachments ───────────────────────────────────────────────────────────

def buscar_adjuntos(case_id):
    url = f"{API_BASE}/v2/files/search/{case_id}"
    for intento in range(3):
        try:
            r = requests.get(url, headers=API_HEADERS, timeout=30)
            if r.status_code == 200:
                return r.json()
            elif r.status_code in (429, 500, 502, 503):
                time.sleep(2 ** intento)
            else:
                return []
        except requests.exceptions.RequestException:
            time.sleep(2)
    return []

def preguntar_adjunto(uuid):
    url = f"{API_BASE}/v2/files/ask"
    payload = {
        "user_prompt": USER_PROMPT,
        "effort": "HIGH",
        "files": [{"source": "FPR_ATTACHMENTS", "reference": uuid}]
    }
    for intento in range(3):
        try:
            r = requests.post(
                url,
                headers={**API_HEADERS, "Content-Type": "application/json"},
                data=json.dumps(payload),
                timeout=180
            )
            if r.status_code == 200:
                return r.json().get("ai_response", "")
            elif r.status_code in (429, 500, 502, 503):
                time.sleep([2, 5, 15][intento])
            else:
                return f"ERROR {r.status_code}"
        except requests.exceptions.RequestException as e:
            time.sleep(5)
    return "ERROR: max reintentos"

# ── Clasificación ─────────────────────────────────────────────────────────────

def es_denuncia(texto):
    t = texto.lower()
    return any(kw in t for kw in KW_DENUNCIA)

def clasificar_fraude(texto):
    t = texto.lower()
    scores = {}
    for cat, kws in CATEGORIAS.items():
        hits = [(kw, t.find(kw)) for kw in kws if kw in t]
        if hits:
            primer_idx = min(idx for _, idx in hits)
            scores[cat] = (len(hits), -primer_idx)
    if not scores:
        return "No clasificado"
    return max(scores, key=lambda c: scores[c])

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=== Monitor Repatriación MLA ===")
    print(f"Inicio: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")

    # 1. Obtener casos de BQ
    bq_rows = get_case_ids()

    # Agrupar metadata por case_id
    metadata = defaultdict(dict)
    for row in bq_rows:
        cid = str(row.REPATRI_CASE_ID)
        metadata[cid] = {
            "site":           row.SITE,
            "created_at":     str(row.CREATED_AT_DATETIME)[:10],
            "mes":            row.MES,
            "case_result":    row.CASE_RESULT_REASON,
            "cust_id":        row.CUST_CUST_ID,
            "payment_id":     row.PAY_PAYMENT_ID,
            "monto_ars":      row.MONTO_ARS,
            "product_type":   row.PRODUCT_TYPE,
            "product_method": row.PRODUCT_METHOD,
            "banco_origen":   row.BANCO_ORIGEN,
        }

    case_ids = list(metadata.keys())
    print(f"Casos únicos: {len(case_ids)}\n")

    resultados = []

    for i, case_id in enumerate(case_ids, 1):
        print(f"[{i}/{len(case_ids)}] Caso {case_id}", end=" ")
        meta = metadata[case_id]

        adjuntos = buscar_adjuntos(case_id)

        if not adjuntos:
            print("→ sin adjunto")
            resultados.append({**meta, "repatri_case_id": case_id,
                                "tiene_adjunto": "NO", "tiene_denuncia": "NO",
                                "tipo_fraude": "", "ai_response": ""})
            time.sleep(0.3)
            continue

        texto_denuncia = None
        for adjunto in adjuntos:
            uuid     = adjunto.get("uuid", "")
            filename = adjunto.get("filename", "")
            print(f"→ {filename}", end=" ")

            ai_response = preguntar_adjunto(uuid)

            if es_denuncia(ai_response) and not texto_denuncia:
                texto_denuncia = ai_response

            time.sleep(1)

        tiene_denuncia = texto_denuncia is not None
        tipo_fraude    = clasificar_fraude(texto_denuncia) if tiene_denuncia else ""
        print(f"→ {'denuncia: ' + tipo_fraude if tiene_denuncia else 'sin denuncia'}")

        resultados.append({
            **meta,
            "repatri_case_id": case_id,
            "tiene_adjunto":   "SI",
            "tiene_denuncia":  "SI" if tiene_denuncia else "NO",
            "tipo_fraude":     tipo_fraude,
            "ai_response":     texto_denuncia or "",
        })

    # 2. Guardar CSV
    fecha   = datetime.today().strftime("%Y%m%d")
    outfile = OUTPUT_DIR / f"repatri_monitor_{fecha}.csv"
    campos  = [
        "repatri_case_id", "site", "created_at", "mes", "case_result",
        "cust_id", "payment_id", "monto_ars", "product_type", "product_method",
        "banco_origen", "tiene_adjunto", "tiene_denuncia", "tipo_fraude", "ai_response"
    ]
    with open(outfile, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=campos)
        writer.writeheader()
        writer.writerows(resultados)

    # 3. Resumen
    print(f"\n{'='*40}")
    print(f"Total casos:           {len(resultados)}")
    print(f"Con adjunto:           {sum(1 for r in resultados if r['tiene_adjunto']=='SI')}")
    print(f"Con denuncia policial: {sum(1 for r in resultados if r['tiene_denuncia']=='SI')}")
    print(f"\nPor tipo de fraude:")
    from collections import Counter
    for tipo, cnt in Counter(r["tipo_fraude"] for r in resultados if r["tiene_denuncia"]=="SI").most_common():
        print(f"  {tipo:<40} {cnt}")
    print(f"\nCSV guardado: {outfile}")
    print(f"Fin: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    return outfile

if __name__ == "__main__":
    main()
