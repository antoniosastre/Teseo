"""Puntuación del nivel de protección de los datos de una tarea de copia.

El scoring valora lo bien o mal protegidos que están los datos de un origen,
dada una tarea de copia concreta hacia un destino. Módulo aislado: el resto de
la aplicación lo consume vía ``app/services.py:tarea_score()``.

Fórmula (suma de puntos, base 0; máximo 6):
  - Redundancia en el origen (mutuamente excluyentes):
      raid1 -> +1, raid2 -> +2
  - El origen tiene copia de seguridad:                +1
  - Redundancia en el destino (mutuamente excluyentes):
      raid1 -> +1, raid2 -> +2
  - Ubicación física del destino distinta a la del origen:  +1
"""
from __future__ import annotations

from dataclasses import dataclass

# Puntos por redundancia RAID (aplicable a origen y a destino por separado).
RAID_POINTS = {"single": 0, "raid1": 1, "raid2": 2}

MAX_SCORE = 6  # raid2 origen (2) + copia (1) + raid2 destino (2) + ubicación distinta (1)


@dataclass
class ScoreInputs:
    origen_proteccion: str
    destino_proteccion: str
    origen_ubicacion_id: int | None
    destino_ubicacion_id: int | None
    tiene_copia: bool = True  # una tarea ES una copia de seguridad -> normalmente True


def score(inp: ScoreInputs) -> int:
    """Puntuación total de protección (0..MAX_SCORE)."""
    pts = 0
    # Redundancia en el origen.
    pts += RAID_POINTS.get(inp.origen_proteccion, 0)
    # El origen tiene copia de seguridad.
    if inp.tiene_copia:
        pts += 1
    # Redundancia en el destino.
    pts += RAID_POINTS.get(inp.destino_proteccion, 0)
    # Ubicación física del destino distinta a la del origen.
    if (
        inp.origen_ubicacion_id is not None
        and inp.destino_ubicacion_id is not None
        and inp.origen_ubicacion_id != inp.destino_ubicacion_id
    ):
        pts += 1
    return pts


@dataclass
class CopiaInputs:
    """Protección del lado destino de una copia concreta (una tarea del origen)."""

    destino_proteccion: str
    ubicacion_distinta: bool


def origen_score(origen_proteccion: str, copias: list[CopiaInputs]) -> int:
    """Puntuación de protección de un ORIGEN (0..MAX_SCORE).

    - Redundancia del origen (RAID del volumen): raid1 +1 / raid2 +2.
    - Si el origen tiene ≥1 copia: +1.
    - Lado destino, regla "mejor copia": máximo, sobre las tareas del origen, de
      (RAID destino + ubicación distinta). Sin copias no suma nada de esto.
    """
    pts = RAID_POINTS.get(origen_proteccion, 0)
    if copias:
        pts += 1  # el origen tiene copia de seguridad
        pts += max(
            RAID_POINTS.get(c.destino_proteccion, 0) + (1 if c.ubicacion_distinta else 0)
            for c in copias
        )
    return pts


def classify(points: int) -> str:
    """Etiqueta cualitativa a partir de la puntuación (para tooltip/aria)."""
    if points >= 5:
        return "excelente"
    if points >= 3:
        return "buena"
    if points >= 1:
        return "básica"
    return "mínima"


@dataclass
class ScoreBar:
    """Representación gráfica de la puntuación para la barra de la UI."""

    puntos: int
    pct: int      # llenado de la barra (10..100)
    color: str    # token de color: rojo|naranja|amarillo|verde|azul
    texto: str    # etiqueta cualitativa (mínima/básica/buena/excelente)


# Mapeo puntuación -> (llenado %, color) definido por el usuario.
_BAR = {
    0: (10, "rojo"),
    1: (20, "naranja"),
    2: (40, "amarillo"),
    3: (60, "verde"),
    4: (80, "verde"),
    5: (90, "azul"),
    6: (100, "azul"),
}


def score_bar(points: int) -> ScoreBar:
    """Traduce una puntuación a los parámetros gráficos de la barra."""
    points = max(0, min(MAX_SCORE, points))
    pct, color = _BAR[points]
    return ScoreBar(puntos=points, pct=pct, color=color, texto=classify(points))
