# Disponibilités de stock DTI — página web

Página que muestra el stock disponible de DTI en una tabla con búsqueda, filtros
y ordenamiento por columna. Se publica en GitHub Pages y **solo se ve iniciando
sesión** con una cuenta. Los datos salen del ERP (A.R.E.S) y se actualizan solos
cada 15 minutos.

## Cómo funciona

- **La página es pública, los datos no.** `datos.js` se publica cifrado
  (AES-256-GCM). Al iniciar sesión, el navegador descifra la tabla con la
  contraseña de la cuenta. Sin una cuenta válida, el archivo es ilegible.
- **Una PC de la oficina hace de puente.** Como GitHub Pages solo sirve archivos,
  una tarea programada de Windows consulta el ERP cada 15 minutos (solo lectura,
  nunca costos), cifra la tabla y hace `git push` si el stock cambió. Si nada
  cambia, publica una vez al día para que la fecha de la página confirme que la
  sincronización sigue activa.
- **Lo privado no está en el repositorio.** La lista de referencias, la conexión
  al ERP y las cuentas viven solo en esa PC:

| Dónde | Qué |
|---|---|
| Repositorio (público) | `index.html`, `datos.js` (cifrado), los scripts y este README |
| `%LOCALAPPDATA%\StockDTI\config_stock.json` | Qué referencias se publican, con su código del ERP y el texto de la página |
| `%LOCALAPPDATA%\StockDTI\conexion.dat` | Conexión al ERP, cifrada con tu usuario de Windows |
| `%LOCALAPPDATA%\StockDTI\usuarios.dat` | Cuentas de acceso, cifradas con tu usuario de Windows |
| `%LOCALAPPDATA%\StockDTI\programa\` | Copia fija de los scripts que ejecuta la tarea |
| `%LOCALAPPDATA%\StockDTI\repo\` | Copia del repositorio donde la tarea escribe y publica `datos.js` |
| `%LOCALAPPDATA%\StockDTI\sincronizar.log` | Registro de la sincronización |

La tarea **nunca ejecuta código descargado de GitHub**: usa la copia fija que
instala `--instalar`. Así, aunque alguien consiguiera subir algo al repositorio,
no podría ejecutar nada en la PC de la oficina.

Todo lo anterior es por usuario de Windows: la tarea, las cuentas y la conexión
tienen que instalarse con el mismo usuario. La tarea solo corre mientras su sesión
de Windows está iniciada y la PC encendida; si se apaga, la página sigue
mostrando el último stock publicado, con su fecha.

## Archivos del repositorio

| Archivo | Para qué sirve |
|---|---|
| `index.html` | La página: inicio de sesión y tabla. |
| `datos.js` | Los datos, cifrados. **Se genera automáticamente**, no se edita a mano. |
| `generar_datos.py` | Lee el ERP (o un Excel) y escribe `datos.js` cifrado. |
| `usuarios.py` | Crea y quita las cuentas de acceso. |
| `sincronizar.py` | Instala la tarea programada y publica en GitHub. |
| `config_stock.ejemplo.json` | Plantilla de la lista de referencias. |
| `requirements.txt` | Librerías de Python que necesitan los scripts. |

## Antes de instalar: el repositorio de GitHub

1. Un repositorio **público** en GitHub con la rama `main` (con una cuenta
   gratuita, GitHub Pages solo funciona en repositorios públicos).
2. La carpeta de trabajo de la PC puente tiene que ser un **clon** de ese
   repositorio (`git clone <url>`), no una descarga en ZIP.
3. Haz al menos un `git push` a mano en esa PC, para que Windows guarde la
   credencial de GitHub. La tarea nunca puede pedirla: si falta, el push falla.
4. En el repositorio: **Settings → Pages → Build and deployment → Source:
   Deploy from a branch**, rama `main`, carpeta `/ (root)`. La página queda en
   `https://USUARIO.github.io/NOMBRE-DEL-REPO/`.

> **Si el repositorio ya publicó datos sin cifrar** (versiones anteriores de
> `datos.js`), esas versiones siguen siendo públicas en el historial. Reescribir
> el historial **no basta**: en GitHub los commits quedan accesibles por su
> identificador y cualquier copia del repositorio los conserva. Lo único seguro
> es crear un repositorio nuevo y vacío, publicar ahí el estado ya cifrado,
> apuntar Pages al nuevo y borrar (o poner privado) el anterior.

## Instalación (una sola vez, en la PC que hará de puente)

1. Python 3 y las librerías:

   ```bash
   pip install -r requirements.txt
   ```

2. Conexión al ERP. Pide servidor, base de datos, usuario de solo lectura y
   contraseña; los prueba y los guarda cifrados:

   ```bash
   python generar_datos.py --configurar
   ```

3. Lista de referencias. Copia la plantilla y edítala (ver «Referencias»):

   ```bash
   copy config_stock.ejemplo.json "%LOCALAPPDATA%\StockDTI\config_stock.json"
   python generar_datos.py --buscar marts 23
   python generar_datos.py --db --probar
   ```

   `--probar` muestra la tabla sin publicar nada: repite hasta que se vea bien.

4. Al menos una cuenta de acceso. Muestra una contraseña **una sola vez**:

   ```bash
   python usuarios.py agregar maria.lopez
   ```

5. Primera publicación, **todo en un solo commit**. Es importante: si `index.html`
   y `datos.js` se publican por separado, entre uno y otro la página queda con un
   error para todos.

   ```bash
   python generar_datos.py --db
   git pull
   git add -A
   git commit -m "Página con acceso y datos del ERP"
   git push
   ```

6. Instalar la tarea programada. Registra «StockDTI\Sincronizar stock», copia los
   scripts a `%LOCALAPPDATA%\StockDTI\programa` y sincroniza por primera vez:

   ```bash
   python sincronizar.py --instalar
   ```

   Para otro intervalo: `python sincronizar.py --instalar --cada 30`.

## Cuentas de acceso

| Quiero… | Comando |
|---|---|
| Dar acceso a alguien | `python usuarios.py agregar NOMBRE` |
| Darle una contraseña nueva | `python usuarios.py contrasena NOMBRE` |
| Quitarle el acceso | `python usuarios.py quitar NOMBRE` |
| Ver las cuentas | `python usuarios.py lista` |

- `agregar` y `contrasena` generan una contraseña de 12 caracteres y la muestran
  **una sola vez**: anótala y pásasela a la persona. Con `--escribir` puedes
  escribirla tú (mínimo 14 caracteres y nada fácil de adivinar: cualquiera puede
  descargar `datos.js` y probar contraseñas sin límite en su propia máquina).
- El nombre de la cuenta es lo que la persona escribe en «Identifiant»: letras
  sin acentos, números, punto, guion o @ (por ejemplo `maria.lopez`).
- Crear, renovar o quitar una cuenta publica el cambio automáticamente; la página
  lo tiene uno o dos minutos después (lo que tarda GitHub Pages). Hasta entonces
  sigue valiendo la contraseña anterior.
- Quien marca «Rester connecté» no vuelve a escribir la contraseña en ese
  navegador durante 30 días, o hasta pulsar «Se déconnecter», o hasta que se le
  cambie la contraseña o se le quite la cuenta.
- Al quitar una cuenta, esa persona deja de poder abrir los datos que se
  publiquen **después**. Si era la única cuenta, no se puede publicar nada nuevo
  y la página seguirá mostrando los últimos datos, que esa cuenta todavía puede
  abrir: crea otra cuenta y publica para cerrarle el paso.

## Uso diario

| Quiero… | Comando |
|---|---|
| Ver si la sincronización funciona | `python sincronizar.py --estado` |
| Publicar ya, sin esperar | `python sincronizar.py` |
| Buscar el código ERP de un material | `python generar_datos.py --buscar pyro glass` |
| Quitar la tarea programada | `python sincronizar.py --desinstalar` |

## Referencias

Se editan en `%LOCALAPPDATA%\StockDTI\config_stock.json` (con el Bloc de notas
basta). La plantilla `config_stock.ejemplo.json` trae el archivo completo:

| Clave | Qué es |
|---|---|
| `titulo`, `subtitulo` | Encabezado de la página. |
| `columnas` | Los 5 nombres de columna, en este orden: tipo, descripción, afectación, unidad, stock. |
| `decimales` | Decimales de las cantidades (0 a 6). |
| `notas` | Lista de textos que salen al pie de la tabla. |
| `referencias` | Una línea por referencia (abajo). |

```json
{"codigo": "TE0123", "tipo": "Tissu", "descripcion": "Marts 23 Black", "afectacion": "Stock global DTI", "unidad": "ml"}
```

- `codigo`: el `idMaterial` del ERP. `--buscar` lo encuentra por código,
  descripción, tela o color (las telas del ERP suelen estar en español: «negro»,
  «azul»…). Si una referencia suma varios materiales, usa una lista:
  `["TE0123", "TE0124"]`.
- `tipo`, `descripcion`, `afectacion`, `unidad`: el texto que se ve en la página.
  Si `afectacion` empieza por «★ Exclusif», la página la muestra con distintivo.
  El ERP no sabe qué material es exclusivo de un cliente: esa marca solo existe
  en este archivo.
- `factor` (opcional): número mayor que 0 que multiplica la cantidad del ERP, por
  ejemplo para convertir unidades.

Después de editarlo, comprueba con `python generar_datos.py --db --probar`. La
tarea usa el archivo nuevo en su siguiente ejecución.

Este archivo **no está en el repositorio** y `%LOCALAPPDATA%` no se respalda solo:
guarda una copia en un lugar seguro (no en el repositorio) si no quieres volver a
buscar los códigos.

Por seguridad no se publica nada si el ERP no devuelve stock para ninguna
referencia (por ejemplo, durante un mantenimiento): la página sigue mostrando el
último stock bueno. Si de verdad todo está en cero y quieres publicarlo:
`python sincronizar.py --forzar`.

## Cambiar el código

Los cambios de `index.html` se publican como siempre (`git pull`, commit, `git push`).
Haz `git pull` antes de empezar: la tarea sube `datos.js` varias veces al día, así
que tu carpeta suele ir por detrás de GitHub.

Si cambias `generar_datos.py`, `usuarios.py` o `sincronizar.py`, vuelve a ejecutar
`python sincronizar.py --instalar` para que la tarea use la versión nueva; mientras
no lo hagas, `--estado` y el registro avisan de que la copia instalada está vieja.

## Si algo falla

- **La fecha de la página no avanza**: `python sincronizar.py --estado` muestra el
  último error.
- **Error de conexión al ERP**: vuelve a ejecutar `python generar_datos.py --configurar`
  (por ejemplo, si cambió la contraseña).
- **Error al publicar en GitHub**: o la sesión de GitHub caducó, o esa PC nunca
  guardó la credencial. Abre una terminal en `%LOCALAPPDATA%\StockDTI\repo`,
  ejecuta `git push` e inicia sesión en la ventana que aparece.
- **Alguien no puede entrar**: comprueba el nombre con `python usuarios.py lista`
  y, si hace falta, dale una contraseña nueva con `python usuarios.py contrasena NOMBRE`.

## Respaldo: publicar desde el Excel

Si el ERP no está disponible, se puede publicar desde `Stock report.xlsx` (con
las cuentas actuales):

```bash
python sincronizar.py --excel "C:\ruta\Stock report.xlsx"
```

Solo se publican las referencias que estén en `config_stock.json`; de las demás
avisa y no las sube. Cuando el ERP vuelva, la tarea publicará otra vez sus datos
en cuanto el stock cambie.

El script busca en la primera hoja del Excel: una fila con el título, una fila
que empieza por «Date de mise à jour» (fecha y subtítulo), el encabezado de la
tabla (primera celda `TYPE`), los datos (las filas en blanco se ignoran) y, debajo,
las notas (filas con una sola celda de texto).

## Qué protege el inicio de sesión y qué no

- **Protegido**: las cantidades, las referencias, sus textos y las notas. Solo se
  ven con una cuenta; los nombres de las cuentas tampoco aparecen en el archivo
  publicado.
- **Visible para cualquiera**: que la página existe, el código de la página y de
  los scripts, cuántas cuentas hay y a qué horas se actualiza (el historial de
  git es público).
- **Contraseñas**: no se guardan en GitHub ni en la página; en la PC puente solo
  se guarda la clave que se deriva de ellas, cifrada con Windows. Las contraseñas
  generadas por `usuarios.py` resisten ataques de fuerza bruta; una escrita a mano
  y fácil de adivinar deja al descubierto toda la tabla, porque cualquiera puede
  probar contraseñas contra el archivo descargado.
- **No es retroactivo**: cada publicación queda para siempre en el historial
  público. Quien haya tenido una contraseña puede seguir abriendo, con esa
  contraseña, todo lo que se publicó mientras la tenía, aunque después se le
  quite la cuenta. Quitar una cuenta o cambiar una contraseña solo protege lo que
  se publique a partir de ese momento.
- **En el navegador**: «Rester connecté» guarda en ese navegador una clave que
  sirve hasta que se cambie la contraseña (30 días como máximo). No conviene
  marcarlo en una computadora compartida.

La página lleva la etiqueta `noindex`, de modo que los buscadores no la listan.
