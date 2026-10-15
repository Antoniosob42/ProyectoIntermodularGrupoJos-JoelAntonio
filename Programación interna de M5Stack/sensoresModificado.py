import M5
from M5 import Widgets
import time
import network
import requests
import json
import gc

# --- Configuracion Firebase y WiFi ---
# CAMBIO 1: usamos la URL real de TU Realtime Database, en vez de la URL vacia/anterior.
BASE_URL = "https://b-42-4988d-default-rtdb.europe-west1.firebasedatabase.app"
# CAMBIO 2: las credenciales se dejan como marcadores para no publicar la contrasena en el codigo.
# Rellenar estos dos valores en el dispositivo antes de ejecutarlo.
WIFI_SSID = "PON_AQUI_EL_NOMBRE_DE_TU_WIFI"
WIFI_PASSWORD = "PON_AQUI_LA_CONTRASENA_DE_TU_WIFI"

# CAMBIO 3: se reduce el envio de 5 lecturas/segundo a 1 lectura/segundo.
# Esto limita las peticiones a Firebase y sirve para la telemetria actual del proyecto.
FRECUENCIA_ENVIO_MS = 1000
# CAMBIO 4: comprobamos el Wi-Fi cada 2 segundos en vez de depender de un estado inicial.
WIFI_CHECK_INTERVAL = 2000

# --- Estado general ---
wifi_conectado = False
ULTIMA_REVISION_WIFI = 0
# CAMBIO 5: la cola guarda lecturas que todavía no se han podido enviar a Firebase.
telemetria_pendientes = []
# CAMBIO 6: variables para crear una sesión única y mantener su historial.
sesion_creada_firebase = False
ID_SESION = ""
INICIO_SESION = 0
# CAMBIO 7: variables usadas para calcular timestamps en milisegundos y mantenerlos estables.
reloj_sincronizado = False
intento_sincronizar_reloj = False
BASE_TIEMPO_MS = 0
BASE_TICKS_MS = 0


# CAMBIO 8: Firebase recibirá la fecha como timestamp Unix en milisegundos;
# el código original enviaba time.time() directamente, normalmente en segundos.
def timestamp_ms():
    """Devuelve timestamp Unix en milisegundos, usando ticks para subsegundos."""
    try:
        if reloj_sincronizado:
            return BASE_TIEMPO_MS + time.ticks_diff(time.ticks_ms(), BASE_TICKS_MS)

        # La mayoria de placas MicroPython usan la epoca 2000, no la de Unix (1970).
        desplazamiento = 946684800 if time.gmtime(0)[0] == 2000 else 0
        return int((time.time() + desplazamiento) * 1000)
    except Exception:
        return int(time.ticks_ms())


# CAMBIO 9: sincronizamos el reloj por NTP cuando hay Wi-Fi para que las fechas
# de distintas lecturas/sesiones sean comparables y no dependan solo del arranque.
def sincronizar_reloj():
    """Intenta sincronizar la hora por Internet para guardar timestamps correctos."""
    global reloj_sincronizado, intento_sincronizar_reloj
    global BASE_TIEMPO_MS, BASE_TICKS_MS

    intento_sincronizar_reloj = True
    try:
        import ntptime
        ntptime.settime()

        desplazamiento = 946684800 if time.gmtime(0)[0] == 2000 else 0
        BASE_TIEMPO_MS = int((time.time() + desplazamiento) * 1000)
        BASE_TICKS_MS = time.ticks_ms()
        reloj_sincronizado = True
        print("Hora sincronizada por Internet")
    except Exception as e:
        reloj_sincronizado = False
        print("No se pudo sincronizar la hora:", e)
        print("Se intentara continuar; los timestamps podrian no ser correctos.")


# CAMBIO 10: centralizamos las peticiones REST a Firebase en una sola función.
# Así todas las rutas usan el mismo BASE_URL, control de errores y cierre de respuesta.
def firebase_request(metodo, ruta, datos=None):
    """Realiza una peticion a la API REST de Firebase y cierra la respuesta."""
    respuesta = None
    resultado = None
    url = BASE_URL + "/" + ruta + ".json"

    try:
        if metodo == "GET":
            respuesta = requests.get(url)
        elif metodo == "PUT":
            respuesta = requests.put(
                url, data=json.dumps(datos),
                headers={"Content-Type": "application/json"}
            )
        elif metodo == "PATCH":
            respuesta = requests.patch(
                url, data=json.dumps(datos),
                headers={"Content-Type": "application/json"}