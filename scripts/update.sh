#!/usr/bin/env bash
# Actualiza Teseo a una release de GitHub (o a la última) y reinicia los servicios.
#
# Uso (en el servidor, como usuario con permiso sobre /opt/teseo y sudo systemctl):
#   scripts/update.sh            # actualiza a la última etiqueta vX.Y.Z
#   scripts/update.sh v0.2.0     # actualiza a una versión concreta
set -euo pipefail

APP_DIR="${TESEO_DIR:-/opt/teseo}"
export TESEO_CONFIG="${TESEO_CONFIG:-/etc/teseo/config.ini}"

cd "$APP_DIR"

echo "==> Obteniendo etiquetas del remoto…"
git fetch --tags --prune origin

TAG="${1:-}"
if [ -z "$TAG" ]; then
  TAG="$(git tag -l 'v*' --sort=-v:refname | head -n1)"
fi
if [ -z "$TAG" ]; then
  echo "No hay ninguna etiqueta vX.Y.Z publicada." >&2
  exit 1
fi

echo "==> Actualizando a $TAG"
git checkout -q "$TAG"

echo "==> Instalando dependencias"
.venv/bin/pip install -q -r requirements.txt

# Aplica las migraciones de esquema pendientes (Alembic). Sobre una BD creada por
# el instalador (create_all) y aún sin sellar, esto ejecuta el baseline vacío y la
# deja sellada; en adelante aplica las migraciones de cada versión.
echo "==> Aplicando migraciones de esquema (alembic upgrade head)"
.venv/bin/alembic upgrade head

VERSION_DESPLEGADA="$(cat VERSION)"

# Reinicio de servicios. Requiere sudo; el usuario de servicio 'teseo' NO debe
# tener sudo general. Si no hay sudo sin contraseña disponible, no abortamos con
# el error críptico de sudo: avisamos con la orden exacta a ejecutar a mano
# (o configura el sudoers acotado que se describe en docs/DESPLIEGUE.md).
echo "==> Reiniciando servicios"
if sudo -n systemctl restart teseo-web teseod 2>/dev/null; then
  echo "==> Servicios reiniciados."
else
  echo "!! No se pudieron reiniciar automáticamente (falta sudo sin contraseña)."
  echo "   Remátalo a mano con un usuario con sudo:"
  echo "       sudo systemctl restart teseo-web teseod"
fi

echo "==> Listo. Versión desplegada: $VERSION_DESPLEGADA"
