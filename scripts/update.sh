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

# Crea las TABLAS nuevas que hubiera (create_all es aditivo: NO altera columnas
# de tablas existentes; los cambios de columnas requieren migración manual).
echo "==> Sincronizando esquema (tablas nuevas)"
.venv/bin/python -c "from app.db import init_engine; from app.models import Base; Base.metadata.create_all(init_engine())"

echo "==> Reiniciando servicios"
sudo systemctl restart teseo-web teseod

echo "==> Listo. Versión desplegada: $(cat VERSION)"
