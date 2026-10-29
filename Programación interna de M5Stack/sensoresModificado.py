# -*- coding: utf-8 -*-
# =====================================================================
#  Telemetría IoT para automovilismo - M5Stack Core2 + Firebase RTDB
#  MicroPython (UIFlow2 / Thonny)
#
#  Novedades respecto a la versión anterior:
#   1. Buffer persistente en microSD + envío por lotes (1 sola petición
#      con actualización multi-ruta de Firebase).
#   2. Reconexión Wi-Fi activa, heartbeat (ultimo_latido), límite de buffer
#      y recuperación de sesiones que quedaron "activas" tras un corte.
#   3. Detección automática de vueltas (geovalla), sectores, mejor vuelta,
#      delta y vuelta teórica óptima.
#   4. Shift lights con la barra LED del M5GO Bottom2 (opcional) y barra
#      de RPM en pantalla.
#   5. GPS real por UART (Unit GPS) con fusión con el IMU y 4G por PPP
#      (EXPERIMENTAL, desactivado por defecto).
#   6. Comandos y mensajes desde boxes a través del nodo "control".
#
#  La red corre en un hilo aparte para que las peticiones HTTP nunca
#  bloqueen la lectura del IMU ni la pantalla.
# =====================================================================
import sys
import os
import time
import json
import gc
import math
import random
import M5
from M5 import Widgets
import network
import requests
import _thread

# ---------------------------------------------------------------------
#  CONFIGURACIÓN (se puede sobrescribir con /flash/config.json o
#  /sd/config.json sin tocar el código)
# ---------------------------------------------------------------------
CFG = {
    # Red y Firebase
    "base_url": "https://b-42-4988d-default-rtdb.europe-west1.firebasedatabase.app",
    "wifi_ssid": "TU_SSID",
    "wifi_password": "TU_PASSWORD",
    "wifi_espera_s": 10,          # espera máxima al arrancar
    "wifi_check_ms": 2000,
    "wifi_reintento_ms": 8000,
    "utc_offset_h": 1,            # España: 1 invierno / 2 verano

    # Identificación
    "vehiculo": "coche_1",
    "piloto": "piloto_1",

    # Muestreo y subida
    "muestreo_ms": 1000,          # cada cuánto se guarda una lectura
    "subida_ms": 2000,            # cada cuánto se sube el lote acumulado
    "lote_max": 20,               # registros por petición
    "latido_ms": 5000,            # heartbeat aunque no haya datos nuevos
    "control_ms": 2000,           # consulta de comandos/mensajes
    "ram_max": 40,                # con SD: a partir de aquí se vuelca a la SD
    "ram_max_sin_sd": 200,        # sin SD: tope duro (se descartan los más viejos)

    # Posición
    "gps_real": False,            # True = Unit GPS por UART
    "gps_uart": 2,
    "gps_rx": 13,                 # Puerto C del Core2 (si no llegan datos, intercambia rx/tx)
    "gps_tx": 14,
    "gps_baud": 9600,
    "centro_lat": 41.5700,        # centro del circuito simulado
    "centro_lng": 2.2611,
    "radio_circuito_m": 300,
    "sim_factor": 3,              # multiplicador de avance en el simulador
    "sim_vel_kmh": 0,             # >0 = velocidad fija de demo en modo simulado

    # Vueltas
    "radio_meta_m": 20,
    "vuelta_min_s": 20,
    "n_sectores": 3,

    # IMU / dinámica
    "eje_long": 1,                # 0=X, 1=Y, 2=Z (eje longitudinal del coche)
    "signo_long": 1,              # -1 si la aceleración sale invertida
    "umbral_vib_g": 0.012,        # vibración por debajo de la cual se considera parado
    "rpm_ralenti": 900,
    "rpm_max": 8000,
    "rpm_k_acc": 2500,            # rpm extra por cada g de aceleración
    "rpm_k_vel": 45,              # rpm extra por cada km/h

    # Shift lights
    "leds": True,
    "led_pin": 25,                # M5GO Bottom2
    "led_n": 10,
    "rpm_shift_min": 5500,
    "rpm_flash": 7500,

    # Modo pit-lane
    "pit_limite_kmh": 60,

    # 4G por PPP (EXPERIMENTAL / sin probar)
    "modem_4g": False,
    "modem_uart": 1,
    "modem_tx": 0,
    "modem_rx": 0,
    "modem_baud": 115200,
    "apn": "",
    "modem_espera_s": 20,         # segundos sin Wi-Fi antes de levantar 4G
}

E = None  # estado compartido (se crea en main)

# ---------------------------------------------------------------------
#  RELOJ
# ---------------------------------------------------------------------
_OFF_EPOCH = 946684800 if time.gmtime(0)[0] == 2000 else 0
_ref_ms = None
_ref_ticks = 0


def hora_valida():
    try:
        return (time.time() + _OFF_EPOCH) > 1700000000
    except Exception:
        return False


def sync_ref():
    """Fija la referencia para tener marcas de tiempo con resolución de ms."""
    global _ref_ms, _ref_ticks
    _ref_ticks = time.ticks_ms()
    _ref_ms = int((time.time() + _OFF_EPOCH) * 1000)


def timestamp_ms():
    try:
        if _ref_ms is None:
            if hora_valida():
                sync_ref()
            else:
                return int(time.ticks_ms())
        return _ref_ms + time.ticks_diff(time.ticks_ms(), _ref_ticks)
    except Exception:
        return int(time.ticks_ms())


def hora_texto():
    try:
        t = time.gmtime(time.time() + int(CFG["utc_offset_h"] * 3600))
        return "%02d:%02d:%02d" % (t[3], t[4], t[5])
    except Exception:
        return "--:--:--"


def fmt_t(ms):
    if ms is None:
        return "--:--.--"
    return "%d:%05.2f" % (ms // 60000, (ms % 60000) / 1000)


def sincronizar_ntp():
    E.t_ntp = time.ticks_ms()
    try:
        import ntptime
        ntptime.host = "pool.ntp.org"
        ntptime.settime()
    except Exception as e:
        print("NTP fallo:", e)
        return False
    sync_ref()
    E.reloj_ok = True
    # Intento de dejar también la hora en el RTC BM8563 (la API varía según firmware).
    try:
        t = time.gmtime()
        try:
            M5.Rtc.setDateTime((t[0], t[1], t[2], t[3], t[4], t[5]))
        except Exception:
            M5.Rtc.setDateTime((t[0], t[1], t[2], t[3], t[4], t[5], 0))
    except Exception:
        pass
    print("Hora sincronizada por NTP")
    return True


# ---------------------------------------------------------------------
#  GEOMETRÍA
# ---------------------------------------------------------------------
def a_metros(lat, lng, lat0, lng0):
    x = (lng - lng0) * 111320.0 * math.cos(math.radians(lat0))
    y = (lat - lat0) * 111320.0
    return x, y


def dist_m(lat1, lng1, lat2, lng2):
    x, y = a_metros(lat2, lng2, lat1, lng1)
    return math.sqrt(x * x + y * y)


def dist_seg(prev, cur, meta):
    """Distancia mínima de la meta al segmento prev->cur (en metros)."""
    ax, ay = a_metros(prev[0], prev[1], meta[0], meta[1])
    bx, by = a_metros(cur[0], cur[1], meta[0], meta[1])
    dx = bx - ax
    dy = by - ay
    l2 = dx * dx + dy * dy
    if l2 < 1e-6:
        return math.sqrt(ax * ax + ay * ay)
    t = -(ax * dx + ay * dy) / l2
    if t < 0:
        t = 0
    elif t > 1:
        t = 1
    px = ax + t * dx
    py = ay + t * dy
    return math.sqrt(px * px + py * py)


# ---------------------------------------------------------------------
#  FIREBASE (REST)
# ---------------------------------------------------------------------
def firebase_request(metodo, ruta, datos=None):
    url = CFG["base_url"] + "/" + ruta + ".json"
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
        return False, None
    finally:
        if respuesta is not None:
            try:
                respuesta.close()
            except Exception:
                pass
        gc.collect()


# ---------------------------------------------------------------------
#  COLA PERSISTENTE (RAM + microSD)
#  Cada registro es {"p": ruta_firebase, "d": valor}. El orden se conserva:
#  primero lo que hay en la SD (más antiguo) y después lo que hay en RAM.
# ---------------------------------------------------------------------
class Cola:
    def __init__(self, base, usa_sd):
        self.lock = _thread.allocate_lock()
        self.ram = []
        self.sd = usa_sd
        self.enviando = False
        self.perdidos = 0
        self.pos = 0
        self.n_sd = 0
        self.f_data = (base or "") + "/pendientes.jsonl"
        self.f_pos = (base or "") + "/pendientes.pos"
        if self.sd:
            self._cargar()

    def _cargar(self):
        try:
            try:
                with open(self.f_pos) as f:
                    self.pos = int(f.read().strip() or 0)
            except Exception:
                self.pos = 0
            n = 0
            with open(self.f_data) as f:
                f.seek(self.pos)
                while f.readline():
                    n += 1
            self.n_sd = n
        except Exception:
            self.pos = 0
            self.n_sd = 0
        if self.n_sd:
            print("Pendientes en SD:", self.n_sd)

    def total(self):
        return len(self.ram) + self.n_sd

    def add(self, ruta, valor):
        with self.lock:
            self.ram.append({"p": ruta, "d": valor})
            if self.enviando:
                return
            if self.sd:
                if len(self.ram) > CFG["ram_max"]:
                    self._spill()
            else:
                while len(self.ram) > CFG["ram_max_sin_sd"]:
                    self.ram.pop(0)
                    self.perdidos += 1

    def _spill(self):
        try:
            with open(self.f_data, "a") as f:
                for r in self.ram:
                    f.write(json.dumps(r))
                    f.write("\n")
            self.n_sd += len(self.ram)
            self.ram = []
        except Exception as e:
            print("Error escribiendo SD:", e)
            while len(self.ram) > CFG["ram_max_sin_sd"]:
                self.ram.pop(0)
                self.perdidos += 1

    def volcar(self):
        """Pasa todo lo que haya en RAM a la SD (antes de apagar)."""
        with self.lock:
            if self.sd and self.ram:
                self._spill()

    def _limpiar_sd(self):
        for ruta in (self.f_data, self.f_pos):
            try:
                os.remove(ruta)
            except Exception:
                pass
        self.pos = 0
        self.n_sd = 0

    def tomar(self, n):
        """Devuelve (origen, lote, info). origen: 'sd', 'ram' o None."""
        with self.lock:
            if self.sd and self.n_sd > 0:
                lote = []
                nueva = self.pos
                leidas = 0
                try:
                    with open(self.f_data) as f:
                        f.seek(self.pos)
                        while leidas < n:
                            ln = f.readline()
                            if not ln or not ln.endswith("\n"):
                                break
                            nueva = f.tell()
                            leidas += 1
                            try:
                                lote.append(json.loads(ln))
                            except Exception:
                                pass  # línea corrupta: se salta
                except Exception as e:
                    print("Error leyendo SD:", e)
                if leidas > 0:
                    return "sd", lote, (nueva, leidas)
                self._limpiar_sd()  # incoherente: se reinicia el fichero
            if self.ram:
                self.enviando = True
                lote = self.ram[:n]
                return "ram", lote, len(lote)
            return None, [], 0

    def confirmar(self, origen, info):
        with self.lock:
            if origen == "sd":
                nueva, leidas = info
                self.pos = nueva
                self.n_sd -= leidas
                if self.n_sd <= 0:
                    self._limpiar_sd()
                else:
                    try:
                        with open(self.f_pos, "w") as f:
                            f.write(str(self.pos))
                    except Exception as e:
                        print("Error guardando posición:", e)
            elif origen == "ram":
                del self.ram[:info]
                self.enviando = False

    def liberar(self):
        with self.lock:
            self.enviando = False


# ---------------------------------------------------------------------
#  SD
# ---------------------------------------------------------------------
def montar_sd():
    try:
        if "sd" not in os.listdir("/"):
            from machine import SDCard, Pin
            sd = SDCard(slot=3, sck=Pin(18), miso=Pin(38), mosi=Pin(23), cs=Pin(4))
            os.mount(sd, "/sd")
    except Exception as e:
        print("Sin SD:", e)
        return None
    base = "/sd/telemetria"
    try:
        try:
            os.mkdir(base)
        except Exception:
            pass
        with open(base + "/.test", "w") as f:
            f.write("ok")
        os.remove(base + "/.test")
        return base
    except Exception as e:
        print("SD no escribible:", e)
        return None


def cargar_config():
    for ruta in ("/flash/config.json", "/sd/config.json"):
        try:
            with open(ruta) as f:
                CFG.update(json.load(f))
            print("Configuración cargada:", ruta)
        except Exception:
            pass


# ---------------------------------------------------------------------
#  GPS REAL (NMEA por UART)
# ---------------------------------------------------------------------
class GpsReal:
    def __init__(self):
        from machine import UART
        self.uart = UART(CFG["gps_uart"], baudrate=CFG["gps_baud"],
                         tx=CFG["gps_tx"], rx=CFG["gps_rx"])
        self.buf = b""
        self.lat = 0.0
        self.lng = 0.0
        self.vel = 0.0      # km/h
        self.rumbo = 0.0
        self.alt = 0.0
        self.sats = 0
        self.fix = False
        self.t_fix = 0

    def leer(self):
        try:
            n = self.uart.any()
            if not n:
                return
            d = self.uart.read(n)
            if d:
                self.buf += d
            if len(self.buf) > 1024:
                self.buf = self.buf[-512:]
            while True:
                i = self.buf.find(b"\n")
                if i < 0:
                    break
                linea = self.buf[:i]
                self.buf = self.buf[i + 1:]
                self._parsear(linea)
        except Exception as e:
            print("Error GPS:", e)

    @staticmethod
    def _grados(v, hemi):
        pt = v.find(".")
        g = float(v[:pt - 2]) + float(v[pt - 2:]) / 60.0
        return -g if hemi in ("S", "W") else g

    def _parsear(self, linea):
        try:
            s = linea.decode().strip()
        except Exception:
            return
        if not s.startswith("$") or "*" not in s:
            return
        cuerpo, _, cs = s[1:].partition("*")
        x = 0
        for ch in cuerpo:
            x ^= ord(ch)
        if ("%02X" % x) != cs[:2].upper():
            return
        c = cuerpo.split(",")
        tipo = c[0][2:]
        try:
            if tipo == "RMC" and len(c) > 8:
                if c[2] == "A" and c[3] and c[5]:
                    self.lat = self._grados(c[3], c[4])
                    self.lng = self._grados(c[5], c[6])
                    self.vel = (float(c[7]) if c[7] else 0.0) * 1.852
                    if c[8]:
                        self.rumbo = float(c[8])
                    self.fix = True
                    self.t_fix = time.ticks_ms()
                else:
                    self.fix = False
            elif tipo == "GGA" and len(c) > 9:
                self.sats = int(c[7]) if c[7] else 0
                self.alt = float(c[9]) if c[9] else 0.0
        except Exception:
            pass

    def valido(self):
        return self.fix and time.ticks_diff(time.ticks_ms(), self.t_fix) < 3000


# ---------------------------------------------------------------------
#  CIRCUITO SIMULADO (curva polar armónica, cerrada y única por sesión)
# ---------------------------------------------------------------------
class Circuito:
    def __init__(self):
        random.seed(timestamp_ms() & 0x3FFFFFFF)
        self.R0 = float(CFG["radio_circuito_m"])
        self.arm = []
        for k in (2, 3, 5):
            self.arm.append((k, random.uniform(0.05, 0.15), random.uniform(0, 2 * math.pi)))
        self.theta = 0.0

    def radio(self, th):
        r = 1.0
        for k, a, p in self.arm:
            r += a * math.cos(k * th + p)
        return self.R0 * r

    def avanzar(self, ds):
        r = self.radio(self.theta)
        self.theta = (self.theta + ds / max(r, 50.0)) % (2 * math.pi)

    def latlng(self):
        r = self.radio(self.theta)
        x = r * math.cos(self.theta)
        y = r * math.sin(self.theta)
        lat0 = CFG["centro_lat"]
        return (lat0 + y / 111320.0,
                CFG["centro_lng"] + x / (111320.0 * math.cos(math.radians(lat0))))


# ---------------------------------------------------------------------
#  DINÁMICA: velocidad por IMU con filtrado de deriva y ZVU
# ---------------------------------------------------------------------
def leer_imu():
    try:
        a = M5.Imu.getAccel()
        return (a[0], a[1], a[2])
    except Exception:
        return (0.0, 0.0, 0.0)


class Dinamica:
    def __init__(self):
        self.bias = [0.0, 0.0, 1.0]
        self.a = [0.0, 0.0, 1.0]
        self.v = 0.0
        self.vib = 0.0
        self.quiet = 0
        self.dev = 0.0
        self.rpm = float(CFG["rpm_ralenti"])

    def calibrar(self, muestras=60):
        s = [0.0, 0.0, 0.0]
        for _ in range(muestras):
            M5.update()
            r = leer_imu()
            for i in range(3):
                s[i] += r[i]
            time.sleep_ms(15)
        for i in range(3):
            self.bias[i] = s[i] / muestras
            self.a[i] = self.bias[i]

    def actualizar(self, dt, gps_ms=None):
        raw = leer_imu()
        e = CFG["eje_long"]
        for i in range(3):
            self.a[i] += 0.2 * (raw[i] - self.a[i])
        self.vib += 0.05 * (abs(raw[e] - self.a[e]) - self.vib)
        dev = (self.a[e] - self.bias[e]) * CFG["signo_long"]
        self.dev = dev

        # Inmovilidad: sin aceleración neta Y sin vibración (un coche en marcha vibra)
        parado = abs(dev) < 0.03 and self.vib < CFG["umbral_vib_g"]
        if gps_ms is not None and gps_ms < 0.3:
            parado = True
        self.quiet = self.quiet + 1 if parado else 0

        if self.quiet >= 25:
            self.v = 0.0
            for i in range(3):  # recalibra el sesgo en reposo
                self.bias[i] += 0.01 * (self.a[i] - self.bias[i])
        else:
            self.v += dev * 9.81 * dt
            self.v -= self.v * 0.02 * dt   # fuga contra la deriva
            if self.v < 0:
                self.v = 0.0
            elif self.v > 100:
                self.v = 100.0

        # Fusión con GPS real: el GPS corrige, el IMU interpola entre fixes
        if gps_ms is not None:
            self.v += 0.15 * (gps_ms - self.v)

        # RPM estimadas (heurística; sustitúyela por un sensor real cuando lo tengas)
        objetivo = (CFG["rpm_ralenti"] + abs(dev) * CFG["rpm_k_acc"]
                    + self.v * 3.6 * CFG["rpm_k_vel"])
        self.rpm += 0.1 * (objetivo - self.rpm)
        if self.rpm > CFG["rpm_max"]:
            self.rpm = float(CFG["rpm_max"])
        return self.v


# ---------------------------------------------------------------------
#  VUELTAS, SECTORES Y DELTA
#  Los sectores se definen por distancia recorrida: la primera vuelta
#  completa fija la longitud del circuito y se divide en N partes iguales.
# ---------------------------------------------------------------------
class Vueltas:
    def __init__(self):
        self.meta = None
        self.reset()

    def reset(self):
        self.num = 0
        self.t0 = 0
        self.ult_cruce = 0
        self.dist = 0.0
        self.armado = False
        self.limites = None
        self.sec_i = 0
        self.cum = []
        self.traza = []
        self.t_traza = 0
        self.mejor = None
        self.mejor_cum = None
        self.mejores_sec = None
        self.ultima = None
        self.ult_secs = None
        self.delta = None

    def fijar_meta(self, lat, lng, ahora):
        self.meta = (lat, lng)
        self.reset()
        self.t0 = ahora
        self.ult_cruce = ahora
        self.t_traza = ahora

    def teorica(self):
        return sum(self.mejores_sec) if self.mejores_sec else None

    def _delta(self, i, t):
        if self.mejor_cum and i < len(self.mejor_cum) - 1:
            self.delta = t - self.mejor_cum[i]

    def _definir_sectores(self, total_ms):
        n = CFG["n_sectores"]
        if n < 2 or len(self.traza) < 3 or self.dist < 1:
            self.limites = []
            self.cum = []
            return
        pts = [(0, 0)] + self.traza + [(int(self.dist), total_ms)]
        self.limites = [self.dist * (i + 1) / n for i in range(n - 1)]
        cum = []
        for lim in self.limites:
            t = total_ms
            for j in range(1, len(pts)):
                d0, t0 = pts[j - 1]
                d1, t1 = pts[j]
                if d0 <= lim <= d1:
                    t = t0 + (lim - d0) * (t1 - t0) / max(d1 - d0, 1)
                    break
            cum.append(int(t))
        self.cum = cum
        self.traza = []

    def _cerrar(self, ahora):
        t = time.ticks_diff(ahora, self.t0)
        self.num += 1
        if self.limites is None:
            self._definir_sectores(t)
        if self.limites is not None and len(self.cum) == len(self.limites):
            cum = self.cum + [t]
        else:
            cum = [t]
        secs = [cum[0]] + [cum[i] - cum[i - 1] for i in range(1, len(cum))]
        self.delta = (t - self.mejor) if self.mejor is not None else None
        es_mejor = self.mejor is None or t < self.mejor
        if es_mejor:
            self.mejor = t
            self.mejor_cum = cum
        if self.mejores_sec is None or len(self.mejores_sec) != len(secs):
            self.mejores_sec = secs[:]
        else:
            for i in range(len(secs)):
                if secs[i] < self.mejores_sec[i]:
                    self.mejores_sec[i] = secs[i]
        self.ultima = t
        self.ult_secs = secs
        self.t0 = ahora
        self.ult_cruce = ahora
        self.dist = 0.0
        self.sec_i = 0
        self.cum = []
        self.armado = False
        return [("vuelta", self.num, t, secs, es_mejor)]

    def actualizar(self, prev, cur, inc, ahora):
        ev = []
        if self.meta is None:
            return ev
        self.dist += inc
        t = time.ticks_diff(ahora, self.t0)
        lim = self.limites

        if lim and self.sec_i < len(lim) and self.dist >= lim[self.sec_i]:
            self.cum.append(t)
            self._delta(len(self.cum) - 1, t)
            self.sec_i += 1
            ev.append(("sector", self.sec_i, t))

        if lim is None and len(self.traza) < 600 and time.ticks_diff(ahora, self.t_traza) >= 1000:
            self.t_traza = ahora
            self.traza.append((int(self.dist), t))

        R = CFG["radio_meta_m"]
        d_act = dist_m(cur[0], cur[1], self.meta[0], self.meta[1])
        if self.armado and time.ticks_diff(ahora, self.ult_cruce) >= CFG["vuelta_min_s"] * 1000:
            cruza = d_act < R or (prev is not None and dist_seg(prev, cur, self.meta) < R)
            if cruza:
                ev.extend(self._cerrar(ahora))
                return ev
        if d_act > 2 * R:
            self.armado = True
        return ev


# ---------------------------------------------------------------------
#  SHIFT LIGHTS (barra LED del M5GO Bottom2, opcional)
# ---------------------------------------------------------------------
class Leds:
    COLORES = [(0, 40, 0), (0, 40, 0), (45, 35, 0), (45, 35, 0), (55, 0, 0)]

    def __init__(self):
        self.np = None
        self.ult = None
        if not CFG["leds"]:
            return
        try:
            from machine import Pin
            import neopixel
            self.np = neopixel.NeoPixel(Pin(CFG["led_pin"]), CFG["led_n"])
            self._pintar(0, False)
        except Exception as e:
            print("Sin LEDs:", e)
            self.np = None

    def _pintar(self, nivel, rojo_total):
        n = CFG["led_n"]
        mitad = n // 2
        for i in range(n):
            self.np[i] = (0, 0, 0)
        if rojo_total:
            for i in range(n):
                self.np[i] = (60, 0, 0)
        else:
            for i in range(min(nivel, mitad, len(self.COLORES))):
                self.np[i] = self.COLORES[i]
                self.np[n - 1 - i] = self.COLORES[i]
        self.np.write()

    def actualizar(self, rpm, ahora):
        if self.np is None:
            return
        try:
            lo = CFG["rpm_shift_min"]
            hi = CFG["rpm_max"]
            if rpm >= CFG["rpm_flash"]:
                estado = ("F", (ahora // 120) % 2)
            elif rpm <= lo:
                estado = ("N", 0)
            else:
                estado = ("N", min(5, 1 + int(5 * (rpm - lo) / max(hi - lo, 1))))
            if estado == self.ult:
                return
            self.ult = estado
            if estado[0] == "F":
                self._pintar(0, estado[1] == 1)
            else:
                self._pintar(estado[1], False)
        except Exception as e:
            print("Error LEDs:", e)

    def apagar(self):
        if self.np is not None:
            try:
                self._pintar(0, False)
            except Exception:
                pass


# ---------------------------------------------------------------------
#  PANTALLA
# ---------------------------------------------------------------------
class Pantalla:
    FILAS = [("estado", 0xFFFFFF), ("vel", 0x00FF00), ("rpm", 0xFFFF00),
             ("gps", 0x00FFFF), ("vuelta", 0xFF9900), ("mejor", 0xFF9900),
             ("sect", 0xCCCCCC), ("sis", 0xFFFFFF), ("sd", 0x888888),
             ("msg", 0xFFFFFF)]

    def __init__(self):
        Widgets.fillScreen(0x111111)
        self.l = {}
        self.cache = {}
        self.msg_estilo = None
        self.bar_key = None
        y = 2
        for nombre, color in self.FILAS:
            self.l[nombre] = Widgets.Label("", 6, y, 1.0, color, 0x111111,
                                           Widgets.FONTS.DejaVu18)
            y += 22

    def set(self, k, txt):
        txt = (txt + " " * 30)[:30]
        if self.cache.get(k) != txt:
            self.cache[k] = txt
            try:
                self.l[k].setText(txt)
            except Exception:
                pass

    def mensaje(self, texto, estilo):
        key = (texto, estilo)
        if self.cache.get("msg_key") == key:
            return
        self.cache["msg_key"] = key
        try:
            if estilo == 0:
                self.l["msg"].setColor(0x000000, 0xFFFF00)
            elif estilo == 1:
                self.l["msg"].setColor(0xFFFFFF, 0xCC0000)
            else:
                self.l["msg"].setColor(0xFFFFFF, 0x111111)
        except Exception:
            pass
        self.set("msg", texto)

    def barra(self, rpm):
        try:
            w = int(308 * min(1.0, max(0.0, rpm / float(CFG["rpm_max"]))))
            if rpm < CFG["rpm_shift_min"]:
                color = 0x00CC00
            elif rpm < CFG["rpm_flash"]:
                color = 0xFFCC00
            else:
                color = 0xFF0000
            key = (w // 3, color)
            if key == self.bar_key:
                return
            self.bar_key = key
            M5.Lcd.fillRect(6, 224, 308, 12, 0x222222)
            if w > 0:
                M5.Lcd.fillRect(6, 224, w, 12, color)
        except Exception:
            pass


def poner_brillo(pct):
    try:
        pct = max(5, min(100, int(pct)))
        M5.Lcd.setBrightness(int(pct * 255 / 100))
    except Exception as e:
        print("Brillo:", e)


# ---------------------------------------------------------------------
#  4G POR PPP (EXPERIMENTAL)
# ---------------------------------------------------------------------
class Modem4G:
    def __init__(self):
        self.uart = None
        self.ppp = None
        self.t_intento = 0

    def _at(self, cmd, espera=b"OK", ms=3000):
        self.uart.write(cmd + "\r\n")
        t0 = time.ticks_ms()
        buf = b""
        while time.ticks_diff(time.ticks_ms(), t0) < ms:
            if self.uart.any():
                d = self.uart.read()
                if d:
                    buf += d
            if espera in buf:
                return True
            if b"ERROR" in buf:
                return False
            time.sleep_ms(20)
        return False

    def iniciar(self):
        self.t_intento = time.ticks_ms()
        try:
            from machine import UART
            self.uart = UART(CFG["modem_uart"], baudrate=CFG["modem_baud"],
                             tx=CFG["modem_tx"], rx=CFG["modem_rx"])
            if not (self._at("AT") and self._at("ATE0")):
                raise Exception("el modem no responde")
            self._at('AT+CGDCONT=1,"IP","%s"' % CFG["apn"])
            if not self._at("ATD*99#", b"CONNECT", 20000):
                raise Exception("no hay datos móviles")
            self.ppp = network.PPP(self.uart)
            self.ppp.active(True)
            self.ppp.connect()
            for _ in range(30):
                if self.ppp.isconnected():
                    print("4G conectado")
                    return True
                time.sleep(1)
        except Exception as e:
            print("4G fallo:", e)
        return False

    def conectado(self):
        try:
            return self.ppp is not None and self.ppp.isconnected()
        except Exception:
            return False


# ---------------------------------------------------------------------
#  ESTADO COMPARTIDO
# ---------------------------------------------------------------------
class Estado:
    def __init__(self):
        self.activo = True          # bucle principal
        self.red_on = False         # hilo de red
        self.red_viva = False
        self.apagar = False
        self.finalizada = False
        self.id = ""
        self.inicio = 0
        self.seq = 0
        self.ultimo_dato = None
        self.carrera = None
        self.entrada = []
        self.lock_in = _thread.allocate_lock()
        self.ack = None
        self.wlan = None
        self.modem = None
        self.wifi_ok = False
        self.modem_ok = False
        self.t_wifi = 0
        self.t_reintento = 0
        self.t_sin_wifi = None
        self.reloj_ok = hora_valida()
        self.t_ntp = 0
        self.t_subida = 0
        self.t_flush = 0
        self.t_fallo = -100000
        self.t_control = 0
        self.control_ok = False
        self.ult_cmd = None
        self.ult_msg = None
        self.recuperada = False
        self.intentos_rec = 0
        self.muestreo_ms = CFG["muestreo_ms"]
        self.pit = False
        self.msg_texto = ""
        self.msg_hasta = 0
        self.msg_estilo = 0
        self.cola = None
        self.vue = Vueltas()
        self.circ = None
        self.gps = None
        self.pos = (0.0, 0.0, "nofix")


def enlace_ok():
    return E.wifi_ok or E.modem_ok


def mensaje(texto, seg=5, estilo=0):
    E.msg_texto = texto
    E.msg_estilo = estilo
    E.msg_hasta = time.ticks_add(time.ticks_ms(), int(seg * 1000))


# ---------------------------------------------------------------------
#  SESIONES
# ---------------------------------------------------------------------
def nueva_sesion():
    E.inicio = timestamp_ms()
    E.id = "ses_%d_%03x" % (E.inicio, random.getrandbits(12))
    E.seq = 0
    E.ultimo_dato = None
    E.carrera = None
    base = "sesiones/%s/" % E.id
    for k, v in (("inicio", E.inicio), ("fin", 0), ("estado", "en_curso"),
                 ("vehiculo", CFG["vehiculo"]), ("piloto", CFG["piloto"]),
                 ("reloj_ok", E.reloj_ok)):
        E.cola.add(base + k, v)
    print("Sesión:", E.id)


def cerrar_sesion_en_cola(estado="finalizada"):
    base = "sesiones/%s/" % E.id
    E.cola.add(base + "fin", timestamp_ms())
    E.cola.add(base + "estado", estado)


def reiniciar_sesion():
    cerrar_sesion_en_cola()
    nueva_sesion()
    E.vue = Vueltas()
    E.circ = Circuito()
    mensaje("Nueva sesion", 3)


def estado_vivo(final=False):
    if final:
        return {"sesion_actual/activa": False, "sesion_actual/id": "",
                "sesion_actual/inicio": 0}
    v = {
        "sesion_actual/activa": True,
        "sesion_actual/id": E.id,
        "sesion_actual/inicio": E.inicio,
        "sesion_actual/ultimo_latido": timestamp_ms(),
        "sesion_actual/pendientes": E.cola.total(),
    }
    if E.ultimo_dato:
        v["sesion_actual/ultimo_dato"] = E.ultimo_dato
    if E.carrera:
        v["sesion_actual/carrera"] = E.carrera
    return v


def flush(final=False):
    """Sube un lote (hasta lote_max registros) + estado vivo en UNA petición."""
    origen, lote, info = E.cola.tomar(CFG["lote_max"])
    upd = {}
    for r in lote:
        upd[r["p"]] = r["d"]
    upd.update(estado_vivo(final))
    ack = E.ack
    if ack:
        upd["control/ack"] = ack
    ok, _ = firebase_request("PATCH", "", upd)
    if ok:
        E.cola.confirmar(origen, info)
        if ack is not None and E.ack is ack:
            E.ack = None
    else:
        E.cola.liberar()
    return ok, len(lote)


def finalizar_sesion(estado="finalizada"):
    if E.finalizada:
        return
    E.finalizada = True
    E.red_on = False
    t0 = time.ticks_ms()
    while E.red_viva and time.ticks_diff(time.ticks_ms(), t0) < 8000:
        time.sleep_ms(50)
    try:
        cerrar_sesion_en_cola(estado)
    except Exception:
        pass
    enviado = False
    try:
        if enlace_ok():
            for _ in range(15):
                ok, _n = flush(final=True)
                if not ok:
                    break
                if E.cola.total() == 0:
                    enviado = True
                    break
    except Exception as e:
        print("Error en cierre:", e)
    if not enviado:
        E.cola.volcar()   # lo pendiente queda en la SD para el próximo arranque
    print("Sesión finalizada" if enviado else "Sesión cerrada con pendientes en SD")


# ---------------------------------------------------------------------
#  HILO DE RED
# ---------------------------------------------------------------------
def gestionar_enlace():
    now = time.ticks_ms()
    if time.ticks_diff(now, E.t_wifi) < CFG["wifi_check_ms"]:
        return
    E.t_wifi = now
    try:
        E.wifi_ok = E.wlan.isconnected()
    except Exception:
        E.wifi_ok = False

    if E.wifi_ok:
        E.t_sin_wifi = None
    else:
        if E.t_sin_wifi is None:
            E.t_sin_wifi = now
        # Reconexión activa
        if time.ticks_diff(now, E.t_reintento) >= CFG["wifi_reintento_ms"]:
            E.t_reintento = now
            try:
                E.wlan.disconnect()
            except Exception:
                pass
            try:
                E.wlan.connect(CFG["wifi_ssid"], CFG["wifi_password"])
                print("Reintentando Wi-Fi...")
            except Exception as e:
                print("Error reconectando:", e)
        # Respaldo 4G
        m = E.modem
        if (m is not None and not m.conectado()
                and time.ticks_diff(now, E.t_sin_wifi) > CFG["modem_espera_s"] * 1000
                and time.ticks_diff(now, m.t_intento) > 60000):
            m.iniciar()
    if E.modem is not None:
        E.modem_ok = E.modem.conectado()


def recuperar_sesion_anterior():
    ok, d = firebase_request("GET", "sesion_actual")
    if not ok:
        return False
    if isinstance(d, dict) and d.get("activa") is True:
        viejo = d.get("id")
        if viejo and viejo != E.id:
            ud = d.get("ultimo_dato")
            fin = d.get("ultimo_latido") or (ud.get("timestamp") if isinstance(ud, dict) else None) \
                or timestamp_ms()
            firebase_request("PATCH", "sesiones/" + viejo,
                             {"fin": fin, "estado": "interrumpida"})
            print("Sesión anterior marcada como interrumpida:", viejo)
    return True


def leer_control():
    ok, d = firebase_request("GET", "control")
    if not ok:
        E.t_fallo = time.ticks_ms()
        return
    if not isinstance(d, dict):
        d = {}
    cmd = d.get("comando")
    msg = d.get("mensaje")
    cid = cmd.get("id") if isinstance(cmd, dict) else None
    mid = msg.get("id") if isinstance(msg, dict) else None
    if not E.control_ok:       # primera lectura: se ignora lo que ya estaba escrito
        E.ult_cmd = cid
        E.ult_msg = mid
        E.control_ok = True
        return
    if cid is not None and cid != E.ult_cmd:
        E.ult_cmd = cid
        with E.lock_in:
            E.entrada.append(("cmd", cid, cmd.get("accion", ""), cmd.get("valor")))
    if mid is not None and mid != E.ult_msg:
        E.ult_msg = mid
        with E.lock_in:
            E.entrada.append(("msg", mid, str(msg.get("texto", "")), None))


def ciclo_red():
    gestionar_enlace()
    if not enlace_ok():
        return
    now = time.ticks_ms()
    if time.ticks_diff(now, E.t_fallo) < 2000:
        return
    if not E.reloj_ok and time.ticks_diff(now, E.t_ntp) >= 30000:
        sincronizar_ntp()
    if not E.recuperada:
        if recuperar_sesion_anterior():
            E.recuperada = True
        else:
            E.intentos_rec += 1
            E.t_fallo = now
            if E.intentos_rec >= 3:
                E.recuperada = True
        return

    hay = E.cola.total() > 0
    backlog = E.cola.n_sd > 0
    if ((hay and (backlog or time.ticks_diff(now, E.t_subida) >= CFG["subida_ms"]))
            or time.ticks_diff(now, E.t_flush) >= CFG["latido_ms"]):
        E.t_subida = now
        ok, _n = flush()
        if ok:
            E.t_flush = now
        else:
            E.t_fallo = time.ticks_ms()
            return

    if time.ticks_diff(now, E.t_control) >= CFG["control_ms"]:
        E.t_control = now
        leer_control()


def tarea_red():
    E.red_viva = True
    try:
        while E.red_on:
            try:
                ciclo_red()
            except Exception as e:
                print("Error en hilo de red:", e)
                E.t_fallo = time.ticks_ms()
            time.sleep_ms(100)
    finally:
        E.red_viva = False


# ---------------------------------------------------------------------
#  COMANDOS Y ACCIONES
# ---------------------------------------------------------------------
def marca_manual(origen):
    hora = hora_texto()
    ts = timestamp_ms()
    E.seq += 1
    E.cola.add("sesiones/%s/marcas/%013d_%05d" % (E.id, ts, E.seq),
               {"hora_marca": hora, "timestamp": ts, "origen": origen})
    mensaje("Marca " + hora, 2)


def fijar_meta_aqui():
    lat, lng, fuente = E.pos
    if fuente == "nofix":
        mensaje("Sin fix GPS", 3, 1)
        return
    E.vue.fijar_meta(lat, lng, time.ticks_ms())
    E.cola.add("sesiones/%s/meta" % E.id, {"lat": lat, "lng": lng})
    mensaje("Meta fijada", 3)


def ejecutar_comando(cid, accion, valor):
    ok = True
    try:
        if accion == "marca":
            marca_manual("box")
        elif accion == "frecuencia":
            E.muestreo_ms = max(200, min(10000, int(valor)))
        elif accion == "pit":
            E.pit = valor in (True, 1, "1", "on", "true", "si")
        elif accion == "limite_pit":
            CFG["pit_limite_kmh"] = int(valor)
        elif accion == "brillo":
            poner_brillo(valor)
        elif accion == "meta":
            fijar_meta_aqui()
        elif accion == "nueva_sesion":
            reiniciar_sesion()
        elif accion == "cerrar":
            E.activo = False
        elif accion == "apagar":
            E.apagar = True
            E.activo = False
        else:
            ok = False
    except Exception as e:
        print("Error en comando:", e)
        ok = False
    E.ack = {"id": cid, "accion": accion, "ok": ok, "timestamp": timestamp_ms()}


def procesar_entrada():
    while True:
        with E.lock_in:
            item = E.entrada.pop(0) if E.entrada else None
        if item is None:
            return
        if item[0] == "cmd":
            ejecutar_comando(item[1], item[2], item[3])
        else:
            mensaje(item[2], 12, 0)
            E.ack = {"id": item[1], "accion": "mensaje", "ok": True,
                     "timestamp": timestamp_ms()}


def procesar_eventos(eventos):
    vue = E.vue
    for ev in eventos:
        if ev[0] == "vuelta":
            _, num, t, secs, es_mejor = ev
            E.cola.add("sesiones/%s/vueltas/%03d" % (E.id, num), {
                "num": num, "tiempo_ms": t, "sectores": secs, "mejor": es_mejor,
                "timestamp": timestamp_ms()})
            E.cola.add("sesiones/%s/resumen" % E.id, {
                "vueltas": num, "mejor_ms": vue.mejor or 0,
                "teorica_ms": vue.teorica() or 0, "ultima_ms": t})
    if eventos:
        E.carrera = {
            "vuelta_actual": vue.num + 1,
            "completadas": vue.num,
            "ultima_ms": vue.ultima or 0,
            "mejor_ms": vue.mejor or 0,
            "teorica_ms": vue.teorica() or 0,
            "delta_ms": vue.delta if vue.delta is not None else 0,
        }


# ---------------------------------------------------------------------
#  BUCLE PRINCIPAL
# ---------------------------------------------------------------------
def pintar(ui, ahora, vel_kmh, rpm, bateria):
    vue = E.vue
    lat, lng, fuente = E.pos
    red = "WiFi" if E.wifi_ok else ("4G" if E.modem_ok else "SIN RED")
    ui.set("estado", "En pista | " + red + (" | PIT" if E.pit else ""))
    ui.set("vel", "Velocidad: %.1f km/h" % vel_kmh)
    ui.set("rpm", "RPM: %d" % int(rpm))
    letra = {"real": "R", "sim": "S"}.get(fuente, "?")
    ui.set("gps", "GPS[%s] %.4f, %.4f" % (letra, lat, lng))
    ui.set("vuelta", "V%d  Ult %s" % (vue.num + 1, fmt_t(vue.ultima)))
    delta = ""
    if vue.delta is not None:
        delta = " D%+.2f" % (vue.delta / 1000.0)
    ui.set("mejor", "Mej %s%s" % (fmt_t(vue.mejor), delta))
    if vue.ult_secs:
        ui.set("sect", "S " + "/".join("%.1f" % (s / 1000.0) for s in vue.ult_secs))
    else:
        ui.set("sect", "S --")
    ui.set("sis", "Bat %d%%  Mem %dK  Pend %d" % (bateria, gc.mem_free() // 1024,
                                                   E.cola.total()))
    ui.set("sd", "SD %s  %s" % ("OK" if E.cola.sd else "NO", E.id[-12:]))

    if E.pit and vel_kmh > CFG["pit_limite_kmh"]:
        ui.mensaje("PIT! limite %d km/h" % CFG["pit_limite_kmh"], 1)
    elif E.msg_texto and time.ticks_diff(E.msg_hasta, ahora) > 0:
        ui.mensaje(E.msg_texto, E.msg_estilo)
    else:
        ui.mensaje("", -1)
    ui.barra(rpm)


def leer_bateria():
    try:
        return M5.Power.getBatteryLevel()
    except Exception:
        return 0


def bucle_principal(ui, leds, din):
    t_ult = time.ticks_ms()
    t_muestra = t_ult
    t_ui = 0
    t_gc = t_ult
    t_bat = 0
    bateria = leer_bateria()
    pos_prev = None
    vel_kmh = 0.0

    while E.activo:
        M5.update()
        ahora = time.ticks_ms()
        dt = time.ticks_diff(ahora, t_ult) / 1000.0
        t_ult = ahora
        if dt <= 0:
            dt = 0.001

        if M5.BtnA.wasPressed():
            marca_manual("boton")
        if M5.BtnB.wasPressed():
            E.apagar = True
            E.activo = False
            break
        if M5.BtnC.wasPressed():
            fijar_meta_aqui()
        if E.entrada:
            procesar_entrada()

        # --- Posición y velocidad ---
        gps_ms = None
        if E.gps is not None:
            E.gps.leer()
            if E.gps.valido():
                lat, lng, fuente = E.gps.lat, E.gps.lng, "real"
                gps_ms = E.gps.vel / 3.6
            else:
                lat, lng, fuente = E.pos[0], E.pos[1], "nofix"
        vel_ms = din.actualizar(dt, gps_ms)
        if E.gps is None:
            if CFG["sim_vel_kmh"] > 0:
                vel_ms = CFG["sim_vel_kmh"] / 3.6
            E.circ.avanzar(vel_ms * dt * CFG["sim_factor"])
            lat, lng = E.circ.latlng()
            fuente = "sim"
        vel_kmh = vel_ms * 3.6
        E.pos = (lat, lng, fuente)

        # --- Vueltas ---
        if fuente != "nofix":
            if pos_prev is None:
                pos_prev = (lat, lng)
            if E.vue.meta is None:
                E.vue.fijar_meta(lat, lng, ahora)
                E.cola.add("sesiones/%s/meta" % E.id, {"lat": lat, "lng": lng})
            inc = dist_m(pos_prev[0], pos_prev[1], lat, lng) if vel_kmh > 1.0 else 0.0
            eventos = E.vue.actualizar(pos_prev, (lat, lng), inc, ahora)
            pos_prev = (lat, lng)
            if eventos:
                procesar_eventos(eventos)

        leds.actualizar(din.rpm, ahora)

        # --- Muestreo ---
        if time.ticks_diff(ahora, t_muestra) >= E.muestreo_ms:
            t_muestra = ahora
            if time.ticks_diff(ahora, t_bat) >= 5000:
                t_bat = ahora
                bateria = leer_bateria()
            ts = timestamp_ms()
            E.seq += 1
            paquete = {
                "timestamp": ts,
                "aceleracion_x": round(din.a[0], 3),
                "aceleracion_y": round(din.a[1], 3),
                "aceleracion_z": round(din.a[2], 3),
                "latitud": round(lat, 6),
                "longitud": round(lng, 6),
                "velocidad": round(vel_kmh, 1),
                "rpm": int(din.rpm),
                "bateria_m5": bateria,
                "vuelta": E.vue.num + 1,
                "gps": fuente,
            }
            E.ultimo_dato = paquete
            E.cola.add("sesiones/%s/datos/%013d_%05d" % (E.id, ts, E.seq), paquete)

        # --- Pantalla (5 Hz) ---
        if time.ticks_diff(ahora, t_ui) >= 200:
            t_ui = ahora
            pintar(ui, ahora, vel_kmh, din.rpm, bateria)

        if time.ticks_diff(ahora, t_gc) >= 10000:
            t_gc = ahora
            gc.collect()

        time.sleep_ms(10)


def main():
    global E
    M5.begin()
    ui = Pantalla()
    ui.set("estado", "Iniciando...")

    base = montar_sd()
    cargar_config()
    E = Estado()
    E.cola = Cola(base, base is not None)
    if CFG["modem_4g"]:
        E.modem = Modem4G()
    if CFG["gps_real"]:
        try:
            E.gps = GpsReal()
        except Exception as e:
            print("GPS no disponible:", e)
            E.gps = None
    leds = Leds()
    din = Dinamica()

    # Wi-Fi
    ui.set("estado", "Conectando WiFi...")
    E.wlan = network.WLAN(network.STA_IF)
    E.wlan.active(True)
    try:
        E.wlan.connect(CFG["wifi_ssid"], CFG["wifi_password"])
    except Exception as e:
        print("Error Wi-Fi:", e)
    for _ in range(int(CFG["wifi_espera_s"])):
        if E.wlan.isconnected():
            break
        time.sleep(1)
    E.wifi_ok = E.wlan.isconnected()
    print("WiFi conectado:", E.wifi_ok)
    if E.wifi_ok:
        sincronizar_ntp()

    ui.set("estado", "Calibrando IMU...")
    ui.set("vel", "No muevas el equipo")
    din.calibrar()

    E.circ = Circuito()
    nueva_sesion()

    try:
        _thread.stack_size(16 * 1024)
    except Exception:
        pass
    E.red_on = True
    _thread.start_new_thread(tarea_red, ())

    print("Iniciando sistema de telemetría...")
    estado_final = "finalizada"
    try:
        bucle_principal(ui, leds, din)
    except BaseException as e:   # incluye KeyboardInterrupt y fallos inesperados
        try:
            sys.print_exception(e)
        except Exception:
            print("Error:", e)
    # Cierre controlado ante cualquier salida
    leds.apagar()
    finalizar_sesion(estado_final)
    if E.apagar:
        try:
            M5.Power.powerOff()
        except Exception as e:
            print("No se pudo apagar:", e)


if __name__ == "__main__":
    main()