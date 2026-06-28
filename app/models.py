"""Modelos ORM (SQLAlchemy 2.0) — esquema de la base de datos de Teseo."""
from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# --- Enumeraciones de dominio -------------------------------------------------

AUTH_METHODS = ("key", "password")
PROTECCIONES = ("single", "raid1", "raid2")  # disco único, raid 1 disco, raid 2 discos
TIPOS_TAREA = ("espejo", "incremental")
ESTADOS_TAREA = ("esperando", "en_progreso", "terminada", "fallida")
ESTADOS_CONEXION = ("desconocido", "conectado", "inaccesible", "en_uso")
RESULTADOS_EJEC = ("ok", "fallo", "cancelada")


class Admin(Base):
    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())


class Ubicacion(Base):
    """Ubicación física (sala/edificio/sede). Sirve para puntuar protección."""

    __tablename__ = "ubicaciones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nombre: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)


class HostOrigen(Base):
    __tablename__ = "hosts_origen"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nombre: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    puerto: Mapped[int] = mapped_column(Integer, default=22)
    usuario: Mapped[str] = mapped_column(String(128), nullable=False)
    auth_method: Mapped[str] = mapped_column(Enum(*AUTH_METHODS), default="password")
    secret_cifrado: Mapped[Optional[str]] = mapped_column(Text)  # password o clave privada cifrada
    host_key: Mapped[Optional[str]] = mapped_column(Text)        # known_hosts del origen
    es_raid: Mapped[str] = mapped_column(Enum(*PROTECCIONES), default="single")
    ubicacion_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ubicaciones.id"))
    estado_conexion: Mapped[str] = mapped_column(Enum(*ESTADOS_CONEXION), default="desconocido")
    last_check: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    ubicacion: Mapped[Optional[Ubicacion]] = relationship()
    tareas: Mapped[list["Tarea"]] = relationship(back_populates="host_origen", cascade="all, delete-orphan")


class Destino(Base):
    __tablename__ = "destinos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nombre: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    puerto: Mapped[int] = mapped_column(Integer, default=22)
    usuario: Mapped[str] = mapped_column(String(128), nullable=False)
    auth_method: Mapped[str] = mapped_column(Enum(*AUTH_METHODS), default="password")
    secret_cifrado: Mapped[Optional[str]] = mapped_column(Text)
    host_key: Mapped[Optional[str]] = mapped_column(Text)
    carpeta_base: Mapped[str] = mapped_column(String(512), nullable=False)
    proteccion: Mapped[str] = mapped_column(Enum(*PROTECCIONES), default="single")
    ubicacion_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ubicaciones.id"))
    estado: Mapped[str] = mapped_column(Enum(*ESTADOS_CONEXION), default="desconocido")
    espacio_total: Mapped[Optional[int]] = mapped_column(BigInteger)     # bytes
    espacio_backups: Mapped[Optional[int]] = mapped_column(BigInteger)   # bytes
    espacio_libre: Mapped[Optional[int]] = mapped_column(BigInteger)     # bytes
    last_check: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    ubicacion: Mapped[Optional[Ubicacion]] = relationship()
    tareas: Mapped[list["Tarea"]] = relationship(back_populates="destino", cascade="all, delete-orphan")


class SshKeypair(Base):
    """Par de claves dedicado a la confianza origen→destino de una tarea."""

    __tablename__ = "ssh_keypairs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    private_key_cifrada: Mapped[str] = mapped_column(Text, nullable=False)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[Optional[str]] = mapped_column(String(255))
    estado: Mapped[str] = mapped_column(String(32), default="pendiente")  # pendiente|provisionada|error
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())


class Tarea(Base):
    __tablename__ = "tareas"
    __table_args__ = (
        UniqueConstraint("host_origen_id", "destino_id", "carpeta_origen", name="uq_tarea"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    host_origen_id: Mapped[int] = mapped_column(ForeignKey("hosts_origen.id"), nullable=False)
    destino_id: Mapped[int] = mapped_column(ForeignKey("destinos.id"), nullable=False)
    carpeta_origen: Mapped[str] = mapped_column(String(512), nullable=False)
    tipo: Mapped[str] = mapped_column(Enum(*TIPOS_TAREA), default="espejo")
    cron: Mapped[str] = mapped_column(String(128), default="0 2 * * *")  # programación estilo cron
    comando_rsync: Mapped[Optional[str]] = mapped_column(Text)            # override editable
    rsync_extra: Mapped[Optional[str]] = mapped_column(Text)              # flags extra de usuario
    retencion: Mapped[int] = mapped_column(Integer, default=7)            # nº de snapshots a conservar
    estado: Mapped[str] = mapped_column(Enum(*ESTADOS_TAREA), default="esperando")
    porcentaje: Mapped[int] = mapped_column(Integer, default=0)
    run_now: Mapped[bool] = mapped_column(Boolean, default=False)         # bandera "ejecutar ya"
    activa: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)
    next_run_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)
    ssh_keypair_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ssh_keypairs.id"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    host_origen: Mapped[HostOrigen] = relationship(back_populates="tareas")
    destino: Mapped[Destino] = relationship(back_populates="tareas")
    keypair: Mapped[Optional[SshKeypair]] = relationship()
    ejecuciones: Mapped[list["Ejecucion"]] = relationship(
        back_populates="tarea", cascade="all, delete-orphan", order_by="Ejecucion.inicio.desc()"
    )


class Ejecucion(Base):
    __tablename__ = "ejecuciones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tarea_id: Mapped[int] = mapped_column(ForeignKey("tareas.id"), nullable=False)
    inicio: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    fin: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)
    resultado: Mapped[Optional[str]] = mapped_column(Enum(*RESULTADOS_EJEC))
    bytes_transferidos: Mapped[Optional[int]] = mapped_column(BigInteger)
    snapshot_path: Mapped[Optional[str]] = mapped_column(String(512))
    resumen: Mapped[Optional[str]] = mapped_column(Text)
    error: Mapped[Optional[str]] = mapped_column(Text)

    tarea: Mapped[Tarea] = relationship(back_populates="ejecuciones")
