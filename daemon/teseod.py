"""Daemon de Teseo: scheduler interno + orquestador de copias.

Coordina con la web exclusivamente a través de la base de datos:
  - LANZA las tareas vencidas (``run_now`` o ``next_run_at`` cumplido) como copias
    DESCOLGADAS en el origen (ver daemon/runner.lanzar_tarea) — no espera a que acaben,
  - SONDEA en cada tick las copias en curso hasta que terminan (runner.sondear_tarea);
    esto re-adopta también las copias que sobrevivieron a un reinicio del controlador,
  - monitoriza periódicamente destinos/orígenes y dispara el analizador.

Cola de copias: MAX_CONCURRENCY limita las copias VIVAS simultáneas en total y,
además, cada destino admite como mucho ``Destino.max_copias`` a la vez y cada
host de origen el ajuste global ``max_copias_por_origen`` (ambos 1 por defecto).
Una tarea vencida que no cabe se queda esperando turno y se lanza en cuanto se
libera hueco: en discos mecánicos varias escrituras simultáneas en el mismo
destino rinden mucho menos que encoladas. Pensado para systemd (deploy/).
"""
from __future__ import annotations

import datetime as dt
import logging
import signal
import threading
import time
from collections import Counter
from dataclasses import dataclass

from croniter import croniter
from sqlalchemy import select

from app.config import config_exists, load_config
from app.crypto import SecretBox
from app.db import init_engine, session_scope
from app.models import Destino, Origen, Tarea, Volumen
from app.settings import (
    analizador_run_now,
    intervalo_analizador_horas,
    marcar_analizador_run_now,
    max_copias_por_origen,
)
from daemon.analyzer import run_analisis
from daemon.monitor import check_destinos, check_origenes
from daemon.runner import lanzar_tarea, sondear_tarea

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("teseod")

TICK_SECONDS = 5
MONITOR_INTERVAL = 300          # cada 5 min sondea espacio/accesibilidad
MAX_CONCURRENCY = 3             # copias simultáneas máximas (en total)


@dataclass(frozen=True)
class Candidata:
    """Tarea vencida que espera turno, con lo que la sitúa en la cola."""

    tarea_id: int
    destino_id: int
    host_id: int


def elegir_lanzables(
    ocupadas: list[tuple[int, int]],
    candidatas: list[Candidata],
    *,
    max_total: int,
    max_por_destino: dict[int, int],
    max_por_origen: int,
) -> list[int]:
    """Decide qué tareas vencidas se lanzan ya, respetando los tres límites.

    ``ocupadas`` son los pares (destino_id, host_id) de las copias vivas o en
    lanzamiento; ``candidatas`` llegan ya ordenadas por prioridad. Una candidata
    que no cabe NO corta la cola: se salta y las siguientes (de otro destino u
    origen) pueden lanzarse. Función pura, sin BD ni SSH.
    """
    por_destino = Counter(d for d, _ in ocupadas)
    por_host = Counter(h for _, h in ocupadas)
    total = len(ocupadas)
    elegidas: list[int] = []
    for c in candidatas:
        if total >= max_total:
            break
        if por_destino[c.destino_id] >= max(1, max_por_destino.get(c.destino_id, 1)):
            continue
        if por_host[c.host_id] >= max(1, max_por_origen):
            continue
        elegidas.append(c.tarea_id)
        total += 1
        por_destino[c.destino_id] += 1
        por_host[c.host_id] += 1
    return elegidas


class Daemon:
    def __init__(self):
        self._stop = threading.Event()
        self._running: dict[int, threading.Thread] = {}
        # Tareas con un worker de lanzamiento activo: aún pueden no figurar como
        # "en_progreso" en la BD, pero ya ocupan su hueco en la cola.
        self._lanzando: set[int] = set()
        self._lock = threading.Lock()
        self._last_monitor = 0.0
        self._monitor_activo = False
        self._last_analisis = 0.0
        self._analisis_activo = False
        self._box: SecretBox | None = None

    # --- ciclo de vida -------------------------------------------------------

    def stop(self, *_):
        log.info("Señal de parada recibida, terminando…")
        self._stop.set()

    def run(self):
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)

        self._wait_until_configured()
        config = load_config()
        init_engine(config)
        self._box = SecretBox(config.encryption_key)
        self._seed_next_runs()
        log.info("teseod iniciado.")

        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:  # noqa: BLE001 - el bucle nunca debe morir
                log.exception("Error en el ciclo principal")
            self._stop.wait(TICK_SECONDS)

        self._await_running()
        log.info("teseod detenido.")

    def _wait_until_configured(self):
        while not config_exists() and not self._stop.is_set():
            log.info("Esperando a que la aplicación esté instalada (config.ini)…")
            self._stop.wait(10)

    # --- scheduling ----------------------------------------------------------

    def _seed_next_runs(self):
        """Calcula next_run_at para tareas que no lo tengan todavía."""
        now = dt.datetime.now()
        with session_scope() as session:
            for t in session.scalars(select(Tarea).where(Tarea.next_run_at.is_(None), Tarea.activa.is_(True))):
                if croniter.is_valid(t.cron):
                    t.next_run_at = croniter(t.cron, now).get_next(dt.datetime)

    def _tick(self):
        now = dt.datetime.now()

        # Monitorización periódica (en hilo aparte para no frenar el scheduler).
        # Nunca dos a la vez: si la anterior sigue (destinos lentos), se espera.
        if not self._monitor_activo and time.time() - self._last_monitor > MONITOR_INTERVAL:
            self._last_monitor = time.time()
            self._monitor_activo = True
            threading.Thread(target=self._run_monitor, daemon=True).start()

        # Analizador: por intervalo configurable (ajustes) o disparo manual.
        self._maybe_analisis()

        # 1) Sondear las copias en curso (re-adopta también las que sobrevivieron a un
        #    reinicio del controlador; el runner las finaliza cuando terminan).
        self._sondear_en_progreso()

        # 2) Lanzar las tareas vencidas, respetando la concurrencia máxima de copias vivas.
        self._lanzar_pendientes(now)

    def _worker(self, kind: str, tarea_id: int):
        try:
            if kind == "launch":
                log.info("Lanzando copia de la tarea %s", tarea_id)
                lanzar_tarea(tarea_id, self._box)
            else:
                estado = sondear_tarea(tarea_id, self._box)
                if estado in ("done", "interrupted", "cancelled"):
                    log.info("Tarea %s: %s", tarea_id, estado)
        except Exception:  # noqa: BLE001 - un worker nunca debe tumbar el daemon
            log.exception("Error en worker %s de la tarea %s", kind, tarea_id)
        finally:
            with self._lock:
                self._running.pop(tarea_id, None)
                self._lanzando.discard(tarea_id)

    def _arrancar_worker(self, kind: str, tarea_id: int) -> bool:
        """Arranca un worker si la tarea no tiene ya uno en curso. Devuelve si arrancó."""
        with self._lock:
            if tarea_id in self._running:
                return False
            th = threading.Thread(target=self._worker, args=(kind, tarea_id), daemon=True)
            self._running[tarea_id] = th
            if kind == "launch":
                self._lanzando.add(tarea_id)
            th.start()
            return True

    def _sondear_en_progreso(self):
        with session_scope() as session:
            ids = [t.id for t in session.scalars(
                select(Tarea).where(Tarea.estado == "en_progreso"))]
        for tarea_id in ids:
            self._arrancar_worker("poll", tarea_id)

    def _lanzar_pendientes(self, now):
        with self._lock:
            lanzando = set(self._lanzando)
        with session_scope() as session:
            limite_origen = max_copias_por_origen(session)
            filas = session.execute(
                select(Tarea, Volumen.host_origen_id, Destino.max_copias)
                .join(Origen, Tarea.origen_id == Origen.id)
                .join(Volumen, Origen.volumen_id == Volumen.id)
                .join(Destino, Tarea.destino_id == Destino.id)
            ).all()
            ocupadas: list[tuple[int, int]] = []
            limites_destino: dict[int, int] = {}
            vencidas: list[tuple[tuple, Candidata]] = []
            for t, host_id, max_copias in filas:
                limites_destino[t.destino_id] = max_copias or 1
                if t.estado == "en_progreso" or t.id in lanzando:
                    ocupadas.append((t.destino_id, host_id))
                    continue
                if not t.activa:
                    continue
                if t.run_now or (t.next_run_at is not None and t.next_run_at <= now):
                    # Prioridad: "Ejecutar ya" primero; después, la que más lleva esperando.
                    orden = (0 if t.run_now else 1, t.next_run_at or now, t.id)
                    vencidas.append((orden, Candidata(t.id, t.destino_id, host_id)))
                elif t.next_run_at is None and croniter.is_valid(t.cron):
                    t.next_run_at = croniter(t.cron, now).get_next(dt.datetime)

        vencidas.sort(key=lambda par: par[0])
        lanzables = elegir_lanzables(
            ocupadas,
            [c for _, c in vencidas],
            max_total=MAX_CONCURRENCY,
            max_por_destino=limites_destino,
            max_por_origen=limite_origen,
        )
        for tarea_id in lanzables:
            self._arrancar_worker("launch", tarea_id)

    def _run_monitor(self):
        try:
            check_destinos(self._box)
            check_origenes(self._box)
        except Exception:  # noqa: BLE001
            log.exception("Error en la monitorización")
        finally:
            self._monitor_activo = False

    def _maybe_analisis(self):
        if self._analisis_activo:
            return
        with session_scope() as session:
            intervalo_s = intervalo_analizador_horas(session) * 3600
            manual = analizador_run_now(session)
            if manual:
                marcar_analizador_run_now(session, False)  # consumimos la bandera
        if manual or (time.time() - self._last_analisis > intervalo_s):
            self._last_analisis = time.time()
            self._analisis_activo = True
            threading.Thread(target=self._run_analisis, daemon=True).start()

    def _run_analisis(self):
        log.info("Analizador: inicio (re-exploración + tamaños)")
        try:
            run_analisis(self._box)
        except Exception:  # noqa: BLE001 - el analizador nunca debe tumbar el daemon
            log.exception("Error en el analizador")
        finally:
            self._analisis_activo = False
            log.info("Analizador: fin")

    def _await_running(self):
        with self._lock:
            hilos = list(self._running.values())
        for th in hilos:
            th.join(timeout=30)


def main():
    Daemon().run()


if __name__ == "__main__":
    main()
