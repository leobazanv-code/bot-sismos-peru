"""Monitor sísmico para Perú con alertas push vía ntfy.

Fuentes:
  - EMSC (seismicportal.eu) por WebSocket, en tiempo real.
  - USGS por consulta HTTP cada 25 s, como respaldo.

Ver README.md para instalación, despliegue en Render y variables de entorno.
"""
import os
import sys
import json
import math
import asyncio
import urllib.request
from html import escape as html_escape
from datetime import datetime, timezone
import websockets
from aiohttp import web

# ================= CONFIGURACIÓN =================
# Canal ntfy al que está suscrito el teléfono. Los canales de ntfy.sh son públicos:
# quien conozca el nombre puede leer y publicar. Defínelo en la variable NTFY_TOPIC.
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "sismos_peru_dm_alerta")

# Si se define, /test_alarma exige este token (evita que cualquiera haga sonar el teléfono).
TEST_TOKEN = os.environ.get("TEST_TOKEN", "")

# Puerto asignado por Render ($PORT) o 8088 local
PORT = int(os.environ.get("PORT", 8088))

# Magnitud mínima para alertar
MAG_MIN = 3.8

# Rectángulo aproximado que contiene a Perú y su franja marítima.
# Ojo: también incluye el sur de Ecuador, Acre (Brasil), el oeste de Bolivia y Arica (Chile).
PERU_BOUNDS = {
    "min_lat": -19.5,
    "max_lat": 0.5,
    "min_lon": -82.5,
    "max_lon": -68.0
}

# Una ciudad de referencia por cada uno de los 24 departamentos (más Callao y ciudades costeras)
CIUDADES_PERU = [
    {"nombre": "Lima", "lat": -12.0464, "lon": -77.0428},
    {"nombre": "Callao", "lat": -12.0565, "lon": -77.1181},
    {"nombre": "Ica", "lat": -14.0678, "lon": -75.7286},
    {"nombre": "Nazca / Marcona", "lat": -14.8309, "lon": -74.9385},
    {"nombre": "Pisco / Chincha", "lat": -13.7103, "lon": -76.2054},
    {"nombre": "Arequipa", "lat": -16.4090, "lon": -71.5375},
    {"nombre": "Camaná / Mollendo", "lat": -16.6236, "lon": -72.7111},
    {"nombre": "Moquegua / Ilo", "lat": -17.1938, "lon": -70.9333},
    {"nombre": "Tacna", "lat": -18.0066, "lon": -70.2463},
    {"nombre": "Trujillo (La Libertad)", "lat": -8.1091, "lon": -79.0269},
    {"nombre": "Chimbote / Huaraz (Áncash)", "lat": -9.0745, "lon": -78.5936},
    {"nombre": "Chiclayo (Lambayeque)", "lat": -6.7714, "lon": -79.8409},
    {"nombre": "Piura", "lat": -5.1945, "lon": -80.6328},
    {"nombre": "Tumbes", "lat": -3.5669, "lon": -80.4515},
    {"nombre": "Amazonas (Chachapoyas/Bagua)", "lat": -5.6398, "lon": -78.5312},
    {"nombre": "Cajamarca", "lat": -7.1638, "lon": -78.5003},
    {"nombre": "San Martín (Moyobamba/Tarapoto)", "lat": -6.4859, "lon": -76.3686},
    {"nombre": "Loreto (Iquitos)", "lat": -3.7437, "lon": -73.2516},
    {"nombre": "Ucayali (Pucallpa)", "lat": -8.3791, "lon": -74.5539},
    {"nombre": "Huánuco", "lat": -9.9306, "lon": -76.2422},
    {"nombre": "Pasco (Cerro de Pasco)", "lat": -10.6675, "lon": -76.2564},
    {"nombre": "Junín (Huancayo)", "lat": -12.0651, "lon": -75.2049},
    {"nombre": "Huancavelica", "lat": -12.7864, "lon": -74.9757},
    {"nombre": "Ayacucho", "lat": -13.1588, "lon": -74.2239},
    {"nombre": "Apurímac (Abancay)", "lat": -13.6339, "lon": -72.8814},
    {"nombre": "Cusco", "lat": -13.5319, "lon": -71.9675},
    {"nombre": "Puno / Juliaca", "lat": -15.8422, "lon": -70.0199},
    {"nombre": "Madre de Dios (Puerto Maldonado)", "lat": -12.5933, "lon": -69.1891}
]

# Línea de costa peruana simplificada (lat, lon), de norte a sur.
# shortcut: interpolación lineal con ~20 puntos, error de unos 20-30 km; usar un shapefile si se necesita precisión.
COSTA_PERU = [
    (-3.40, -80.30),   # Tumbes / Zarumilla
    (-4.10, -81.10),   # Máncora
    (-4.58, -81.27),   # Talara
    (-5.09, -81.11),   # Paita
    (-5.80, -81.10),   # Punta Aguja
    (-6.80, -79.95),   # Pimentel (Chiclayo)
    (-7.40, -79.57),   # Pacasmayo
    (-8.10, -79.05),   # Trujillo
    (-9.08, -78.60),   # Chimbote
    (-10.07, -78.16),  # Huarmey
    (-11.10, -77.62),  # Huacho
    (-12.05, -77.15),  # Callao
    (-13.70, -76.22),  # Pisco
    (-15.35, -75.17),  # San Juan de Marcona
    (-15.85, -74.25),  # Chala
    (-16.62, -72.71),  # Camaná
    (-17.02, -72.02),  # Mollendo
    (-17.64, -71.34),  # Ilo
    (-18.35, -70.40),  # Frontera con Chile
]

# Velocidad aproximada de la onda S (la que más sacude), en km/s
VEL_ONDA_S = 3.5

# Eventos ya alertados: (id, epoch_origen, lat, lon). Sirve para no repetir alertas,
# incluso cuando EMSC y USGS reportan el mismo sismo con IDs distintos.
alertados = []
VENTANA_HISTORIAL_S = 3 * 3600   # se olvidan eventos de más de 3 h
DUP_SEGUNDOS = 120               # mismo sismo si difieren menos de 2 min...
DUP_KM = 150                     # ...y menos de 150 km entre epicentros

# Estado real de las fuentes, mostrado en el panel web
estado = {"emsc_conectado": False, "emsc_ultimo_msg": None, "usgs_ultimo_ok": None, "usgs_error": None}
# =================================================


def distancia_km(lat1, lon1, lat2, lon2):
    """Distancia en km sobre una Tierra esférica (Haversine). Error típico menor al 0.5 %."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lam = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lam / 2)**2
    return R * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))


def ciudad_mas_cercana(lat, lon):
    """Devuelve (nombre de la ciudad más cercana, km a esa ciudad, km a Lima)."""
    mas_cercana = min(CIUDADES_PERU, key=lambda c: distancia_km(c["lat"], c["lon"], lat, lon))
    dist_cercana = distancia_km(mas_cercana["lat"], mas_cercana["lon"], lat, lon)
    dist_lima = distancia_km(-12.0464, -77.0428, lat, lon)
    return mas_cercana["nombre"], dist_cercana, dist_lima


def lon_costa(lat):
    """Longitud de la costa peruana a una latitud dada (interpolada; fuera del rango, el extremo)."""
    if lat >= COSTA_PERU[0][0]:
        return COSTA_PERU[0][1]
    for (lat_a, lon_a), (lat_b, lon_b) in zip(COSTA_PERU, COSTA_PERU[1:]):
        if lat_b <= lat <= lat_a:
            return lon_a + (lat - lat_a) / (lat_b - lat_a) * (lon_b - lon_a)
    return COSTA_PERU[-1][1]


def es_costero_o_marino(lat, lon, margen_km=50):
    """True si el epicentro está en el mar o a menos de `margen_km` tierra adentro."""
    margen_grados = margen_km / (111.32 * math.cos(math.radians(lat)))
    return lon < lon_costa(lat) + margen_grados


def evaluar_tsunami(mag, prof_km, lat, lon):
    """Estimación orientativa propia, NO es un aviso oficial.
    Los avisos oficiales de tsunami en Perú los emite la DHN (Marina de Guerra) y los difunde INDECI."""
    if not es_costero_o_marino(lat, lon):
        return "🟢 Epicentro tierra adentro: sin condiciones para tsunami."
    if mag >= 7.0 and prof_km < 60:
        return "🔴 POSIBLE TSUNAMI (estimación, no oficial). Aléjate de la playa y sigue a DHN / INDECI."
    if mag >= 6.5 and prof_km < 70:
        return "⚠️ Revisa los avisos de la DHN: sismo costero con cierto potencial de tsunami."
    return "🟢 No reúne condiciones típicas para tsunami."


def es_duplicado(sismo_id, t_origen, lat, lon):
    """True si el evento ya fue alertado (mismo ID, o mismo sismo reportado por otra fuente)."""
    ahora = datetime.now(timezone.utc).timestamp()
    alertados[:] = [e for e in alertados if ahora - e[1] < VENTANA_HISTORIAL_S]
    for e_id, e_t, e_lat, e_lon in alertados:
        if e_id == sismo_id:
            return True
        if abs(e_t - t_origen) < DUP_SEGUNDOS and distancia_km(e_lat, e_lon, lat, lon) < DUP_KM:
            return True
    return False


def procesar_y_alertar(sismo_id, t_origen, lat, lon, mag, depth, region):
    if es_duplicado(sismo_id, t_origen, lat, lon):
        return False
    alertados.append((sismo_id, t_origen, lat, lon))

    ciudad, dist_cercana, dist_lima = ciudad_mas_cercana(lat, lon)
    # Tiempo que tarda la onda S en llegar, contado desde el origen del sismo (no desde la alerta).
    # Usa distancia al hipocentro (incluye la profundidad).
    t_onda_cercana = int(math.hypot(dist_cercana, depth) / VEL_ONDA_S)
    t_onda_lima = int(math.hypot(dist_lima, depth) / VEL_ONDA_S)

    # Prioridad ntfy: 'urgent' (5) = vibración larga y sonido; 'high' (4) = vibración y sonido.
    # El teléfono puede silenciarlas (No Molestar, ajustes del canal en la app ntfy).
    if mag >= 6.0:
        alerta_tipo = "🚨 ¡TERREMOTO / SISMO MAYOR!"
        prioridad = "urgent"
        tags = "rotating_light,sos,warning"
    elif mag >= 5.0:
        alerta_tipo = "🚨 ¡SISMO FUERTE!"
        prioridad = "urgent"
        tags = "warning,rotating_light,earth_americas"
    elif mag >= 4.2:
        alerta_tipo = "⚠️ SISMO MODERADO"
        prioridad = "urgent"
        tags = "warning,earth_americas"
    else:  # MAG_MIN a < 4.2
        alerta_tipo = "📢 SISMO LEVE"
        prioridad = "high"
        tags = "earth_americas,bell"

    tsunami_txt = evaluar_tsunami(mag, depth, lat, lon)

    titulo = f"{alerta_tipo} · M {mag:.1f}"
    cuerpo = (
        f"💥 Magnitud: M {mag:.1f}\n"
        f"📍 Zona: {region}\n"
        f"📏 Profundidad: {depth:.0f} km\n\n"
        f"🏙️ Más cercano a: {ciudad} (~{dist_cercana:.0f} km)\n"
        f"🏛️ Distancia a Lima: ~{dist_lima:.0f} km\n"
        f"⏱️ Onda S llega ~{t_onda_cercana}s (cercana) / ~{t_onda_lima}s (Lima) tras el sismo\n\n"
        f"🌊 Tsunami: {tsunami_txt}\n"
        f"Mantén la calma y mantente informado."
    )

    actions = f"view, Ver Epicentro en Google Maps, https://maps.google.com/?q={lat},{lon}"
    print(f"\n[ENVIANDO ALERTA] {titulo} | Cerca de: {ciudad} ({dist_cercana:.0f}km)")
    enviar_ntfy(titulo, cuerpo, prioridad=prioridad, tags=tags, actions=actions)
    return True


def enviar_ntfy(titulo, cuerpo, prioridad="urgent", tags="warning", actions=None):
    url = f"https://ntfy.sh/{NTFY_TOPIC}"
    headers = {
        "Title": titulo.encode("utf-8"),
        "Priority": prioridad,
        "Tags": tags
    }
    if actions:
        headers["Actions"] = actions.encode("utf-8")

    req = urllib.request.Request(url, data=cuerpo.encode("utf-8"), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"❌ Error al enviar alerta a ntfy: {e}")
        return False


def hace(epoch):
    if epoch is None:
        return "nunca"
    s = int(datetime.now(timezone.utc).timestamp() - epoch)
    return f"hace {s} s" if s < 120 else f"hace {s // 60} min"


# ================= VISTA WEB / PANEL DE CONTROL =================
async def index_handler(request):
    emsc = "🟢 Conectado" if estado["emsc_conectado"] else "🔴 Desconectado (reintentando)"
    usgs = f"último OK {hace(estado['usgs_ultimo_ok'])}"
    if estado["usgs_error"]:
        usgs += f" · último error: {html_escape(estado['usgs_error'])}"
    js_token = 'prompt("Token de prueba:")' if TEST_TOKEN else '""'
    html =f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Monitor Sísmico Nacional - Todo el Perú</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 24px; text-align: center; }}
        .card {{ max-width: 520px; margin: 0 auto; background: #1e293b; border-radius: 16px; padding: 24px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); border: 1px solid #334155; }}
        h1 {{ font-size: 24px; margin-bottom: 8px; color: #38bdf8; }}
        .badge {{ display: inline-block; background: #0284c7; color: white; padding: 6px 14px; border-radius: 999px; font-weight: bold; margin-bottom: 20px; }}
        .status {{ background: #0f172a; padding: 16px; border-radius: 12px; margin-bottom: 20px; text-align: left; font-size: 14px; line-height: 1.6; }}
        .btn {{ display: block; width: 100%; padding: 14px; margin: 10px 0; border: none; border-radius: 10px; font-size: 16px; font-weight: bold; cursor: pointer; transition: 0.2s; }}
        .btn-test {{ background: #ef4444; color: white; margin-top: 20px; }}
        .btn:hover {{ opacity: 0.9; transform: scale(1.02); }}
    </style>
</head>
<body>
    <div class="card">
        <h1>🚨 Monitor Sísmico Nacional</h1>
        <div class="badge">🇵🇪 Cobertura: Perú y franja marítima (M ≥ {MAG_MIN})</div>

        <div class="status">
            <div><strong>📡 EMSC (tiempo real):</strong> {emsc} · último mensaje {hace(estado['emsc_ultimo_msg'])}</div>
            <div><strong>🛡️ USGS (respaldo):</strong> {usgs}</div>
            <div><strong>📳 Prioridad:</strong> urgente (5) si M ≥ 4.2 · alta (4) si M &lt; 4.2</div>
            <div><strong>🔔 Alertas enviadas (últimas 3 h):</strong> {len(alertados)}</div>
        </div>

        <button class="btn btn-test" onclick="probarAlarma()">📳 Probar Vibración y Alarma en Celular</button>
    </div>

    <script>
        function probarAlarma() {{
            const token = {js_token};
            if (token === null) return;
            fetch('/test_alarma', {{ method: 'POST', headers: {{ 'X-Token': token }} }})
                .then(res => res.text())
                .then(txt => alert('🔔 ' + txt));
        }}
    </script>
</body>
</html>"""
    return web.Response(text=html, content_type="text/html")


async def test_alarma_handler(request):
    if TEST_TOKEN and request.headers.get("X-Token") != TEST_TOKEN:
        return web.Response(status=403, text="Token incorrecto.")
    titulo = "📳 PRUEBA DE ALARMA Y VIBRACIÓN"
    cuerpo = (
        "Prueba de alerta sísmica con prioridad urgente.\n"
        "Si tu teléfono vibró y sonó, las notificaciones de ntfy están bien configuradas."
    )
    ok = enviar_ntfy(titulo, cuerpo, prioridad="urgent", tags="warning,rotating_light,earth_americas")
    if not ok:
        return web.Response(status=502, text="No se pudo enviar a ntfy. Revisa los logs.")
    return web.Response(text="Alarma de prueba enviada a tu app ntfy.")


# ================= MOTOR SÍSMICO EN TIEMPO REAL =================
def epoch_iso(texto):
    """Convierte '2024-05-01T12:34:56.7Z' a epoch UTC. Si falla, usa la hora actual."""
    try:
        return datetime.strptime(texto[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).timestamp()


def en_zona(lat, lon, mag):
    return (PERU_BOUNDS["min_lat"] <= lat <= PERU_BOUNDS["max_lat"] and
            PERU_BOUNDS["min_lon"] <= lon <= PERU_BOUNDS["max_lon"] and
            mag >= MAG_MIN)


async def monitor_websocket():
    uri = "wss://www.seismicportal.eu/standing_order/websocket"
    while True:
        try:
            async with websockets.connect(uri, ping_interval=20, ping_timeout=20) as ws:
                estado["emsc_conectado"] = True
                print("⚡ [EMSC] Conectado al flujo en tiempo real.")
                while True:
                    evento = json.loads(await ws.recv())
                    estado["emsc_ultimo_msg"] = datetime.now(timezone.utc).timestamp()

                    if evento.get("action") in ["create", "update"]:
                        props = evento.get("data", {}).get("properties", {})
                        lat = float(props.get("lat", 0))
                        lon = float(props.get("lon", 0))
                        mag = float(props.get("mag", 0))
                        if en_zona(lat, lon, mag):
                            s_id = str(props.get("unid") or props.get("source_id") or "")
                            depth = float(props.get("depth", 0))
                            region = props.get("flynn_region", "Perú")
                            procesar_y_alertar(s_id, epoch_iso(props.get("time")), lat, lon, mag, depth, region)

        except Exception as e:
            estado["emsc_conectado"] = False
            print(f"⚠️ [EMSC] Reconectando en 5s... ({e})")
            await asyncio.sleep(5)


async def monitor_usgs_backup():
    """Respaldo: consulta USGS cada 25 s y alerta los sismos de las últimas 2 h que EMSC no haya reportado."""
    print("🛡️ [USGS] Iniciando monitor de respaldo cada 25s...")
    url = (
        "https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson"
        f"&minmagnitude={MAG_MIN}"
        f"&minlatitude={PERU_BOUNDS['min_lat']}&maxlatitude={PERU_BOUNDS['max_lat']}"
        f"&minlongitude={PERU_BOUNDS['min_lon']}&maxlongitude={PERU_BOUNDS['max_lon']}"
        "&limit=20"
    )
    await asyncio.sleep(10)
    while True:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "bot-sismos-peru"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())
            estado["usgs_ultimo_ok"] = datetime.now(timezone.utc).timestamp()
            estado["usgs_error"] = None
            ahora = datetime.now(timezone.utc).timestamp()
            for f in data.get("features", []):
                p = f["properties"]
                lon, lat, depth = (float(x) for x in f["geometry"]["coordinates"][:3])
                t_origen = p["time"] / 1000
                if ahora - t_origen < 7200:
                    procesar_y_alertar(str(f["id"]), t_origen, lat, lon, float(p["mag"]), depth, p["place"])
        except Exception as e:
            estado["usgs_error"] = str(e)[:120]
            print(f"⚠️ [USGS] Error en consulta de respaldo: {e}")
        await asyncio.sleep(25)


async def main():
    app = web.Application()
    app.router.add_get("/", index_handler)
    app.router.add_post("/test_alarma", test_alarma_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"🟢 Servidor web listo en puerto {PORT}")

    await asyncio.gather(
        monitor_websocket(),
        monitor_usgs_backup()
    )

if __name__ == "__main__":
    # Evita que un print con emojis falle (y corte el envío de la alerta) en consolas cp1252 de Windows
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Monitor sísmico detenido.")
