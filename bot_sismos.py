import os
import json
import math
import asyncio
import urllib.request
import websockets
from aiohttp import web

# ================= CONFIGURACIÓN =================
# Canal único para la app ntfy en tu teléfono
NTFY_TOPIC = "sismos_peru_dm_alerta"

# Archivo para recordar tu última ubicación al reiniciar el bot
UBICACION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ubicacion_actual.json")

# Límites geográficos de Perú
PERU_BOUNDS = {
    "min_lat": -19.0,
    "max_lat": 0.0,
    "min_lon": -82.0,
    "max_lon": -68.0
}

# Puerto del panel web local o asignado por la nube (Render asigna $PORT automáticamente)
PORT = int(os.environ.get("PORT", 8088))
# =================================================

# Cargar o inicializar ubicación (por defecto Lima)
ubicacion_usuario = {
    "lat": -12.0464,
    "lon": -77.0428,
    "ciudad": "Lima (Centro)"
}

if os.path.exists(UBICACION_FILE):
    try:
        with open(UBICACION_FILE, "r", encoding="utf-8") as f:
            ubicacion_usuario.update(json.load(f))
    except Exception as e:
        print("Error leyendo ubicacion_actual.json:", e)

def guardar_ubicacion():
    try:
        with open(UBICACION_FILE, "w", encoding="utf-8") as f:
            json.dump(ubicacion_usuario, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Error guardando ubicacion_actual.json:", e)

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

def evaluar_impacto_usuario(sismo_lat, sismo_lon, mag, prof_km):
    dist = distancia_km(ubicacion_usuario["lat"], ubicacion_usuario["lon"], sismo_lat, sismo_lon)
    
    # Velocidad de la onda destructiva S (~3.5 km/s)
    tiempo_llegada_segundos = max(0, int(dist / 3.5))

    # Radio estimado de percepción según la magnitud
    radio_percepcion_km = 10 ** (0.43 * mag - 0.5)

    if dist <= radio_percepcion_km:
        if dist <= 120 or mag >= 6.5:
            prioridad = "urgent"  # Nivel 5: Rompe silencio, activa sirena y enciende pantalla
            alerta_tipo = f"🔴 ¡ALERTA SÍSMICA CRÍTICA EN {ubicacion_usuario['ciudad'].upper()}!"
        else:
            prioridad = "high"    # Nivel 4: Sonido fuerte
            alerta_tipo = f"🟡 SISMO PERCEPTIBLE EN {ubicacion_usuario['ciudad'].upper()}"
        debe_sonar = True
    else:
        # Sismo en otra región del país que no se sentirá en tu ubicación
        prioridad = "low"
        alerta_tipo = f"ℹ️ Sismo en Perú (Lejano a {ubicacion_usuario['ciudad']})"
        debe_sonar = False

    return debe_sonar, prioridad, alerta_tipo, dist, tiempo_llegada_segundos

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
    <title>Monitor Sísmico Perú · Control de Ubicación</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 24px; text-align: center; }}
        .card {{ max-width: 480px; margin: 0 auto; background: #1e293b; border-radius: 16px; padding: 24px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); border: 1px solid #334155; }}
        h1 {{ font-size: 24px; margin-bottom: 8px; color: #38bdf8; }}
        .badge {{ display: inline-block; background: #0284c7; color: white; padding: 6px 14px; border-radius: 999px; font-weight: bold; margin-bottom: 20px; }}
        .status {{ background: #0f172a; padding: 16px; border-radius: 12px; margin-bottom: 20px; text-align: left; }}
        .btn {{ display: block; width: 100%; padding: 14px; margin: 10px 0; border: none; border-radius: 10px; font-size: 16px; font-weight: bold; cursor: pointer; transition: 0.2s; }}
        .btn-gps {{ background: #10b981; color: white; }}
        .btn-city {{ background: #3b82f6; color: white; }}
        .btn-test {{ background: #ef4444; color: white; margin-top: 20px; }}
        .btn:hover {{ opacity: 0.9; transform: scale(1.02); }}
    </style>
</head>
<body>
    <div class="card">
        <h1>🚨 Monitor Sísmico Perú</h1>
        <div class="badge">Canal ntfy: {NTFY_TOPIC}</div>
        
        <div class="status">
            <div><strong>📍 Ubicación activa:</strong> <span id="current-city">{ubicacion_usuario['ciudad']}</span></div>
            <div><strong>🌐 Coordenadas:</strong> <span id="current-coords">{ubicacion_usuario['lat']:.4f}, {ubicacion_usuario['lon']:.4f}</span></div>
        </div>

        <button class="btn btn-gps" onclick="obtenerGPS()">📍 Usar mi GPS actual (Móvil/Laptop)</button>
        
        <div style="margin: 16px 0; color: #94a3b8; font-size: 14px;">— O seleccionar ciudad rápida —</div>
        <button class="btn btn-city" onclick="cambiarCiudad('Lima', -12.0464, -77.0428)">📍 Estoy en Lima</button>
        <button class="btn btn-city" onclick="cambiarCiudad('Bagua (Amazonas)', -5.6398, -78.5312)">📍 Estoy en Bagua</button>
        <button class="btn btn-city" onclick="cambiarCiudad('Arequipa', -16.4090, -71.5375)">📍 Estoy en Arequipa</button>
        <button class="btn btn-city" onclick="cambiarCiudad('Trujillo', -8.1118, -79.0287)">📍 Estoy en Trujillo</button>
        <button class="btn btn-city" onclick="cambiarCiudad('Cusco', -13.5319, -71.9675)">📍 Estoy en Cusco</button>

        <button class="btn btn-test" onclick="probarAlarma()">🔔 Probar Alarma en mi Celular</button>
    </div>

    <script>
        function cambiarCiudad(nombre, lat, lon) {{
            fetch('/actualizar_ubicacion', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ ciudad: nombre, lat: lat, lon: lon }})
            }}).then(res => res.json()).then(data => {{
                document.getElementById('current-city').innerText = data.ciudad;
                document.getElementById('current-coords').innerText = data.lat.toFixed(4) + ', ' + data.lon.toFixed(4);
                alert('✅ Ubicación actualizada a ' + data.ciudad);
            }});
        }}

        function obtenerGPS() {{
            if (!navigator.geolocation) {{
                alert('Tu navegador no soporta geolocalización.');
                return;
            }}
            navigator.geolocation.getCurrentPosition(pos => {{
                cambiarCiudad('Ubicación GPS Automática', pos.coords.latitude, pos.coords.longitude);
            }}, err => {{
                alert('No se pudo obtener GPS: ' + err.message);
            }});
        }}

        function probarAlarma() {{
            fetch('/test_alarma', {{ method: 'POST' }})
                .then(res => res.text())
                .then(txt => alert('🔔 ' + txt));
        }}
    </script>
</body>
</html>"""
    return web.Response(text=html, content_type="text/html")

async def actualizar_ubicacion_handler(request):
    global ubicacion_usuario
    data = await request.json()
    ubicacion_usuario["lat"] = float(data.get("lat"))
    ubicacion_usuario["lon"] = float(data.get("lon"))
    ubicacion_usuario["ciudad"] = str(data.get("ciudad", "Ubicación personal"))
    guardar_ubicacion()
    print(f"📍 Ubicación actualizada a: {ubicacion_usuario['ciudad']} ({ubicacion_usuario['lat']:.4f}, {ubicacion_usuario['lon']:.4f})")
    return web.json_response(ubicacion_usuario)

async def test_alarma_handler(request):
    titulo = f"🔔 PRUEBA DE ALARMA - {ubicacion_usuario['ciudad']}"
    cuerpo = (
        f"Esta es una prueba de sonido de emergencia para tu ubicación activa ({ubicacion_usuario['ciudad']}).\n"
        f"Si escuchaste este aviso, tu sistema de alerta sísmica está 100% operativo."
    )
    enviar_ntfy(titulo, cuerpo, prioridad="urgent", tags="bell,rotating_light")
    return web.Response(text="Alarma de prueba enviada a tu app ntfy.")

# ================= MOTOR SÍSMICO EN TIEMPO REAL =================
async def monitor_sismos():
    uri = "wss://www.seismicportal.eu/standing_order/websocket"
    print(f"\n=======================================================")
    print(f"🟢 MONITOR SÍSMICO PERÚ ACTIVADO")
    print(f"📍 Ubicación inicial: {ubicacion_usuario['ciudad']}")
    print(f"📲 Canal ntfy en tu móvil: https://ntfy.sh/{NTFY_TOPIC}")
    print(f"🌐 Panel de control web: http://localhost:{PORT}")
    print(f"=======================================================\n")
    
    while True:
        try:
            async with websockets.connect(uri, ping_interval=20, ping_timeout=20) as ws:
                print("⚡ Conectado al flujo global de estaciones sísmicas...")
                while True:
                    mensaje = await ws.recv()
                    evento = json.loads(mensaje)
                    
                    if evento.get("action") in ["create", "update"]:
                        props = evento.get("data", {}).get("properties", {})
                        lat = float(props.get("lat", 0))
                        lon = float(props.get("lon", 0))
                        mag = float(props.get("mag", 0))
                        depth = float(props.get("depth", 0))
                        region = props.get("flynn_region", "Perú")

                        # Filtrar solo sismos dentro de Perú
                        if (PERU_BOUNDS["min_lat"] <= lat <= PERU_BOUNDS["max_lat"] and
                            PERU_BOUNDS["min_lon"] <= lon <= PERU_BOUNDS["max_lon"]):
                            
                            debe_sonar, prioridad, alerta_tipo, dist, t_seg = evaluar_impacto_usuario(lat, lon, mag, depth)
                            tsunami_txt = evaluar_tsunami(mag, depth, lon)

                            print(f"\n[EVENTO SÍSMICO] M{mag:.1f} en {region} (Prof: {depth:.0f}km) | Distancia a {ubicacion_usuario['ciudad']}: {dist:.0f}km | Debe sonar: {debe_sonar}")

                            # Si el sismo se sentirá en tu ubicación, o es un sismo mayor a 5.5 en el país
                            if debe_sonar or mag >= 5.5:
                                titulo = f"🚨 M {mag:.1f} - a {dist:.0f} km de {ubicacion_usuario['ciudad']}"
                                cuerpo = (
                                    f"{alerta_tipo}\n\n"
                                    f"📍 Tu ubicación actual: {ubicacion_usuario['ciudad']}\n"
                                    f"💥 Magnitud: M {mag:.1f}\n"
                                    f"📏 Distancia al epicentro: {dist:.0f} km\n"
                                    f"⏱️ Tiempo estimado de llegada: ~{t_seg} s\n"
                                    f"🌊 Tsunami: {tsunami_txt}\n\n"
                                    f"Conserva la calma y ubícate en zonas seguras."
                                )
                                actions = f"view, Ver Epicentro en Mapa, https://maps.google.com/?q={lat},{lon}"
                                tags = "warning,rotating_light" if prioridad == "urgent" else "information_source"
                                enviar_ntfy(titulo, cuerpo, prioridad=prioridad, tags=tags, actions=actions)

        except Exception as e:
            print(f"⚠️ Reconectando websocket en 5 segundos... ({e})")
            await asyncio.sleep(5)

async def main():
    app = web.Application()
    app.router.add_get("/", index_handler)
    app.router.add_post("/actualizar_ubicacion", actualizar_ubicacion_handler)
    app.router.add_post("/test_alarma", test_alarma_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    # Ejecutar servidor web y monitor sísmico en paralelo
    await monitor_sismos()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Monitor sísmico detenido por el usuario.")
