import os
import json
import math
import asyncio
import urllib.request
from datetime import datetime, timezone
import websockets
from aiohttp import web

# ================= CONFIGURACIÓN =================
# Canal único para la app ntfy en tu teléfono
NTFY_TOPIC = "sismos_peru_dm_alerta"

# Puerto asignado por Render ($PORT) o 8088 local
PORT = int(os.environ.get("PORT", 8088))

# Límites geográficos de Perú (incluye franja marítima donde nacen los tsunamis y fallas)
PERU_BOUNDS = {
    "min_lat": -19.5,
    "max_lat": 0.5,
    "min_lon": -82.5,
    "max_lon": -68.0
}

# Principales ciudades de referencia en los 24 departamentos del Perú
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

# Historial en memoria para no repetir alertas del mismo evento
sismos_procesados = set()
# =================================================

def distancia_km(lat1, lon1, lat2, lon2):
    """Calcula la distancia esférica exacta en km (fórmula de Haversine)"""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lam = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lam / 2)**2
    return R * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))

def encontrar_ciudades_cercanas(lat, lon):
    """Encuentra la ciudad más cercana y la distancia a Lima"""
    ciudades_ordenadas = sorted(
        CIUDADES_PERU,
        key=lambda c: distancia_km(c["lat"], c["lon"], lat, lon)
    )
    mas_cercana = ciudades_ordenadas[0]
    dist_cercana = distancia_km(mas_cercana["lat"], mas_cercana["lon"], lat, lon)
    
    # Distancia a Lima
    dist_lima = distancia_km(-12.0464, -77.0428, lat, lon)
    
    return mas_cercana["nombre"], dist_cercana, dist_lima

def evaluar_tsunami(mag, prof_km, lon):
    if mag >= 7.0 and prof_km < 60 and lon < -75.0:
        return "🔴 ¡ALERTA ROJA DE TSUNAMI! Evacuar litoral y zonas costeras bajas."
    elif mag >= 6.5 and prof_km < 70 and lon < -75.0:
        return "⚠️ ATENCIÓN DE TSUNAMI: Monitoreo preventivo del litoral peruano."
    return "🟢 NO reúne condiciones para tsunami en el litoral peruano."

def procesar_y_alertar(sismo_id, lat, lon, mag, depth, region):
    global sismos_procesados
    
    # Evitar duplicados
    if sismo_id in sismos_procesados:
        return
    sismos_procesados.add(sismo_id)
    if len(sismos_procesados) > 300:
        sismos_procesados.clear()

    # Encontrar la ciudad más cercana en todo el Perú y distancia a Lima
    ciudad_cercana, dist_cercana, dist_lima = encontrar_ciudades_cercanas(lat, lon)
    t_onda_cercana = max(0, int(dist_cercana / 3.5))
    t_onda_lima = max(0, int(dist_lima / 3.5))

    # Definir nivel de severidad y VIBRACIÓN GARANTIZADA:
    # 'urgent' (prioridad 5 en ntfy) ACTIVA VIBRACIÓN OBLIGATORIA Y SONIDO DE EMERGENCIA
    if mag >= 6.0:
        alerta_tipo = "🚨 ¡TERREMOTO / SISMO MAYOR EN PERÚ!"
        prioridad = "urgent"
        tags = "rotating_light,sos,warning"
    elif mag >= 5.0:
        alerta_tipo = "🚨 ¡SISMO FUERTE EN PERÚ!"
        prioridad = "urgent"
        tags = "warning,rotating_light,earth_americas"
    elif mag >= 4.2:
        alerta_tipo = "⚠️ SISMO MODERADO EN PERÚ"
        prioridad = "urgent"  # URGENT para garantizar que vibre y suene en el celular
        tags = "warning,earth_americas"
    else: # 3.8 a 4.1
        alerta_tipo = "📢 SISMO LEVE EN PERÚ"
        prioridad = "high"   # HIGH activa vibración y sonido
        tags = "earth_americas,bell"

    tsunami_txt = evaluar_tsunami(mag, depth, lon)

    titulo = f"{alerta_tipo} · M {mag:.1f}"
    cuerpo = (
        f"💥 Magnitud: M {mag:.1f}\n"
        f"📍 Zona: {region}\n"
        f"📏 Profundidad: {depth:.0f} km\n\n"
        f"🏙️ Más cercano a: {ciudad_cercana} (~{dist_cercana:.0f} km | Onda: ~{t_onda_cercana}s)\n"
        f"🏛️ Distancia a Lima: ~{dist_lima:.0f} km (Onda: ~{t_onda_lima}s)\n\n"
        f"🌊 Tsunami: {tsunami_txt}\n"
        f"Mantén la calma y mantente informado."
    )

    actions = f"view, Ver Epicentro en Google Maps, https://maps.google.com/?q={lat},{lon}"
    print(f"\n[ENVIANDO ALERTA A TODO PERÚ] {titulo} | Cerca de: {ciudad_cercana} ({dist_cercana:.0f}km)")
    enviar_ntfy(titulo, cuerpo, prioridad=prioridad, tags=tags, actions=actions)

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

# ================= VISTA WEB / PANEL DE CONTROL =================
async def index_handler(request):
    html = f"""<!DOCTYPE html>
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
        <div class="badge">🇵🇪 Cobertura 24/7: Todo el Perú</div>

        <div class="status">
            <div><strong>🟢 Estado:</strong> Enlace en tiempo real activo</div>
            <div><strong>📡 Cobertura:</strong> 24 departamentos y litoral peruano</div>
            <div><strong>📳 Modo Vibración:</strong> Activado (Prioridad Urgente 5)</div>
            <div><strong>📲 Canal ntfy:</strong> {NTFY_TOPIC}</div>
        </div>

        <button class="btn btn-test" onclick="probarAlarma()">📳 Probar Vibración y Alarma en Celular</button>
    </div>

    <script>
        function probarAlarma() {{
            fetch('/test_alarma', {{ method: 'POST' }})
                .then(res => res.text())
                .then(txt => alert('🔔 ' + txt));
        }}
    </script>
</body>
</html>"""
    return web.Response(text=html, content_type="text/html")

async def test_alarma_handler(request):
    titulo = "📳 PRUEBA DE ALARMA Y VIBRACIÓN - TODO EL PERÚ"
    cuerpo = (
        "¡Prueba de alerta sísmica nacional con vibración obligatoria!\n"
        "Configuración activa: Cobertura total para los 24 departamentos de Perú.\n"
        "Si tu teléfono vibró y sonó, el sistema está 100% operativo para cualquier sismo."
    )
    enviar_ntfy(titulo, cuerpo, prioridad="urgent", tags="warning,rotating_light,earth_americas")
    return web.Response(text="Alarma de prueba con vibración enviada a tu app ntfy.")

# ================= MOTOR SÍSMICO EN TIEMPO REAL =================
async def monitor_websocket():
    uri = "wss://www.seismicportal.eu/standing_order/websocket"
    print("⚡ [WebSocket] Conectado al flujo global en tiempo real...")
    while True:
        try:
            async with websockets.connect(uri, ping_interval=20, ping_timeout=20) as ws:
                while True:
                    mensaje = await ws.recv()
                    evento = json.loads(mensaje)
                    
                    if evento.get("action") in ["create", "update"]:
                        props = evento.get("data", {}).get("properties", {})
                        s_id = str(props.get("unid") or props.get("source_id") or "")
                        lat = float(props.get("lat", 0))
                        lon = float(props.get("lon", 0))
                        mag = float(props.get("mag", 0))
                        depth = float(props.get("depth", 0))
                        region = props.get("flynn_region", "Perú")

                        # Cobertura nacional: Todo Perú y magnitud >= 3.8
                        if (PERU_BOUNDS["min_lat"] <= lat <= PERU_BOUNDS["max_lat"] and
                            PERU_BOUNDS["min_lon"] <= lon <= PERU_BOUNDS["max_lon"] and
                            mag >= 3.8):
                            procesar_y_alertar(s_id, lat, lon, mag, depth, region)

        except Exception as e:
            print(f"⚠️ [WebSocket] Reconectando en 5s... ({e})")
            await asyncio.sleep(5)

async def monitor_usgs_backup():
    """Motor de respaldo que consulta USGS cada 25 segundos para asegurar que ningún sismo se escape"""
    print("🛡️ [Backup USGS] Iniciando monitor de respaldo nacional cada 25s...")
    url = (
        "https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson"
        "&minmagnitude=3.8"
        f"&minlatitude={PERU_BOUNDS['min_lat']}&maxlatitude={PERU_BOUNDS['max_lat']}"
        f"&minlongitude={PERU_BOUNDS['min_lon']}&maxlongitude={PERU_BOUNDS['max_lon']}"
        "&limit=8"
    )
    await asyncio.sleep(10)
    while True:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())
                for f in data.get("features", []):
                    p = f["properties"]
                    geom = f["geometry"]["coordinates"]
                    s_id = str(f["id"])
                    lon = float(geom[0])
                    lat = float(geom[1])
                    depth = float(geom[2])
                    mag = float(p["mag"])
                    place = p["place"]

                    # Verificar si ocurrió hace menos de 2 horas
                    time_epoch = p["time"] / 1000
                    ahora = datetime.now(timezone.utc).timestamp()
                    if (ahora - time_epoch) < 7200:
                        procesar_y_alertar(s_id, lat, lon, mag, depth, place)
        except Exception as e:
            pass
        await asyncio.sleep(25)

async def main():
    app = web.Application()
    app.router.add_get("/", index_handler)
    app.router.add_post("/test_alarma", test_alarma_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"🟢 Servidor web nacional listo en puerto {PORT}")

    await asyncio.gather(
        monitor_websocket(),
        monitor_usgs_backup()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Monitor sísmico nacional detenido.")
