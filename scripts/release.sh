#!/usr/bin/env bash
# Publica una nueva versión: actualiza VERSION, commitea, etiqueta y empuja.
# El push del tag dispara el workflow de GitHub que crea la Release con notas.
#
# Uso (en local, sobre main y con el árbol limpio):
#   scripts/release.sh 0.2.0
set -euo pipefail

VER="${1:?Uso: scripts/release.sh X.Y.Z}"
VER="${VER#v}"   # admite '0.2.0' o 'v0.2.0'

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
[ "$BRANCH" = "main" ] || { echo "Debes estar en main (estás en '$BRANCH')." >&2; exit 1; }
git diff --quiet && git diff --cached --quiet || { echo "Árbol sucio: commitea/limpia antes." >&2; exit 1; }

git pull --ff-only origin main
printf '%s\n' "$VER" > VERSION
git add VERSION
git commit -m "Release v$VER"
git tag -a "v$VER" -m "Teseo v$VER"
git push origin main
git push origin "v$VER"

echo "Tag v$VER empujado. GitHub Actions publicará la Release en unos segundos."
