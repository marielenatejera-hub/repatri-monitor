# Monitor Clasificador de Estafas

Analiza automáticamente los casos de repatriación para detectar si existe una denuncia policial adjunta y clasificar el tipo de fraude involucrado. Permite priorizar bloqueos cautelares y entender los vectores de ataque más frecuentes.

---

## ¿Qué hace?

1. **Busca los casos** — consulta BigQuery para obtener todos los casos de repatriación de las últimas 3 semanas (transferencias donde el usuario no reconoce el movimiento).
2. **Busca adjuntos** — por cada caso, llama a la API de MercadoPago para ver si hay documentos adjuntos (PDFs, imágenes, etc.).
3. **Lee los adjuntos con IA** — por cada documento, le pregunta a un modelo de IA: ¿esto es una denuncia policial? ¿de qué tipo de estafa se trata?
4. **Clasifica el fraude en dos niveles:**
   - **Nivel 1 — Clasificación libre:** la IA clasifica con sus propias palabras sin categorías fijas (ej: "vishing y préstamo fraudulento", "falso curandero"). Permite detectar patrones nuevos automáticamente.
   - **Nivel 2 — Agrupación en categorías:** el script mapea esa clasificación a 7 categorías principales. Si no matchea ninguna, aparece como categoría dinámica con el nombre que le asignó la IA.
5. **Genera el reporte** — crea un HTML con gráficos, ejemplos reales y métricas (cantidad de casos, montos en USD, bancos más frecuentes) y lo sube automáticamente a GitHub.

---

## Categorías de fraude

| Categoría | Descripción |
|---|---|
| 📞 Llamada entrante del estafador | El estafador llama haciéndose pasar por un banco o empresa (vishing) |
| 🔍 Búsqueda activa de contacto | La víctima busca en Google y contacta sin saberlo a un estafador (phishing) |
| 📲 Acceso remoto (app instalada) | Instalan AnyDesk, Ultra VNC u otra app para tomar control del dispositivo |
| 💳 Préstamo fraudulento | Sacan un préstamo a nombre de la víctima sin su consentimiento |
| 📱 Estafa en redes sociales | Publicidades o perfiles falsos en Facebook, Instagram u otras redes |
| 👤 Cuento del tío digital | Alguien se hace pasar por familiar o conocido por WhatsApp |
| 📵 Robo de dispositivo | Robo físico del celular seguido de transferencias no autorizadas |

---

## Outputs

- `output/repatri_monitor_YYYYMMDD.csv` — resultados por corrida con clasificación por caso
- `output/reporte.html` — dashboard visual actualizado automáticamente

🔗 **Reporte:** https://marielenatejera-hub.github.io/repatri-monitor/output/reporte.html  
📖 **Guía completa:** https://marielenatejera-hub.github.io/repatri-monitor/docs/guia.html

---

## Ejecución

Corre automáticamente todos los **martes a las 10am** vía cron, con la Mac encendida y VPN activa.

Para correr manualmente:
```bash
bash ~/Documents/repatri-monitor/run.sh
