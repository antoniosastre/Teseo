"""Daemon de Teseo: scheduler interno + ejecutor de copias.

Coordina con la web exclusivamente a través de la base de datos:
  - toma tareas marcadas ``run_now`` o cuya ``next_run_at`` ha vencido,
  - las ejecuta con control de concurrencia,
  - monitoriza periódicamente destinos y orígenes.

Pensado para correr como servicio systemd (ver deploy/teseod.service).
"""
from __future__ import annotations

import datetime as dt
import logging
import signal
import threading
import time

from croniter import croniter
from sqlalchemy import select

from app.config import config_exists, load_config
from app.crypto import SecretBox
from app.db import init_engine, session_scope
from app.models import Tarea
from app.settings import analizador_run_now, intervalo_analizador_horas, marcar_analizador_run_now
from daemon.analyzer import run_analisis
from daemon.monitor import check_destinos, check_origenes
from daemon.runner import run_tarea

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("teseod")

TICK_SECONDS = 5
MONITOR_INTERVAL = 300          # cada 5 min sondea espacio/accesibilidad
MAX_CONCURRENCY = 3             # copias simultáneas máximas


class Daemon:
    def __init__(self):
        self._stop = threading.Event()
        self._running: dict[int, threading.Thread] = {}
        self._lock = threading.Lock()
        self._last_monitor = 0.0
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
        self._reap()

        # Monitorización periódica (en hilo aparte para no frenar el scheduler).
        if time.time() - self._last_monitor > MONITOR_INTERVAL:
            self._last_monitor = time.time()
            threading.Thread(target=self._run_monitor, daemon=True).start()

        # Analizador: por intervalo configurable (ajustes) o disparo manual.
        self._maybe_analisis()

        # Selección de tareas a ejecutar.
        with session_scope() as session:
            tareas = list(
                session.scalars(
                    select(Tarea).where(Tarea.activa.is_(True), Tarea.estado != "en_progreso")
                )
            )
            due = []
            for t in tareas:
                if t.id in self._running:
                    continue
                if t.run_now or (t.next_run_at is not None and t.next_run_at <= now):
                    due.append(t.id)
                elif t.next_run_at is None and croniter.is_valid(t.cron):
                    t.next_run_at = croniter(t.cron, now).get_next(dt.datetime)

        for tarea_id in due:
            with self._lock:
                if len(self._running) >= MAX_CONCURRENCY:
                    break
                if tarea_id in self._running:
                    continue
                th = threading.Thread(target=self._execute, args=(tarea_id,), daemon=True)
                self._running[tarea_id] = th
                th.start()

    def _execute(self, tarea_id: int):
        log.info("Ejecutando tarea %s", tarea_id)
        try:
            run_tarea(tarea_id, self._box)
        except Exception:  # noqa: BLE001
            log.exception("Fallo ejecutando tarea %s", tarea_id)
        finally:
            with self._lock:
                self._running.pop(tarea_id, None)
            log.info("Tarea %s finalizada", tarea_id)

    def _reap(self):
        with self._lock:
            muertas = [tid for tid, th in self._running.items() if not th.is_alive()]
            for tid in muertas:
                self._running.pop(tid, None)

    def _run_monitor(self):
        try:
            check_destinos(self._box)
            check_origenes(self._box)
        except Exception:  # noqa: BLE001
            log.exception("Error en la monitorización")

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
