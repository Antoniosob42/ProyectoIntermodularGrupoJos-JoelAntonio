import M5
from M5 import Widgets
import time
import network
import requests
import json
import gc
# --- Funciones Generales Reutilizadas y Adaptadas ---
def check_wifi(wifi):
    global wifi_conectado, ULTIMA_REVISION_WIFI
    now = time.ticks_ms()
    if time.ticks_diff(now, ULTIMA_REVISION_WIFI) < WIFI_CHECK_INTERVAL:
        return wifi_conectado

    ULTIMA_REVISION_WIFI = now
    wifi_conectado = wifi.isconnected()
    return wifi_conectado

def get_date_str():
    t = time.localtime()
    return "{:04d}-{:02d}-{:02d}".format(t[0], t[1], t[2])

def sincronizar_pendientes():
    global telemetria_pendientes
    if not wifi_conectado or not telemetria_pendientes:
        return

    print("🔄 Sincronizando telemetría pendiente...")
    for item in telemetria_pendientes:
        try:
            url = f"{FIREBASE_DB_URL}historial_telemetria/{item['fecha']}.json"
            res = requests.post(url, data=json.dumps(item["data"]))
            res.close()
            del res
        except:
            print("⚠️ Error enviando paquete pendiente")
            return
    telemetria_pendientes = []
    print("✅ Sincronización completa")

def enviar_telemetria_instantanea(data):
    """Envía los datos de forma casi instantánea a Firebase"""
    url = f"{FIREBASE_DB_URL}telemetria_actual.json"
    try:
        # Usamos PUT para actualizar en tiempo real la posición actual del coche
        res = requests.put(url, data=json.dumps(data))
        res.close()
        del res
    except Exception as e:
        print("❌ Error enviando telemetria:", e)
        gc.collect()

# --- Funciones Mock para Sensores de Coche (A implementar según hardware físico) ---

def leer_velocidad():
    # TODO: Implementar lectura real (ej. por acelerómetro, encoder de rueda o GPS)
    return 0.0

def leer_rpm():
    # TODO: Implementar lectura real (ej. interrupción por pin conectado al encendido/ECU)
    return 0

def leer_gps():
    # TODO: Implementar lectura real (ej. módulo GPS UART M5Stack / Unit GPS)
    # Debe retornar un diccionario con latitud y longitud
    return {"lat": 0.0, "lng": 0.0}


M5.begin()

# --- Configuración de Pantalla ---
Widgets.fillScreen(0x111111)

label_status = Widgets.Label(
    "Estado: Inicializando...", 20, 20, 1.0, 0xFFFFFF, 0x111111, Widgets.FONTS.DejaVu18
)
label_speed = Widgets.Label(
    "Velocidad: 0 km/h", 20, 60, 1.0, 0x00FF00, 0x111111, Widgets.FONTS.DejaVu18
)
label_rpm = Widgets.Label(
    "RPM: 0", 20, 100, 1.0, 0xFFFF00, 0x111111, Widgets.FONTS.DejaVu18
)
label_gps = Widgets.Label(
    "GPS: Buscando...", 20, 140, 1.0, 0x00FFFF, 0x111111, Widgets.FONTS.DejaVu18
)
label_battery = Widgets.Label(
    "Bateria: %", 20, 180, 1.0, 0xFFFFFF, 0x111111, Widgets.FONTS.DejaVu18
)

# --- Estado de conectividad (Reutilizado de movilidad.py) ---
wifi_conectado = False
ULTIMA_REVISION_WIFI = 0
WIFI_CHECK_INTERVAL = 15000  # ms (15 segundos)

# --- Colas locales para modo Offline (Reutilizado) ---
telemetria_pendientes = []

# --- Configuración Firebase y WiFi ---
BASE_URL = "Insertar url aqui."

VEHICULO_ID = "coche_01" 
FIREBASE_DB_URL = f"{BASE_URL}/vehiculos/{VEHICULO_ID}/"

wifi = network.WLAN(network.STA_IF)
wifi.active(True)
wifi.connect("NAVIGO7DFE35", "nJteQxdqYWm7fw25")

for i in range(20):
    if wifi.isconnected():
        break
    time.sleep(1)

print("WiFi conectado:", wifi.isconnected())




# --- Bucle Principal de Alta Frecuencia ---

# Intervalo para envío en tiempo real (ej. cada 200 ms para alta velocidad/fluidez -> 5 veces por segundo)
FRECUENCIA_ENVIO_MS = 200 
last_send_time = time.ticks_ms()

print("🚀 Iniciando sistema de telemetría de carreras...")

while True:
    M5.update()
    now = time.ticks_ms()
    battery = M5.Power.getBatteryLevel()

    # 1. Lectura de sensores del coche
    velocidad = leer_velocidad()
    rpm = leer_rpm()
    gps_pos = leer_gps()

    # 2. Actualizar pantalla localmente de forma inmediata
    label_status.setText("Estado: En pista 🏎️")
    label_speed.setText(f"Velocidad: {velocidad:.1f} km/h")
    label_rpm.setText(f"RPM: {rpm}")
    label_gps.setText(f"GPS: {gps_pos['lat']:.4f}, {gps_pos['lng']:.4f}")
    label_battery.setText(f"Bateria: {battery}%")

    # 3. Envío casi instantáneo a Firebase controlado por tiempo (ej. cada 200ms)
    if time.ticks_diff(now, last_send_time) >= FRECUENCIA_ENVIO_MS:
        last_send_time = now
        
        paquete_datos = {
            "timestamp": time.time(),
            "velocidad": velocidad,
            "rpm": rpm,
            "posicion": gps_pos,
            "bateria": battery
        }

        if check_wifi(wifi):
            # Sincronizar paquetes acumulados si los hubiera
            sincronizar_pendientes()
            # Enviar dato actual en tiempo real
            enviar_telemetria_instantanea(paquete_datos)
        else:
            # Guardar en cola local si se corta la conexión en plena carrera
            fecha_actual = get_date_str()
            telemetria_pendientes.append({
                "fecha": fecha_actual,
                "data": paquete_datos
            })
            print("📦 Sin conexión: Telemetría guardada en búfer local")

    # Pausa muy breve para no saturar el procesador del M5Stack (permite ~50 Hz de muestreo interno)
    time.sleep(0.02)