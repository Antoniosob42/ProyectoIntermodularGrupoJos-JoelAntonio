import M5
from M5 import Widgets
import time
import network
import requests
import json
import gc

# CAMBIO 1: se usa la URL de la Realtime Database que ya hemos probado.
BASE_URL = "https://b-42-4988d-default-rtdb.europe-west1.firebasedatabase.app"

# CAMBIO 2: las credenciales no se publican; rellénalas en tu copia local.
WIFI_SSID = "PON_AQUI_EL_NOMBRE_DE_TU_WIFI"
WIFI_PASSWORD = "PON_AQUI_LA_CONTRASENA_DE_TU_WIFI"

# CAMBIO 3: enviamos una lectura por segundo en vez de cinco para no generar
# peticiones innecesarias mientras probamos el proyecto.
FRECUENCIA_ENVIO_MS = 1000
WIFI_CHECK_INTERVAL = 2000

wifi_conectado = False
ULTIMA_REVISION_WIFI = 0
telemetria_pendientes = []

# CAMBIO 4: estos valores identifican la sesión que se guarda en Firebase.
ID_SESION = ""
INICIO_SESION = 0

# CAMBIO 5: se usa un timestamp en milisegundos, como en la prueba de Firebase.
def timestamp_ms():
    try:
        # Algunas versiones de MicroPython cuentan desde el año 2000.
        diferencia = 946684800 if time.gmtime(0)[0] == 2000 else 0
        return int((time.time() + diferencia) * 1000)
    except Exception:
        return int(time.ticks_ms())


# CAMBIO 6: función pequeña para enviar datos a cualquier ruta de Firebase.
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


# CAMBIO 7: Firebase crea el identificador de la sesión automáticamente con POST.
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

    # CAMBIO 8: actualizamos el nodo sesion_actual que ya existe en tu base de datos.
    estado_actual = {
        "activa": True,
        "id": ID_SESION,
        "inicio": INICIO_SESION
    }
    firebase_request("PATCH", "sesion_actual", estado_actual)
    print("Sesión creada:", ID_SESION)
    return True


# CAMBIO 9: las lecturas que quedaron pendientes se añaden al historial al volver Wi-Fi.
def sincronizar_pendientes():
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


# CAMBIO 10: cada lectura se guarda en el historial y se actualiza la lectura actual.
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


# CAMBIO 11: comprobamos el Wi-Fi con más frecuencia para detectar reconexiones.
def check_wifi(wifi):
    global wifi_conectado, ULTIMA_REVISION_WIFI

    ahora = time.ticks_ms()
    if time.ticks_diff(ahora, ULTIMA_REVISION_WIFI) < WIFI_CHECK_INTERVAL:
        return wifi_conectado

    ULTIMA_REVISION_WIFI = ahora
    wifi_conectado = wifi.isconnected()
    return wifi_conectado


# Funciones de sensores originales: todavía están pendientes de conectar al hardware.
def leer_velocidad():
    return 0.0


def leer_rpm():
    return 0


def leer_gps():
    return {"lat": 0.0, "lng": 0.0}


# CAMBIO 12: función para marcar la sesión como finalizada al parar el programa.
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

# --- Configuración de pantalla original ---
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

# CAMBIO 13: conectar usando las variables definidas al principio del archivo.
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

        velocidad = leer_velocidad()
        rpm = leer_rpm()
        gps_pos = leer_gps()

        label_status.setText("Estado: En pista")
        label_speed.setText(f"Velocidad: {velocidad:.1f} km/h")
        label_rpm.setText(f"RPM: {rpm}")
        label_gps.setText(f"GPS: {gps_pos['lat']:.4f}, {gps_pos['lng']:.4f}")
        label_battery.setText(f"Bateria: {battery}%")

        if time.ticks_diff(now, last_send_time) >= FRECUENCIA_ENVIO_MS:
            last_send_time = now

            # CAMBIO 14: usamos los nombres de campos que ya tiene tu Firebase.
            paquete_datos = {
                "timestamp": timestamp_ms(),
                "aceleracion_x": 0,
                "aceleracion_y": 0,
                "aceleracion_z": 0,
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
    # CAMBIO 15: al detener el programa normalmente, cerramos la sesión en Firebase.
    finalizar_sesion()
