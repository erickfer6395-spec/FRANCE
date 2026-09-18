# -*- coding: utf-8 -*-
r"""
usuarios.py
Cuentas de acceso a la página y cifrado de los datos que se publican.

El repositorio y GitHub Pages son públicos, así que la tabla se publica cifrada
(AES-256-GCM). Cada cuenta tiene su contraseña: la página la pide y descifra los
datos en el navegador. Sin una cuenta válida, datos.js es ilegible.

Uso (en la PC que publica, con el mismo usuario de Windows que la tarea programada):
    python usuarios.py agregar NOMBRE               crea la cuenta y muestra su contraseña
    python usuarios.py agregar NOMBRE --escribir    igual, pero tú escribes la contraseña
    python usuarios.py contrasena NOMBRE            genera una contraseña nueva
    python usuarios.py quitar NOMBRE                elimina la cuenta
    python usuarios.py lista                        muestra las cuentas

Los cambios llegan a la página en la siguiente sincronización (o ya, con:
python sincronizar.py). Al quitar una cuenta, esa persona deja de poder abrir
los datos que se publiquen desde ese momento.

Las cuentas se guardan cifradas con Windows (DPAPI) en
%LOCALAPPDATA%\StockDTI\usuarios.dat. De cada una no se guarda la contraseña,
sino la clave que se deriva de ella.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

# Datos locales de esta máquina (fuera del repositorio, que es público)
DIR_LOCAL = Path(os.environ.get("STOCKDTI_DIR")
                 or Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "StockDTI")
REGISTRO = DIR_LOCAL / "usuarios.dat"

# PBKDF2-SHA256: mismo algoritmo que usa la página con WebCrypto
ITERACIONES = 600_000
_NOMBRE_VALIDO = re.compile(r"^[a-z0-9._@-]{2,64}$")
# Palabras que hacen adivinable una contraseña escrita a mano
_PROHIBIDAS = ("stock", "dti", "lalle", "contrasena", "contraseña", "password", "motdepasse",
               "123456", "qwerty", "azerty", "abcdef")
# Sin caracteres que se confunden al dictarlos o copiarlos (l/1/I, o/0/O)
_ALFABETO = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


# ---------------------------------------------------------------------------
# DPAPI de Windows
# ---------------------------------------------------------------------------

def _dpapi(datos, proteger):
    """Cifra o descifra bytes con DPAPI: solo el mismo usuario de Windows, en
    esta misma máquina, puede descifrarlos."""
    if os.name != "nt":
        raise SystemExit("El cifrado local solo funciona en Windows.")
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(datos, len(datos))
    entrada = Blob(len(datos), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    salida = Blob()
    funcion = ctypes.windll.crypt32.CryptProtectData if proteger else ctypes.windll.crypt32.CryptUnprotectData
    CRYPTPROTECT_UI_FORBIDDEN = 0x01
    if not funcion(ctypes.byref(entrada), None, None, None, None,
                   CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(salida)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(salida.pbData, salida.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(salida.pbData, ctypes.c_void_p))


def hay_consola():
    """True si hay una consola de verdad para escribir la contraseña. En Windows
    isatty() también es cierto para NUL, y getpass se quedaría esperando para siempre."""
    if os.name != "nt":
        return bool(sys.stdin and sys.stdin.isatty())
    import ctypes
    k32 = ctypes.windll.kernel32
    k32.GetStdHandle.restype = ctypes.c_void_p
    k32.GetStdHandle.argtypes = [ctypes.c_ulong]
    k32.GetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    modo = ctypes.c_ulong()
    entrada = k32.GetStdHandle(ctypes.c_ulong(0xFFFFFFF6))      # STD_INPUT_HANDLE (-10)
    return bool(entrada and k32.GetConsoleMode(entrada, ctypes.byref(modo)))


def proteger(datos):
    return _dpapi(datos, proteger=True)


def desproteger(datos):
    return _dpapi(datos, proteger=False)


def guardar_protegido(ruta, objeto):
    """Guarda un objeto JSON cifrado con DPAPI (escritura atómica)."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_name(ruta.name + ".tmp")
    temporal.write_bytes(proteger(json.dumps(objeto, ensure_ascii=False).encode("utf-8")))
    os.replace(temporal, ruta)


def leer_protegido(ruta):
    try:
        return json.loads(desproteger(ruta.read_bytes()).decode("utf-8"))
    except OSError as e:
        raise SystemExit(f"No se pudo descifrar {ruta} ({e}). Se cifra por usuario de Windows: "
                         "hay que usar el mismo usuario que lo creó.")


# ---------------------------------------------------------------------------
# Cuentas
# ---------------------------------------------------------------------------

def _b64(datos):
    return base64.b64encode(datos).decode("ascii")


def _desde_b64(texto):
    return base64.b64decode(texto)


def cargar():
    """Lee el registro de cuentas y completa lo que falte (la sal de esta
    instalación y la clave de la huella), guardándolo si el archivo ya existía."""
    reg = leer_protegido(REGISTRO) if REGISTRO.exists() else {}
    reg.setdefault("usuarios", {})
    faltaba = False
    for clave, valor in (("clave_huella", lambda: _b64(secrets.token_bytes(32))),
                         ("sal", lambda: _b64(secrets.token_bytes(16)))):
        if not reg.get(clave):
            reg[clave] = valor()
            faltaba = True
    if not reg.get("iteraciones"):
        reg["iteraciones"] = ITERACIONES
        faltaba = True
    if faltaba and REGISTRO.exists():
        guardar_protegido(REGISTRO, reg)
    return reg


def normalizar_nombre(nombre):
    n = str(nombre).strip().lower()
    if not _NOMBRE_VALIDO.match(n):
        raise SystemExit("El nombre de la cuenta debe tener de 2 a 64 caracteres: letras sin acentos, "
                         "números, punto, guion, guion bajo o @ (por ejemplo: maria.lopez).")
    return n


def derivar(contrasena, sal, nombre, iteraciones):
    """Clave de la cuenta, a partir de su contraseña y de su nombre.

    La sal es la de esta instalación, distinta en cada una, y el nombre entra en
    el cálculo: así dos cuentas con la misma contraseña tienen claves distintas.
    """
    texto = unicodedata.normalize("NFC", contrasena).encode("utf-8")
    return hashlib.pbkdf2_hmac("sha256", texto, sal + b"|" + nombre.encode("utf-8"),
                               iteraciones, 32)


def id_cuenta(clave):
    """Identificador que se publica en datos.js para localizar la cuenta.

    Se calcula desde la clave derivada, no desde el nombre: quien descargue
    datos.js no puede saber qué cuentas existen sin conocer su contraseña.
    """
    return hashlib.sha256(b"stockdti-id:" + clave).hexdigest()[:24]


def generar_contrasena():
    """12 caracteres en 3 grupos (~70 bits): resiste ataques por fuerza bruta."""
    return "-".join("".join(secrets.choice(_ALFABETO) for _ in range(4)) for _ in range(3))


def _pedir_contrasena(nombre):
    """Pide una contraseña escrita a mano. Como datos.js es público, quien lo
    descargue puede probar contraseñas sin límite en su propia máquina: por eso
    se exige una contraseña larga y no evidente."""
    import getpass
    if not hay_consola():
        raise SystemExit("--escribir necesita una consola interactiva: ábrelo en PowerShell o CMD.")
    try:
        primera = getpass.getpass("Contraseña (mínimo 14 caracteres, no se muestra): ")
        if len(primera) < 14:
            raise SystemExit("Demasiado corta: usa al menos 14 caracteres, o mejor una frase. "
                             "No se guardó nada.")
        simple = primera.lower()
        if any(p in simple for p in _PROHIBIDAS) or nombre.split("@")[0] in simple:
            raise SystemExit("Demasiado fácil de adivinar: no uses el nombre de la cuenta ni "
                             "palabras como «stock», «dti» o «contraseña». No se guardó nada.")
        if getpass.getpass("Repítela: ") != primera:
            raise SystemExit("No coinciden. No se guardó nada.")
    except EOFError:
        raise SystemExit("No se pudo leer la contraseña: ejecútalo en una consola. No se guardó nada.")
    return primera


def _fijar_contrasena(reg, nombre, contrasena):
    clave = derivar(contrasena, _desde_b64(reg["sal"]), nombre, reg["iteraciones"])
    reg["usuarios"][nombre] = {
        "clave": _b64(clave),
        "creado": reg["usuarios"].get(nombre, {}).get("creado")
        or datetime.now().astimezone().isoformat(timespec="seconds"),
        "cambiado": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------------------
# Cifrado de lo que se publica
# ---------------------------------------------------------------------------

def huella(datos, reg=None):
    """Firma de lo publicado (datos sin la hora + cuentas). Sirve para saber si
    hay algo nuevo que publicar sin poder descifrar datos.js. Usa una clave
    local, así que no revela nada del contenido."""
    reg = reg or cargar()
    contenido = json.dumps({
        "datos": {k: v for k, v in datos.items() if k != "fecha_actualizacion"},
        "cuentas": sorted(id_cuenta(_desde_b64(u["clave"])) for u in reg["usuarios"].values()),
    }, sort_keys=True, ensure_ascii=False)
    return hmac.new(_desde_b64(reg["clave_huella"]), contenido.encode("utf-8"), "sha256").hexdigest()


def cifrar(datos, reg=None):
    """Devuelve el objeto que se publica en datos.js (DATOS_CIFRADOS).

    Los datos se cifran con una clave aleatoria nueva en cada publicación, y esa
    clave se cifra una vez por cuenta con la clave derivada de su contraseña.
    """
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise SystemExit("Falta la librería cryptography. Instálala con: pip install -r requirements.txt")
    reg = reg or cargar()
    if not reg["usuarios"]:
        raise SystemExit("No hay cuentas de acceso, así que nadie podría ver la tabla. "
                         "Crea una con: python usuarios.py agregar NOMBRE")

    clave = AESGCM.generate_key(bit_length=256)
    iv = secrets.token_bytes(12)
    plano = json.dumps(datos, ensure_ascii=False).encode("utf-8")
    cuentas = {}
    for nombre, u in sorted(reg["usuarios"].items()):
        clave_cuenta = _desde_b64(u["clave"])
        iv_cuenta = secrets.token_bytes(12)
        cuentas[id_cuenta(clave_cuenta)] = {
            "iv": _b64(iv_cuenta),
            "clave": _b64(AESGCM(clave_cuenta).encrypt(iv_cuenta, clave, None)),
        }
    return {
        "version": 2,
        "fecha": datos.get("fecha_actualizacion", ""),
        "huella": huella(datos, reg),
        "sal": reg["sal"],
        "iteraciones": reg["iteraciones"],
        "cuentas": cuentas,
        "iv": _b64(iv),
        "datos": _b64(AESGCM(clave).encrypt(iv, plano, None)),
    }


def descifrar(publicado, nombre, contrasena):
    """Lo mismo que hace la página al iniciar sesión (para pruebas y diagnóstico)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    kek = derivar(contrasena, _desde_b64(publicado["sal"]), normalizar_nombre(nombre),
                  int(publicado["iteraciones"]))
    cuenta = publicado["cuentas"].get(id_cuenta(kek))
    if cuenta is None:
        raise ValueError("cuenta o contraseña incorrecta")
    clave = AESGCM(kek).decrypt(_desde_b64(cuenta["iv"]), _desde_b64(cuenta["clave"]), None)
    plano = AESGCM(clave).decrypt(_desde_b64(publicado["iv"]), _desde_b64(publicado["datos"]), None)
    return json.loads(plano.decode("utf-8"))


# ---------------------------------------------------------------------------
# Línea de comandos
# ---------------------------------------------------------------------------

def _mostrar_contrasena(nombre, contrasena, generada):
    if generada:
        print(f"  Contraseña: {contrasena}")
        print("  Anótala ahora: no se vuelve a mostrar. Si se pierde, genera otra con")
        print(f"    python usuarios.py contrasena {nombre}")
    print("\nLa página se actualiza en la siguiente sincronización (o ya, con: python sincronizar.py).")


def consola_segura():
    """Evita que un carácter como «★» haga fallar la salida cuando no es una
    consola (redirección a archivo, tubería, herramientas)."""
    for flujo in (sys.stdout, sys.stderr):
        if flujo is not None and hasattr(flujo, "reconfigure"):
            flujo.reconfigure(errors="replace")


def main(argv=None):
    consola_segura()
    p = argparse.ArgumentParser(prog="usuarios.py",
                                description="Cuentas de acceso a la página de stock.")
    sub = p.add_subparsers(dest="accion", required=True, metavar="ACCIÓN")
    a = sub.add_parser("agregar", help="crear una cuenta")
    a.add_argument("nombre")
    a.add_argument("--escribir", action="store_true", help="escribir la contraseña en vez de generarla")
    c = sub.add_parser("contrasena", help="cambiar la contraseña de una cuenta")
    c.add_argument("nombre")
    c.add_argument("--escribir", action="store_true", help="escribir la contraseña en vez de generarla")
    q = sub.add_parser("quitar", help="eliminar una cuenta")
    q.add_argument("nombre")
    sub.add_parser("lista", help="ver las cuentas")
    args = p.parse_args(argv)

    reg = cargar()
    if args.accion == "lista":
        if not reg["usuarios"]:
            print("No hay cuentas. Crea una con: python usuarios.py agregar NOMBRE")
        for nombre, u in sorted(reg["usuarios"].items()):
            print(f"  {nombre:<30} creada {u.get('creado', '')[:10]}   contraseña del {u.get('cambiado', '')[:10]}")
        return

    nombre = normalizar_nombre(args.nombre)
    if args.accion == "quitar":
        if reg["usuarios"].pop(nombre, None) is None:
            raise SystemExit(f"No existe la cuenta «{nombre}».")
        guardar_protegido(REGISTRO, reg)
        print(f"Cuenta «{nombre}» eliminada.")
        if reg["usuarios"]:
            print("Dejará de ver los datos en cuanto se publique la siguiente actualización "
                  "(o ya, con: python sincronizar.py).")
        else:
            print("AVISO: ya no queda ninguna cuenta, así que no se puede publicar nada nuevo y\n"
                  "la página seguirá mostrando los últimos datos publicados, que esa cuenta todavía\n"
                  "puede abrir. Crea otra cuenta y publica (python sincronizar.py) para cerrarle el paso.")
        return

    if args.accion == "agregar" and nombre in reg["usuarios"]:
        raise SystemExit(f"La cuenta «{nombre}» ya existe. Para darle otra contraseña: "
                         f"python usuarios.py contrasena {nombre}")
    if args.accion == "contrasena" and nombre not in reg["usuarios"]:
        raise SystemExit(f"No existe la cuenta «{nombre}».")
    contrasena = _pedir_contrasena(nombre) if args.escribir else generar_contrasena()
    _fijar_contrasena(reg, nombre, contrasena)
    guardar_protegido(REGISTRO, reg)
    print(f"Cuenta «{nombre}» {'creada' if args.accion == 'agregar' else 'actualizada'}.")
    _mostrar_contrasena(nombre, contrasena, generada=not args.escribir)


if __name__ == "__main__":
    main()
