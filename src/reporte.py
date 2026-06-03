import csv
import json
import re
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime

OUTPUT_DIR  = Path.home() / "Documents" / "repatri-monitor" / "output"
OUTPUT_HTML = OUTPUT_DIR / "reporte.html"

# Usar el CSV más reciente de la carpeta output/
csvs = sorted(OUTPUT_DIR.glob("repatri_monitor_*.csv"), reverse=True)
if not csvs:
    raise FileNotFoundError("No se encontró ningún CSV en output/. Corré el monitor primero.")
INPUT_CSV = csvs[0]
print(f"Leyendo: {INPUT_CSV.name}")

# ── Paleta Meli ───────────────────────────────────────────────────────────────
MELI_BLUE    = "#3483FA"
MELI_YELLOW  = "#FFE600"
MELI_GREEN   = "#00A650"
MELI_LBLUE   = "#2968C8"
MELI_ORANGE  = "#FF7733"
MELI_RED     = "#F23D4F"
MELI_DARK    = "#333333"
MELI_GRAY    = "#EBEBEB"

# ── Config ────────────────────────────────────────────────────────────────────
# Fechas y total se calculan dinámicamente desde el CSV
FECHA_INICIO = None
FECHA_FIN    = None
TOTAL_CASOS  = None

def obtener_tipo_cambio():
    try:
        import requests as req
        r    = req.get("https://dolarapi.com/v1/dolares/oficial", timeout=5)
        data = r.json()
        tc   = (data["compra"] + data["venta"]) / 2
        print(f"Tipo de cambio oficial: ${tc:,.0f} ARS/USD")
        return tc, data.get("fechaActualizacion","")[:10]
    except Exception as e:
        print(f"Sin tipo de cambio ({e}), usando $1.400")
        return 1400, "N/D"

TIPO_CAMBIO, TC_FECHA = obtener_tipo_cambio()

# ── Categorías ────────────────────────────────────────────────────────────────
CATEGORIAS = {
    "Llamada entrante del estafador": {
        "emoji": "📞",
        "desc": "El estafador llama a la víctima haciéndose pasar por un banco, fintech o empresa de servicios.",
        "kw": ["recibió una llamada","recibió un llamado","lo llamaron","la llamaron","llamada telefónica","llamada de un"],
        "color": MELI_BLUE,
        "ejemplos": [
            ("2350033", "Silvina Paola Avila fue víctima de estafa telefónica. Un hombre se hizo pasar por cliente y contactó por WhatsApp para una supuesta compra. Le pidió datos bancarios y le envió un comprobante falso por un monto mayor al acordado, convenciéndola de devolver la diferencia mediante transferencias reales a cuentas de terceros."),
            ("2344680", "Claudia Mabel Polverigiani fue contactada telefónicamente por alguien que se hizo pasar por empleado de PAMI, tras haber ingresado sus datos en una publicidad falsa. La guiaron para instalar una aplicación en su teléfono y obtener credenciales de homebanking, vaciando sus cuentas y solicitando un préstamo a su nombre."),
        ],
    },
    "Búsqueda activa de contacto": {
        "emoji": "🔍",
        "desc": "La víctima busca en Google o redes el número de atención de una entidad y contacta sin saberlo a un estafador.",
        "kw": ["buscó en google","buscó el número","buscó en internet","al buscar","encontró en google","ingresó a un enlace"],
        "color": MELI_LBLUE,
        "ejemplos": [
            ("2372195", "Juan Alberto Morales buscó en internet el número de atención de VISA tras notar una transferencia desconocida en su cuenta. Fue contactado por WhatsApp por un estafador que se presentó como representante de la empresa. Con la excusa de unificar cuentas y reintegrar el dinero, obtuvo sus credenciales bancarias y realizó múltiples transferencias no autorizadas."),
            ("2362412", "María Susana Urbani buscó en Google el número de Tarjeta Naranja para consultar un problema. Los estafadores le solicitaron datos personales y realizaron una videollamada para fotografiar su rostro. Con esa información ingresaron a su Home Banking, sacaron un préstamo personal a su nombre y transfirieron la totalidad de los fondos."),
        ],
    },
    "Acceso remoto (app instalada)": {
        "emoji": "📲",
        "desc": "Los estafadores convencen a la víctima de instalar una app de control remoto (AnyDesk, Ultra VNC, Supremo) para tomar el control del dispositivo.",
        "kw": ["acceso remoto","ultra vnc","anydesk","tomaron control","control de su teléfono","descargó una aplicación","supremo"],
        "color": MELI_ORANGE,
        "ejemplos": [
            ("2371331", "Marta Yolanda Maidana intentó descargar un recibo en una página falsa del IPS y dejó su número de contacto. Fue contactada por WhatsApp y durante una videollamada la indujeron a compartir pantalla y descargar 'SUPREMO MOBILE'. Los estafadores tomaron control total de su dispositivo, robaron credenciales de homebanking, solicitaron préstamos a su nombre y vaciaron todas sus cuentas."),
            ("2366252", "La víctima recibió una llamada de WhatsApp de alguien que se presentó como empleado de Movistar ofreciendo beneficios para jubilados. La convencieron de descargar ULTRA VNC mediante un enlace enviado por el mismo canal. Una vez instalada la app, los estafadores tomaron control remoto del teléfono y realizaron múltiples transferencias bancarias sin su consentimiento."),
        ],
    },
    "Préstamo fraudulento": {
        "emoji": "💳",
        "desc": "Los estafadores acceden a las cuentas de la víctima y solicitan préstamos a su nombre sin consentimiento, transfiriendo el dinero de inmediato.",
        "kw": ["préstamo a su nombre","préstamo sin su consentimiento","préstamo no autorizado","solicitaron un préstamo","adelanto de haberes"],
        "color": MELI_RED,
        "ejemplos": [
            ("2349747", "La víctima publicó muebles en Facebook Marketplace. Un supuesto comprador envió comprobante falso alegando haber transferido un monto mayor por error. Estafadores que se hicieron pasar por personal del Banco Macro la guiaron para solicitar un préstamo y devolver la diferencia, transfiriendo los fondos a cuentas de terceros que ella desconocía."),
            ("2362412", "María Susana Urbani buscó el número de Tarjeta Naranja en Google. Los estafadores obtuvieron sus datos biométricos mediante videollamada, ingresaron a su Home Banking y solicitaron un préstamo personal a su nombre sin consentimiento alguno, transfiriendo de inmediato la totalidad de los fondos obtenidos."),
        ],
    },
    "Estafa en redes sociales": {
        "emoji": "📱",
        "desc": "El fraude se origina en publicidades o perfiles falsos en Facebook, Instagram u otras redes, ofreciendo inversiones, beneficios o productos inexistentes.",
        "kw": ["publicidad en facebook","publicidad en instagram","redes sociales","perfil falso","anuncio en facebook","facebook marketplace","marketplace"],
        "color": MELI_GREEN,
        "ejemplos": [
            ("2379686", "Nadya Andrea Carauni contactó a la supuesta 'Academia de Negocios Vanguard Group' a través de un anuncio en redes sociales. Bajo instrucción de alguien identificado como 'Mariana López', fue inducida a depositar dinero en una billetera virtual para adquirir 'carteras de acciones', con promesa de altos rendimientos que nunca se materializaron y sin posibilidad de recuperar el capital invertido."),
            ("2350163", "María Rosa Alaniz fue atraída por una publicidad falsa en Facebook que ofrecía computadoras gratuitas para jubilados. Al manifestar interés, recibió una llamada telefónica donde terceros la guiaron para acceder a su teléfono y cuentas bancarias, solicitando un préstamo a su nombre sin consentimiento y transfiriendo el dinero de forma inmediata."),
        ],
    },
    "Cuento del tío digital": {
        "emoji": "👤",
        "desc": "Alguien se hace pasar por un familiar, amigo o conocido vía WhatsApp para solicitar dinero urgente con una excusa fabricada.",
        "kw": ["se hizo pasar por su","haciéndose pasar por su","dijo ser su","cambió de número","necesitaba dinero urgente","consuegro","amiga","hijo","hija"],
        "color": MELI_DARK,
        "ejemplos": [
            ("2356437", "José Ceferino Saa recibió una llamada de un desconocido que se identificó como su consuegro, alegando que se le había roto el auto y necesitaba asistencia de grúa urgente. Al comunicarse con el supuesto seguro, le exigieron un pago anticipado para enviar el servicio. Saa realizó la transferencia y luego verificó que su consuegro real se encontraba en su domicilio y desconocía completamente el hecho."),
            ("2362902", "Ester Lidia Fiore recibió mensajes de WhatsApp de alguien que se hizo pasar por su amiga María Teresa Cardenas, solicitando dinero de urgencia desde un número desconocido. Bajo ese engaño, realizó dos transferencias bancarias a nombre de una tercera persona antes de sospechar. Al llamar directamente a su amiga, confirmó que el teléfono había sido hackeado y que ella nunca había solicitado dinero."),
        ],
    },
    "Robo de dispositivo": {
        "emoji": "📵",
        "desc": "El fraude ocurre tras el robo o hurto del celular de la víctima, aprovechando el acceso físico para realizar transferencias no autorizadas.",
        "kw": ["robo de celular","hurto de dispositivo","robo a mano armada","vaciamiento de cuentas por acceso físico"],
        "color": "#7F8C8D",
        "ejemplos": [],
    },
}

KW_DENUNCIA = ["denuncia","constancia policial","actuaciones policiales","denunciante","damnificad"]

# Solo bancos emisores — excluimos Mercado Pago que es siempre receptor
BANCOS = [
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

MONTO_RE = re.compile(r'\$\s?([\d]{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?)')

def parsear_monto(s):
    s = s.strip().replace(" ","")
    m = re.match(r'^([\d.,]+)[.,](\d{1,2})$', s)
    if m:
        return float(re.sub(r'[.,]','',m.group(1))+'.'+m.group(2))
    return float(re.sub(r'[.,]','',s))

def extraer_montos(texto):
    vals = []
    for match in MONTO_RE.finditer(texto):
        try:
            v = parsear_monto(match.group(1))
            if 0 < v <= 10_000_000:
                vals.append(v)
        except: pass
    return vals

MAPEO_IA = {
    "Llamada entrante del estafador": [
        "vishing", "estafa telefónica", "falso soporte", "falso empleado",
        "llamada", "falso banco", "falso accidente",
    ],
    "Búsqueda activa de contacto": [
        "phishing whatsapp", "phishing", "buscó en google", "buscó el número",
        "falso asesor", "número falso", "pantalla compartida",
        "compra fraudulenta por whatsapp", "estafa compra de dólares",
        "venta de dólares", "compra fraudulenta instagram",
    ],
    "Acceso remoto (app instalada)": [
        "acceso remoto", "anydesk", "ultra vnc", "supremo", "malware",
        "toma de control", "hackeo", "acceso no autorizado",
        "defraudación informática",
    ],
    "Préstamo fraudulento": [
        "préstamo fraudulento", "préstamo no autorizado", "crédito fraudulento",
        "transferencia no autorizada", "transferencias no autorizadas",
        "transferencia no reconocida", "transferencias no reconocidas",
        "movimientos no reconocidos", "movimientos bancarios no reconocidos",
        "fraude home banking", "fraude bancario", "fraude por home banking",
        "vaciamiento de cuenta", "transferencia fraudulenta",
        "transferencias fraudulentas", "transferencias bancarias no autorizadas",
        "transferencias bancarias no reconocidas", "transferencia por error",
        "estafa por transferencia",
    ],
    "Estafa en redes sociales": [
        "falsa venta online", "falsa venta en redes", "falsa venta por redes",
        "falso alquiler", "estafa alquiler", "falsa venta por whatsapp",
        "marketplace", "compra online", "inversión fraudulenta",
        "plataforma de inversión", "inversión en cripto", "criptomonedas",
        "estafa de inversión", "falsa plataforma", "estafa en compraventa",
        "compraventa falsa", "falsa venta de vehículo", "compraventa de vehículo",
        "falso servicio", "estafa por venta", "estafa por alquiler",
        "estafa genérica", "estafa de compraventa",
    ],
    "Cuento del tío digital": [
        "suplantación de identidad", "cuento del tío", "falso familiar",
        "falso conocido", "hackeo de whatsapp", "robo de whatsapp",
        "extorsión", "curandero", "falso curandero", "estafa espiritual",
    ],
    "Robo de dispositivo": [
        "robo de celular", "hurto de dispositivo", "robo a mano armada",
        "robo/hurto", "robo con engaño", "hurto seguido",
        "vaciamiento de cuentas por acceso físico",
    ],
}

def mapear_tipo_ia(tipo_fraude):
    """Mapea el tipo_fraude libre de la IA a una categoría del reporte."""
    t = tipo_fraude.lower()
    for cat, terminos in MAPEO_IA.items():
        if any(term in t for term in terminos):
            return cat
    return tipo_fraude  # devuelve el valor original si no matchea

def clasificar(texto):
    t = texto.lower()
    for cat, cfg in CATEGORIAS.items():
        if any(kw in t for kw in cfg["kw"]):
            return cat
    return "No clasificado"

def fmt_usd(n): return f"USD {n/TIPO_CAMBIO:,.0f}".replace(",",".")

# ── Procesar ──────────────────────────────────────────────────────────────────

with open(INPUT_CSV, newline="", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))

# Calcular fechas y total dinámicamente
TOTAL_CASOS  = len(rows)
fechas = [r.get("created_at","") for r in rows if r.get("created_at","")]
FECHA_INICIO = min(fechas)[:10] if fechas else "N/D"
FECHA_FIN    = max(fechas)[:10] if fechas else "N/D"

con_adjunto    = [r for r in rows if r.get("tiene_adjunto") == "SI"]
denuncias      = [r for r in rows if r.get("tiene_denuncia") == "SI"]
counter_fraude = Counter()
montos_por_cat = {}
todos_montos   = []
counter_bancos = Counter()

for r in denuncias:
    texto  = r.get("ai_response", "")
    # Usar tipo_fraude del CSV (clasificado por IA), mapearlo a categoría
    tipo_ia = r.get("tipo_fraude", "").strip()
    if tipo_ia and tipo_ia != "No clasificado":
        cat = mapear_tipo_ia(tipo_ia)
    else:
        cat = clasificar(texto) or "No clasificado"
    montos = extraer_montos(texto)
    bancos = [nombre for nombre, variantes in BANCOS if any(v in texto.lower() for v in variantes)]
    counter_fraude[cat] += 1
    montos_por_cat.setdefault(cat, []).extend(montos)
    todos_montos.extend(montos)
    for b in bancos: counter_bancos[b] += 1

total_monto = sum(todos_montos)
mediana     = sorted(todos_montos)[len(todos_montos)//2] if todos_montos else 0

# Generar ejemplos reales del CSV por categoría
def formatear_respuesta_ia(texto):
    """Parsea la respuesta JSON de la IA y la devuelve como HTML con bullets."""
    try:
        import json as _json
        match = re.search(r'\{.*\}', texto, re.DOTALL)
        if match:
            data = _json.loads(match.group())
            items = []
            if data.get("resumen"):
                items.append(f"<b>Resumen:</b> {data['resumen']}")
            if data.get("montos"):
                montos = data["montos"]
                if isinstance(montos, list):
                    montos_str = ", ".join(f"${m}" for m in montos if m)
                else:
                    montos_str = str(montos)
                items.append(f"<b>Montos:</b> {montos_str}")
            if data.get("instituciones"):
                insts = data["instituciones"]
                if isinstance(insts, list):
                    insts_str = ", ".join(str(i) for i in insts if i)
                else:
                    insts_str = str(insts)
                items.append(f"<b>Instituciones:</b> {insts_str}")
            if items:
                return "<ul style='margin:4px 0 0 16px;padding:0'>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>"
    except Exception:
        pass
    # Fallback: texto limpio sin JSON
    limpio = re.sub(r'```json|```', '', texto).replace("\n", " ").replace("**", "").strip()
    return limpio

ejemplos_reales = {}
casos_por_cat = defaultdict(list)
for r in denuncias:
    tipo_ia = r.get("tipo_fraude", "").strip()
    cat = mapear_tipo_ia(tipo_ia) if tipo_ia and tipo_ia != "No clasificado" else clasificar(r.get("ai_response",""))
    casos_por_cat[cat].append(r)

for cat, casos in casos_por_cat.items():
    ejemplos = []
    for r in casos[:2]:
        texto = r.get("ai_response", "")
        resumen = formatear_respuesta_ia(texto)
        ejemplos.append((r["repatri_case_id"], resumen))
    ejemplos_reales[cat] = ejemplos

# Inyectar ejemplos reales en CATEGORIAS
for cat in CATEGORIAS:
    if cat in ejemplos_reales:
        CATEGORIAS[cat]["ejemplos"] = ejemplos_reales[cat]

# Ordenar categorías conocidas por cantidad de denuncias, las dinámicas al final
cats_conocidas = sorted(
    [(cat, cfg) for cat, cfg in CATEGORIAS.items() if cat in counter_fraude],
    key=lambda x: counter_fraude.get(x[0], 0),
    reverse=True
)
# Categorías dinámicas que la IA detectó y no estaban predefinidas
cats_dinamicas = [
    (cat, {"emoji": "🔎", "color": "#7F8C8D", "desc": "Categoría detectada automáticamente por IA", "ejemplos": ejemplos_reales.get(cat, [])})
    for cat in counter_fraude
    if cat not in CATEGORIAS and cat != "No clasificado"
]
cats_ordenadas = cats_conocidas + cats_dinamicas

# ── Tarjetas ──────────────────────────────────────────────────────────────────

def tarjeta_categoria(cat, cfg):
    cnt   = counter_fraude.get(cat, 0)
    ms    = montos_por_cat.get(cat, [])
    total = sum(ms)
    pct_n = cnt / len(denuncias) * 100 if denuncias else 0
    pct_m = total / total_monto * 100 if total_monto else 0
    c     = cfg.get("color", "#7F8C8D")
    txt   = "#fff" if c != MELI_YELLOW else MELI_DARK
    desc  = cfg.get("desc", "Categoría detectada automáticamente por IA")
    emoji = cfg.get("emoji", "🔎")

    ejemplos_html = ""
    for case_id, texto in cfg.get("ejemplos", []):
        ejemplos_html += f"""
        <div class="ejemplo">
          <span class="ejemplo-id">Caso #{case_id}</span>
          <div>{texto}</div>
        </div>"""

    return f"""
    <div class="cat-card">
      <div class="cat-header" style="background:{c}">
        <div class="cat-emoji">{emoji}</div>
        <div class="cat-title" style="color:{txt}">{cat}</div>
        <div class="cat-desc" style="color:{txt}">{desc}</div>
      </div>
      <div class="cat-body">
        <div class="cat-metrics">
          <div class="metric">
            <div class="metric-val" style="color:{c}">{cnt}</div>
            <div class="metric-lbl">Denuncias</div>
          </div>
          <div class="metric">
            <div class="metric-val" style="color:{c}">{fmt_usd(total)}</div>
            <div class="metric-lbl">Total reclamado</div>
          </div>
          <div class="metric">
            <div class="metric-val" style="color:{c}">{pct_n:.0f}%</div>
            <div class="metric-lbl">% denuncias</div>
          </div>
          <div class="metric">
            <div class="metric-val" style="color:{c}">{pct_m:.0f}%</div>
            <div class="metric-lbl">% monto total</div>
          </div>
        </div>
        <div class="ejemplos-title">Ejemplos reales</div>
        {ejemplos_html}
      </div>
    </div>"""

cats_html = "".join(tarjeta_categoria(cat, cfg) for cat, cfg in cats_ordenadas)

# ── Resumen fraude (barras izquierda) ─────────────────────────────────────────

max_cnt = max(counter_fraude.values()) if counter_fraude else 1

# Ordenar por cantidad de denuncias (descendente), No clasificado al final
cats_por_cnt = sorted(
    [(cat, cfg) for cat, cfg in CATEGORIAS.items()],
    key=lambda x: counter_fraude.get(x[0], 0),
    reverse=True
) + [("No clasificado", {"emoji":"❓","color":"#CCCCCC"})]

resumen_fraude_html = '<div class="hbar-chart">'
for cat, cfg in cats_por_cnt:
    cnt   = counter_fraude.get(cat, 0)
    ms    = montos_por_cat.get(cat, [])
    total = sum(ms)
    pct_c = cnt / max_cnt * 100
    color = cfg["color"]
    monto_str = f" · {fmt_usd(total)}" if total > 0 else ""
    resumen_fraude_html += f"""
    <div class="hbar-row">
      <div class="hbar-label">{cfg['emoji']} {cat}</div>
      <div class="hbar-wrap">
        <div class="hbar-bar" style="width:{pct_c:.1f}%;background:{color}">
          <span class="hbar-val">{cnt}</span>
        </div>
        <span class="hbar-monto">{monto_str}</span>
      </div>
    </div>"""
resumen_fraude_html += "</div>"

# ── Barras bancos ─────────────────────────────────────────────────────────────

max_banco_cnt = counter_bancos.most_common(1)[0][1] if counter_bancos else 1
bancos_html = '<div class="hbar-chart">'
for banco, cnt in counter_bancos.most_common(8):
    pct_c = cnt / max_banco_cnt * 100
    bancos_html += f"""
    <div class="hbar-row">
      <div class="hbar-label">{banco}</div>
      <div class="hbar-wrap">
        <div class="hbar-bar" style="width:{pct_c:.1f}%;background:{MELI_BLUE}">
          <span class="hbar-val">{cnt}</span>
        </div>
      </div>
    </div>"""
bancos_html += "</div>"

# ── KPIs ──────────────────────────────────────────────────────────────────────

def kpi(val, lbl, sub, color):
    return f"""
    <div class="kpi-card">
      <div class="kpi-val" style="color:{color}">{val}</div>
      <div class="kpi-lbl">{lbl}</div>
      <div class="kpi-sub">{sub}</div>
    </div>"""

kpis_html = (
    kpi(f"{TOTAL_CASOS:,}".replace(",","."), "Casos totales", f"{FECHA_INICIO} al {FECHA_FIN}", MELI_BLUE) +
    kpi(f"{len(con_adjunto):,}".replace(",","."), "Con adjunto", f"{len(con_adjunto)/TOTAL_CASOS*100:.0f}% del total", MELI_GREEN) +
    kpi(str(len(denuncias)), "Denuncias policiales", f"{len(denuncias)/TOTAL_CASOS*100:.0f}% del total", MELI_ORANGE) +
    kpi(fmt_usd(total_monto), "Total reclamado", f"Mediana {fmt_usd(mediana)}", MELI_RED)
)

# ── HTML ──────────────────────────────────────────────────────────────────────

html = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <title>Clasificador de STO — MLA</title>
  <style>
    * {{ box-sizing:border-box; margin:0; padding:0; }}
    body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
            background:#f5f5f5; color:{MELI_DARK}; padding:32px; }}

    /* Header */
    .header {{ max-width:1200px; margin:0 auto 28px;
               display:flex; justify-content:space-between; align-items:flex-end;
               border-bottom:3px solid {MELI_YELLOW}; padding-bottom:16px; }}
    .header h1 {{ font-size:26px; font-weight:800; color:{MELI_DARK}; }}
    .header .sub {{ color:#666; font-size:13px; margin-top:4px; }}
    .header-right {{ text-align:right; font-size:12px; color:#888; line-height:1.8; }}
    .header-right strong {{ color:{MELI_DARK}; }}
    .tc-fuente {{ font-size:11px; color:#aaa; }}

    /* KPIs */
    .kpi-row {{ display:grid; grid-template-columns:repeat(4,1fr); gap:16px;
                max-width:1200px; margin:0 auto 28px; }}
    .kpi-card {{ background:#fff; border-radius:12px; padding:20px 24px;
                 box-shadow:0 1px 6px rgba(0,0,0,0.07); text-align:center; }}
    .kpi-val {{ font-size:26px; font-weight:800; margin-bottom:6px; }}
    .kpi-lbl {{ font-size:13px; color:#555; font-weight:600; }}
    .kpi-sub {{ font-size:11px; color:#aaa; margin-top:3px; }}

    /* Sección */
    .section {{ max-width:1200px; margin:0 auto 28px; }}
    .section-title {{ font-size:12px; font-weight:700; text-transform:uppercase;
                      letter-spacing:.08em; color:#999; margin-bottom:14px; }}
    .summary-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:20px; }}
    .bar-emoji {{ margin-right:4px; }}

    /* Gráfico barras horizontal */
    .hbar-chart {{ display:flex; flex-direction:column; gap:10px; }}
    .hbar-row   {{ display:flex; flex-direction:column; gap:4px; }}
    .hbar-label {{ font-size:12px; font-weight:600; color:{MELI_DARK}; }}
    .hbar-wrap  {{ background:{MELI_GRAY}; border-radius:6px; height:28px;
                   overflow:hidden; position:relative; }}
    .hbar-bar   {{ height:100%; border-radius:6px; display:flex;
                   align-items:center; padding-left:8px;
                   transition:width .4s ease; min-width:32px; }}
    .hbar-val   {{ font-size:12px; font-weight:800; color:#fff;
                   text-shadow:0 1px 2px rgba(0,0,0,0.3); white-space:nowrap; }}
    .hbar-wrap  {{ display:flex; align-items:center; gap:8px; }}
    .hbar-monto {{ font-size:11px; color:#888; white-space:nowrap; }}

    /* Tarjetas categoría */
    .cat-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:20px; }}
    .cat-card {{ background:#fff; border-radius:12px; overflow:hidden;
                 box-shadow:0 1px 8px rgba(0,0,0,0.08); }}
    .cat-header {{ padding:18px 20px; text-align:center; }}
    .cat-emoji {{ font-size:32px; margin-bottom:8px; }}
    .cat-title {{ font-size:16px; font-weight:800; margin-bottom:6px; }}
    .cat-desc  {{ font-size:12px; opacity:.9; line-height:1.5; }}
    .cat-body  {{ padding:18px 20px; }}
    .cat-metrics {{ display:grid; grid-template-columns:repeat(4,1fr); gap:8px;
                    margin-bottom:18px; text-align:center; }}
    .metric {{ background:#f7f9fc; border-radius:10px; padding:12px 8px; }}
    .metric-val {{ font-size:16px; font-weight:800; }}
    .metric-lbl {{ font-size:10px; color:#999; margin-top:3px;
                   text-transform:uppercase; letter-spacing:.04em; }}
    .ejemplos-title {{ font-size:11px; font-weight:700; text-transform:uppercase;
                       letter-spacing:.06em; color:#bbb; margin-bottom:10px; }}
    .ejemplo {{ background:#f7f9fc; border-radius:8px; padding:12px 14px; margin-bottom:8px; }}
    .ejemplo:last-child {{ margin-bottom:0; }}
    .ejemplo-id {{ font-size:11px; font-weight:700; color:#aaa; display:block; margin-bottom:4px; }}
    .ejemplo p {{ font-size:13px; color:#444; line-height:1.6; text-align:left; }}
    .ejemplo ul {{ font-size:13px; color:#444; line-height:1.8; text-align:left; margin-top:4px; }}
    .ejemplo li {{ margin-bottom:2px; }}

    /* Bancos */
    .card {{ background:#fff; border-radius:12px; padding:22px 24px;
             box-shadow:0 1px 6px rgba(0,0,0,0.07); }}
    .bar-row   {{ display:flex; align-items:center; gap:10px; margin-bottom:10px; }}
    .bar-label {{ font-size:13px; min-width:140px; }}
    .bar-track {{ flex:1; background:{MELI_GRAY}; border-radius:99px; height:8px; }}
    .bar-fill  {{ height:8px; border-radius:99px; background:{MELI_BLUE}; }}
    .bar-cnt   {{ font-size:12px; color:#666; min-width:80px; text-align:right; }}

    @media(max-width:900px){{
      .kpi-row  {{ grid-template-columns:1fr 1fr; }}
      .cat-grid {{ grid-template-columns:1fr; }}
      .cat-metrics {{ grid-template-columns:1fr 1fr; }}
    }}
  </style>
</head>
<body>

  <div class="header">
    <div>
      <h1>Clasificador de STO — MLA</h1>
      <div class="sub">Bank Transfer · CVU · {FECHA_INICIO} al {FECHA_FIN}</div>
    </div>
    <div class="header-right">
      Última actualización: <strong>{datetime.today().strftime('%d/%m/%Y')}</strong><br>
      Tipo de cambio oficial: <strong>1 USD = ${TIPO_CAMBIO:,.0f} ARS</strong><br>
      <span class="tc-fuente">Fuente: dolarapi.com · Banco Central de la República Argentina · {TC_FECHA}</span>
    </div>
  </div>

  <div class="kpi-row">{kpis_html}</div>

  <!-- Resumen: tipos de fraude + bancos -->
  <div class="section">
    <div class="summary-grid">
      <div class="card">
        <div class="section-title">Casos por tipo de fraude</div>
        {resumen_fraude_html}
      </div>
      <div class="card">
        <div class="section-title">Bancos más frecuentes en denuncias</div>
        {bancos_html}
      </div>
    </div>
  </div>

  <!-- Detalle por categoría -->
  <div class="section">
    <div class="section-title">Detalle por tipo de fraude — ordenados por monto reclamado</div>
    <div class="cat-grid">{cats_html}</div>
  </div>

</body>
</html>"""

OUTPUT_HTML.write_text(html, encoding="utf-8")
print(f"Listo → {OUTPUT_HTML}")
