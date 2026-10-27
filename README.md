# Teseo

Teseo es un panel web para crear, programar y supervisar copias de seguridad entre servidores con [rsync](https://rsync.samba.org/). El controlador organiza las tareas y consulta su estado mediante SSH (Secure Shell); los archivos se transfieren directamente del origen al destino, sin atravesar el servidor del panel.

Está pensado para equipos técnicos que necesitan conocer qué se está copiando, dónde se guarda y qué protección tiene cada origen. Reúne descubrimiento de datos, copias espejo e incrementales, seguimiento en vivo, capacidad de almacenamiento e historial de ejecuciones en una interfaz en español.

## Funciones principales

- Descubrimiento de orígenes en [Synology](https://www.synology.com/) y [Plesk](https://www.plesk.com/), organizados por servidor y volumen.
- Copias espejo e incrementales con históricos mediante enlaces duros y retención configurable por días.
- Programación de tareas, ejecución manual, cancelación y creación de copias en lote.
- Panel con estado, porcentaje, velocidad de transferencia e historial de ejecuciones.
- Supervisión de conectividad, espacio disponible y protección de cada origen según redundancia y ubicación.
- Análisis periódico del tamaño de los datos, evolución histórica y detección de orígenes desaparecidos.
- Asistente de instalación, gestión de ubicaciones y avisos por correo de fallos, inaccesibilidad y orígenes huérfanos.
- Aprovisionamiento de claves de conexión por tarea y almacenamiento cifrado de credenciales.

## Cómo funciona

El panel y el servicio `teseod` comparten una base de datos. El panel configura las tareas y solicita acciones; el servicio atiende la programación, ejecuta las copias y actualiza su estado.

```text
Panel y servicio ── control por SSH ──> Servidor de origen
       │                                      │
       │                               rsync sobre SSH
       │                                      │
       └── control y retención ──────> Destino de las copias
```

La copia se lanza en el origen y continúa aunque se interrumpa la conexión de control. El servicio sondea su resultado y puede solicitar la cancelación. La web recibe actualizaciones mediante SSE (Server-Sent Events).

## Tecnologías

| Área | Componentes |
| --- | --- |
| Aplicación | [Python](https://www.python.org/), [FastAPI](https://fastapi.tiangolo.com/), [Jinja](https://jinja.palletsprojects.com/) y [Uvicorn](https://uvicorn.dev/) |
| Persistencia | [MySQL](https://www.mysql.com/) o [MariaDB](https://mariadb.org/), [SQLAlchemy](https://www.sqlalchemy.org/), [PyMySQL](https://pymysql.readthedocs.io/) y [Alembic](https://alembic.sqlalchemy.org/) |
| Copias y conexiones | rsync, [OpenSSH](https://www.openssh.com/), [Paramiko](https://www.paramiko.org/) y [croniter](https://github.com/pallets-eco/croniter) |
| Credenciales | [cryptography](https://cryptography.io/) para cifrado Fernet y [argon2-cffi](https://argon2-cffi.readthedocs.io/) para contraseñas |
| Servicios y pruebas | [systemd](https://systemd.io/), [pytest](https://docs.pytest.org/) y [GitHub Actions](https://github.com/features/actions) |

Las versiones de las dependencias directas están fijadas en [`requirements.txt`](requirements.txt). La matriz de integración continua utiliza Python 3.11, 3.12 y 3.13.

## Requisitos

- Un servidor [Linux](https://www.kernel.org/) para el controlador; el despliegue incluido utiliza systemd.
- Python 3.11 o superior, con soporte para entornos virtuales, y [Git](https://git-scm.com/).
- Un servidor MySQL o MariaDB accesible y una cuenta con permisos para crear la base de datos de Teseo y sus tablas.
- Conexión SSH del controlador a los orígenes y destinos, y del origen a su destino. Los equipos de copia necesitan rsync y las utilidades de sistema utilizadas por el conector; el origen necesita soporte de `--info=progress2`.
- Permisos de lectura sobre los orígenes y de escritura sobre la carpeta de destino y los archivos de confianza SSH. Las copias incrementales necesitan enlaces duros dentro del mismo sistema de archivos del destino.
- Un servidor de correo opcional para los avisos mediante SMTP (Simple Mail Transfer Protocol).

El descubrimiento de orígenes incluye los conectores Synology y Plesk sobre Linux. La existencia de SSH en otro equipo no implica que tenga un conector de descubrimiento compatible.

## Instalación desde cero

Estos pasos arrancan una instalación local del controlador. Para un servicio permanente, sigue también la [guía de despliegue](docs/DESPLIEGUE.md).

### 1. Obtener el código y las dependencias

```bash
git clone https://github.com/antoniosastre/Teseo.git
cd Teseo
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Para instalar una versión publicada concreta, selecciona su etiqueta antes de instalar dependencias. Las versiones están en la [página de publicaciones](https://github.com/antoniosastre/Teseo/releases).

### 2. Preparar la base de datos

Con MySQL o MariaDB ya instalado, crea una cuenta dedicada desde una sesión administrativa. Sustituye la contraseña de ejemplo antes de ejecutar:

```sql
CREATE USER 'teseo'@'localhost' IDENTIFIED BY 'CAMBIA_ESTA_CLAVE';
GRANT ALL PRIVILEGES ON teseo.* TO 'teseo'@'localhost';
```

El asistente crea la base `teseo` y su esquema. Ajusta el usuario, el origen permitido y los permisos si la base está en otro servidor. No importes `migrations/schema.sql`: es una referencia histórica; la instalación actual usa los modelos de la aplicación y registra la revisión de Alembic.

### 3. Completar el asistente

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8080
```

Abre [el panel local](http://127.0.0.1:8080). Sin configuración, la aplicación muestra `/install`. Introduce la conexión de base de datos, crea el primer administrador y configura el correo si necesitas avisos.

El asistente escribe `config.ini` en la raíz del proyecto y genera las claves de sesión y cifrado. Puedes elegir otra ruta mediante `TESEO_CONFIG` antes de arrancar la web y el servicio. La cuenta que ejecuta la instalación necesita escribir en esa ubicación. Conserva una copia protegida de este archivo junto con la base de datos: sin la clave de cifrado no se recuperan las credenciales almacenadas.

Reinicia la web después de completar la instalación para cargar las claves persistentes. En otra terminal, desde el mismo directorio y con el mismo entorno virtual y configuración, inicia el servicio de copias:

```bash
. .venv/bin/activate
python -m daemon.teseod
```

### 4. Configurar la primera copia

1. Crea las ubicaciones físicas en **Ajustes**.
2. Añade un destino, su carpeta base y sus credenciales. Usa **Probar** para comprobar la conexión.
3. Añade un host de origen con el conector correspondiente. Revisa los volúmenes descubiertos, su redundancia y su ubicación.
4. Entra en un origen y crea una tarea con destino, tipo de copia, programación y retención. También puedes seleccionar varios orígenes y crear tareas en lote.
5. Pulsa **Ejecutar ya**, sigue el progreso y revisa el resultado y los archivos del destino. Comprueba una restauración con datos de prueba antes de confiar en la política de copias.

## Despliegue y operación

[`docs/DESPLIEGUE.md`](docs/DESPLIEGUE.md) describe el usuario de servicio, la instalación en `/opt/teseo`, la configuración en `/etc/teseo/config.ini`, las unidades de systemd y el acceso mediante túnel o proxy.

- `teseo-web` sirve el panel; `teseod` programa y supervisa las copias. Ambos deben usar el mismo `TESEO_CONFIG`.
- Las unidades incluidas escuchan en la interfaz local. Para acceso remoto, usa un túnel SSH o un proxy [nginx](https://nginx.org/) con TLS (Transport Layer Security).
- Tras habilitar HTTPS (Hypertext Transfer Protocol Secure), establece `https_only = true` en `[security]` y reinicia la web.
- En el proxy, desactiva el almacenamiento intermedio de la ruta `/estado/stream` y permite conexiones persistentes para que las actualizaciones lleguen en vivo.
- Revisa los registros con `journalctl -u teseo-web` y `journalctl -u teseod`.
- Para actualizar una instalación, respalda primero la base y la configuración, elige una etiqueta y utiliza `scripts/update.sh vX.Y.Z`. El script instala dependencias, aplica las migraciones y trata de reiniciar los servicios; si la cuenta no tiene ese permiso, indica el reinicio manual necesario.

## Comportamiento de las copias

La estructura del destino es:

```text
<carpeta_base>/<host>/<volumen>/<origen>/<tipo_tarea>/
```

| Modalidad | Contenido y retención |
| --- | --- |
| Espejo | Réplica en `current/` con `--delete`: elimina en el destino los archivos que ya no están en el origen. |
| Incremental | Directorios fechados `YYYY-MM-DD_HHMMSS`, enlaces duros mediante `--link-dest` y enlace `current` a la última copia. La retención elimina históricos anteriores al número de días configurado y conserva siempre el más reciente. |

Las órdenes generadas conservan los enlaces simbólicos sin seguirlos y utilizan `--one-file-system` para no recorrer otros sistemas de archivos montados dentro de un origen. La compresión está desactivada por defecto; puede añadirse en las opciones avanzadas de una tarea.

En Synology se omiten las carpetas de sistema que empiezan por `@`, la carpeta compartida de destino `Teseo` y los metadatos/transitorios definidos en el conector. La configuración de DSM (DiskStation Manager) debe respaldarse con la herramienta nativa del fabricante.

En Plesk se descubren suscripciones y configuraciones; las opciones permiten incluir correo, respaldos existentes, volcados de bases de datos y rutas adicionales. **Teseo no genera esos volcados**: copia los archivos preparados externamente y no respalda directamente los archivos de una base de datos en ejecución.

Un resultado **parcial** identifica, entre otros casos, el código 23 de rsync. Revisa su detalle: no equivale a una copia íntegra. El panel distingue actividad en cian y progreso completado en verde.

## Indicador de protección y límites

La barra de protección aplica la regla de la mejor copia: redundancia del volumen de origen, existencia de una copia configurada, redundancia del destino y ubicación física distinta. Los valores de RAID (Redundant Array of Independent Disks) y ubicación los introduce el operador. Es un indicador de configuración, no una comprobación de restaurabilidad ni una auditoría automática del almacenamiento.

La interfaz incluye la creación del administrador inicial; no incluye una pantalla para administrar múltiples cuentas. Tampoco ofrece un asistente de restauración. El código de las operaciones reales debe validarse con los servidores, permisos y sistemas de archivos de cada instalación.

## Desarrollo y verificación

```bash
python -m pip install -r requirements-dev.txt
pytest -q
```

La suite utiliza [SQLite](https://www.sqlite.org/) en memoria y dobles de prueba para conexiones remotas. Cubre autenticación, cifrado, conectores, acciones del panel, generación de órdenes, seguimiento de ejecuciones y puntuación. No sustituye una prueba de transferencia y restauración con MySQL/MariaDB y dos equipos SSH reales.

| Directorio | Contenido |
| --- | --- |
| `app/` | Panel, instalador, modelos, plantillas y recursos visuales |
| `connectors/` | Descubrimiento y filtros por plataforma |
| `daemon/` | Programador, ejecución, claves, retención, monitor y avisos |
| `scoring/` | Cálculo del indicador de protección |
| `alembic/` | Migraciones de esquema |
| `deploy/` | Unidades de servicio |
| `tests/` | Pruebas automatizadas |

## Licencia

El código propio de Teseo se distribuye bajo GNU GPL (GNU General Public License), versión 3. Consulta el texto íntegro en [`LICENSE.md`](LICENSE.md), procedente de la [fuente oficial](https://www.gnu.org/licenses/gpl-3.0.html).

Las dependencias conservan sus licencias y atribuciones originales. El tema visual conserva en `app/static/style.css` la referencia a los proyectos de los que procede; esta licencia no sustituye las licencias de componentes de terceros.
