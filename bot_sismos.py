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

# Límites geográficos de Perú
PERU_BOUNDS = {
    "min_lat": -19.0,
    "max_lat": 0.0,
    "min_lon": -82.0,
    "max_lon": -68.0
}

# Coordenadas de las dos sedes que vigilas simultáneamente
SEDES_MONITOREO = {
    "Lima": {"lat": -12.0464, "lon": -77.0428, "nombre": "Lima"},
    "Amazonas": {"lat": -5.6398, "lon": -78.5312, "nombre": "Amazonas (Bagua)"}
}

# Historial en memoria de sismos ya procesados para evitar alertas duplicadas
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

def evaluar_tsunami(mag, prof_km, lon):
    if mag >= 7.0 and prof_km < 60 and lon < -76.0:
        return "🔴 ¡ALERTA DE TSUNAMI! Evacuar litoral costero."
    return "🟢 NO reúne condiciones para tsunami."

def procesar_y_alertar(sismo_id, lat, lon, mag, depth, region):
    global sismos_procesados
    
    # Si ya lo alertamos, no repetirlo
    if sismo_id in sismos_procesados:
        return
    sismos_procesados.add(sismo_id)
    # Limitar memoria de historial
    if len(sismos_procesados) > 200:
        sismos_procesados.clear()

    # Calcular distancias y tiempos de llegada a LIMA y a AMAZONAS
    dist_lima = distancia_km(SEDES_MONITOREO["Lima"]["lat"], SEDES_MONITOREO["Lima"]["lon"], lat, lon)
    dist_amaz = distancia_km(SEDES_MONITOREO["Amazonas"]["lat"], SEDES_MONITOREO["Amazonas"]["lon"], lat, lon)
    
    t_onda_lima = max(0, int(dist_lima / 3.5))
    t_onda_amaz = max(0, int(dist_amaz / 3.5))

    # Radio estimado de percepción según la magnitud
    radio_percepcion = 10 ** (0.43 * mag - 0.5)

    cerca_lima = dist_lima <= max(radio_percepcion, 150)
    cerca_amaz = dist_amaz <= max(radio_percepcion, 150)

    # Determinar nivel de prioridad y mensaje
    if cerca_amaz and cerca_lima:
        alerta_tipo = "🚨 ¡SISMO PERCEPTIBLE EN AMAZONAS Y LIMA!"
        prioridad = "urgent"
        tags = "warning,rotating_light,sos"
    elif cerca_amaz:
        alerta_tipo = "🚨 ¡ALERTA SÍSMICA EN AMAZONAS (BAGUA)!"
        prioridad = "urgent" if (dist_amaz <= 100 or mag >= 5.0) else "high"
        tags = "warning,rotating_light"
    elif cerca_lima:
        alerta_tipo = "🚨 ¡ALERTA SÍSMICA EN LIMA!"
        prioridad = "urgent" if (dist_lima <= 100 or mag >= 5.0) else "high"
        tags = "warning,rotating_light"
    else:
        # Sismo en otra región de Perú (ej. Ica, Arequipa, etc.) -> Notificación informativa que SÍ te llega
        alerta_tipo = "📢 SISMO EN PERÚ"
        prioridad = "high" if mag >= 5.0 else "default"
        tags = "earth_americas,bell"

    tsunami_txt = evaluar_tsunami(mag, depth, lon)

    titulo = f"{alerta_tipo} · M {mag:.1f}"
    cuerpo = (
        f"💥 Magnitud: M {mag:.1f}\n"
        f"📍 Epicentro: {region}\n"
        f"📏 Profundidad: {depth:.0f} km\n\n"
        f"📍 Distancia a AMAZONAS: {dist_amaz:.0f} km (Ondas: ~{t_onda_amaz}s)\n"
        f"📍 Distancia a LIMA: {dist_lima:.0f} km (Ondas: ~{t_onda_lima}s)\n\n"
        f"🌊 Tsunami: {tsunami_txt}\n"
        f"Conserva la calma y ubícate en zonas seguras."
    )

    actions = f"view, Ver Epicentro en Mapa, https://maps.google.com/?q={lat},{lon}"
    print(f"\n[ENVIANDO ALERTA] {titulo} | Dist Lima: {dist_lima:.0f}km | Dist Amazonas: {dist_amaz:.0f}km")
    enviar_ntfy(titulo, cuerpo, prioridad=prioridad, tags=tags, actions=actions)

def enviar_ntfy(titulo, cuerpo, prioridad="default", tags="warning", actions=None):
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
        with urllib.request.urlopen(req, timeout=5) as resp:
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
    <title>Monitor Sísmico Perú (Lima y Amazonas)</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 24px; text-align: center; }}
        .card {{ max-width: 480px; margin: 0 auto; background: #1e293b; border-radius: 16px; padding: 24px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); border: 1px solid #334155; }}
        h1 {{ font-size: 24px; margin-bottom: 8px; color: #38bdf8; }}
        .badge {{ display: inline-block; background: #0284c7; color: white; padding: 6px 14px; border-radius: 999px; font-weight: bold; margin-bottom: 20px; }}
        .status {{ background: #0f172a; padding: 16px; border-radius: 12px; margin-bottom: 20px; text-align: left; font-size: 14px; }}
        .btn {{ display: block; width: 100%; padding: 14px; margin: 10px 0; border: none; border-radius: 10px; font-size: 16px; font-weight: bold; cursor: pointer; transition: 0.2s; }}
        .btn-test {{ background: #ef4444; color: white; margin-top: 20px; }}
        .btn:hover {{ opacity: 0.9; transform: scale(1.02); }}
    </style>
</head>
<body>
    <div class="card">
        <h1>🚨 Monitor Sísmico Perú</h1>
        <div class="badge">Vigilancia 24/7: Lima + Amazonas</div>
        
        <div class="status">
            <div><strong>🟢 Estado:</strong> Conectado a la Red Sísmica Global</div>
            <div><strong>📍 Sede 1:</strong> Lima (-12.04, -77.04)</div>
            <div><strong>📍 Sede 2:</strong> Amazonas / Bagua (-5.64, -78.53)</div>
            <div><strong>📲 Canal ntfy:</strong> {NTFY_TOPIC}</div>
        </div>

        <button class="btn btn-test" onclick="probarAlarma()">🔔 Probar Alarma en mi Celular</button>
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
    titulo = "🔔 PRUEBA DE ALARMA - LIMA Y AMAZONAS"
    cuerpo = (
        "Esta es una prueba de sonido de emergencia para tu sistema de alerta sísmica dual.\n"
        "Vigilando en tiempo real tanto LIMA como AMAZONAS (Bagua).\n"
        "Si escuchaste este aviso, tu sistema está 100% operativo."
    )
    enviar_ntfy(titulo, cuerpo, prioridad="urgent", tags="bell,rotating_light")
    return web.Response(text="Alarma de prueba enviada a tu app ntfy.")

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

                        # Filtrar solo sismos dentro de Perú y magnitud >= 3.8
                        if (PERU_BOUNDS["min_lat"] <= lat <= PERU_BOUNDS["max_lat"] and
                            PERU_BOUNDS["min_lon"] <= lon <= PERU_BOUNDS["max_lon"] and
                            mag >= 3.8):
                            procesar_y_alertar(s_id, lat, lon, mag, depth, region)

        except Exception as e:
            print(f"⚠️ [WebSocket] Reconectando en 5s... ({e})")
            await asyncio.sleep(5)

async def monitor_usgs_backup():
    """Motor de respaldo que consulta USGS cada 25 segundos para asegurar que ningún sismo se escape"""
    print("🛡️ [Backup USGS] Iniciando monitor de respaldo cada 25s...")
    url = (
        "https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson"
        "&minmagnitude=3.8"
        f"&minlatitude={PERU_BOUNDS['min_lat']}&maxlatitude={PERU_BOUNDS['max_lat']}"
        f"&minlongitude={PERU_BOUNDS['min_lon']}&maxlongitude={PERU_BOUNDS['max_lon']}"
        "&limit=5"
    )
    await asyncio.sleep(10) # Espera inicial
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
                    if (ahora - time_epoch) < 7200: # 2 horas
                        procesar_y_alertar(s_id, lat, lon, mag, depth, place)
        except Exception as e:
            # Silencioso en caso de micro-corte temporal
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
    print(f"🟢 Servidor web listo en puerto {PORT}")

    # Ejecutar en paralelo: Servidor web + WebSocket de milisegundos + Respaldo USGS
    await asyncio.gather(
        monitor_websocket(),
        monitor_usgs_backup()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Monitor sísmico detenido.")
