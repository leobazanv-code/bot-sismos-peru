# Bot Sismos Perú

Monitor sísmico que envía alertas push al celular, vía [ntfy](https://ntfy.sh), cuando ocurre un sismo de magnitud ≥ 3.8 en Perú o su franja marítima.

> **Aviso:** este bot es informativo. No reemplaza a los canales oficiales. Los avisos de tsunami en Perú los emite la **DHN** (Marina de Guerra) y los difunde **INDECI**. La estimación de tsunami del bot es una regla simple, no un aviso oficial.

## Cómo funciona

- **EMSC** (`wss://www.seismicportal.eu/standing_order/websocket`): fuente principal, en tiempo real por WebSocket.
- **USGS** (API FDSN): respaldo, consultado cada 25 s. Solo alerta sismos de las últimas 2 h.
- **Zona**: rectángulo lat -19.5 a 0.5, lon -82.5 a -68.0. Es aproximado: también incluye el sur de Ecuador, Acre (Brasil), el oeste de Bolivia y Arica (Chile).
- **Sin duplicados**: un mismo sismo no se alerta dos veces, aunque lo reporten EMSC y USGS con IDs distintos. Se consideran el mismo sismo si difieren menos de 2 min y 150 km.
- **Prioridad ntfy**: `urgent` (5) si M ≥ 4.2, `high` (4) si M < 4.2. Que vibre y suene depende también del teléfono (No Molestar, ajustes del canal en la app ntfy).
- **Tsunami (estimación)**: se evalúa solo si el epicentro está en el mar o a menos de 50 km de la costa. M ≥ 7.0 y profundidad < 60 km da "posible tsunami". M ≥ 6.5 y profundidad < 70 km da "revisar avisos de la DHN".
- **Tiempo de onda**: tiempo de llegada de la onda S (3.5 km/s) a la ciudad más cercana y a Lima, contado desde el origen del sismo. Cuando llega la alerta, ese tiempo puede haber pasado ya.

## Instalación local

Requiere Python 3.9 o superior.

```powershell
pip install -r requirements.txt
python bot_sismos.py
```

En Windows también puedes usar `iniciar_bot.bat`. El panel queda en http://localhost:8088.

## Recibir las alertas en el celular

1. Instala la app **ntfy** (Android o iOS).
2. Suscríbete al canal definido en `NTFY_TOPIC`.
3. Abre el panel web y pulsa **Probar Vibración y Alarma**.
4. Si no suena, revisa en la app ntfy la configuración del canal y permite que ignore No Molestar.

## Variables de entorno

| Variable | Uso | Valor por defecto |
|---|---|---|
| `NTFY_TOPIC` | Canal ntfy al que se envían las alertas | `sismos_peru_dm_alerta` |
| `TEST_TOKEN` | Si se define, el botón de prueba pide este token | vacío (botón abierto) |
| `NTFY_TOKEN` | Token de acceso de una cuenta ntfy.sh (`tk_...`). Necesario en Render: sin cuenta, ntfy limita por IP y la IP compartida de Render agota la cuota diaria (error 429) | vacío (envío anónimo) |
| `PORT` | Puerto del panel web (Render lo asigna solo) | `8088` |

**Seguridad:** los canales de ntfy.sh son públicos. Quien conozca el nombre del canal puede leer tus alertas y enviarte avisos falsos. Usa un nombre difícil de adivinar en `NTFY_TOPIC` y no lo publiques. Define `TEST_TOKEN` para que nadie pueda hacer sonar tu teléfono desde el panel público.

## Despliegue en Render

- Tipo: **Web Service**. Build: `pip install -r requirements.txt`. Start: `python bot_sismos.py`.
- Define `NTFY_TOPIC`, `TEST_TOKEN` y `NTFY_TOKEN` en *Environment*.
- **Importante:** en el plan gratuito, Render apaga el servicio tras unos 15 min sin visitas web. Apagado, el bot **no vigila sismos**. Usa un plan de pago o un servicio externo que visite la URL cada pocos minutos (por ejemplo, UptimeRobot).

## Panel web

`GET /` muestra el estado real de las fuentes: conexión con EMSC, última consulta correcta a USGS y alertas enviadas en las últimas 3 h.
`POST /test_alarma` envía una alerta de prueba, como máximo una cada 10 min. Exige la cabecera `X-Token` si está definido `TEST_TOKEN`.
