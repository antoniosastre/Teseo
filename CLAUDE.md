# CLAUDE.md — Memoria del proyecto Teseo

Documento de contexto para que cualquier sesión de Claude (incluso en otra cuenta)
pueda retomar el trabajo sin perder información. Mantenlo actualizado al cerrar cada
hito relevante.

---

## 1. Qué es Teseo

Aplicación web para **crear, ejecutar y supervisar copias de seguridad con `rsync`**.
El servidor del panel es un **"controlador aéreo"**: orquesta, programa y monitoriza,
pero **los datos NUNCA pasan por él**. El controlador abre SSH al host **origen** y
lanza ahí el `rsync` que empuja los datos **directamente al destino**.

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
                                        └────────────┘
```

Idioma del proyecto: **español** (UI, comentarios, mensajes de commit, identificadores
de dominio como `tarea`, `destino`, `origen`, `ubicacion`).

---

## 2. Estado actual (al día de hoy)

- **PR #1 — FUSIONADO** en `main`: implementación inicial completa.
  https://github.com/antoniosastre/Teseo/pull/1
  (No reabrir ni crear otro PR para ese mismo cambio.)
- El repositorio nació **vacío**. Se creó un commit raíz `Initial commit` en `main` y la
  rama de trabajo se rebasó encima para poder abrir un PR con historia común. Tenlo en
  cuenta si vuelve a aparecer un problema de "no history in common".
- **No hay CI real** configurado (solo el workflow gestionado por GitHub "Dependency
  Graph" de Dependabot). Si se desea CI, habría que añadir un workflow que ejecute
  `pytest` (ver §7).
- **14 tests en verde** (`pytest -q`).

### Rama de desarrollo
- La tarea original usaba la rama `claude/rsync-backup-panel-requirements-j9nv3l` (ya
  fusionada). Para cambios nuevos, crea una rama `claude/<descripcion>` partiendo de
  `origin/main` y abre PR contra `main`.

---

## 3. Decisiones de arquitectura cerradas (NO reabrir sin motivo)

| Decisión | Elección |
|----------|----------|
| Stack | **Python** — FastAPI + Jinja (web) y daemon systemd independiente |
| BD | **MySQL/MariaDB** vía SQLAlchemy 2.0 + PyMySQL |
| Confianza SSH origen→destino | **Auto-provisión** por el controlador (genera par ed25519, privada al origen, pública a `authorized_keys` del destino) |
| Scheduler | **Daemon propio** (`teseod`), reloj interno, cron por tarea, concurrencia |
| Credenciales | **Claves SSH preferidas**; contraseña como fallback. Secretos **cifrados (Fernet/AES)** en BD; la clave de cifrado vive en `config.ini`, fuera de la BD |
| "Incremental" | **Snapshots con histórico** vía `rsync --link-dest` (estilo Time Machine) |
| Retención | Configurable por tarea (nº de snapshots) |
| Avisos | **Email SMTP** ante fallo de copia u origen inaccesible |
| Puntuación de protección | Módulo aislado `scoring/` con **fórmula DEFINITIVA** (usuario, 070726). Máx. 6: RAID origen (raid1+1/raid2+2) + tiene copia (+1) + RAID destino (raid1+1/raid2+2) + ubicación distinta (+1). UI: barra gráfica no numérica (0 rojo 10% … 6 azul 100%) |

Web y daemon **no se comunican directamente**: coordinan a través de la BD (la web marca
`run_now`/programación; el daemon toma las tareas vencidas).

---

## 4. Estructura del repositorio

```
app/                      # Web FastAPI + Jinja
  main.py                 # create_app(): middleware "gate" (redirige a /install si no hay config), SessionMiddleware, monta routers
  config.py               # carga/escritura config.ini (DatabaseConfig, SmtpConfig, AppConfig); CONFIG_PATH (env TESEO_CONFIG)
  db.py                   # engine/sesión SQLAlchemy; session_scope(); init_engine()/reset_engine()
  crypto.py               # SecretBox (Fernet) + generate_key()
  models.py               # ORM: Admin, Ubicacion, Ajuste, HostOrigen, Volumen, Origen, HistoricoTamano, Destino, SshKeypair, Tarea, Ejecucion + enums
  auth.py                 # hash/verify argon2, authenticate(), helpers de sesión
  deps.py                 # require_login (lanza RedirectException), get_secret_box()
  templating.py           # Jinja2Templates + filtros human_bytes / datetime_fmt
  services.py             # ssh_target_*, host_semaforo(), origen_score_bar(), estado_copia(), ultima_copia_ok(), explorar_host()/persistir_descubrimiento()
  rsync_cmd.py            # build_plan()/preview_command()/validate_override(): comando rsync, layout, filtros del conector
  remote.py               # paramiko: connect() (pinning host_key), run(), test_connection(), disk_usage()
  installer/              # asistente /install (service.py: test_connection, create_database, run_install)
  routers/               # dashboard, auth, ubicaciones, destinos, origenes (wizard+jerarquía), tareas, estado (SSE)
  templates/ static/      # Jinja + style.css + app.js (SSE, probar conexión, alta inline)
connectors/               # estrategia por dispositivo: __init__ (interfaz+registro), synology.py (reglas), plesk.py (stub)
daemon/                   # Servicio teseod
  teseod.py               # bucle scheduler (clase Daemon), concurrencia MAX_CONCURRENCY=3, monitor cada 300s
  keyprov.py              # ensure_trust(): genera/instala/valida claves SSH origen→destino
  runner.py               # run_tarea(): rsync con streaming de progreso; usa el conector para fuente+filtros
  retention.py            # set_current_symlink(), apply_retention() (por DÍAS)
  monitor.py              # check_destinos() (df/du), check_origenes() (accesibilidad + aprende host_key)
  notify.py               # email SMTP (notify_failure, notify_unreachable)
scoring/__init__.py       # origen_score()/score_bar()/classify(): fórmula por origen + barra gráfica
migrations/schema.sql     # esquema de referencia (DESACTUALIZADO; la verdad es create_all sobre models.py)
deploy/                   # teseo-web.service, teseod.service (systemd)
tests/                    # pytest sobre SQLite en memoria (conftest.py, test_app.py, test_rsync_cmd.py)
config.ini.example        # ejemplo (el real lo genera /install; está en .gitignore)
```

---

## 5. Modelo de datos (resumen)

Jerarquía de orígenes: **Host → Volumen → Origen → (0..n) Tarea**. El RAID es del
**volumen**; la ubicación física, del **host**. El conector descubre los orígenes.

- **admins**(username único, password_hash argon2, email)
- **ubicaciones**(nombre único) — ubicación física del host, para puntuar protección
- **ajustes**(clave PK, valor) — ajustes globales editables (p. ej. intervalo del analizador)
- **hosts_origen**(nombre único, tipo_conector synology|plesk, host, puerto, usuario,
  auth_method, secret_cifrado, host_key, ubicacion_id, estado_conexion)
- **volumenes**(host_origen_id, nombre, proteccion single|raid1|raid2) — UNIQUE(host,nombre)
- **origenes**(volumen_id, nombre, tipo carpeta|config, ruta, tamano_bytes, last_size_check,
  estado activo|desaparecido) — UNIQUE(volumen,ruta). "desaparecido" ⇒ tareas huérfanas
- **historico_tamano**(origen_id, timestamp, bytes) — serie temporal del tamaño
- **destinos**(nombre único, conexión SSH, host_key, carpeta_base, proteccion, ubicacion_id,
  estado, espacio_total/backups/libre)
- **ssh_keypairs**(private_key_cifrada, public_key, fingerprint, estado)
- **tareas**(origen_id, destino_id, tipo espejo|incremental, cron, comando_rsync override,
  rsync_extra, retencion_dias, estado, porcentaje, run_now, activa, last_run_at, next_run_at,
  ssh_keypair_id) — UNIQUE(origen,destino,tipo)
- **ejecuciones**(tarea_id, inicio, fin, resultado, bytes_transferidos, snapshot_path,
  resumen, error) — historial

Enums en `app/models.py`: AUTH_METHODS, PROTECCIONES, CONECTORES, TIPOS_ORIGEN,
ESTADOS_ORIGEN, TIPOS_TAREA, ESTADOS_TAREA, ESTADOS_CONEXION, RESULTADOS_EJEC.

---

## 6. Cómo ejecutar

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt        # o requirements-dev.txt para tests

# Web (la 1ª vez redirige al asistente /install)
uvicorn app.main:app --host 127.0.0.1 --port 8080
# Daemon (en otra terminal o como servicio)
python -m daemon.teseod
```

- Sin `config.ini`, TODA ruta redirige a `/install`. El asistente prueba MySQL, crea BD y
  tablas (`Base.metadata.create_all`), crea el primer admin y escribe `config.ini`
  (genera `secret_key` y `encryption_key`).
- Ruta del config: `./config.ini` por defecto; override con la variable `TESEO_CONFIG`.
- Despliegue: ver `deploy/*.service` (usuario `teseo`, `/opt/teseo`,
  `TESEO_CONFIG=/etc/teseo/config.ini`).

### Layout de copias en el destino
```
<carpeta_base>/<host>/<volumen>/<origen>/<tipo_tarea>/<contenido>
```
- **espejo**: `.../current/` con `--delete`.
- **incremental**: `.../<YYYY-MM-DD_HHMMSS>/` + enlace `current` → último snapshot, con
  `--link-dest=../current` (hardlinks). `apply_retention()` borra los snapshots con más de
  N **días** de antigüedad (conserva siempre el más reciente).

---

## 7. Testing

```bash
pip install -r requirements-dev.txt
pytest -q          # 14 tests, SQLite en memoria
```

- `tests/conftest.py`: monta config.ini efímero + engine SQLite (StaticPool) + admin +
  `TestClient`. Fixtures `client` y `auth_client`.
- Cubre: login, cifrado de secretos, CRUD, generación de comandos rsync (espejo/incremental),
  scoring, acciones sobre tareas, SSE json.
- **Limitación**: el flujo rsync extremo-a-extremo necesita MySQL real + 2 hosts SSH
  (origen y destino), no disponibles en CI. La lógica de aplicación sí se valida.
- Si añades CI: workflow que instale `requirements-dev.txt` y ejecute `pytest`.

---

## 8. Trabajo pendiente / próximos pasos

1. ~~Fórmula de scoring~~ **CERRADO (070726).** `scoring/__init__.py`: `origen_score()` (regla
   "mejor copia") + `score_bar()`; lo consume `app/services.py:origen_score_bar()`. Máx. 6:
   RAID volumen (raid1+1/raid2+2) + tiene copia (+1) + RAID destino (raid1+1/raid2+2) +
   ubicación distinta (+1). UI: barra gráfica no numérica (0 rojo 10% → 6 azul 100%).
2. **Rediseño de orígenes por conectores** — núcleo, wizard y vista HECHOS (rama
   `claude/rediseno-origenes-nucleo`): modelo Host→Volumen→Origen→Tarea, `connectors/`
   (Synology completo, Plesk stub), wizard de 2 pantallas, vista jerárquica, retención por días.
   PENDIENTE en fases siguientes: **analizador periódico** (refresco de tamaño con `du` +
   `historico_tamano`, re-exploración, detección de huérfanas + email; intervalo en tabla
   `ajustes`, disparable manual), **CRUD de ubicaciones** en Ajustes, cálculo real de tamaño
   por `du`, y las reglas del **conector Plesk**.
3. Mejoras varias: múltiples admins UI, cancelación de copias en curso, paginación del
   historial. (CI con pytest ya existe en rama aparte.)

---

## 9. Convenciones y avisos importantes

- **rsync se ejecuta en el ORIGEN** (vía SSH desde el controlador), empujando al destino.
  Los datos no pasan por el panel. No romper esta invariante.
- **Secretos**: nunca guardar contraseñas/claves en claro. Usar `SecretBox`
  (`app/crypto.py`); la clave de cifrado está en `config.ini` (`[security] encryption_key`).
- **`config.ini` está en `.gitignore`** — contiene secretos. No commitearlo nunca.
- Estilo: comentarios y UI en español; seguir el estilo existente (dataclasses para config,
  `session_scope()` para transacciones, routers finos que delegan en `services`/`remote`).
- Semáforo de host (`services.host_semaforo`): rojo=inaccesible, naranja=alguna fallida,
  amarillo=alguna en progreso, verde=todo ok, gris=sin tareas.
- El daemon nunca debe morir por una excepción de una tarea (bucle protegido en `teseod`).

---

## 10. Entorno y GitHub

- Repo: `antoniosastre/teseo`. Acceso de la sesión limitado a ese repo.
- Operaciones GitHub: vía herramientas MCP `mcp__github__*` (no hay `gh` CLI).
- Tras push, abrir PR contra `main` (listo para revisión, no draft).
- `git push -u origin <rama>` con reintentos exponenciales ante errores de red.
