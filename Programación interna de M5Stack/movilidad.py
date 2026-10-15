import M5
from M5 import Widgets
import time
import math
import random

import network
import requests
import json
import time
import gc

M5.begin()

battery = M5.Power.getBatteryLevel()     # %

# --- Estado de conectividad ---
wifi_conectado = False
ULTIMA_REVISION_WIFI = 0
WIFI_CHECK_INTERVAL = 15000  # ms (15 segundos)

# --- Cola local de sesiones pendientes ---
sesiones_pendientes = []
movimientos_pendientes = []

# Declaración de funciones


def check_wifi(wifi):
    global wifi_conectado, ULTIMA_REVISION_WIFI

    now = time.ticks_ms()
    if time.ticks_diff(now, ULTIMA_REVISION_WIFI) < WIFI_CHECK_INTERVAL:
        return wifi_conectado

    ULTIMA_REVISION_WIFI = now
    wifi_conectado = wifi.isconnected()

    if wifi_conectado:
        print("✅ WiFi disponible")
    else:
        print("❌ Sin WiFi")
    return wifi_conectado

def sincronizar_pendientes():
    global sesiones_pendientes, movimientos_pendientes

    if not wifi_conectado:
        return

    print("🔄 Sincronizando datos pendientes...")

    # Enviar sesiones
    for sesion in sesiones_pendientes:
        try:
            guardar_sesion(
                FIREBASE_DB_URL,
                sesion["fecha"],
                sesion["data"]
            )
        except:
            print("⚠️ Error enviando sesión pendiente")
            return  # salimos y lo intentamos luego

    sesiones_pendientes = []

    # Enviar tiempos acumulados
    for mov in movimientos_pendientes:
        try:
            update_today_time(
                FIREBASE_DB_URL,
                mov["fecha"],
                mov["tiempo"]
            )
        except:
            print("⚠️ Error enviando movimiento pendiente")
            return

    movimientos_pendientes = []

    print("✅ Sincronización completa")

# Función que calcula el nivel de habilidad de cada sesión en formato numérico
def calcular_score(colisiones, aceleraciones, paradas, estabilidad):
    score = 100
    score -= colisiones * 5
    score -= aceleraciones * 2
    score -= paradas * 2
    score += estabilidad * 0.3

    if score < 0:
        score = 0
    if score > 100:
        score = 100

    return int(score)

# Función para guardar la sesión en Firebase
def guardar_sesion(base_url, date, sesion):
    url = base_url + "sesiones/{}.json".format(date)

    try:
        res = requests.post(url, data=json.dumps(sesion))
        print("Sesión enviada:")
        res.close()
        del res
    except Exception as e:
        print("Error enviando sesión:", e)
        gc.collect()

# Función que calcula el nivel de habilidad de cada sesión en formato texto
def calcular_nivel(score):
    if score <= 20:
        return "Novato"
    elif score <= 30:
        return "Novato curioso"
    elif score <= 40:
        return "Principiante"
    elif score <= 50:
        return "Principiante avanzado"
    elif score <= 60:
        return "Principiante perfeccionado"
    elif score <= 75:
        return "Competente"
    elif score <= 90:
        return "Aventajado"
    else:
        return "Experto"

# Obtenemos fecha actual 
def get_date_str():
    t = time.localtime()
    return "{:04d}-{:02d}-{:02d}".format(t[0], t[1], t[2])

# Obtenemos registro de firebase de la fecha actual si existe 
def get_today_time(base_url, date):
    url = base_url + "movimiento/{}.json".format(date)
    
    try:
        res = requests.get(url)
        data = res.json()
        res.close()
        del res
        
        if data and "tiempo" in data:
            return data["tiempo"]
        else:
            return 0
        
    except:
        gc.collect()
        return 0

def update_today_time(base_url, date, tiempo_a_sumar):
    url = base_url + "movimiento/{}.json".format(date)

    try:
        # 1️⃣ Leer el valor actual REAL de Firebase
        res = requests.get(url)
        data = res.json()
        res.close()
        del res

        tiempo_actual = data["tiempo"] if data and "tiempo" in data else 0

        # 2️⃣ Sumar
        nuevo_total = tiempo_actual + tiempo_a_sumar

        # 3️⃣ Guardar
        payload = { "tiempo": nuevo_total }
        res = requests.put(url, data=json.dumps(payload))
        res.close()
        del res

        print("⏱ Tiempo acumulado actualizado:", nuevo_total)

        return nuevo_total

    except Exception as e:
        print("❌ Error actualizando tiempo:")
        gc.collect()
        raise e
# --------------------------------

# - Programa principal -

M5.begin()
Widgets.fillScreen(0x222222)

# Nombre del usuario de la aplicación. Debe cambiarse, es decir, cada niño tendrá su propio USER_ID
PIN = 543785
#USER_ID = "Alberto"



# 🔥 Firebase
FIREBASE_DB_URL = "https://movilidad-motorizada-default-rtdb.europe-west1.firebasedatabase.app/"

BASE_URL = "https://movilidad-motorizada-default-rtdb.europe-west1.firebasedatabase.app"

#FIREBASE_DB_URL = (
#    "https://movilidad-motorizada-default-rtdb.europe-west1.firebasedatabase.app/pacientes/{}/"
#)

# 🔌 Conectar WiFi
wifi = network.WLAN(network.STA_IF)
wifi.active(True)
#wifi.connect("iPhone", "MSVH-4K6g")
wifi.connect("NAVIGO7DFE35", "nJteQxdqYWm7fw25")

for i in range(20):
    if wifi.isconnected():
        break
    time.sleep(1)

print(wifi.isconnected())
print(wifi.ifconfig())

print("Conectando WiFi...")
#wifi = network.WLAN(network.STA_IF)
#wifi.active(True)
#wifi.connect("iPhone", "MSVH-4K6g")
#wifi.connect("NAVIGO7DFE35", "nJteQxdqYWm7fw25")

print("Intentando conectar a WiFi (modo no bloqueante)")

print("WiFi conectado:")

# Obtenemos id del paciente a partir del pin
def get_paciente_id_by_pin(base_url, pin):
    url = base_url + "/pacientes.json"
    print("URL: ", url)

    try:
        
        paciente_id = "-Osaydzqcj6V5IkQ_Gid"
        return paciente_id
        
        #query = '?orderBy="pin"&equalTo={}'.format(pin)
        #full_url = url + query
        #res = requests.get(full_url)
        
        #data = res.json()
        #res.close()
        #del res
        #if data:
            # Cogemos el primer resultado
        #    for paciente_id in data:
        #        print("✅ Paciente encontrado:", paciente_id)
        #        return paciente_id

        #print("❌ No existe paciente con ese PIN")
        #return None

    except Exception as e:
        print("Error buscando paciente:", e)
        gc.collect()
        return None

paciente_id = None

if check_wifi(wifi):
    paciente_id = get_paciente_id_by_pin(BASE_URL, PIN)

if paciente_id:
    FIREBASE_DB_URL = BASE_URL + "/pacientes/{}/".format(paciente_id)
    #print("✅ URL paciente:", FIREBASE_DB_URL)
else:
    print("⚠️ No se pudo obtener paciente")

fecha_actual = get_date_str()
print("Fecha actual: ", fecha_actual)

tiempo_acumulado = get_today_time(FIREBASE_DB_URL, fecha_actual)
print("Tiempo acumulado: ", tiempo_acumulado)

label_state = Widgets.Label(
    "Estado: Reposo",
    20, 60, 1.0,
    0xFFFFFF, 0x222222,
    Widgets.FONTS.DejaVu18
)

label_time = Widgets.Label(
    "Tiempo mov.: 0 s",
    20, 100, 1.0,
    0xFFFFFF, 0x222222,
    Widgets.FONTS.DejaVu18
)

label_battery = Widgets.Label(
    "Batería: %",
    20, 140, 1.0,
    0xFFFFFF, 0x222222,
    Widgets.FONTS.DejaVu18
)
# 🚨 Petición protegida
try:
    res = requests.put(url, data=json.dumps(payload))
    #print("Respuesta:", res.text)
    res.close()
    del res
except Exception as e:
    print("Error:", e)
    gc.collect()

# Temporizador para guardar la información cada 5 minutos
last_sync = time.ticks_ms()
SYNC_INTERVAL = 5 * 60 * 1000  # 5 minutos
hay_cambios = False

# Parámetros
THRESHOLD = 0.05      # sensibilidad (ajustable)
moving = False
moving_time_ms = 0
last_time = time.ticks_ms()

# 🚨 Métricas avanzadas
colisiones = 0
aceleraciones_bruscas = 0
paradas_bruscas = 0

# Sensores más finos
COLLISION_THRESHOLD = 1.8
ACCEL_THRESHOLD = 0.3
STOP_THRESHOLD = 0.02

prev_magnitude = 1.0
# Guardamos el tiempo inicial
inicio = time.time()
print("Inicio: ", inicio)
# Duración en segundos (5 minutos = 300 segundos)
# duracion = 5 * 60
duracion = 60

while True:
    M5.update()
    battery = M5.Power.getBatteryLevel()
    now = time.ticks_ms()
    dt = time.ticks_diff(now, last_time)
    last_time = now

    ax, ay, az = M5.Imu.getAccel()
    magnitude = math.sqrt(ax*ax + ay*ay + az*az)

    delta = abs(magnitude - prev_magnitude)

    # 🚨 Detectar colisión (golpe fuerte)
    if magnitude > COLLISION_THRESHOLD:
        colisiones += 1
        print("💥 Colisión detectada")

    # ⚡ Aceleración brusca
    if delta > ACCEL_THRESHOLD:
        aceleraciones_bruscas += 1

    # 🛑 Parada brusca
    if delta > ACCEL_THRESHOLD and magnitude < STOP_THRESHOLD:
        paradas_bruscas += 1

    prev_magnitude = magnitude
    # ¿Está en movimiento?
    if abs(magnitude - 1.0) > THRESHOLD:
        moving = True
        moving_time_ms += dt
    else:
        moving = False
    
    # Mostrar estado
    label_state.setText(
        "Estado: Movimiento" if moving else "Estado: Reposo"
    )

    label_time.setText(
        "Tiempo mov.: {:.1f} s".format(moving_time_ms / 1000)
    )

    label_battery.setText(
        "Bateria: " + str(battery) + "%"
    )

# Comprobamos y si han pasado 5 minutos escribimos en bbdd si es necesario
    
    tiempo_actual = time.time()
    tiempo_transcurrido = tiempo_actual - inicio

    if tiempo_transcurrido >= duracion:
        print("Han pasado 5 minutos")
        inicio = time.time()
        if moving_time_ms > 0:
          # Calculamos la estabilidad antes de enviar
          estabilidad = 100 - (aceleraciones_bruscas * 2 + colisiones * 3)
          if estabilidad < 0:
            estabilidad = 0
          score = calcular_score(
              colisiones,
              aceleraciones_bruscas,
              paradas_bruscas,
              estabilidad
          )
          nivel = calcular_nivel(score)
          #print("Score:", score, "Nivel:", nivel)

          sesion = {
              "timestamp": time.time(),
              "tiempo_movimiento": moving_time_ms,
              "colisiones": colisiones,
              "aceleraciones_bruscas": aceleraciones_bruscas,
              "paradas_bruscas": paradas_bruscas,
              "estabilidad": estabilidad,
              "score": score,
              "nivel": nivel
          }
          #print("Sesión completa", sesion)
          
          if check_wifi(wifi):
              guardar_sesion(FIREBASE_DB_URL, fecha_actual, sesion)
              update_today_time(
                  FIREBASE_DB_URL,
                  fecha_actual,
                  tiempo_acumulado + moving_time_ms
              )
          else:
              print("📦 Guardando sesión localmente")

              sesiones_pendientes.append({
                  "fecha": fecha_actual,
                  "data": sesion
              })

              movimientos_pendientes.append({
                  "fecha": fecha_actual,
                  "tiempo": moving_time_ms
              })
 
          moving_time_ms = 0
          # Actualizamos el tiempo acumulado
          tiempo_acumulado = get_today_time(FIREBASE_DB_URL, fecha_actual)
          colisiones = 0
          aceleraciones_bruscas = 0
          paradas_bruscas = 0
          moving_time_ms = 0
          gc.mem_free()
          gc.collect()
      
    time.sleep(0.5)  # 20 Hz