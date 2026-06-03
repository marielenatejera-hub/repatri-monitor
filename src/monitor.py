import csv
import json
import re
import time
import requests
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

import gspread
from google.auth import default as google_auth_default
from google.cloud import bigquery

# ── Configuración ────────────────────────────────────────────────────────────

BQ_PROJECT    = "meli-bi-data"
SHEETS_ID     = "1slABqtcAmMVOVGq1HEEeN31geH-c2E5hSk3pVIdp47M"
SHEETS_TAB    = "Datos"
API_BASE    = "https://fpr-cross.melioffice.com/attachments"
API_HEADERS = {"X-Scope": "fraud", "X-External-System": "FPR_REPATRIATION"}
USER_PROMPT = (
    "Analizá el documento y respondé en formato JSON con estos campos:\n"
    "- es_denuncia_policial: true/false\n"
    "- tipo_fraude: string con el tipo de estafa en pocas palabras (ej: 'vishing', 'phishing whatsapp', 'préstamo fraudulento', etc.). "
    "Si no es denuncia, dejá vacío.\n"
    "- resumen: una oración resumiendo el motivo\n"
    "- montos: lista de montos mencionados\n"
    "- instituciones: lista de instituciones involucradas\n"
    "Respondé SOLO con el JSON, sin texto adicional."
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
    # Ventana: últimas 3 semanas completas (lunes a domingo)
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
    WHERE a.CREATED_AT_DATETIME >= DATETIME(
            DATE_SUB(DATE_TRUNC(DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY), WEEK(MONDAY)), INTERVAL 3 WEEK)
          )
      AND a.CREATED_AT_DATETIME <  DATETIME(
            DATE_ADD(DATE_TRUNC(DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY), WEEK(MONDAY)), INTERVAL 1 WEEK)
          )
      AND a.FLOW_TYPE       = 'REFUND'
      AND a.CURRENT_STATUS  = 'CLOSED'
      AND a.SITE            = 'MLA'
      AND txn.PRODUCT_TYPE   = 'bank_transfer'
      AND txn.PRODUCT_METHOD = 'cvu'
    """

    client = bigquery.Client(project=BQ_PROJECT)
    print(f"Corriendo query BQ (últimas 3 semanas completas)...")
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

def parsear_respuesta_ia(texto):
    """Intenta parsear el JSON que devuelve la IA."""
    try:
        # A veces la IA envuelve el JSON en ```json ... ```
        match = re.search(r'\{.*\}', texto, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception:
        pass
    return {}

def es_denuncia(texto):
    parsed = parsear_respuesta_ia(texto)
    if parsed:
        return bool(parsed.get("es_denuncia_policial", False))
    # Fallback a keywords si la IA no devolvió JSON
    t = texto.lower()
    return any(kw in t for kw in KW_DENUNCIA)

def clasificar_fraude(texto):
    parsed = parsear_respuesta_ia(texto)
    if parsed:
        return parsed.get("tipo_fraude", "No clasificado") or "No clasificado"
    return "No clasificado"

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

    # 4. Upload a Google Sheets
    try:
        print("Subiendo a Google Sheets...")
        creds, _ = google_auth_default(scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ])
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(SHEETS_ID)

        # Crear o limpiar el tab
        try:
            ws = sh.worksheet(SHEETS_TAB)
            ws.clear()
        except gspread.exceptions.WorksheetNotFound:
            ws = sh.add_worksheet(title=SHEETS_TAB, rows=5000, cols=20)

        # Escribir encabezado + datos
        header = campos
        filas  = [[str(r.get(c, "")) for c in campos] for r in resultados]
        ws.update([header] + filas)
        print(f"Google Sheets actualizado: {len(resultados)} filas")
    except Exception as e:
        print(f"Error al subir a Sheets: {e}")

    # 5. Generar HTML con reporte_repatri.py
    try:
        print("Generando reporte HTML...")
        import subprocess
        resultado = subprocess.run(
            ["python3", str(Path(__file__).parent / "reporte.py")],
            capture_output=True, text=True
        )
        print(resultado.stdout)
        if resultado.returncode != 0:
            print(f"Error en reporte.py: {resultado.stderr}")
    except Exception as e:
        print(f"Error al generar HTML: {e}")

    print(f"Fin: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    return outfile


# ── Generador HTML ────────────────────────────────────────────────────────────

MELI_BLUE   = "#3483FA"
MELI_GREEN  = "#00A650"
MELI_ORANGE = "#FF7733"
MELI_RED    = "#F23D4F"
MELI_LBLUE  = "#2968C8"
MELI_DARK   = "#333333"
MELI_YELLOW = "#FFE600"
MELI_GRAY   = "#EBEBEB"

CAT_CFG = {
    "Llamada entrante del estafador": {"emoji": "📞", "color": MELI_BLUE},
    "Búsqueda activa de contacto":    {"emoji": "🔍", "color": MELI_LBLUE},
    "Acceso remoto (app instalada)":  {"emoji": "📲", "color": MELI_ORANGE},
    "Préstamo fraudulento":           {"emoji": "💳", "color": MELI_RED},
    "Estafa en redes sociales":       {"emoji": "📱", "color": MELI_GREEN},
    "Cuento del tío digital":         {"emoji": "👤", "color": MELI_DARK},
    "No clasificado":                 {"emoji": "❓", "color": "#CCCCCC"},
}

BANCOS_EMISORES = [
    ("Banco Nación",    ["banco nación","banco nacion","bna+","bna "]),
    ("Galicia",         ["galicia"]),
    ("Macro",           ["banco macro"," macro"]),
    ("Santander",       ["santander"]),
    ("BBVA",            ["bbva"]),
    ("Banco Provincia", ["banco provincia","bapro"]),
    ("Brubank",         ["brubank"]),
    ("Nu",              ["nubank"," nu "]),
    ("Naranja X",       ["naranja x","naranjax"]),
    ("ICBC",            ["icbc"]),
    ("Itaú",            ["itaú","itau"]),
    ("Supervielle",     ["supervielle"]),
]

MONTO_HTML_RE = re.compile(r'\$\s?([\d]{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?)')

def _parsear_monto(s):
    s = s.strip().replace(" ","")
    m = re.match(r'^([\d.,]+)[.,](\d{1,2})$', s)
    if m:
        return float(re.sub(r'[.,]','',m.group(1))+'.'+m.group(2))
    return float(re.sub(r'[.,]','',s))

def _extraer_montos(texto):
    vals = []
    for match in MONTO_HTML_RE.finditer(texto):
        try:
            v = _parsear_monto(match.group(1))
            if 0 < v <= 10_000_000:
                vals.append(v)
        except: pass
    return vals

def _tipo_cambio():
    try:
        r    = requests.get("https://dolarapi.com/v1/dolares/oficial", timeout=5)
        data = r.json()
        return (data["compra"] + data["venta"]) / 2, data.get("fechaActualizacion","")[:10]
    except:
        return 1400, "N/D"

def generar_html(resultados, csv_path):
    from collections import Counter as Ctr

    tc, tc_fecha = _tipo_cambio()

    def fmt_usd(n): return f"USD {n/tc:,.0f}".replace(",",".")

    kw_denuncia = ["denuncia","constancia policial","actuaciones policiales","denunciante","damnificad"]
    denuncias   = [r for r in resultados if r.get("tiene_denuncia") == "SI"]
    total_casos = len(resultados)

    # Montos y bancos
    counter_fraude = Ctr(r["tipo_fraude"] for r in denuncias if r["tipo_fraude"])
    montos_por_cat = {}
    counter_bancos = Ctr()
    todos_montos   = []

    for r in denuncias:
        texto  = r.get("ai_response","")
        cat    = r.get("tipo_fraude","No clasificado") or "No clasificado"
        montos = _extraer_montos(texto)
        montos_por_cat.setdefault(cat, []).extend(montos)
        todos_montos.extend(montos)
        for nombre, variantes in BANCOS_EMISORES:
            if any(v in texto.lower() for v in variantes):
                counter_bancos[nombre] += 1

    total_monto = sum(todos_montos)
    mediana     = sorted(todos_montos)[len(todos_montos)//2] if todos_montos else 0
    con_adjunto = sum(1 for r in resultados if r.get("tiene_adjunto") == "SI")

    # Ordenar cats por cantidad de denuncias
    max_cnt = max(counter_fraude.values()) if counter_fraude else 1
    cats_ordenadas = sorted(CAT_CFG.items(), key=lambda x: counter_fraude.get(x[0], 0), reverse=True)

    def hbar(label, emoji, color, cnt, monto_str=""):
        pct = cnt / max_cnt * 100 if max_cnt else 0
        return f"""
        <div class="hbar-row">
          <div class="hbar-label">{emoji} {label}</div>
          <div class="hbar-wrap">
            <div class="hbar-track">
              <div class="hbar-bar" style="width:{pct:.1f}%;background:{color}">
                <span class="hbar-val">{cnt}</span>
              </div>
            </div>
            <span class="hbar-monto">{monto_str}</span>
          </div>
        </div>"""

    fraude_html = '<div class="hbar-chart">'
    for cat, cfg in cats_ordenadas:
        cnt   = counter_fraude.get(cat, 0)
        total = sum(montos_por_cat.get(cat, []))
        fraude_html += hbar(cat, cfg["emoji"], cfg["color"], cnt, fmt_usd(total) if total else "")
    nc = counter_fraude.get("No clasificado", 0)
    fraude_html += hbar("No clasificado", "❓", "#CCCCCC", nc)
    fraude_html += "</div>"

    max_banco = max(counter_bancos.values()) if counter_bancos else 1
    bancos_html = '<div class="hbar-chart">'
    for banco, cnt in counter_bancos.most_common(8):
        pct = cnt / max_banco * 100
        bancos_html += f"""
        <div class="hbar-row">
          <div class="hbar-label">{banco}</div>
          <div class="hbar-wrap">
            <div class="hbar-track">
              <div class="hbar-bar" style="width:{pct:.1f}%;background:{MELI_BLUE}">
                <span class="hbar-val">{cnt}</span>
              </div>
            </div>
          </div>
        </div>"""
    bancos_html += "</div>"

    def kpi(val, lbl, sub, color):
        return f"""<div class="kpi-card">
          <div class="kpi-val" style="color:{color}">{val}</div>
          <div class="kpi-lbl">{lbl}</div>
          <div class="kpi-sub">{sub}</div>
        </div>"""

    kpis = (
        kpi(f"{total_casos:,}".replace(",","."), "Casos totales", "MLA · CVU · bank transfer", MELI_BLUE) +
        kpi(f"{con_adjunto:,}".replace(",","."), "Con adjunto", f"{con_adjunto/total_casos*100:.0f}% del total" if total_casos else "-", MELI_GREEN) +
        kpi(str(len(denuncias)), "Denuncias policiales", f"{len(denuncias)/total_casos*100:.0f}% del total" if total_casos else "-", MELI_ORANGE) +
        kpi(fmt_usd(total_monto), "Total reclamado", f"Mediana {fmt_usd(mediana)}", MELI_RED)
    )

    fecha_reporte = datetime.today().strftime("%d/%m/%Y")

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <title>Clasificador de STO — MLA</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f5f5;color:{MELI_DARK};padding:32px}}
    .header{{max-width:1200px;margin:0 auto 28px;display:flex;justify-content:space-between;align-items:flex-end;border-bottom:3px solid {MELI_YELLOW};padding-bottom:16px}}
    .header h1{{font-size:26px;font-weight:800}}
    .header .sub{{color:#666;font-size:13px;margin-top:4px}}
    .header-right{{text-align:right;font-size:12px;color:#888;line-height:1.8}}
    .header-right strong{{color:{MELI_DARK}}}
    .tc-fuente{{font-size:11px;color:#aaa}}
    .kpi-row{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;max-width:1200px;margin:0 auto 28px}}
    .kpi-card{{background:#fff;border-radius:12px;padding:20px 24px;box-shadow:0 1px 6px rgba(0,0,0,0.07);text-align:center}}
    .kpi-val{{font-size:26px;font-weight:800;margin-bottom:6px}}
    .kpi-lbl{{font-size:13px;color:#555;font-weight:600}}
    .kpi-sub{{font-size:11px;color:#aaa;margin-top:3px}}
    .section{{max-width:1200px;margin:0 auto 28px}}
    .section-title{{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:#999;margin-bottom:14px}}
    .summary-grid{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}
    .card{{background:#fff;border-radius:12px;padding:22px 24px;box-shadow:0 1px 6px rgba(0,0,0,0.07)}}
    .hbar-chart{{display:flex;flex-direction:column;gap:10px}}
    .hbar-row{{display:flex;flex-direction:column;gap:4px}}
    .hbar-label{{font-size:12px;font-weight:600;color:{MELI_DARK}}}
    .hbar-wrap{{display:flex;align-items:center;gap:8px}}
    .hbar-track{{flex:1;background:{MELI_GRAY};border-radius:6px;height:28px;overflow:hidden}}
    .hbar-bar{{height:100%;border-radius:6px;display:flex;align-items:center;padding-left:8px;min-width:32px}}
    .hbar-val{{font-size:12px;font-weight:800;color:#fff;text-shadow:0 1px 2px rgba(0,0,0,0.3);white-space:nowrap}}
    .hbar-monto{{font-size:11px;color:#888;white-space:nowrap;min-width:80px}}
  </style>
</head>
<body>
  <div class="header">
    <div>
      <h1>Clasificador de STO — MLA</h1>
      <div class="sub">Bank Transfer · CVU · Últimas 3 semanas</div>
    </div>
    <div class="header-right">
      Última actualización: <strong>{fecha_reporte}</strong><br>
      Tipo de cambio oficial: <strong>1 USD = ${tc:,.0f} ARS</strong><br>
      <span class="tc-fuente">Fuente: dolarapi.com · BCRA · {tc_fecha}</span>
    </div>
  </div>
  <div class="kpi-row">{kpis}</div>
  <div class="section">
    <div class="summary-grid">
      <div class="card">
        <div class="section-title">Casos por tipo de fraude</div>
        {fraude_html}
      </div>
      <div class="card">
        <div class="section-title">Bancos más frecuentes en denuncias</div>
        {bancos_html}
      </div>
    </div>
  </div>
</body>
</html>"""

    html_path = OUTPUT_DIR / "reporte.html"
    html_path.write_text(html, encoding="utf-8")
    return html_path


if __name__ == "__main__":
    main()
