"""Puntuación del nivel de protección de los datos de una tarea.

NOTA: la fórmula definitiva la definirá el usuario. Este módulo es un
**placeholder** documentado y aislado: basta con cambiar ``score()`` y
``classify()`` para ajustar el criterio sin tocar el resto de la aplicación.

Criterio provisional (suma de puntos):
  - Origen con RAID:                 raid1 -> +1, raid2 -> +2
  - Destino con RAID:                raid1 -> +1, raid2 -> +2
  - Ubicación física distinta:       +2  (regla 3-2-1: copia fuera de sede)
"""
from __future__ import annotations

from dataclasses import dataclass

RAID_POINTS = {"single": 0, "raid1": 1, "raid2": 2}


@dataclass
class ScoreInputs:
    origen_proteccion: str
    destino_proteccion: str
    origen_ubicacion_id: int | None
    destino_ubicacion_id: int | None


def score(inp: ScoreInputs) -> int:
    pts = 0
    pts += RAID_POINTS.get(inp.origen_proteccion, 0)
    pts += RAID_POINTS.get(inp.destino_proteccion, 0)
    if (
        inp.origen_ubicacion_id is not None
        and inp.destino_ubicacion_id is not None
        and inp.origen_ubicacion_id != inp.destino_ubicacion_id
    ):
        pts += 2
    return pts


def classify(points: int) -> str:
    """Etiqueta cualitativa a partir de la puntuación."""
    if points >= 5:
        return "excelente"
    if points >= 3:
        return "buena"
    if points >= 1:
        return "básica"
    return "mínima"


MAX_SCORE = 6  # raid2 origen (2) + raid2 destino (2) + ubicación distinta (2)
