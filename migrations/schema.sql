-- Esquema de referencia de Teseo (generado desde app/models.py).
-- El instalador crea estas tablas automaticamente; este fichero es informativo.

CREATE TABLE admins (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	username VARCHAR(64) NOT NULL, 
	password_hash VARCHAR(255) NOT NULL, 
	email VARCHAR(255), 
	created_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id), 
	UNIQUE (username)
);

CREATE TABLE ubicaciones (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	nombre VARCHAR(128) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (nombre)
);

CREATE TABLE ssh_keypairs (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	private_key_cifrada TEXT NOT NULL, 
	public_key TEXT NOT NULL, 
	fingerprint VARCHAR(255), 
	estado VARCHAR(32) NOT NULL, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id)
);

CREATE TABLE hosts_origen (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	nombre VARCHAR(128) NOT NULL, 
	host VARCHAR(255) NOT NULL, 
	puerto INTEGER NOT NULL, 
	usuario VARCHAR(128) NOT NULL, 
	auth_method ENUM('key','password') NOT NULL, 
	secret_cifrado TEXT, 
	host_key TEXT, 
	es_raid ENUM('single','raid1','raid2') NOT NULL, 
	ubicacion_id INTEGER, 
	estado_conexion ENUM('desconocido','conectado','inaccesible','en_uso') NOT NULL, 
	last_check DATETIME, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id), 
	UNIQUE (nombre), 
	FOREIGN KEY(ubicacion_id) REFERENCES ubicaciones (id)
);

CREATE TABLE destinos (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	nombre VARCHAR(128) NOT NULL, 
	host VARCHAR(255) NOT NULL, 
	puerto INTEGER NOT NULL, 
	usuario VARCHAR(128) NOT NULL, 
	auth_method ENUM('key','password') NOT NULL, 
	secret_cifrado TEXT, 
	host_key TEXT, 
	carpeta_base VARCHAR(512) NOT NULL, 
	proteccion ENUM('single','raid1','raid2') NOT NULL, 
	ubicacion_id INTEGER, 
	estado ENUM('desconocido','conectado','inaccesible','en_uso') NOT NULL, 
	espacio_total BIGINT, 
	espacio_backups BIGINT, 
	espacio_libre BIGINT, 
	last_check DATETIME, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id), 
	UNIQUE (nombre), 
	FOREIGN KEY(ubicacion_id) REFERENCES ubicaciones (id)
);

CREATE TABLE tareas (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	host_origen_id INTEGER NOT NULL, 
	destino_id INTEGER NOT NULL, 
	carpeta_origen VARCHAR(512) NOT NULL, 
	tipo ENUM('espejo','incremental') NOT NULL, 
	cron VARCHAR(128) NOT NULL, 
	comando_rsync TEXT, 
	rsync_extra TEXT, 
	retencion INTEGER NOT NULL, 
	estado ENUM('esperando','en_progreso','terminada','fallida') NOT NULL, 
	porcentaje INTEGER NOT NULL, 
	run_now BOOL NOT NULL, 
	activa BOOL NOT NULL, 
	last_run_at DATETIME, 
	next_run_at DATETIME, 
	ssh_keypair_id INTEGER, 
	created_at DATETIME NOT NULL DEFAULT now(), 
	PRIMARY KEY (id), 
	CONSTRAINT uq_tarea UNIQUE (host_origen_id, destino_id, carpeta_origen), 
	FOREIGN KEY(host_origen_id) REFERENCES hosts_origen (id), 
	FOREIGN KEY(destino_id) REFERENCES destinos (id), 
	FOREIGN KEY(ssh_keypair_id) REFERENCES ssh_keypairs (id)
);

CREATE TABLE ejecuciones (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	tarea_id INTEGER NOT NULL, 
	inicio DATETIME NOT NULL DEFAULT now(), 
	fin DATETIME, 
	resultado ENUM('ok','fallo','cancelada'), 
	bytes_transferidos BIGINT, 
	snapshot_path VARCHAR(512), 
	resumen TEXT, 
	error TEXT, 
	PRIMARY KEY (id), 
	FOREIGN KEY(tarea_id) REFERENCES tareas (id)
);
