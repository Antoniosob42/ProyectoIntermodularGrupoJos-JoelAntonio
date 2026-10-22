import M5
from M5 import Widgets, BtnA, BtnB
import time
import network
import requests
import json
import gc
import math

# URL de la Realtime Database de Firebase
BASE_URL = "https://b-42-4988d-default-rtdb.europe-west1.firebasedatabase.app"

# Credenciales de red
WIFI_SSID = "B14-ALUMNOS"
WIFI_PASSWORD = "56#?qwal"

# Configuración de tiempos
FRECUENCIA_ENVIO_MS = 1000
WIFI_CHECK_INTERVAL = 2000

wifi_conectado = False
ULTIMA_REVISION_WIFI = 0
telemetria_pendientes = []

ID_SESION = ""
INICIO_SESION = 0

# Variables para el cálculo de velocidad por integración de IMU
velocidad_actual = 0.0
ultimo_tiempo_vel = time.ticks_ms()

def timestamp_ms():
    try:
        diferencia = 946684800 if time.gmtime(0)[0] == 2000 else 0
        return int((time.time() + diferencia) * 1000)
    except Exception:
        return int(time.ticks_ms())

# Función para obtener la hora formateada (HH:MM:SS)
def obtener_hora_exacta():
    try:
        t = time.localtime()
        return "{:02d}:{:02d}:{:02d}".format(t[3], t[4], t[5])
    except Exception:
        return "00:00:00"

def firebase_request(metodo, ruta, datos=None):
    url = BASE_URL + "/" + ruta + ".json"
    respuesta = None

    try:
        cuerpo = json.dumps(datos) if datos is not None else None

        if metodo == "POST":
            respuesta = requests.post(url, data=cuerpo)
        elif metodo == "PUT":
            respuesta = requests.put(url, data=cuerpo)
        elif metodo == "PATCH":
            respuesta = requests.patch(url, data=cuerpo)
        elif metodo == "GET":
            respuesta = requests.get(url)
        else:
            return False, None

        correcto = 200 <= respuesta.status_code < 300
        resultado = respuesta.json() if correcto and metodo in ("POST", "GET") else None
        return correcto, resultado

    except Exception as e:
        print("Error de Firebase:", e)
        gc.collect()
        return False, None

    finally:
        if respuesta is not None:
            try:
                respuesta.close()
            except Exception:
                pass

def crear_sesion_firebase():
    global ID_SESION, INICIO_SESION

    if ID_SESION:
        return True

    INICIO_SESION = timestamp_ms()
    datos_sesion = {
        "inicio": INICIO_SESION,
        "fin": 0,
        "estado": "en_curso"
    }

    correcto, respuesta = firebase_request("POST", "sesiones", datos_sesion)

    if not correcto or not respuesta or "name" not in respuesta:
        print("No se pudo crear la sesión en Firebase")
        return False

    ID_SESION = respuesta["name"]

    estado_actual = {
        "activa": True,
        "id": ID_SESION,
        "inicio": INICIO_SESION
    }
    firebase_request("PATCH", "sesion_actual", estado_actual)
    print("Sesión creada:", ID_SESION)
    return True

def sincronizar_pendientes():
    global wifi_conectado, ID_SESION
    if not wifi_conectado or not ID_SESION:
        return

    while telemetria_pendientes:
        dato = telemetria_pendientes[0]
        correcto, _ = firebase_request("POST", "sesiones/" + ID_SESION + "/datos", dato)

        if not correcto:
            print("No se pudo sincronizar una lectura pendiente")
            return

        telemetria_pendientes.pop(0)

    print("Lecturas pendientes sincronizadas")

def enviar_telemetria_instantanea(data):
    if not wifi_conectado:
        telemetria_pendientes.append(data)
        return

    if not crear_sesion_firebase():
        telemetria_pendientes.append(data)
        return

    sincronizar_pendientes()

    correcto, _ = firebase_request("POST", "sesiones/" + ID_SESION + "/datos", data)
    if not correcto:
        telemetria_pendientes.append(data)
        return

    estado_actual = {
        "activa": True,
        "id": ID_SESION,
        "inicio": INICIO_SESION,
        "ultimo_dato": data
    }
    firebase_request("PATCH", "sesion_actual", estado_actual)

def registrar_marca_boton():
    global ID_SESION, wifi_conectado
    hora_marca = obtener_hora_exacta()
    print("¡Botón A pulsado! Hora marca:", hora_marca)
    
    datos_marca = {
        "timestamp": timestamp_ms(),
        "hora_marca": hora_marca,
        "tipo": "marca_manual"
    }
    
    if wifi_conectado and ID_SESION:
        correcto, _ = firebase_request("POST", "sesiones/" + ID_SESION + "/marcas", datos_marca)
        if correcto:
            print("Marca enviada a Firebase con éxito.")
        else:
            print("Error al enviar la marca a Firebase.")
    else:
        print("Sin Wi-Fi o sesión activa, no se pudo enviar la marca.")

# Función para finalizar sesión y apagar el hardware por completo (consumo 0)
def apagar_dispositivo():
    print("Apagando dispositivo por solicitud del usuario...")
    finalizar_sesion()
    
    # Mostrar aviso en pantalla antes del apagado
    Widgets.fillScreen(0x000000)
    Widgets.Label("Sistema Apagado", 60, 100, 1.0, 0xFF0000, 0x000000, Widgets.FONTS.DejaVu18)
    time.sleep(1.5)
    
    try:
        M5.Power.powerOff()  # Corta la alimentación del PMIC (consumo cero real en batería)
    except Exception as e:
        print("Error al apagar hardware:", e)

def check_wifi(wifi):
    global wifi_conectado, ULTIMA_REVISION_WIFI

    ahora = time.ticks_ms()
    if time.ticks_diff(ahora, ULTIMA_REVISION_WIFI) < WIFI_CHECK_INTERVAL:
        return wifi_conectado

    ULTIMA_REVISION_WIFI = ahora
    wifi_conectado = wifi.isconnected()
    return wifi_conectado

def leer_aceleracion():
    try:
        accel = M5.Imu.getAccel()
        return {
            "x": round(float(accel[0]), 3),
            "y": round(float(accel[1]), 3),
            "z": round(float(accel[2]), 3)
        }
    except Exception:
        return {"x": 0.0, "y": 0.0, "z": 0.0}

def calcular_velocidad(accel_x):
    global velocidad_actual, ultimo_tiempo_vel
    
    ahora = time.ticks_ms()
    dt = time.ticks_diff(ahora, ultimo_tiempo_vel) / 1000.0
    if dt <= 0:
        dt = 0.02
    ultimo_tiempo_vel = ahora

    if abs(accel_x) < 0.03:
        accel_x = 0.0
        velocidad_actual *= 0.90
        if velocidad_actual < 0.2:
            velocidad_actual = 0.0

    accel_ms2 = accel_x * 9.81
    velocidad_ms = (velocidad_actual / 3.6) + (accel_ms2 * dt)
    
    if velocidad_ms < 0:
        velocidad_ms = 0.0

    velocidad_actual = velocidad_ms * 3.6
    return round(velocidad_actual, 1)

def leer_rpm(accel_y):
    rpm = int(abs(accel_y) * 3000)
    return min(rpm, 8500)

def leer_gps():
    return {"lat": 38.9848, "lng": -3.9274}

def finalizar_sesion():
    if not ID_SESION or not wifi.isconnected():
        return

    firebase_request("PATCH", "sesiones/" + ID_SESION, {
        "fin": timestamp_ms(),
        "estado": "finalizada"
    })
    firebase_request("PATCH", "sesion_actual", {
        "activa": False,
        "id": "",
        "inicio": 0
    })
    print("Sesión finalizada")

M5.begin()

# --- Configuración de pantalla ---
Widgets.fillScreen(0x111111)

label_status = Widgets.Label(
    "A: Marcar | B: Apagar", 10, 10, 0.6, 0xFFFFFF, 0x111111, Widgets.FONTS.DejaVu18
)
label_speed = Widgets.Label(
    "Velocidad: 0.0 km/h", 20, 50, 1.0, 0x00FF00, 0x111111, Widgets.FONTS.DejaVu18
)
label_rpm = Widgets.Label(
    "RPM: 0", 20, 90, 1.0, 0xFFFF00, 0x111111, Widgets.FONTS.DejaVu18
)
label_time = Widgets.Label(
    "Hora: 00:00:00", 20, 130, 1.0, 0xFF00FF, 0x111111, Widgets.FONTS.DejaVu18
)
label_gps = Widgets.Label(
    "GPS: Fijo", 20, 170, 1.0, 0x00FFFF, 0x111111, Widgets.FONTS.DejaVu18
)
label_battery = Widgets.Label(
    "Bateria: %", 20, 210, 0.8, 0xFFFFFF, 0x111111, Widgets.FONTS.DejaVu18
)

wifi = network.WLAN(network.STA_IF)
wifi.active(True)
wifi.connect(WIFI_SSID, WIFI_PASSWORD)

for i in range(20):
    if wifi.isconnected():
        break
    time.sleep(1)

wifi_conectado = wifi.isconnected()
print("WiFi conectado:", wifi_conectado)

last_send_time = time.ticks_ms()
print("Iniciando sistema de telemetría...")

try:
    while True:
        M5.update()
        now = time.ticks_ms()
        battery = M5.Power.getBatteryLevel()

        # Comprobar botones táctiles
        if BtnA.wasPressed():
            registrar_marca_boton()
            
        if BtnB.wasPressed():
            apagar_dispositivo()
            break

        # Lectura de sensores y hora actual
        acel = leer_aceleracion()
        velocidad = calcular_velocidad(acel["x"])
        rpm = leer_rpm(acel["y"])
        gps_pos = leer_gps()
        hora_actual = obtener_hora_exacta()

        # Actualizar pantalla local
        label_speed.setText(f"Velocidad: {velocidad:.1f} km/h")
        label_rpm.setText(f"RPM: {rpm}")
        label_time.setText(f"Hora: {hora_actual}")
        label_gps.setText(f"GPS: {gps_pos['lat']}, {gps_pos['lng']}")
        label_battery.setText(f"Bateria: {battery}%")

        if time.ticks_diff(now, last_send_time) >= FRECUENCIA_ENVIO_MS:
            last_send_time = now

            paquete_datos = {
                "timestamp": timestamp_ms(),
                "hora_exacta": hora_actual,
                "aceleracion_x": acel["x"],
                "aceleracion_y": acel["y"],
                "aceleracion_z": acel["z"],
                "latitud": gps_pos["lat"],
                "longitud": gps_pos["lng"],
                "velocidad": velocidad,
                "rpm": rpm,
                "bateria_m5": battery
            }

            if check_wifi(wifi):
                enviar_telemetria_instantanea(paquete_datos)
            else:
                telemetria_pendientes.append(paquete_datos)
                print("Sin Wi-Fi: lectura guardada en memoria")

        time.sleep(0.02)

except KeyboardInterrupt:
    finalizar_sesion()