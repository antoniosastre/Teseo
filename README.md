# Teseo — Panel web de copias de seguridad rsync

Teseo es un **controlador aéreo** de copias de seguridad: un panel web que crea,
programa y supervisa backups con `rsync`, pero **sin que los datos pasen por el
servidor del panel**. El controlador abre SSH al **host origen** y lanza ahí el
`rsync` que empuja los datos **directamente al destino**.

> 📒 **Para continuar el desarrollo** (decisiones cerradas, estado del proyecto,
> trabajo pendiente y convenciones) consulta [`CLAUDE.md`](CLAUDE.md): es la memoria
> del proyecto pensada para retomar la sesión desde cualquier entorno.

```
┌────────────┐      SSH (control)      ┌────────────┐
│  Teseo     │ ───────────────────────▶│  Origen    │
│ (panel +   │                         │  (servidor │
│  daemon)   │ ◀── estado/progreso ────│   con SSH) │
└────────────┘                         └─────┬──────┘
       ▲                                      │ rsync directo (datos)
       │ SSH (control: symlink/retención)     ▼
       └──────────────────────────────▶┌────────────┐
                                        │  Destino   │
                                        │  (volumen) │
                                        └────────────┘
```

## Características

- **Asistente de instalación**: si no existe `config.ini`, la app lleva a
  `/install` para configurar MySQL, crear el primer administrador, crear las
  tablas y (opcional) el SMTP de avisos.
- **Destinos**: estado de conexión, espacio total / ocupado por backups / libre,
  protección (disco único, RAID 1, RAID 2), ubicación física, y despliegue con
  los orígenes que copian en cada destino.
- **Orígenes**: hosts SSH con semáforo (verde/amarillo/naranja/rojo) y despliegue
  de sus tareas (tipo, estado, % en vivo, última y próxima copia, **puntuación**
  de protección de datos).
- **Tareas**: tipo **espejo** (`rsync --delete`) o **incremental** (snapshots con
  `--link-dest`, estilo Time Machine) + programación cron + retención + comando
  rsync editable en *opciones avanzadas*.
- **Auto-provisión SSH origen→destino**: el controlador genera un par de claves
  por tarea, instala la privada en el origen y la pública en el destino.
- **Daemon**: scheduler propio, ejecución con concurrencia, parseo de progreso
  (`--info=progress2`), retención de snapshots, monitor de espacio/accesibilidad
  y avisos por email.
- **Seguridad**: secretos cifrados (Fernet/AES) en BD con la clave en el fichero
  de config; contraseñas de admin con argon2; preferencia por claves SSH.

## Arquitectura

| Componente | Descripción |
|------------|-------------|
| `app/`     | Web FastAPI + Jinja: panel, instalador, login, routers, SSE de estado. |
| `daemon/`  | `teseod`: scheduler + ejecutor (rsync, claves, retención, monitor, email). |
| `scoring/` | Puntuación de protección (módulo aislado, **placeholder** sustituible). |
| `migrations/schema.sql` | Esquema de referencia (el instalador lo crea solo). |
| `deploy/`  | Units systemd para web y daemon. |

Web y daemon **no se comunican directamente**: coordinan a través de la BD (la web
marca `run_now` / programación; el daemon toma las tareas vencidas).

## Instalación

Requisitos: Python 3.11+, un servidor MySQL/MariaDB accesible, y `rsync`/`ssh` en
los hosts origen y destino.

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

# 1) Arranca la web (la primera vez te llevará al asistente /install)
uvicorn app.main:app --host 127.0.0.1 --port 8080

# 2) Arranca el daemon (en otra terminal / servicio)
python -m daemon.teseod
```

Abre `http://127.0.0.1:8080`, completa el asistente y empieza a añadir destinos,
hosts y orígenes.

### Despliegue como servicio (systemd)

Copia el proyecto a `/opt/teseo`, crea el usuario `teseo`, ubica la config en
`/etc/teseo/config.ini` y habilita los servicios:

```bash
cp deploy/teseo-web.service deploy/teseod.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now teseo-web teseod
```

La variable `TESEO_CONFIG` indica la ruta del fichero de configuración.

## Layout de las copias en el destino

```
<carpeta_base>/<host_origen>/<tipo_tarea>/<carpeta_origen>/<contenido>
```

- **espejo**: `.../current/` (réplica exacta, con `--delete`).
- **incremental**: `.../<YYYY-MM-DD_HHMMSS>/` + enlace `current` → último snapshot,
  con `--link-dest` para deduplicar por hardlinks. La retención conserva los N
  snapshots más recientes.

## Puntuación de protección

`scoring/__init__.py` implementa un criterio **provisional** (pendiente de la
fórmula definitiva): suma puntos por RAID en origen, RAID en destino y por que la
copia esté en una ubicación física distinta. Para ajustarla basta con editar
`score()` y `classify()` sin tocar el resto de la aplicación.

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

Las pruebas usan SQLite en memoria y cubren login, cifrado de secretos, CRUD,
generación de comandos rsync, scoring y acciones sobre tareas.

> **Nota sobre verificación end-to-end**: las copias reales requieren un MySQL y
> dos hosts SSH (origen y destino). La suite de tests valida toda la lógica de
> aplicación; el flujo completo de rsync se prueba en un entorno con esos hosts.
