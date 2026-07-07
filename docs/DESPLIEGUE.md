# Despliegue y actualización de Teseo

Guía para poner Teseo en marcha en un servidor Linux con systemd y mantenerlo
actualizado mediante releases de GitHub.

> Ejemplos con Debian/Ubuntu (`apt`). Adapta el gestor de paquetes a tu distro.
> **Python 3.11 o 3.12** (los pines actuales de `requirements.txt` aún no soportan 3.14).

---

## 1. Requisitos

- Servidor Linux con `systemd`, `git`, `rsync` y `ssh`.
- Python 3.11/3.12 + `venv`.
- MySQL o MariaDB (puede ser en el propio servidor).
- Acceso SSH desde el servidor a los **hosts origen** y **destinos** (Teseo es el
  controlador; los datos van origen→destino directamente, no pasan por él).

---

## 2. Preparar el servidor

```bash
sudo apt update
sudo apt install -y git rsync openssh-client python3-venv mariadb-server

# Usuario de servicio sin login y carpeta de la app
sudo useradd --system --home /opt/teseo --shell /usr/sbin/nologin teseo
sudo mkdir -p /opt/teseo /etc/teseo
```

Asegura MariaDB y crea un usuario para Teseo (el asistente creará la BD y las tablas):

```bash
sudo mysql_secure_installation   # opcional pero recomendado
sudo mysql -e "CREATE USER 'teseo'@'localhost' IDENTIFIED BY 'CAMBIA_ESTA_CLAVE';
               GRANT ALL PRIVILEGES ON teseo.* TO 'teseo'@'localhost';
               GRANT CREATE ON *.* TO 'teseo'@'localhost';
               FLUSH PRIVILEGES;"
```

---

## 3. Instalar la aplicación en /opt/teseo

```bash
sudo git clone https://github.com/antoniosastre/Teseo.git /opt/teseo
cd /opt/teseo
# Desplegar la última versión publicada (no la punta de main)
sudo git checkout "$(git tag -l 'v*' --sort=-v:refname | head -n1)"

sudo python3 -m venv .venv
sudo .venv/bin/pip install --upgrade pip
sudo .venv/bin/pip install -r requirements.txt

sudo chown -R teseo:teseo /opt/teseo /etc/teseo
```

---

## 4. Configuración inicial (asistente /install)

La primera vez, cualquier ruta redirige a `/install`, donde se configura MySQL, se
crea el primer administrador y se generan las claves (sesión y cifrado) en
`/etc/teseo/config.ini`.

La web escucha en `127.0.0.1:8080` (solo local). Para completar el asistente desde
tu navegador, abre un **túnel SSH** desde tu equipo:

```bash
ssh -L 8080:127.0.0.1:8080 usuario@tu-servidor
```

Arranca la web una vez (a mano) para instalar, o instala ya los servicios (§5) y
navega por el túnel a <http://127.0.0.1:8080>. Completa el asistente:

- **Base de datos**: host `localhost`, usuario `teseo`, la clave de arriba, BD `teseo`.
- **Administrador**: usuario y contraseña (mín. 8 caracteres).
- **SMTP** (opcional): para los avisos de fallo / origen inaccesible / huérfanos.

En producción, edita luego `/etc/teseo/config.ini` y pon `https_only = true` en
`[security]` (ver §6).

---

## 5. Servicios systemd

```bash
sudo cp /opt/teseo/deploy/teseo-web.service /opt/teseo/deploy/teseod.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now teseo-web teseod

# Comprobar
systemctl status teseo-web teseod
journalctl -u teseod -f            # log del daemon (scheduler, analizador, copias)
```

- `teseo-web`: panel (uvicorn en 127.0.0.1:8080).
- `teseod`: scheduler + ejecución de copias + monitor + analizador.

---

## 6. Acceso en producción (nginx + TLS)

Para exponerlo con HTTPS, pon un nginx por delante como *reverse proxy*:

```nginx
server {
    listen 443 ssl;
    server_name teseo.tudominio.com;
    ssl_certificate     /etc/letsencrypt/live/teseo.tudominio.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/teseo.tudominio.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
    }
}
```

Y en `/etc/teseo/config.ini` → `[security] https_only = true` (cookie de sesión
solo por HTTPS). Reinicia: `sudo systemctl restart teseo-web`.

---

## 7. Releases y actualizaciones

### Publicar una versión (desde tu equipo de desarrollo)

El versionado vive en el fichero `VERSION` (se muestra en la barra del panel). Para
publicar, con `main` limpio:

```bash
scripts/release.sh 0.2.0
```

Esto actualiza `VERSION`, crea el tag `v0.2.0` y lo empuja. El workflow
`.github/workflows/release.yml` crea automáticamente la **Release** en GitHub con
notas generadas a partir de los PR/commits desde la versión anterior.

### Actualizar el servidor a una release

```bash
cd /opt/teseo
sudo -u teseo TESEO_CONFIG=/etc/teseo/config.ini scripts/update.sh          # última versión
sudo -u teseo TESEO_CONFIG=/etc/teseo/config.ini scripts/update.sh v0.2.0   # versión concreta
```

`update.sh` hace `git fetch`, `checkout` del tag, reinstala dependencias, aplica las
**migraciones** de esquema (`alembic upgrade head`) y reinicia los servicios.

### Migraciones de esquema (Alembic)

- El **esquema inicial** lo crea el asistente de instalación (`create_all`). Alembic
  parte de un **baseline vacío** que se auto-sella la primera vez que corre
  `alembic upgrade head` (es un no-op sobre el esquema ya creado). A partir de ahí,
  cada cambio de esquema va en su propia migración.
- **Al desarrollar un cambio de esquema** (nueva columna, tabla, etc.), genera la
  migración contra una BD de desarrollo y añádela al repo:

  ```bash
  # con TESEO_CONFIG apuntando a tu BD de desarrollo
  .venv/bin/alembic revision --autogenerate -m "describe el cambio"
  # revisa el fichero generado en alembic/versions/ antes de commitearlo
  ```

- En el servidor, `update.sh` aplica esas migraciones automáticamente. Para ver el
  estado: `.venv/bin/alembic current` / `.venv/bin/alembic history`.

---

## 8. Comprobación rápida (smoke test)

1. Panel accesible y muestra la versión correcta en la barra superior.
2. Añade un **destino** y pulsa "Probar" (fija la clave de host, TOFU).
3. Añade un **host de origen** con su conector; el wizard debe explorar y listar
   volúmenes/orígenes.
4. Configura una **tarea** en un origen y pulsa "Ejecutar ya"; sigue el progreso en
   vivo y revisa `journalctl -u teseod -f`.
