"""Modelos ORM (SQLAlchemy 2.0) — esquema de la base de datos de Teseo.

Jerarquía de orígenes de copia:  Host → Volumen → Origen → (0..n) Tarea.
El RAID es una propiedad del **volumen**; la ubicación física, del **host**.
Un `Origen` es una unidad de datos respaldable que descubre un conector (una
carpeta compartida o un *bundle* como la "Configuración" de Synology), y puede
tener 0..n tareas de copia (espejo/incremental) a destinos iguales o distintos.
"""
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
CONECTORES = ("synology", "plesk_linux")      # tipos de conector de host (ver connectors/)
TIPOS_ORIGEN = ("carpeta", "config")          # carpeta real | bundle sintético (@ de Synology)
ESTADOS_ORIGEN = ("activo", "desaparecido")   # "desaparecido" => tareas huérfanas
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


class Ajuste(Base):
    """Ajustes globales editables desde la UI (clave -> valor)."""

    __tablename__ = "ajustes"

    clave: Mapped[str] = mapped_column(String(64), primary_key=True)
    valor: Mapped[str] = mapped_column(String(255), nullable=False)


class HostOrigen(Base):
    __tablename__ = "hosts_origen"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nombre: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    tipo_conector: Mapped[str] = mapped_column(Enum(*CONECTORES), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    puerto: Mapped[int] = mapped_column(Integer, default=22)
    usuario: Mapped[str] = mapped_column(String(128), nullable=False)
    auth_method: Mapped[str] = mapped_column(Enum(*AUTH_METHODS), default="password")
    secret_cifrado: Mapped[Optional[str]] = mapped_column(Text)  # password o clave privada cifrada
    host_key: Mapped[Optional[str]] = mapped_column(Text)        # clave de host confiada (pinning)
    conector_opciones: Mapped[Optional[str]] = mapped_column(Text)  # opciones de descubrimiento (JSON)
    ubicacion_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ubicaciones.id"))
    estado_conexion: Mapped[str] = mapped_column(Enum(*ESTADOS_CONEXION), default="desconocido")
    last_check: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    ubicacion: Mapped[Optional[Ubicacion]] = relationship()
    volumenes: Mapped[list["Volumen"]] = relationship(
        back_populates="host_origen", cascade="all, delete-orphan"
    )


class Volumen(Base):
    """Un volumen de un host (p. ej. volume1). El RAID se define aquí."""

    __tablename__ = "volumenes"
    __table_args__ = (UniqueConstraint("host_origen_id", "nombre", name="uq_volumen"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    host_origen_id: Mapped[int] = mapped_column(ForeignKey("hosts_origen.id"), nullable=False)
    nombre: Mapped[str] = mapped_column(String(128), nullable=False)  # "volume1" o punto de montaje "/var/www"
    dispositivo: Mapped[Optional[str]] = mapped_column(String(128))   # dispositivo del montaje (p. ej. /dev/md4)
    proteccion: Mapped[str] = mapped_column(Enum(*PROTECCIONES), default="single")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    host_origen: Mapped[HostOrigen] = relationship(back_populates="volumenes")
    origenes: Mapped[list["Origen"]] = relationship(
        back_populates="volumen", cascade="all, delete-orphan"
    )


class Origen(Base):
    """Un origen de copia (carpeta compartida o bundle) dentro de un volumen."""

    __tablename__ = "origenes"
    __table_args__ = (UniqueConstraint("volumen_id", "ruta", name="uq_origen"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    volumen_id: Mapped[int] = mapped_column(ForeignKey("volumenes.id"), nullable=False)
    nombre: Mapped[str] = mapped_column(String(255), nullable=False)  # "Configuración", "web"
    tipo: Mapped[str] = mapped_column(Enum(*TIPOS_ORIGEN), default="carpeta")
    ruta: Mapped[str] = mapped_column(String(512), nullable=False)   # "/volume1/web"
    tamano_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)  # caché del último tamaño
    last_size_check: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)
    estado: Mapped[str] = mapped_column(Enum(*ESTADOS_ORIGEN), default="activo")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    volumen: Mapped[Volumen] = relationship(back_populates="origenes")
    tareas: Mapped[list["Tarea"]] = relationship(
        back_populates="origen", cascade="all, delete-orphan"
    )
    historicos: Mapped[list["HistoricoTamano"]] = relationship(
        back_populates="origen", cascade="all, delete-orphan",
        order_by="HistoricoTamano.timestamp.desc(), HistoricoTamano.id.desc()",
    )


class HistoricoTamano(Base):
    """Serie temporal del tamaño de un origen (para análisis de evolución)."""

    __tablename__ = "historico_tamano"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    origen_id: Mapped[int] = mapped_column(ForeignKey("origenes.id"), nullable=False)
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)

    origen: Mapped[Origen] = relationship(back_populates="historicos")


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
        UniqueConstraint("origen_id", "destino_id", "tipo", name="uq_tarea"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    origen_id: Mapped[int] = mapped_column(ForeignKey("origenes.id"), nullable=False)
    destino_id: Mapped[int] = mapped_column(ForeignKey("destinos.id"), nullable=False)
    tipo: Mapped[str] = mapped_column(Enum(*TIPOS_TAREA), default="espejo")
    cron: Mapped[str] = mapped_column(String(128), default="0 2 * * *")  # programación estilo cron
    comando_rsync: Mapped[Optional[str]] = mapped_column(Text)            # override editable
    rsync_extra: Mapped[Optional[str]] = mapped_column(Text)              # flags extra de usuario
    retencion_dias: Mapped[int] = mapped_column(Integer, default=7)       # conservar snapshots N días
    estado: Mapped[str] = mapped_column(Enum(*ESTADOS_TAREA), default="esperando")
    porcentaje: Mapped[int] = mapped_column(Integer, default=0)
    run_now: Mapped[bool] = mapped_column(Boolean, default=False)         # bandera "ejecutar ya"
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)  # bandera "cancelar copia"
    activa: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)
    next_run_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)
    ssh_keypair_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ssh_keypairs.id"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    origen: Mapped[Origen] = relationship(back_populates="tareas")
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
