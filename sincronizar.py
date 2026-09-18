# -*- coding: utf-8 -*-
r"""
sincronizar.py
Publica automáticamente en GitHub Pages el stock leído del ERP (cifrado).

Una tarea programada de Windows lo ejecuta cada 15 minutos con pythonw.exe
(sin ventana). En cada ejecución:
  1. pone su copia del repositorio igual que GitHub,
  2. lee el stock del ERP y escribe datos.js cifrado (generar_datos.py --db),
  3. si cambió algo, hace commit de datos.js y push.

Todo lo que usa la tarea vive en %LOCALAPPDATA%\StockDTI, fuera de tu carpeta
de trabajo:
    programa\           copia fija de los scripts (la instala --instalar; la
                        tarea nunca ejecuta código descargado de GitHub)
    repo\               copia del repositorio donde se escribe datos.js
    config_stock.json   referencias que se publican
    conexion.dat        conexión al ERP (cifrada con Windows)
    usuarios.dat        cuentas de acceso a la página (cifradas con Windows)

Uso:
    python sincronizar.py --instalar [--cada 15]   instala (o actualiza) la tarea programada
    python sincronizar.py                          sincroniza ahora
    python sincronizar.py --excel "C:\ruta\Stock report.xlsx"   publica desde el Excel
    python sincronizar.py --estado                 estado de la tarea y últimas líneas del registro
    python sincronizar.py --desinstalar            elimina la tarea programada

Registro: %LOCALAPPDATA%\StockDTI\sincronizar.log
"""
import argparse
import hashlib
import json
import logging
import logging.handlers
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

import usuarios

BASE = Path(__file__).resolve().parent
DIR_LOCAL = usuarios.DIR_LOCAL
PROGRAMA = DIR_LOCAL / "programa"
CLON = DIR_LOCAL / "repo"
CONFIG = DIR_LOCAL / "config_stock.json"
REGISTRO = DIR_LOCAL / "sincronizar.log"
ESTADO = DIR_LOCAL / "estado.json"
BLOQUEO = DIR_LOCAL / "sincronizar.lock"
ARCHIVOS_PROGRAMA = ("generar_datos.py", "usuarios.py", "sincronizar.py")
TAREA = r"\StockDTI\Sincronizar stock"
RAMA = "main"
INTENTOS = 3
# La tarea se cancela a los 10 minutos (ExecutionTimeLimit), así que el ciclo
# se rinde antes para poder registrar el error y no dejar nada a medias.
LIMITE_CICLO = 8 * 60
# Identidad de los commits automáticos (la copia no hereda la de tu carpeta de trabajo)
IDENTIDAD = ["-c", "user.name=Stock DTI (automático)",
             "-c", "user.email=stock-dti@users.noreply.github.com"]
SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)

log = logging.getLogger("stockdti")


class ErrorSync(Exception):
    pass


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def configurar_registro():
    if log.handlers:
        return
    DIR_LOCAL.mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.INFO)
    archivo = logging.handlers.RotatingFileHandler(
        REGISTRO, maxBytes=200_000, backupCount=2, encoding="utf-8")
    archivo.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s",
                                           "%Y-%m-%d %H:%M:%S"))
    log.addHandler(archivo)
    if sys.stdout is not None:          # con pythonw no hay consola
        consola = logging.StreamHandler(sys.stdout)
        consola.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(consola)


def _entorno():
    env = dict(os.environ)
    env.update({
        "GIT_TERMINAL_PROMPT": "0",     # nunca pedir usuario/contraseña por consola
        "GCM_INTERACTIVE": "never",     # ni abrir la ventana de inicio de sesión de GitHub
        # Si la red se queda colgada a mitad de una transferencia, git la corta
        # (el timeout de subprocess no basta: no mata a los procesos hijos de git)
        "GIT_HTTP_LOW_SPEED_LIMIT": "1000",
        "GIT_HTTP_LOW_SPEED_TIME": "60",
        "PYTHONUTF8": "1",
    })
    return env


def ejecutar(cmd, cwd=None, check=True, timeout=90, encoding="utf-8"):
    cmd = [str(c) for c in cmd]
    try:
        r = subprocess.run(
            cmd, cwd=str(cwd or CLON), env=_entorno(), stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding=encoding, errors="replace",
            creationflags=SIN_VENTANA, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise ErrorSync(f"Tiempo agotado ({timeout} s): {' '.join(cmd)}")
    if check and r.returncode != 0:
        raise ErrorSync(f"Falló: {' '.join(cmd)}\n{(r.stderr or r.stdout).strip()}")
    return r


def git(*args, **kw):
    return ejecutar(["git", *args], **kw)


def python_consola():
    """python.exe junto al intérprete actual (la tarea corre con pythonw.exe)."""
    exe = Path(sys.executable)
    candidato = exe.with_name("python.exe")
    return candidato if candidato.exists() else exe


def _huellas_programa(carpeta):
    return {nombre: hashlib.sha256((carpeta / nombre).read_bytes()).hexdigest()
            for nombre in ARCHIVOS_PROGRAMA if (carpeta / nombre).exists()}


def scripts_desactualizados():
    """Nombres de los scripts que cambiaron en la carpeta de trabajo desde el
    último --instalar (la tarea seguiría usando la copia antigua)."""
    try:
        info = json.loads((PROGRAMA / "version.json").read_text(encoding="utf-8"))
        origen = Path(info["origen"])
        if not origen.exists() or origen == PROGRAMA:
            return []
        actuales = _huellas_programa(origen)
        return sorted(n for n, h in info["archivos"].items() if actuales.get(n) != h)
    except (OSError, ValueError, KeyError):
        return []


def es_copia_automatica():
    """Solo la copia creada por --instalar se puede sobrescribir con reset --hard."""
    if not (CLON / ".git").exists():
        return False
    r = git("config", "--local", "--get", "stockdti.auto", check=False)
    return r.stdout.strip() == "true"


def tomar_bloqueo():
    """Evita dos sincronizaciones a la vez (p. ej. la tarea y una ejecución manual)."""
    DIR_LOCAL.mkdir(parents=True, exist_ok=True)
    f = open(BLOQUEO, "a+b")
    try:
        f.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def limpiar_bloqueos_git():
    """Borra los bloqueos que deja git si la PC se apagó o la tarea se canceló a
    mitad de una sincronización. Seguro: la copia es solo de esta tarea y
    sincronizar.lock garantiza que no hay otra en curso; el minuto de margen
    cubre a un git hijo que todavía estuviera cerrando."""
    g = CLON / ".git"
    candidatos = [g / n for n in ("index.lock", "HEAD.lock", "ORIG_HEAD.lock", "FETCH_HEAD.lock",
                                  "packed-refs.lock", "config.lock", "shallow.lock")]
    candidatos += list((g / "refs").rglob("*.lock"))
    for ruta in candidatos:
        try:
            if ruta.exists() and time.time() - ruta.stat().st_mtime > 60:
                log.warning(f"Se borra un bloqueo de git abandonado: {ruta.relative_to(CLON)}")
                ruta.unlink()
        except OSError:
            pass


def ahora():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def guardar_estado(resultado, detalle):
    try:
        previo = json.loads(ESTADO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previo = {}
    estado = {
        "ultima_ejecucion": ahora(),
        "resultado": resultado,
        "detalle": detalle,
        "ultima_publicacion": ahora() if resultado == "publicado" else previo.get("ultima_publicacion"),
    }
    temporal = ESTADO.with_name(ESTADO.name + ".tmp")
    temporal.write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporal, ESTADO)


# ---------------------------------------------------------------------------
# Sincronización
# ---------------------------------------------------------------------------

def ciclo(fuente):
    """Una sincronización. fuente: ["--db"] o [ruta del Excel].
    Devuelve (resultado, última línea del generador)."""
    if not es_copia_automatica():
        raise ErrorSync(f"No existe la copia automática en {CLON}. "
                        "Ejecuta: python sincronizar.py --instalar")
    limpiar_bloqueos_git()
    detalle = ""
    limite = time.monotonic() + LIMITE_CICLO
    for intento in range(1, INTENTOS + 1):
        if time.monotonic() > limite:
            raise ErrorSync("Se agotó el tiempo de la sincronización; se reintentará en el "
                            "siguiente ciclo.\n" + detalle)
        git("fetch", "--quiet", "origin", RAMA)
        git("reset", "--hard", "--quiet", f"origin/{RAMA}")
        git("clean", "-fd", "--quiet")

        # El generador es el de esta carpeta (la copia fija de --instalar, o tu
        # carpeta de trabajo si lo ejecutas a mano), nunca el descargado de GitHub.
        gen = ejecutar([python_consola(), BASE / "generar_datos.py", *fuente,
                        "--salida", CLON / "datos.js"], cwd=BASE, check=False, timeout=180)
        salida = [l for l in (gen.stdout + "\n" + gen.stderr).splitlines() if l.strip()]
        if gen.returncode != 0:
            raise ErrorSync("No se pudieron generar los datos:\n" + "\n".join(salida[-15:]))
        for linea in salida:
            if linea.startswith("AVISO"):
                log.warning(linea)
        ultima = salida[-1] if salida else ""

        if not git("status", "--porcelain", "--", "datos.js").stdout.strip():
            return "sin cambios", ultima
        git("add", "datos.js")
        git(*IDENTIDAD, "commit", "--quiet", "-m", f"Stock automático {datetime.now():%Y-%m-%d %H:%M}")
        push = git("push", "--quiet", "origin", f"HEAD:{RAMA}", check=False, timeout=120)
        if push.returncode == 0:
            return "publicado", ultima
        detalle = (push.stderr or push.stdout).strip()
        log.warning(f"El push no se completó (intento {intento}/{INTENTOS}): {detalle}")
    raise ErrorSync(
        f"No se pudo publicar en GitHub tras {INTENTOS} intentos. Si la sesión de GitHub caducó, "
        f"abre una terminal en {CLON} y ejecuta 'git push' una vez para volver a iniciar sesión.\n"
        + detalle)


def sincronizar(fuente=("--db",)):
    bloqueo = tomar_bloqueo()
    if bloqueo is None:
        log.info("Ya hay una sincronización en curso; se omite esta.")
        return 0
    try:
        log.info("Inicio de la sincronización" + ("" if fuente[0] == "--db" else f" desde {fuente[0]}"))
        viejos = scripts_desactualizados()
        if viejos:
            log.warning(f"La copia instalada no tiene los últimos cambios de {', '.join(viejos)}; "
                        "ejecuta: python sincronizar.py --instalar")
        resultado, ultima = ciclo(list(fuente))
        log.info(f"{resultado.upper()}: {ultima}")
        guardar_estado(resultado, ultima)
        return 0
    except ErrorSync as e:
        log.error(str(e))
        guardar_estado("error", str(e))
        return 1
    except Exception as e:
        log.exception("Error inesperado")
        guardar_estado("error", f"{type(e).__name__}: {e}")
        return 1
    finally:
        bloqueo.close()


# ---------------------------------------------------------------------------
# Instalación de la tarea programada
# ---------------------------------------------------------------------------

# Mismo orden de elementos que las tareas que exporta el Programador de tareas.
TAREA_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Publica en GitHub Pages el stock DTI leído del ERP (sincronizar.py).</Description>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <Repetition>
        <Interval>PT{cada}M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>{inicio}</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{usuario}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT10M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{comando}</Command>
      <Arguments>{argumentos}</Arguments>
      <WorkingDirectory>{carpeta}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def preparar_copia():
    """Crea (o comprueba) la copia del repositorio que usa la tarea."""
    origen = git("remote", "get-url", "origin", cwd=BASE).stdout.strip()
    DIR_LOCAL.mkdir(parents=True, exist_ok=True)
    if (CLON / ".git").exists():
        actual = git("remote", "get-url", "origin").stdout.strip()
        if actual != origen:
            raise ErrorSync(f"{CLON} es una copia de {actual}, no de {origen}.")
    else:
        if CLON.exists() and any(CLON.iterdir()):
            raise ErrorSync(f"{CLON} existe y no es un repositorio de git: muévelo o bórralo.")
        print(f"Clonando {origen}\n  en {CLON}…")
        git("clone", "--quiet", "--branch", RAMA, origen, CLON, cwd=DIR_LOCAL)
    git("config", "--local", "stockdti.auto", "true")
    git("config", "--local", "user.name", "Stock DTI (automático)")
    git("config", "--local", "user.email", "stock-dti@users.noreply.github.com")


def registrar_tarea(cada):
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        pythonw = Path(sys.executable)
    dominio, usuario = os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME", "")
    xml = TAREA_XML.format(
        cada=int(cada),
        inicio=datetime.now().replace(second=0, microsecond=0).isoformat(),
        usuario=escape(f"{dominio}\\{usuario}" if dominio else usuario),
        comando=escape(str(pythonw)),
        argumentos=escape(f'"{PROGRAMA / "sincronizar.py"}"'),
        carpeta=escape(str(PROGRAMA)),
    )
    archivo = DIR_LOCAL / "tarea.xml"
    archivo.write_text(xml, encoding="utf-16")
    try:
        ejecutar(["schtasks", "/Create", "/TN", TAREA, "/XML", archivo, "/F"],
                 cwd=DIR_LOCAL, encoding="oem")
    finally:
        archivo.unlink(missing_ok=True)


def instalar(cada):
    if os.name != "nt":
        raise ErrorSync("La tarea programada solo se puede instalar en Windows.")
    if not 5 <= cada <= 1440:
        raise ErrorSync("--cada debe estar entre 5 y 1440 minutos.")

    # 1. Lo que la tarea necesita en esta PC
    if not usuarios.cargar()["usuarios"]:
        raise ErrorSync("No hay cuentas de acceso a la página. Crea al menos una con: "
                        "python usuarios.py agregar NOMBRE")
    print(f"Probando la lectura del ERP con {CONFIG}…")
    prueba = ejecutar([python_consola(), BASE / "generar_datos.py", "--db", "--probar"],
                      cwd=BASE, check=False, timeout=180)
    print((prueba.stdout + prueba.stderr).rstrip())
    if prueba.returncode != 0:
        raise ErrorSync("La lectura del ERP falló; corrige lo anterior y vuelve a ejecutar --instalar.")

    # 2. Copia del repositorio y copia fija del programa
    preparar_copia()
    PROGRAMA.mkdir(parents=True, exist_ok=True)
    for nombre in ARCHIVOS_PROGRAMA:
        shutil.copyfile(BASE / nombre, PROGRAMA / nombre)
    shutil.rmtree(PROGRAMA / "__pycache__", ignore_errors=True)
    (PROGRAMA / "version.json").write_text(
        json.dumps({"origen": str(BASE), "archivos": _huellas_programa(BASE),
                    "instalado": ahora()}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Programa instalado en {PROGRAMA}")

    # 3. Tarea programada
    registrar_tarea(cada)
    print(f"Tarea programada «{TAREA}» instalada: cada {cada} minutos mientras tu sesión "
          "de Windows esté iniciada.")

    # 4. Primera sincronización, con la copia instalada (igual que la tarea)
    print("\nPrimera sincronización:")
    r = ejecutar([python_consola(), PROGRAMA / "sincronizar.py"], cwd=PROGRAMA,
                 check=False, timeout=600)
    print((r.stdout + r.stderr).rstrip())
    return r.returncode


def desinstalar():
    r = ejecutar(["schtasks", "/Delete", "/TN", TAREA, "/F"], cwd=DIR_LOCAL.parent,
                 check=False, encoding="oem")
    print("Tarea programada eliminada." if r.returncode == 0 else "La tarea programada no estaba instalada.")
    print(f"La configuración, las cuentas y la copia del repositorio siguen en {DIR_LOCAL}.")


def mostrar_estado():
    r = ejecutar(["schtasks", "/Query", "/TN", TAREA, "/V", "/FO", "LIST"],
                 cwd=DIR_LOCAL.parent, check=False, encoding="oem")
    if r.returncode != 0:
        print("La tarea programada no está instalada.")
    else:
        claves = ("nombre de tarea", "taskname", "estado", "status", "últim", "ultim", "last",
                  "próxim", "proxim", "next")
        for linea in r.stdout.splitlines():
            if any(c in linea.lower() for c in claves):
                print(linea.strip())
    viejos = scripts_desactualizados()
    if viejos:
        print(f"\nAVISO: la copia instalada no tiene los últimos cambios de {', '.join(viejos)}. "
              "Ejecuta: python sincronizar.py --instalar")
    try:
        estado = json.loads(ESTADO.read_text(encoding="utf-8"))
        print(f"\nÚltima ejecución:   {estado.get('ultima_ejecucion')}  ->  {estado.get('resultado')}")
        print(f"Última publicación: {estado.get('ultima_publicacion') or 'ninguna todavía'}")
        if estado.get("resultado") == "error":
            print(f"\n{estado.get('detalle')}")
    except (OSError, ValueError):
        print("\nTodavía no se ha ejecutado ninguna sincronización.")
    try:
        lineas = REGISTRO.read_text(encoding="utf-8", errors="replace").splitlines()
        print(f"\nÚltimas líneas de {REGISTRO}:")
        print("\n".join(lineas[-15:]))
    except OSError:
        pass


def main(argv=None):
    usuarios.consola_segura()
    p = argparse.ArgumentParser(
        prog="sincronizar.py",
        description="Publica en GitHub Pages el stock del ERP (una sincronización si no se indica nada).",
    )
    grupo = p.add_mutually_exclusive_group()
    grupo.add_argument("--instalar", action="store_true", help="instalar o actualizar la tarea programada")
    grupo.add_argument("--desinstalar", action="store_true", help="eliminar la tarea programada")
    grupo.add_argument("--estado", action="store_true", help="ver el estado de la sincronización")
    grupo.add_argument("--excel", metavar="RUTA", type=Path, help="publicar desde un Excel en vez del ERP")
    p.add_argument("--cada", type=int, default=15, metavar="MIN",
                   help="minutos entre sincronizaciones (con --instalar; por defecto 15)")
    p.add_argument("--forzar", action="store_true",
                   help="publicar aunque no haya cambios, o aunque todo el stock sea 0")
    args = p.parse_args(argv)

    try:
        if args.instalar:
            return instalar(args.cada)
        if args.desinstalar:
            desinstalar()
            return 0
        if args.estado:
            mostrar_estado()
            return 0
    except ErrorSync as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    configurar_registro()
    extra = ["--forzar"] if args.forzar else []
    if args.excel:
        if not args.excel.exists():
            log.error(f"No existe el archivo: {args.excel}")
            return 1
        return sincronizar([str(args.excel.resolve()), *extra])
    return sincronizar(["--db", *extra])


if __name__ == "__main__":
    sys.exit(main())
