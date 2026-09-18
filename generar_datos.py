# -*- coding: utf-8 -*-
r"""
generar_datos.py
Genera el archivo datos.js que lee index.html. Los datos se publican cifrados:
solo se ven en la página iniciando sesión con una cuenta de usuarios.py.

Uso:
    python generar_datos.py --db --probar            -> muestra la tabla del ERP (no escribe nada)
    python generar_datos.py --buscar pyro glass      -> busca materiales en el ERP para ver su código
    python generar_datos.py --configurar             -> guarda (cifrada) la conexión al ERP
    python generar_datos.py --db                     -> escribe datos.js con el stock del ERP
    python generar_datos.py "C:/ruta/Stock report.xlsx"  -> escribe datos.js desde el Excel

Para publicar no hace falta ejecutarlo a mano: sincronizar.py lo usa para
escribir datos.js en su propia copia del repositorio y hacer el push.

Requiere Python 3 y las librerías de requirements.txt:
    pip install -r requirements.txt

datos.js solo se reescribe si cambió el stock o las cuentas, o una vez al día
aunque nada cambie, para que la fecha de la página confirme que la
sincronización sigue activa.
"""
import argparse
import json
import math
import os
import re
import sys
import time
from datetime import datetime, date
from decimal import Decimal
from pathlib import Path

import usuarios

BASE = Path(__file__).resolve().parent
SALIDA = BASE / "datos.js"

# Datos locales de esta máquina (fuera del repositorio, que es público)
DIR_LOCAL = usuarios.DIR_LOCAL
CONFIG = DIR_LOCAL / "config_stock.json"
CONEXION = DIR_LOCAL / "conexion.dat"

# Variables de entorno que, si existen, sustituyen al archivo cifrado de --configurar
VARIABLES_DB = {
    "host": "STOCK_DB_HOST",
    "port": "STOCK_DB_PORT",
    "database": "STOCK_DB_NAME",
    "user": "STOCK_DB_USER",
    "password": "STOCK_DB_PASSWORD",
}

# Texto que en realidad es un número: "3440", "876.5", "12,5", "-3"
_NUMERO_TEXTO = re.compile(r"^-?\d+(?:[.,]\d+)?$")
# Códigos con ceros a la izquierda ("001", "0042") se conservan como texto
_CERO_INICIAL = re.compile(r"^-?0\d")


def _vacia(valor):
    return valor is None or str(valor).strip() == ""


def limpiar(valor):
    """Normaliza una celda: números como números, texto sin espacios, vacío como ''.

    Acepta int, float y Decimal (pymysql devuelve Decimal en columnas DECIMAL),
    fechas (se emiten como YYYY-MM-DD) y textos que son claramente números.
    """
    if valor is None:
        return ""
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, (int, float, Decimal)):
        f = float(valor)
        if math.isnan(f) or math.isinf(f):
            return ""
        return int(f) if f.is_integer() else f
    if isinstance(valor, (datetime, date)):
        return valor.strftime("%Y-%m-%d")
    texto = str(valor).strip()
    if _NUMERO_TEXTO.match(texto) and not _CERO_INICIAL.match(texto):
        return limpiar(float(texto.replace(",", ".")))
    return texto


def leer_excel(ruta):
    """Lee el reporte de stock con el formato de 'Stock report.xlsx'.

    Estructura esperada (la posición exacta de las filas no importa):
      - una fila con el título
      - una fila 'Date de mise à jour' | fecha | subtítulo
      - el encabezado de la tabla: primera celda 'TYPE'
      - los datos: una fila por referencia (las filas en blanco se ignoran)
      - las notas: filas con una sola celda de texto, debajo de los datos
    """
    try:
        import openpyxl
    except ImportError:
        raise SystemExit("Falta la librería openpyxl. Instálala con: pip install -r requirements.txt")

    wb = openpyxl.load_workbook(ruta, data_only=True)
    ws = wb.worksheets[0]
    filas = [list(r) for r in ws.iter_rows(values_only=True)]

    # 1. Encabezado: primera fila cuya celda A es 'TYPE'
    idx_enc = next(
        (i for i, f in enumerate(filas)
         if f and isinstance(f[0], str) and f[0].strip().upper() == "TYPE"),
        None,
    )
    if idx_enc is None:
        raise SystemExit("No se encontró la fila de encabezado (celda 'TYPE' en la columna A).")
    enc = filas[idx_enc]
    idx_cols = [j for j, c in enumerate(enc) if not _vacia(c)]   # columnas con nombre
    columnas = [str(enc[j]).strip() for j in idx_cols]

    # 2. Datos: hasta la primera fila con una sola celda (inicio de las notas)
    registros = []
    i = idx_enc + 1
    while i < len(filas):
        fila = [filas[i][j] if j < len(filas[i]) else None for j in idx_cols]
        llenas = [v for v in fila if not _vacia(v)]
        if not llenas:
            i += 1              # fila en blanco entre grupos: se ignora
            continue
        if len(llenas) == 1:
            break               # una sola celda de texto: empiezan las notas
        registros.append([limpiar(v) for v in fila])
        i += 1

    # 3. Notas: todo el texto que quede debajo, esté en la columna que esté
    notas = []
    for fila in filas[i:]:
        celdas = [str(v).strip() for v in fila if not _vacia(v)]
        if celdas:
            notas.append(" ".join(celdas))

    # 4. Fecha y subtítulo: fila 'Date de mise à jour' por encima del encabezado
    idx_fecha = next(
        (k for k in range(idx_enc)
         if filas[k] and isinstance(filas[k][0], str)
         and filas[k][0].strip().lower().startswith("date de mise")),
        None,
    )
    fecha, subtitulo = "", ""
    if idx_fecha is not None:
        resto = [v for v in filas[idx_fecha][1:] if not _vacia(v)]
        if resto:
            fecha = limpiar(resto[0])
            subtitulo = " ".join(str(v).strip() for v in resto[1:])
    if not fecha:
        print("AVISO: no se encontró la fila 'Date de mise à jour'; la página no mostrará fecha.")

    # 5. Título: primera fila con texto por encima del encabezado (que no sea la de la fecha)
    titulo = ""
    for k in range(idx_enc):
        if k == idx_fecha:
            continue
        celdas = [str(v).strip() for v in filas[k] if not _vacia(v)]
        if celdas:
            titulo = " ".join(celdas)
            break

    return {
        "titulo": titulo or "Stock",
        "fecha_actualizacion": str(fecha),
        "subtitulo": subtitulo,
        "columnas": columnas,
        "filas": registros,
        "notas": notas,
        "fuente": "Excel",
    }


# ---------------------------------------------------------------------------
# Conexión al ERP (MariaDB/MySQL, solo lectura)
# ---------------------------------------------------------------------------

def conexion_erp():
    """Datos de conexión: variables de entorno STOCK_DB_* o, si no existen,
    el archivo cifrado que crea --configurar."""
    if os.environ.get(VARIABLES_DB["host"]):
        datos = {clave: os.environ.get(var, "") for clave, var in VARIABLES_DB.items()}
    elif CONEXION.exists():
        datos = usuarios.leer_protegido(CONEXION)
    else:
        raise SystemExit("No hay conexión al ERP configurada. Ejecuta: python generar_datos.py --configurar")
    datos["port"] = int(datos.get("port") or 3306)
    return datos


class _RechazarClaveEnClaro:
    """Si un equipo de la red se hace pasar por el servidor y pide la contraseña
    sin cifrar (mysql_clear_password o dialog), pymysql la enviaría tal cual.
    Este manejador corta la conexión en ese caso."""

    def __init__(self, conexion):
        pass

    def authenticate(self, paquete):
        import pymysql
        raise pymysql.err.OperationalError(
            2059, "El servidor pidió la contraseña sin cifrar; se rechaza la conexión.")


def _conectar(datos):
    try:
        import pymysql
    except ImportError:
        raise SystemExit("Falta la librería pymysql. Instálala con: pip install -r requirements.txt")
    # La base es latin1, pero se pide utf8mb4: el servidor convierte cada texto y
    # nunca falla al decodificar (con charset latin1, pymysql usa cp1252 y se cae
    # con algunos bytes que hay en la base).
    return pymysql.connect(
        host=datos["host"], port=int(datos["port"]), user=datos["user"],
        password=datos["password"], database=datos["database"],
        charset="utf8mb4", connect_timeout=10, read_timeout=60, write_timeout=60,
        auth_plugin_map={"mysql_clear_password": _RechazarClaveEnClaro,
                         "dialog": _RechazarClaveEnClaro},
    )


def conectar():
    datos = conexion_erp()
    try:
        return _conectar(datos)
    except SystemExit:
        raise
    except Exception as e:
        raise SystemExit(f"No se pudo conectar al ERP ({datos['host']}:{datos['port']}): {e}")


def configurar(host=None, puerto=None, base=None, usuario=None):
    """Pide los datos de conexión, los prueba y los guarda cifrados fuera del repositorio.

    Lo que se pase por la línea de comandos (--host, --puerto, --base, --usuario)
    no se vuelve a preguntar: así solo hay que escribir la contraseña, que nunca
    se acepta como argumento para que no quede en el historial de la terminal.
    """
    import getpass

    if not usuarios.hay_consola():
        raise SystemExit("--configurar necesita una consola interactiva: ábrelo en PowerShell o CMD.")
    previo = {}
    if CONEXION.exists():
        try:
            previo = usuarios.leer_protegido(CONEXION)
        except SystemExit:
            previo = {}
    print("Conexión al ERP (se guarda cifrada con tu usuario de Windows en")
    print(f"  {CONEXION})")
    print("Deja un campo vacío para conservar el valor entre corchetes.\n")

    def pedir(etiqueta, clave, dado=None, defecto=""):
        actual = str(previo.get(clave) or defecto)
        if dado:
            print(f"  {etiqueta}: {dado}")
            return str(dado).strip()
        valor = input(f"  {etiqueta}" + (f" [{actual}]" if actual else "") + ": ").strip()
        return valor or actual

    try:
        datos = {
            "host": pedir("Servidor (IP o nombre)", "host", host),
            "port": pedir("Puerto", "port", puerto, "3306"),
            "database": pedir("Base de datos", "database", base),
            "user": pedir("Usuario (de solo lectura)", "user", usuario),
        }
        aviso = " (vacío = conservar la actual)" if previo.get("password") else ""
        datos["password"] = getpass.getpass(f"  Contraseña{aviso}, no se muestra al escribir: ") \
            or previo.get("password", "")
    except EOFError:
        raise SystemExit("\nNo se pudo leer la respuesta: ejecútalo en una consola. No se guardó nada.")
    faltan = [k for k in ("host", "port", "database", "user", "password") if not str(datos[k]).strip()]
    if faltan:
        raise SystemExit(f"Faltan datos: {', '.join(faltan)}. No se guardó nada.")
    try:
        datos["port"] = int(datos["port"])
    except ValueError:
        raise SystemExit("El puerto debe ser un número. No se guardó nada.")

    print("\nProbando la conexión…")
    try:
        con = _conectar(datos)
        with con.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM stock")
            n = cur.fetchone()[0]
        con.close()
        print(f"  Conexión correcta: la tabla stock tiene {n} registros.")
    except SystemExit:
        raise
    except Exception as e:
        print(f"  No se pudo conectar: {e}")
        if input("  ¿Guardar de todos modos? [s/N]: ").strip().lower() not in ("s", "si", "sí"):
            raise SystemExit("No se guardó nada.")

    usuarios.guardar_protegido(CONEXION, datos)
    print(f"\nGuardado en {CONEXION}")


def buscar(texto):
    """Lista materiales del ERP cuyo código, descripción, tela o color contiene
    todas las palabras (las telas se describen por tela + color en el ERP)."""
    palabras = texto.split()
    if not palabras:
        raise SystemExit("Indica qué buscar, por ejemplo: --buscar marts 23")
    campos = ("m.idMaterial", "m.Descripcion", "t.Tela", "c.Color")
    condicion = " AND ".join(
        ["(" + " OR ".join(f"{campo} LIKE %s" for campo in campos) + ")"] * len(palabras))
    parametros = [f"%{w}%" for w in palabras for _ in campos]
    con = conectar()
    try:
        with con.cursor() as cur:
            cur.execute(
                f"""SELECT m.idMaterial, m.Descripcion, m.fk_idTipo, t.Tela, c.Color, cat.uMedida,
                           COALESCE(s.total, 0)
                    FROM materiales m
                    LEFT JOIN (SELECT fk_idMaterial, SUM(Cantidad) total
                               FROM stock GROUP BY fk_idMaterial) s ON s.fk_idMaterial = m.idMaterial
                    LEFT JOIN colorestelas ct ON ct.id_ColoresTelas = m.fk_idColorTela
                    LEFT JOIN telas t ON t.idTela = ct.fk_idTela
                    LEFT JOIN colores c ON c.idColor = ct.fk_idColor
                    LEFT JOIN categorias cat ON cat.idTipo = m.fk_idTipo
                    WHERE {condicion}
                    ORDER BY m.idMaterial
                    LIMIT 201""",
                parametros,
            )
            filas = cur.fetchall()
    finally:
        con.close()
    if not filas:
        print("Sin resultados.")
        return
    tabla = [("CÓDIGO", "DESCRIPCIÓN", "TIPO", "TELA", "COLOR", "UNIDAD", "STOCK")]
    tabla += [tuple(" ".join(str(v or "").split()) for v in f[:6]) + (_num_texto(f[6]),)
              for f in filas[:200]]
    _imprimir_tabla(tabla)
    if len(filas) > 200:
        print("… hay más de 200 resultados; afina la búsqueda.")


# ---------------------------------------------------------------------------
# Stock desde el ERP según config_stock.json (local, no se publica)
# ---------------------------------------------------------------------------

def _codigos(ref):
    """'codigo' puede ser un texto o una lista (la referencia suma varios materiales)."""
    valor = ref.get("codigo", "")
    lista = valor if isinstance(valor, list) else [valor]
    return [str(c).strip() for c in lista if str(c or "").strip()]


def _norm(codigo):
    return str(codigo).strip().upper()


def cargar_config(exigir_codigos=True):
    if not CONFIG.exists():
        raise SystemExit(f"No existe {CONFIG}: es la lista de referencias que se publican "
                         "(ver el README, «Referencias»).")
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    except UnicodeDecodeError:
        raise SystemExit(f"{CONFIG} debe guardarse con codificación UTF-8.")
    except json.JSONDecodeError as e:
        raise SystemExit(f"{CONFIG} tiene un error de formato en la línea {e.lineno}: {e.msg}")

    def error(mensaje):
        raise SystemExit(f"{CONFIG}: {mensaje}")

    if not isinstance(cfg, dict):
        error("debe ser un objeto JSON { … }.")
    for clave in ("titulo", "subtitulo"):
        if not isinstance(cfg.get(clave, ""), str):
            error(f"'{clave}' debe ser un texto.")
    columnas = cfg.get("columnas")
    if not (isinstance(columnas, list) and len(columnas) == 5
            and all(isinstance(c, str) and c.strip() for c in columnas)):
        error("'columnas' debe tener 5 nombres (tipo, descripción, afectación, unidad, stock).")
    notas = cfg.get("notas", [])
    if not (isinstance(notas, list) and all(isinstance(n, str) for n in notas)):
        error("'notas' debe ser una lista de textos.")
    decimales = cfg.get("decimales", 0)
    if isinstance(decimales, bool) or not isinstance(decimales, int) or not 0 <= decimales <= 6:
        error("'decimales' debe ser un número entero de 0 a 6.")
    refs = cfg.get("referencias")
    if not isinstance(refs, list) or not refs:
        error("'referencias' está vacío.")
    for k, ref in enumerate(refs, 1):
        if not (isinstance(ref, dict) and isinstance(ref.get("descripcion"), str)
                and ref["descripcion"].strip()):
            error(f"a la referencia {k} le falta 'descripcion'.")
        nombre = ref["descripcion"]
        for clave in ("tipo", "afectacion", "unidad"):
            if not isinstance(ref.get(clave, ""), str):
                error(f"«{nombre}»: '{clave}' debe ser un texto.")
        codigo = ref.get("codigo", "")
        if not (isinstance(codigo, str)
                or (isinstance(codigo, list) and all(isinstance(c, str) for c in codigo))):
            error(f"«{nombre}»: 'codigo' debe ser un texto o una lista de textos.")
        factor = ref.get("factor", 1)
        if (isinstance(factor, bool) or not isinstance(factor, (int, float))
                or not math.isfinite(factor) or factor <= 0):
            error(f"«{nombre}»: 'factor' debe ser un número mayor que 0.")
        normalizados = [_norm(c) for c in _codigos(ref)]
        if len(set(normalizados)) != len(normalizados):
            error(f"«{nombre}»: tiene el mismo código repetido.")
    sin_codigo = [r["descripcion"] for r in refs if not _codigos(r)]
    if exigir_codigos and sin_codigo:
        error(f"falta el código ERP de {len(sin_codigo)} referencia(s): " + ", ".join(sin_codigo[:10])
              + ". Búscalo con: python generar_datos.py --buscar TEXTO")
    return cfg


def _comparable(valor):
    return " ".join(str(valor or "").split()).lower()


def filtrar_segun_config(datos):
    """En el respaldo desde Excel, publica solo las referencias configuradas: así
    el Excel no devuelve a la página referencias que se quitaron de la lista."""
    if not CONFIG.exists():
        print(f"AVISO: no existe {CONFIG}; se publica el Excel tal cual.")
        return datos
    cfg = cargar_config(exigir_codigos=False)
    columnas = datos["columnas"]
    nombre_col = cfg["columnas"][1]
    col = columnas.index(nombre_col) if nombre_col in columnas else min(1, len(columnas) - 1)
    permitidas = {_comparable(r["descripcion"]) for r in cfg["referencias"]}
    filas = []
    for fila in datos["filas"]:
        if _comparable(fila[col]) in permitidas:
            filas.append(fila)
        else:
            print(f"AVISO: «{fila[col]}» no está en config_stock.json; no se publica.")
    faltan = permitidas - {_comparable(f[col]) for f in filas}
    if faltan:
        print(f"AVISO: el Excel no trae {len(faltan)} referencia(s) de config_stock.json; "
              "esas no aparecerán en la página.")
    if not filas:
        raise SystemExit("Ninguna fila del Excel coincide con config_stock.json; no se publica. "
                         "¿Es el Excel correcto?")
    datos["filas"] = filas
    return datos


def leer_base_de_datos(forzar=False):
    """Lee del ERP el stock de las referencias de config_stock.json.

    Devuelve un diccionario con la misma forma que leer_excel(). Solo publica
    las referencias configuradas y solo la cantidad: nunca costos.
    """
    cfg = cargar_config()
    refs = cfg["referencias"]
    codigos = sorted({_norm(c) for r in refs for c in _codigos(r)})
    marcas = ",".join(["%s"] * len(codigos))

    con = conectar()
    try:
        with con.cursor() as cur:
            cur.execute(f"SELECT idMaterial FROM materiales WHERE idMaterial IN ({marcas})", codigos)
            existentes = {_norm(r[0]) for r in cur.fetchall()}
            cur.execute(
                f"""SELECT fk_idMaterial, SUM(Cantidad) FROM stock
                    WHERE fk_idMaterial IN ({marcas}) GROUP BY fk_idMaterial""",
                codigos,
            )
            cantidades = {}
            for codigo, cantidad in cur.fetchall():
                clave = _norm(codigo)
                cantidades[clave] = cantidades.get(clave, 0.0) + float(cantidad or 0)
    finally:
        con.close()

    faltan = [c for c in codigos if c not in existentes]
    if faltan:
        raise SystemExit(f"Estos códigos no existen en el ERP (revisa {CONFIG}): {', '.join(faltan)}")
    if not forzar and not any(v > 0 for v in cantidades.values()):
        raise SystemExit("El ERP no devolvió stock para ninguna de las referencias configuradas; "
                         "no se publica (¿tabla stock vacía o en recálculo?). Si de verdad todo "
                         "está en 0, usa --forzar.")

    decimales = cfg.get("decimales", 0)
    filas = []
    for ref in refs:
        total = sum(cantidades.get(_norm(c), 0.0) for c in _codigos(ref)) * ref.get("factor", 1)
        if total < 0:
            print(f"AVISO: el ERP tiene stock negativo para «{ref['descripcion']}» ({total}); se muestra 0.")
            total = 0.0
        filas.append([
            ref.get("tipo", "").strip(),
            ref["descripcion"].strip(),
            ref.get("afectacion", "").strip(),
            ref.get("unidad", "").strip(),
            limpiar(round(total, decimales) + 0.0),   # + 0.0 evita "-0"
        ])

    return {
        "titulo": cfg.get("titulo") or "Stock",
        "fecha_actualizacion": datetime.now().astimezone().isoformat(timespec="seconds"),
        "subtitulo": cfg.get("subtitulo", ""),
        "columnas": cfg["columnas"],
        "filas": filas,
        "notas": cfg.get("notas", []),
        "fuente": "ERP",
    }


# ---------------------------------------------------------------------------
# Salida (cifrada)
# ---------------------------------------------------------------------------

_INICIO_PUBLICADO = "const DATOS_CIFRADOS = "


def leer_publicado(salida):
    """El objeto publicado en datos.js, o None si no existe o es de otro formato."""
    try:
        texto = salida.read_text(encoding="utf-8")
        inicio = texto.index(_INICIO_PUBLICADO) + len(_INICIO_PUBLICADO)
        return json.loads(texto[inicio:texto.rindex(";")])
    except (OSError, ValueError):
        return None


def _escribir_atomico(ruta, contenido):
    """Escribe en un temporal y lo reemplaza; si otro programa tiene abierto el
    archivo (antivirus, indexador…), reintenta y en último caso escribe directo."""
    temporal = ruta.with_name(ruta.name + ".tmp")
    temporal.write_text(contenido, encoding="utf-8", newline="\n")
    try:
        for _ in range(5):
            try:
                os.replace(temporal, ruta)
                return
            except PermissionError:
                time.sleep(0.5)
        ruta.write_text(contenido, encoding="utf-8", newline="\n")
    finally:
        temporal.unlink(missing_ok=True)


def escribir_datos_js(datos, salida=SALIDA, forzar=False):
    """Cifra y escribe datos.js si cambió el stock o las cuentas, o si la fecha
    publicada es de otro día. Devuelve True si escribió el archivo."""
    n = len(datos["columnas"])
    malas = [k for k, f in enumerate(datos["filas"]) if len(f) != n]
    if malas:
        raise SystemExit(f"Filas con un número de valores distinto al de columnas ({n}): {malas[:10]}")

    reg = usuarios.cargar()
    if not reg["usuarios"]:
        raise SystemExit("No hay cuentas de acceso, así que nadie podría ver la tabla. "
                         "Crea una con: python usuarios.py agregar NOMBRE")
    huella = usuarios.huella(datos, reg)
    previo = leer_publicado(salida)
    if (not forzar and previo is not None and previo.get("huella") == huella
            and str(previo.get("fecha", ""))[:10] == str(datos["fecha_actualizacion"])[:10]):
        print(f"SIN CAMBIOS: {salida.name} ya tiene estos datos ({len(datos['filas'])} filas).")
        return False

    publicado = usuarios.cifrar(datos, reg)
    contenido = (
        "// Archivo generado automáticamente por generar_datos.py. No editar a mano.\n"
        "// Datos cifrados: la página los descifra al iniciar sesión.\n"
        f"{_INICIO_PUBLICADO}{json.dumps(publicado, ensure_ascii=False, indent=2)};\n"
    )
    _escribir_atomico(salida, contenido)
    print(f"OK: {salida.name} generado con {len(datos['filas'])} filas, "
          f"{len(publicado['cuentas'])} cuenta(s) de acceso.")
    return True


def _num_texto(valor):
    v = limpiar(valor)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return f"{v:,}".replace(",", " ")
    return str(v)


def _imprimir_tabla(filas):
    anchos = [max(len(str(f[i])) for f in filas) for i in range(len(filas[0]))]
    for k, fila in enumerate(filas):
        print("  ".join(str(v).ljust(anchos[i]) for i, v in enumerate(fila)).rstrip())
        if k == 0:
            print("  ".join("-" * a for a in anchos))


def mostrar(datos):
    print(f"{datos['titulo']} — {datos['fecha_actualizacion']}")
    _imprimir_tabla([datos["columnas"]] + [
        [_num_texto(v) if isinstance(v, (int, float)) else v for v in f] for f in datos["filas"]
    ])


def main(argv=None):
    usuarios.consola_segura()
    p = argparse.ArgumentParser(
        prog="generar_datos.py",
        description="Genera datos.js (la tabla de la página, cifrada) desde el ERP o desde un Excel.",
    )
    p.add_argument("excel", nargs="?", help="ruta del Excel de stock")
    p.add_argument("--db", action="store_true", help="leer el stock del ERP según config_stock.json")
    p.add_argument("--probar", action="store_true", help="solo mostrar la tabla, sin escribir datos.js")
    p.add_argument("--forzar", action="store_true",
                   help="escribir datos.js aunque no haya cambios (y aunque todo el stock sea 0)")
    p.add_argument("--salida", metavar="RUTA", type=Path, default=SALIDA,
                   help="dónde escribir datos.js (por defecto, junto a este script)")
    p.add_argument("--buscar", nargs="+", metavar="TEXTO",
                   help="buscar materiales en el ERP por código, descripción, tela o color")
    p.add_argument("--configurar", action="store_true", help="guardar la conexión al ERP (cifrada)")
    for opcion, ayuda in (("--host", "servidor del ERP"), ("--puerto", "puerto (por defecto 3306)"),
                          ("--base", "base de datos"), ("--usuario", "usuario de solo lectura")):
        p.add_argument(opcion, metavar="VALOR", help=f"con --configurar: {ayuda}, para no teclearlo")
    args = p.parse_args(argv)

    modos = [bool(args.excel), args.db, args.buscar is not None, args.configurar]
    if sum(modos) == 0:
        p.print_help()
        raise SystemExit(1)
    if sum(modos) > 1:
        p.error("indica solo una cosa: un Excel, --db, --buscar o --configurar.")

    if args.configurar:
        configurar(args.host, args.puerto, args.base, args.usuario)
        return
    if args.buscar is not None:
        buscar(" ".join(args.buscar))
        return
    if args.db:
        datos = leer_base_de_datos(forzar=args.forzar)
    else:
        ruta = Path(args.excel)
        if not ruta.exists():
            raise SystemExit(f"No existe el archivo: {ruta}")
        datos = filtrar_segun_config(leer_excel(ruta))

    if args.probar:
        mostrar(datos)
    else:
        escribir_datos_js(datos, salida=args.salida, forzar=args.forzar)


if __name__ == "__main__":
    main()
