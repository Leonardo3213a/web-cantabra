"""
WEB-Cántabra - Sistema de procesamiento de órdenes de compra
SPRINT 1: Login por roles + Catálogo de productos
SPRINT 2: Carrito, creación de pedidos y validación de pago
SPRINT 3: Preparación, envío y entrega + control de stock (inventario)
SPRINT 4: Reportes de eficiencia, calificación del cliente y exportación a CSV
SPRINT 5: Campañas semestrales, seguridad de cuenta, comprobantes y avisos

Medidas de seguridad incluidas:
  - Consultas SQL parametrizadas (contra inyección SQL)
  - Escape automático de HTML en las plantillas (contra XSS)
  - Token CSRF en todos los formularios POST
  - Contraseñas cifradas (hash con sal) y mensaje de error genérico en el login
  - Bloqueo temporal tras varios intentos fallidos (contra fuerza bruta)
  - Cookies de sesión HttpOnly + SameSite, y sesión que expira por inactividad
  - Cabeceras de seguridad (CSP, X-Frame-Options, etc.)
  - Control de acceso por rol en el servidor
  - Validación de datos de entrada (tipos, rangos y longitudes)
  - Páginas de error propias, sin mostrar detalles internos
  - Registro de eventos de seguridad en seguridad.log
"""
import hmac
import smtplib
from email.message import EmailMessage
from io import BytesIO
from pathlib import Path
import logging
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timedelta
from contextlib import contextmanager
from functools import wraps

from flask import (Flask, render_template, request, redirect,
                   url_for, session, flash, g, abort, Response, send_from_directory)
from werkzeug.security import generate_password_hash, check_password_hash
try:
    from PIL import Image, UnidentifiedImageError
except ModuleNotFoundError as exc:
    if exc.name == "PIL":
        raise SystemExit(
            "Falta Pillow (módulo PIL). Ejecuta primero: python iniciar.py "
            "(o iniciar_windows.bat). Para ejecución manual usa: "
            "python -m pip install -r requirements.txt"
        ) from None
    raise

import reportes as modulo_reportes

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.environ.get("WEBCANTABRA_DB", os.path.join(BASE_DIR, "webcantabra.db"))


def cargar_clave_secreta():
    """La clave secreta NO va escrita en el código: se toma del entorno o se genera sola."""
    desde_entorno = os.environ.get("SECRET_KEY")
    if desde_entorno:
        return desde_entorno
    ruta = os.path.join(BASE_DIR, "secret.key")
    if not os.path.exists(ruta):
        with open(ruta, "w") as f:
            f.write(secrets.token_hex(32))
    with open(ruta) as f:
        return f.read().strip()


app = Flask(__name__)
app.secret_key = cargar_clave_secreta()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,        # el JavaScript no puede leer la cookie
    SESSION_COOKIE_SAMESITE="Lax",       # la cookie no viaja en peticiones de otros sitios
    SESSION_COOKIE_SECURE=os.environ.get("HTTPS") == "1",  # activar al publicar con HTTPS
    PERMANENT_SESSION_LIFETIME=timedelta(minutes=30),      # expira por inactividad
    MAX_CONTENT_LENGTH=3 * 1024 * 1024,  # formulario + comprobante (máx. 2 MiB)
    TEMPLATES_AUTO_RELOAD=True,
)

# Registro de eventos de seguridad
log_seguridad = logging.getLogger("seguridad")
log_seguridad.setLevel(logging.INFO)
if not log_seguridad.handlers:
    _h = logging.FileHandler(os.path.join(BASE_DIR, "seguridad.log"), encoding="utf-8")
    _h.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
    log_seguridad.addHandler(_h)


# ---------------------------------------------------------------
# BASE DE DATOS
# ---------------------------------------------------------------
def get_db():
    """Abre la conexión a la base de datos (una por petición)."""
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def cerrar_db(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Crea las tablas y carga datos ficticios si la base está vacía."""
    db = sqlite3.connect(DB_PATH)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT UNIQUE NOT NULL,
            nombre  TEXT NOT NULL,
            clave   TEXT NOT NULL,
            rol     TEXT NOT NULL CHECK (rol IN ('cliente','vendedor','almacen')),
            email   TEXT
        );
        CREATE TABLE IF NOT EXISTS productos (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo    TEXT UNIQUE NOT NULL,
            nombre    TEXT NOT NULL,
            categoria TEXT NOT NULL,
            proveedor TEXT NOT NULL,
            precio    REAL NOT NULL CHECK (precio >= 0),
            stock     INTEGER NOT NULL CHECK (stock >= 0)
        );
        CREATE TABLE IF NOT EXISTS pedidos (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente_id      INTEGER NOT NULL REFERENCES usuarios(id),
            fecha           TEXT NOT NULL,
            estado          TEXT NOT NULL,
            total           REAL NOT NULL CHECK (total >= 0),
            metodo_pago     TEXT NOT NULL,
            referencia_pago TEXT NOT NULL,
            motivo_rechazo  TEXT,
            direccion_envio TEXT,
            transportista   TEXT,
            guia_envio      TEXT,
            fecha_envio     TEXT,
            fecha_entrega   TEXT,
            calificacion    INTEGER,
            comentario      TEXT,
            fecha_calificacion TEXT,
            campana_id INTEGER REFERENCES campanas(id),
            comprobante_pago TEXT
        );
        CREATE TABLE IF NOT EXISTS detalle_pedido (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            pedido_id       INTEGER NOT NULL REFERENCES pedidos(id),
            producto_id     INTEGER NOT NULL REFERENCES productos(id),
            codigo_producto TEXT NOT NULL,
            nombre_producto TEXT NOT NULL,
            cantidad        INTEGER NOT NULL CHECK (cantidad > 0),
            precio_unitario REAL NOT NULL CHECK (precio_unitario >= 0)
        );
        CREATE TABLE IF NOT EXISTS historial_pedido (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            pedido_id  INTEGER NOT NULL REFERENCES pedidos(id),
            estado     TEXT NOT NULL,
            usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
            nota       TEXT,
            fecha      TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS campanas (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            anio        INTEGER NOT NULL CHECK (anio BETWEEN 2020 AND 2100),
            semestre    INTEGER NOT NULL CHECK (semestre IN (1,2)),
            nombre      TEXT NOT NULL,
            descripcion TEXT NOT NULL DEFAULT '',
            publicada   INTEGER NOT NULL DEFAULT 0 CHECK (publicada IN (0,1)),
            UNIQUE (anio, semestre)
        );
        CREATE TABLE IF NOT EXISTS campana_productos (
            campana_id INTEGER NOT NULL REFERENCES campanas(id) ON DELETE CASCADE,
            producto_id INTEGER NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
            PRIMARY KEY (campana_id, producto_id)
        );
        CREATE TABLE IF NOT EXISTS notificaciones (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
            texto      TEXT NOT NULL,
            pedido_id  INTEGER REFERENCES pedidos(id),
            fecha      TEXT NOT NULL,
            leida      INTEGER NOT NULL DEFAULT 0 CHECK (leida IN (0,1))
        );
        CREATE TABLE IF NOT EXISTS movimientos_stock (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            producto_id INTEGER NOT NULL REFERENCES productos(id),
            tipo        TEXT NOT NULL,
            cantidad    INTEGER NOT NULL,
            pedido_id   INTEGER REFERENCES pedidos(id),
            usuario_id  INTEGER NOT NULL REFERENCES usuarios(id),
            nota        TEXT,
            fecha       TEXT NOT NULL
        );
    """)

    # Actualiza bases de datos creadas en el Sprint 2 (agrega las columnas nuevas sin perder datos)
    columnas = {fila[1] for fila in db.execute("PRAGMA table_info(pedidos)")}
    nuevas = {"direccion_envio": "TEXT", "transportista": "TEXT", "guia_envio": "TEXT",
              "fecha_envio": "TEXT", "fecha_entrega": "TEXT", "calificacion": "INTEGER",
              "comentario": "TEXT", "fecha_calificacion": "TEXT",
              "campana_id": "INTEGER REFERENCES campanas(id)", "comprobante_pago": "TEXT"}
    for nombre, tipo in nuevas.items():
        if nombre not in columnas:
            db.execute(f"ALTER TABLE pedidos ADD COLUMN {nombre} {tipo}")

    # Migración compatible: las instalaciones anteriores no tenían correo electrónico.
    cols_usuarios = {fila[1] for fila in db.execute("PRAGMA table_info(usuarios)")}
    if "email" not in cols_usuarios:
        db.execute("ALTER TABLE usuarios ADD COLUMN email TEXT")

    usuarios = [
        ("cliente1",  "Carlos Mendoza", "cliente"),
        ("cliente2",  "Ana Torres",     "cliente"),
        ("vendedor1", "Lucía Paredes",  "vendedor"),
        ("almacen1",  "Jorge Salinas",  "almacen"),
    ]
    for usuario, nombre, rol in usuarios:
        existe = db.execute("SELECT 1 FROM usuarios WHERE usuario = ?", (usuario,)).fetchone()
        if not existe:
            db.execute(
                "INSERT INTO usuarios (usuario, nombre, clave, rol) VALUES (?,?,?,?)",
                (usuario, nombre, generate_password_hash("1234"), rol))

    if db.execute("SELECT COUNT(*) FROM productos").fetchone()[0] == 0:
        productos = [
            ("TEC-001", "Auriculares Bluetooth",   "Tecnología", "TecnoSur S.A.",   89.90, 40),
            ("TEC-002", "Teclado mecánico",        "Tecnología", "TecnoSur S.A.",  159.00, 25),
            ("TEC-003", "Mouse inalámbrico",       "Tecnología", "TecnoSur S.A.",   45.50, 60),
            ("TEC-004", "Monitor 24 pulgadas",     "Tecnología", "VisionTech",     599.00, 12),
            ("OFI-001", "Silla ergonómica",        "Oficina",    "OficinaPlus",    389.00,  8),
            ("OFI-002", "Escritorio de madera",    "Oficina",    "OficinaPlus",    450.00,  6),
            ("OFI-003", "Lámpara LED de escritorio","Oficina",   "OficinaPlus",     65.00, 30),
            ("HOG-001", "Mochila impermeable",     "Hogar",      "HogarNorte",     120.00, 35),
            ("HOG-002", "Termo de acero 1L",       "Hogar",      "HogarNorte",      55.00,  4),
            ("HOG-003", "Set de organizadores",    "Hogar",      "HogarNorte",      38.00, 50),
        ]
        db.executemany(
            "INSERT INTO productos (codigo, nombre, categoria, proveedor, precio, stock) "
            "VALUES (?,?,?,?,?,?)", productos)

    # Primera actualización: todos los productos existentes pasan al catálogo
    # semestral vigente. No se eliminan ni duplican productos ni pedidos previos.
    hoy = datetime.now()
    semestre = 1 if hoy.month <= 6 else 2
    campana = db.execute("SELECT id FROM campanas WHERE anio=? AND semestre=?",
                         (hoy.year, semestre)).fetchone()
    if db.execute("SELECT COUNT(*) FROM campanas").fetchone()[0] == 0:
        db.execute("INSERT INTO campanas (anio, semestre, nombre, descripcion, publicada) "
                   "VALUES (?,?,?,?,1)",
                   (hoy.year, semestre, f"Catálogo {hoy.year} - Semestre {'I' if semestre == 1 else 'II'}",
                    "Edición semestral inicial de WEB-Cántabra."))
        campana = db.execute("SELECT id FROM campanas WHERE anio=? AND semestre=?",
                             (hoy.year, semestre)).fetchone()
        db.execute("INSERT OR IGNORE INTO campana_productos (campana_id, producto_id) "
                   "SELECT ?, id FROM productos", (campana["id"] if isinstance(campana, sqlite3.Row) else campana[0],))
    db.commit()
    db.close()


# ---------------------------------------------------------------
# SEGURIDAD GENERAL: CSRF, cabeceras y errores
# ---------------------------------------------------------------
def generar_csrf():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


@app.context_processor
def inyectar_comunes():
    unidades = sum(leer_carrito().values()) if session.get("rol") == "cliente" else 0
    n_avisos = 0
    if session.get("usuario_id"):
        n_avisos = get_db().execute("SELECT COUNT(*) FROM notificaciones WHERE usuario_id=? "
                                   "AND leida=0", (session["usuario_id"],)).fetchone()[0]
    return {"csrf_token": generar_csrf, "items_carrito": unidades,
            "avisos_pendientes": n_avisos}


@app.before_request
def verificar_csrf():
    """Toda petición POST debe traer el token secreto de la sesión."""
    if request.method == "POST":
        esperado = session.get("csrf", "")
        recibido = request.form.get("csrf_token", "")
        if not esperado or not hmac.compare_digest(esperado, recibido):
            log_seguridad.warning("CSRF rechazado | ip=%s | ruta=%s", request.remote_addr, request.path)
            abort(400)


@app.after_request
def cabeceras_seguridad(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "style-src 'self' https://cdn.jsdelivr.net; "
        "script-src 'self' https://cdn.jsdelivr.net; "
        "img-src 'self' data:; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'self'")
    resp.headers["Cache-Control"] = "no-store"   # no guardar páginas privadas en caché
    return resp


MENSAJES_ERROR = {
    400: "Solicitud no válida o sesión expirada. Vuelve a cargar la página e inténtalo de nuevo.",
    404: "La página que buscas no existe.",
    405: "Acción no permitida.",
    413: "El envío es demasiado grande.",
    500: "Ocurrió un error interno. Inténtalo más tarde.",
}


def pagina_error(error):
    codigo = getattr(error, "code", 500)
    if codigo == 500:
        app.logger.exception("Error interno")
    return render_template("error.html", codigo=codigo,
                           mensaje=MENSAJES_ERROR.get(codigo, "Ocurrió un error.")), codigo


for _codigo in (400, 404, 405, 413, 500):
    app.register_error_handler(_codigo, pagina_error)


# ---------------------------------------------------------------
# CONTROL DE ACCESO (login y roles)
# ---------------------------------------------------------------
def login_requerido(vista):
    @wraps(vista)
    def envoltura(*args, **kwargs):
        if "usuario_id" not in session:
            flash("Debes iniciar sesión primero.", "warning")
            return redirect(url_for("login"))
        return vista(*args, **kwargs)
    return envoltura


def rol_requerido(*roles):
    def decorador(vista):
        @wraps(vista)
        @login_requerido
        def envoltura(*args, **kwargs):
            if session.get("rol") not in roles:
                log_seguridad.warning("Acceso denegado | usuario_id=%s | rol=%s | ruta=%s",
                                      session.get("usuario_id"), session.get("rol"), request.path)
                flash("No tienes permiso para entrar a esa sección.", "danger")
                return redirect(url_for("catalogo"))
            return vista(*args, **kwargs)
        return envoltura
    return decorador


# ---------------------------------------------------------------
# CAMPAÑAS: DOS EDICIONES POR AÑO (I: ENE-JUN, II: JUL-DIC)
# ---------------------------------------------------------------
def campana_predeterminada(db):
    hoy = datetime.now()
    semestre = 1 if hoy.month <= 6 else 2
    fila = db.execute("SELECT * FROM campanas WHERE anio=? AND semestre=? AND publicada=1",
                      (hoy.year, semestre)).fetchone()
    if fila is None:
        fila = db.execute("SELECT * FROM campanas WHERE publicada=1 "
                          "ORDER BY anio DESC, semestre DESC LIMIT 1").fetchone()
    return fila


def campana_por_id(db, id_campana, incluir_borradores=False):
    sql = "SELECT * FROM campanas WHERE id=?"
    if not incluir_borradores:
        sql += " AND publicada=1"
    return db.execute(sql, (id_campana,)).fetchone()


def publicado_producto(db, producto_id, campana_id=None):
    sql = ("SELECT 1 FROM campana_productos cp JOIN campanas c ON cp.campana_id=c.id "
           "WHERE cp.producto_id=? AND c.publicada=1")
    params = [producto_id]
    if campana_id is not None:
        sql += " AND cp.campana_id=?"
        params.append(campana_id)
    return db.execute(sql, params).fetchone() is not None


# ---------------------------------------------------------------
# RUTAS
# ---------------------------------------------------------------
@app.route("/")
def inicio():
    if "usuario_id" in session:
        return redirect(url_for("catalogo"))
    return redirect(url_for("login"))


# Protección contra fuerza bruta: máximo 5 fallos y bloqueo de 5 minutos
MAX_INTENTOS = 5
BLOQUEO_SEGUNDOS = 300
INTENTOS_FALLIDOS = {}   # clave -> {"n": fallos, "hasta": momento de desbloqueo}
CLAVE_FALSA = generate_password_hash("clave-falsa")  # evita diferencias de tiempo si el usuario no existe


def esta_bloqueado(clave):
    dato = INTENTOS_FALLIDOS.get(clave)
    return bool(dato and dato["hasta"] > time.time())


def registrar_fallo(clave):
    dato = INTENTOS_FALLIDOS.setdefault(clave, {"n": 0, "hasta": 0})
    if dato["hasta"] and dato["hasta"] <= time.time():   # el bloqueo anterior ya venció
        dato["n"], dato["hasta"] = 0, 0
    dato["n"] += 1
    if dato["n"] >= MAX_INTENTOS:
        dato["hasta"] = time.time() + BLOQUEO_SEGUNDOS


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        usuario = request.form.get("usuario", "").strip()[:50]
        clave = request.form.get("clave", "")[:128]
        ip = request.remote_addr or "?"
        claves_limite = (f"ip:{ip}", f"user:{usuario.lower()}")

        if any(esta_bloqueado(k) for k in claves_limite):
            log_seguridad.warning("Login bloqueado | usuario=%s | ip=%s", usuario, ip)
            flash("Demasiados intentos fallidos. Espera unos minutos e inténtalo de nuevo.", "danger")
            return render_template("login.html"), 429

        fila = get_db().execute(
            "SELECT * FROM usuarios WHERE usuario = ?", (usuario,)).fetchone()
        valido = check_password_hash(fila["clave"] if fila else CLAVE_FALSA, clave)

        if fila and valido:
            for k in claves_limite:
                INTENTOS_FALLIDOS.pop(k, None)
            session.clear()                      # evita fijación de sesión
            session.permanent = True
            session["usuario_id"] = fila["id"]
            session["nombre"] = fila["nombre"]
            session["rol"] = fila["rol"]
            log_seguridad.info("Login correcto | usuario=%s | ip=%s", usuario, ip)
            flash(f"Bienvenido, {fila['nombre']}.", "success")
            return redirect(url_for("catalogo"))

        for k in claves_limite:
            registrar_fallo(k)
        log_seguridad.warning("Login fallido | usuario=%s | ip=%s", usuario, ip)
        flash("Usuario o contraseña incorrectos.", "danger")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Sesión cerrada correctamente.", "info")
    return redirect(url_for("login"))


@app.route("/catalogo")
@login_requerido
def catalogo():
    busqueda = request.args.get("q", "").strip()[:100]
    categoria = request.args.get("categoria", "").strip()[:50]
    db = get_db()
    vendedor = session.get("rol") == "vendedor"
    campanas = db.execute(
        "SELECT c.*, (SELECT COUNT(*) FROM campana_productos cp WHERE cp.campana_id=c.id) AS n_productos "
        "FROM campanas c " + ("" if vendedor else "WHERE c.publicada=1 ") +
        "ORDER BY anio DESC, semestre DESC").fetchall()
    seleccion = request.args.get("campana", "").strip()
    campana = None
    if seleccion == "todas" and vendedor:
        pass
    elif seleccion.isdigit():
        campana = campana_por_id(db, int(seleccion), vendedor)
    if not campana and not (seleccion == "todas" and vendedor):
        campana = campana_predeterminada(db)
        if not campana and vendedor and campanas:
            campana = campanas[0]

    sql = "SELECT DISTINCT p.* FROM productos p"
    params = []
    if campana:
        sql += " JOIN campana_productos cp ON cp.producto_id=p.id"
        sql += " WHERE cp.campana_id=?"
        params.append(campana["id"])
    elif vendedor and seleccion == "todas":
        sql += " WHERE 1=1"
    else:
        sql += (" JOIN campana_productos cp ON cp.producto_id=p.id "
                "JOIN campanas c ON c.id=cp.campana_id WHERE c.publicada=1")
    if busqueda:
        sql += " AND (p.nombre LIKE ? OR p.codigo LIKE ?)"
        params.extend([f"%{busqueda}%", f"%{busqueda}%"])
    if categoria:
        sql += " AND p.categoria=?"
        params.append(categoria)
    sql += " ORDER BY p.categoria,p.nombre"
    productos = db.execute(sql, params).fetchall()
    categorias = [f["categoria"] for f in db.execute(
        "SELECT DISTINCT categoria FROM productos ORDER BY categoria")]
    return render_template("catalogo.html", productos=productos, categorias=categorias,
                           busqueda=busqueda, categoria_sel=categoria,
                           campanas=campanas, campana=campana,
                           campana_sel="todas" if seleccion == "todas" and vendedor
                           else str(campana["id"]) if campana else "")


@app.route("/campanas")
@rol_requerido("vendedor")
def gestionar_campanas():
    db = get_db()
    campanas = db.execute("SELECT c.*, (SELECT COUNT(*) FROM campana_productos cp "
                          "WHERE cp.campana_id=c.id) AS n_productos FROM campanas c "
                          "ORDER BY anio DESC, semestre DESC").fetchall()
    hoy = datetime.now()
    siguiente_anio = hoy.year if hoy.month <= 6 else hoy.year + 1
    siguiente_semestre = 2 if hoy.month <= 6 else 1
    return render_template("campanas.html", campanas=campanas,
                           anio_sugerido=siguiente_anio, semestre_sugerido=siguiente_semestre)


@app.route("/campanas/nueva", methods=["POST"])
@rol_requerido("vendedor")
def campana_nueva():
    try:
        anio = int(request.form.get("anio", ""))
        semestre = int(request.form.get("semestre", ""))
    except ValueError:
        anio, semestre = 0, 0
    nombre = " ".join(request.form.get("nombre", "").split())
    descripcion = " ".join(request.form.get("descripcion", "").split())
    if not (2020 <= anio <= 2100 and semestre in (1, 2)
            and 3 <= len(nombre) <= 100 and len(descripcion) <= 300):
        flash("Revisa el año, semestre, nombre y descripción de la campaña.", "danger")
        return redirect(url_for("gestionar_campanas"))
    try:
        with transaccion(get_db()):
            get_db().execute("INSERT INTO campanas (anio, semestre, nombre, descripcion, publicada) "
                             "VALUES (?,?,?,?,0)", (anio, semestre, nombre, descripcion))
    except sqlite3.IntegrityError:
        flash("Ya existe una campaña para ese año y semestre.", "danger")
        return redirect(url_for("gestionar_campanas"))
    flash("Campaña creada como borrador; asigna productos y publícala cuando esté lista.", "success")
    return redirect(url_for("gestionar_campanas"))


@app.route("/campanas/<int:campana_id>/publicacion", methods=["POST"])
@rol_requerido("vendedor")
def campana_publicacion(campana_id):
    db = get_db()
    campana = campana_por_id(db, campana_id, True)
    if not campana:
        abort(404)
    publicar = request.form.get("accion") == "publicar"
    if request.form.get("accion") not in ("publicar", "ocultar"):
        abort(400)
    if publicar and not db.execute(
        "SELECT 1 FROM campana_productos WHERE campana_id=? LIMIT 1", (campana_id,)).fetchone():
        flash("Asigna al menos un producto antes de publicar la campaña.", "warning")
        return redirect(url_for("gestionar_campanas"))
    with transaccion(db):
        db.execute("UPDATE campanas SET publicada=? WHERE id=?", (int(publicar), campana_id))
    flash("Campaña publicada." if publicar else "Campaña ocultada del catálogo público.", "success")
    return redirect(url_for("gestionar_campanas"))


@app.route("/campanas/<int:campana_id>/productos", methods=["GET", "POST"])
@rol_requerido("vendedor")
def campana_productos(campana_id):
    db = get_db()
    campana = campana_por_id(db, campana_id, True)
    if not campana:
        abort(404)
    if request.method == "POST":
        try:
            producto_id = int(request.form.get("producto_id", ""))
        except ValueError:
            abort(400)
        accion = request.form.get("accion")
        if accion not in ("agregar", "quitar"):
            abort(400)
        if not db.execute("SELECT 1 FROM productos WHERE id=?", (producto_id,)).fetchone():
            abort(404)
        with transaccion(db):
            if accion == "agregar":
                db.execute("INSERT OR IGNORE INTO campana_productos VALUES (?,?)",
                           (campana_id, producto_id))
            else:
                db.execute("DELETE FROM campana_productos WHERE campana_id=? AND producto_id=?",
                           (campana_id, producto_id))
        flash("Asignación del catálogo actualizada.", "success")
        return redirect(url_for("campana_productos", campana_id=campana_id))
    productos = db.execute(
        "SELECT p.*, CASE WHEN cp.producto_id IS NULL THEN 0 ELSE 1 END AS asignado "
        "FROM productos p LEFT JOIN campana_productos cp ON cp.producto_id=p.id "
        "AND cp.campana_id=? ORDER BY p.categoria, p.nombre", (campana_id,)).fetchall()
    return render_template("campana_productos.html", campana=campana, productos=productos)


def leer_formulario_producto():
    """Lee y valida los datos del formulario de producto."""
    datos = {
        "codigo": request.form.get("codigo", "").strip().upper(),
        "nombre": request.form.get("nombre", "").strip(),
        "categoria": request.form.get("categoria", "").strip(),
        "proveedor": request.form.get("proveedor", "").strip(),
        "precio": request.form.get("precio", "").strip(),
        "stock": request.form.get("stock", "").strip(),
    }
    if not all(datos.values()):
        return datos, "Completa todos los campos."
    if not re.fullmatch(r"[A-Z0-9\-]{1,20}", datos["codigo"]):
        return datos, "El código solo puede tener letras, números y guiones (máx. 20)."
    if len(datos["nombre"]) > 100 or len(datos["proveedor"]) > 100 or len(datos["categoria"]) > 50:
        return datos, "Algún texto es demasiado largo."
    try:
        datos["precio"] = round(float(datos["precio"]), 2)
        datos["stock"] = int(datos["stock"])
    except ValueError:
        return datos, "Precio y stock deben ser números válidos."
    if datos["precio"] != datos["precio"] or datos["precio"] in (float("inf"), float("-inf")):
        return datos, "El precio no es válido."
    if datos["precio"] < 0 or datos["stock"] < 0:
        return datos, "Precio y stock no pueden ser negativos."
    if datos["precio"] > 100000 or datos["stock"] > 100000:
        return datos, "Precio o stock fuera del rango permitido."
    return datos, None


@app.route("/producto/nuevo", methods=["GET", "POST"])
@rol_requerido("vendedor")
def producto_nuevo():
    datos = {}
    if request.method == "POST":
        datos, error = leer_formulario_producto()
        if error:
            flash(error, "danger")
        else:
            try:
                db = get_db()
                with transaccion(db):
                    cur = db.execute(
                        "INSERT INTO productos (codigo, nombre, categoria, proveedor, precio, stock) "
                        "VALUES (:codigo, :nombre, :categoria, :proveedor, :precio, :stock)", datos)
                    campana = campana_predeterminada(db)
                    if campana:
                        db.execute("INSERT OR IGNORE INTO campana_productos VALUES (?,?)",
                                   (campana["id"], cur.lastrowid))
                    if datos["stock"] > 0:
                        registrar_movimiento(db, cur.lastrowid, "ingreso", datos["stock"],
                                             session["usuario_id"], nota="Stock inicial del producto")
                flash("Producto registrado correctamente.", "success")
                return redirect(url_for("catalogo"))
            except sqlite3.IntegrityError:
                flash("Ya existe un producto con ese código.", "danger")
    return render_template("producto_form.html", datos=datos, editando=False)


@app.route("/producto/<int:producto_id>/editar", methods=["GET", "POST"])
@rol_requerido("vendedor")
def producto_editar(producto_id):
    db = get_db()
    producto = db.execute("SELECT * FROM productos WHERE id = ?", (producto_id,)).fetchone()
    if producto is None:
        flash("El producto no existe.", "danger")
        return redirect(url_for("catalogo"))

    datos = dict(producto)
    if request.method == "POST":
        datos, error = leer_formulario_producto()
        if error:
            flash(error, "danger")
        else:
            try:
                datos["id"] = producto_id
                with transaccion(db):
                    stock_antes = db.execute("SELECT stock FROM productos WHERE id = ?",
                                             (producto_id,)).fetchone()["stock"]
                    db.execute(
                        "UPDATE productos SET codigo=:codigo, nombre=:nombre, categoria=:categoria, "
                        "proveedor=:proveedor, precio=:precio, stock=:stock WHERE id=:id", datos)
                    if datos["stock"] != stock_antes:
                        registrar_movimiento(db, producto_id, "ajuste", datos["stock"] - stock_antes,
                                             session["usuario_id"], nota="Ajuste manual al editar el producto")
                flash("Producto actualizado correctamente.", "success")
                return redirect(url_for("catalogo"))
            except sqlite3.IntegrityError:
                flash("Ya existe un producto con ese código.", "danger")
    return render_template("producto_form.html", datos=datos, editando=True)


# ---------------------------------------------------------------
# SPRINT 2: CARRITO, PEDIDOS Y VALIDACIÓN DE PAGO
# ---------------------------------------------------------------
class ErrorNegocio(Exception):
    """Error de una regla de negocio: su mensaje se le muestra al usuario."""


# Estados del pedido: código -> (texto que se muestra, color)
ESTADOS = {
    "por_validar":    ("Pago por validar", "warning"),
    "pago_rechazado": ("Pago rechazado",   "danger"),
    "pago_validado":  ("Pago validado",    "success"),
    "en_preparacion": ("En preparación",   "info"),      # Sprint 3
    "enviado":        ("Enviado",          "primary"),   # Sprint 3
    "entregado":      ("Entregado",        "dark"),      # Sprint 3
    "cancelado":      ("Cancelado",        "secondary"),
}

# Máquina de estados: desde cada estado solo se puede pasar a los indicados
TRANSICIONES = {
    "por_validar":    {"pago_validado", "pago_rechazado", "cancelado"},
    "pago_rechazado": {"por_validar", "cancelado"},
    "pago_validado":  {"en_preparacion"},
    "en_preparacion": {"enviado"},
    "enviado":        {"entregado"},
    "entregado":      set(),
    "cancelado":      set(),
}

METODOS_PAGO = ["Transferencia bancaria", "Yape", "Plin", "Tarjeta de crédito/débito"]
TRANSPORTISTAS = ["Olva Courier", "Shalom Empresarial", "Serpost", "Reparto propio"]
UMBRAL_STOCK_BAJO = modulo_reportes.UMBRAL_STOCK_BAJO
MAX_STOCK = 100000
MAX_LINEAS_CARRITO = 30
MAX_UNIDADES_POR_PRODUCTO = 1000
MAX_COMPROBANTE_BYTES = 2 * 1024 * 1024
COMPROBANTES_DIR = Path(os.environ.get("WEBCANTABRA_COMPROBANTES",
                                        os.path.join(BASE_DIR, "comprobantes_privados")))


def leer_comprobante():
    """Comprobante opcional, imagen real (no se confía en extensión ni MIME declarado)."""
    archivo = request.files.get("comprobante")
    if not archivo or not archivo.filename:
        return None
    contenido = archivo.stream.read(MAX_COMPROBANTE_BYTES + 1)
    if not contenido:
        return None
    if len(contenido) > MAX_COMPROBANTE_BYTES:
        raise ErrorNegocio("El comprobante no debe superar 2 MB.")
    if contenido.startswith(b"\x89PNG\r\n\x1a\n"):
        extension = "png"
    elif contenido.startswith(b"\xff\xd8\xff") and contenido.endswith(b"\xff\xd9"):
        extension = "jpg"
    elif contenido[:4] == b"RIFF" and contenido[8:12] == b"WEBP":
        extension = "webp"
    else:
        raise ErrorNegocio("El comprobante debe ser una imagen PNG, JPG o WEBP válida.")
    formatos = {"png": "PNG", "jpg": "JPEG", "webp": "WEBP"}
    try:
        with Image.open(BytesIO(contenido)) as imagen:
            if imagen.format != formatos[extension] or imagen.width * imagen.height > 12_000_000:
                raise ErrorNegocio("La imagen es inválida o su resolución es demasiado grande.")
            imagen.verify()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise ErrorNegocio("El comprobante contiene una imagen corrupta o incompatible.") from None
    return contenido, extension


def guardar_comprobante(imagen):
    if not imagen:
        return None
    contenido, extension = imagen
    COMPROBANTES_DIR.mkdir(parents=True, exist_ok=True)
    nombre = secrets.token_hex(16) + "." + extension
    ruta = COMPROBANTES_DIR / nombre
    with open(ruta, "xb") as archivo:
        archivo.write(contenido)
    return nombre


def eliminar_comprobante(nombre):
    if nombre and re.fullmatch(r"[0-9a-f]{32}\.(?:png|jpg|webp)", nombre):
        try:
            (COMPROBANTES_DIR / nombre).unlink(missing_ok=True)
        except OSError:
            app.logger.warning("No se pudo eliminar comprobante en desuso")


def registrar_notificacion(db, usuario_id, texto, pedido_id=None):
    db.execute("INSERT INTO notificaciones (usuario_id, texto, pedido_id, fecha) "
               "VALUES (?,?,?,?)", (usuario_id, texto[:300], pedido_id, ahora()))


def avisar_vendedores(db, texto, pedido_id):
    for fila in db.execute("SELECT id FROM usuarios WHERE rol='vendedor'"):
        registrar_notificacion(db, fila["id"], texto, pedido_id)


def email_avisos(usuario_ids, asunto, mensaje):
    """Correo transaccional opcional: sin SMTP configurado no intenta enviar."""
    servidor = os.environ.get("SMTP_HOST", "").strip()
    remitente = os.environ.get("SMTP_FROM", "").strip()
    if not servidor or not remitente:
        return
    ids = sorted(set(usuario_ids))
    if not ids:
        return
    filas = get_db().execute(
        "SELECT email FROM usuarios WHERE id IN (" + ",".join("?" for _ in ids) + ") "
        "AND email IS NOT NULL", ids).fetchall()
    destinos = [f["email"] for f in filas if f["email"]]
    if not destinos:
        return
    try:
        puerto = int(os.environ.get("SMTP_PORT", "587"))
        with smtplib.SMTP(servidor, puerto, timeout=5) as smtp:
            if os.environ.get("SMTP_TLS", "1") == "1":
                smtp.starttls()
            if os.environ.get("SMTP_USER"):
                smtp.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
            for destino in destinos:
                carta = EmailMessage()
                carta["From"] = remitente
                carta["To"] = destino
                carta["Subject"] = asunto
                carta.set_content(mensaje)
                smtp.send_message(carta)
    except (OSError, smtplib.SMTPException, ValueError) as error:
        app.logger.warning("No se pudo enviar notificación por correo: %s", type(error).__name__)


@app.route("/mi-cuenta", methods=["GET", "POST"])
@login_requerido
def mi_cuenta():
    db = get_db()
    usuario_id = session["usuario_id"]
    if request.method == "POST":
        accion = request.form.get("accion")
        if accion == "email":
            correo = request.form.get("email", "").strip().lower()
            if correo and (len(correo) > 254 or not re.fullmatch(
                r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+", correo)):
                flash("Ingresa un correo electrónico válido o deja el campo vacío.", "danger")
            else:
                with transaccion(db):
                    db.execute("UPDATE usuarios SET email=? WHERE id=?", (correo or None, usuario_id))
                flash("Correo de contacto actualizado.", "success")
                return redirect(url_for("mi_cuenta"))
        elif accion == "clave":
            anterior = request.form.get("clave_actual", "")[:128]
            nueva = request.form.get("clave_nueva", "")
            confirmar = request.form.get("clave_confirmar", "")
            fila = db.execute("SELECT clave FROM usuarios WHERE id=?", (usuario_id,)).fetchone()
            if not check_password_hash(fila["clave"], anterior):
                flash("La contraseña actual no coincide.", "danger")
            elif nueva != confirmar:
                flash("La nueva contraseña y su confirmación no coinciden.", "danger")
            elif (not 10 <= len(nueva) <= 128 or not re.search(r"[a-z]", nueva)
                  or not re.search(r"[A-Z]", nueva) or not re.search(r"[0-9]", nueva)
                  or nueva == anterior):
                flash("Usa una contraseña nueva de 10 a 128 caracteres con mayúscula, minúscula y número.", "danger")
            else:
                with transaccion(db):
                    db.execute("UPDATE usuarios SET clave=? WHERE id=?",
                               (generate_password_hash(nueva), usuario_id))
                log_seguridad.info("Contraseña cambiada | usuario_id=%s", usuario_id)
                session.clear()  # Obliga a iniciar sesión con la nueva clave.
                flash("Contraseña actualizada. Inicia sesión nuevamente.", "success")
                return redirect(url_for("login"))
        else:
            abort(400)
    fila = db.execute("SELECT usuario, nombre, rol, email FROM usuarios WHERE id=?",
                      (usuario_id,)).fetchone()
    return render_template("mi_cuenta.html", usuario=fila,
                           smtp_disponible=bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_FROM")))


@app.route("/notificaciones")
@login_requerido
def notificaciones():
    filas = get_db().execute("SELECT * FROM notificaciones WHERE usuario_id=? "
                             "ORDER BY id DESC LIMIT 100", (session["usuario_id"],)).fetchall()
    return render_template("notificaciones.html", notificaciones=filas)


@app.route("/notificaciones/leidas", methods=["POST"])
@login_requerido
def notificaciones_leidas():
    with transaccion(get_db()):
        get_db().execute("UPDATE notificaciones SET leida=1 WHERE usuario_id=?",
                         (session["usuario_id"],))
    flash("Notificaciones marcadas como leídas.", "info")
    return redirect(url_for("notificaciones"))


@app.route("/pedido/<int:pedido_id>/comprobante")
@login_requerido
def pedido_comprobante(pedido_id):
    pedido = obtener_pedido_autorizado(get_db(), pedido_id)
    nombre = pedido["comprobante_pago"]
    if not nombre or not re.fullmatch(r"[0-9a-f]{32}\.(?:png|jpg|webp)", nombre):
        abort(404)
    if not (COMPROBANTES_DIR / nombre).is_file():
        abort(404)
    respuesta = send_from_directory(str(COMPROBANTES_DIR), nombre, as_attachment=False)
    respuesta.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return respuesta



@app.template_filter("estado_texto")
def filtro_estado_texto(codigo):
    return ESTADOS.get(codigo, (codigo, "secondary"))[0]


@app.template_filter("estado_color")
def filtro_estado_color(codigo):
    return ESTADOS.get(codigo, (codigo, "secondary"))[1]


@app.template_filter("codigo_pedido")
def filtro_codigo_pedido(pedido_id):
    return f"PED-{int(pedido_id):05d}"


@app.template_filter("duracion")
def filtro_duracion(horas):
    """Convierte horas en un texto fácil de leer (minutos, horas o días)."""
    return modulo_reportes.texto_duracion(horas)


@app.template_filter("porcentaje")
def filtro_porcentaje(valor):
    return "—" if valor is None else f"{valor:.1f} %"


@app.template_filter("soles")
def filtro_soles(valor):
    return f"S/ {float(valor):,.2f}"


def ahora():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def registrar_movimiento(db, producto_id, tipo, cantidad, usuario_id, pedido_id=None, nota=None):
    """Kardex: cada entrada o salida de stock queda registrada (quién, cuándo y por qué)."""
    db.execute(
        "INSERT INTO movimientos_stock (producto_id, tipo, cantidad, pedido_id, usuario_id, nota, fecha) "
        "VALUES (?,?,?,?,?,?,?)", (producto_id, tipo, cantidad, pedido_id, usuario_id, nota, ahora()))


@contextmanager
def transaccion(db):
    """Todo o nada: si algo falla a la mitad, se deshacen todos los cambios.
    BEGIN IMMEDIATE evita que dos operaciones modifiquen la base al mismo tiempo."""
    db.execute("BEGIN IMMEDIATE")
    try:
        yield
        db.commit()
    except Exception:
        db.rollback()
        raise


# ---------- Carrito (se guarda en la sesión del cliente) ----------
def leer_carrito():
    crudo = session.get("carrito", {})
    carrito = {}
    try:
        for clave, cantidad in crudo.items():
            carrito[int(clave)] = int(cantidad)
    except (ValueError, TypeError, AttributeError):
        return {}
    return carrito


def guardar_carrito(carrito):
    session["carrito"] = {str(k): v for k, v in carrito.items()}


def detalle_carrito(db):
    """Devuelve las líneas del carrito con los precios ACTUALES de la base de datos."""
    items, total = [], 0.0
    for producto_id, cantidad in leer_carrito().items():
        p = db.execute("SELECT * FROM productos WHERE id = ?", (producto_id,)).fetchone()
        if p is None:
            continue
        subtotal = round(p["precio"] * cantidad, 2)
        items.append({"producto": p, "cantidad": cantidad, "subtotal": subtotal,
                      "hay_stock": p["stock"] >= cantidad})
        total += subtotal
    return items, round(total, 2)


def mostrar_carrito(datos=None, estado_http=200):
    items, total = detalle_carrito(get_db())
    return render_template("carrito.html", items=items, total=total,
                           metodos=METODOS_PAGO, datos=datos or {}), estado_http


@app.route("/carrito")
@rol_requerido("cliente")
def carrito():
    return mostrar_carrito()


@app.route("/carrito/agregar/<int:producto_id>", methods=["POST"])
@rol_requerido("cliente")
def carrito_agregar(producto_id):
    p = get_db().execute("SELECT * FROM productos WHERE id = ?", (producto_id,)).fetchone()
    if p is None:
        flash("El producto no existe.", "danger")
        return redirect(url_for("catalogo"))
    try:
        cantidad = int(request.form.get("cantidad", "1"))
    except ValueError:
        flash("La cantidad debe ser un número entero.", "danger")
        return redirect(url_for("catalogo"))
    if cantidad < 1 or cantidad > MAX_UNIDADES_POR_PRODUCTO:
        flash(f"La cantidad debe estar entre 1 y {MAX_UNIDADES_POR_PRODUCTO}.", "danger")
        return redirect(url_for("catalogo"))

    db = get_db()
    campana_id = request.form.get("campana_id", "").strip()
    if campana_id.isdigit():
        campana = campana_por_id(db, int(campana_id))
    else:
        campana = campana_predeterminada(db)
    if not campana or not publicado_producto(db, producto_id, campana["id"]):
        flash("Este producto no está disponible en la campaña seleccionada.", "warning")
        return redirect(url_for("catalogo"))
    carrito_actual = leer_carrito()
    campana_carrito = session.get("campana_carrito_id")
    if carrito_actual and campana_carrito and int(campana_carrito) != campana["id"]:
        flash("Finaliza o vacía el carrito de la otra campaña antes de cambiar.", "warning")
        return redirect(url_for("catalogo"))
    if producto_id not in carrito_actual and len(carrito_actual) >= MAX_LINEAS_CARRITO:
        flash("Tu carrito alcanzó el máximo de productos distintos.", "danger")
        return redirect(url_for("catalogo"))
    nueva_cantidad = carrito_actual.get(producto_id, 0) + cantidad
    if nueva_cantidad > p["stock"]:
        flash(f"Solo hay {p['stock']} unidad(es) disponible(s) de «{p['nombre']}».", "warning")
        return redirect(url_for("catalogo"))

    carrito_actual[producto_id] = nueva_cantidad
    guardar_carrito(carrito_actual)
    session["campana_carrito_id"] = campana["id"]
    flash(f"«{p['nombre']}» agregado al carrito.", "success")
    return redirect(url_for("catalogo"))


@app.route("/carrito/quitar/<int:producto_id>", methods=["POST"])
@rol_requerido("cliente")
def carrito_quitar(producto_id):
    carrito_actual = leer_carrito()
    carrito_actual.pop(producto_id, None)
    guardar_carrito(carrito_actual)
    if not carrito_actual:
        session.pop("campana_carrito_id", None)
    flash("Producto quitado del carrito.", "info")
    return redirect(url_for("carrito"))


@app.route("/carrito/vaciar", methods=["POST"])
@rol_requerido("cliente")
def carrito_vaciar():
    session.pop("carrito", None)
    session.pop("campana_carrito_id", None)
    flash("Carrito vaciado.", "info")
    return redirect(url_for("carrito"))


# ---------- Pedidos ----------
def leer_pago_formulario():
    metodo = request.form.get("metodo_pago", "").strip()
    referencia = request.form.get("referencia_pago", "").strip().upper()
    if metodo not in METODOS_PAGO:
        return metodo, referencia, "Selecciona un método de pago válido."
    if not re.fullmatch(r"[A-Z0-9]{6,20}", referencia):
        return metodo, referencia, "El número de operación debe tener entre 6 y 20 letras o números."
    return metodo, referencia, None


def leer_direccion():
    direccion = " ".join(request.form.get("direccion_envio", "").split())   # quita espacios repetidos
    if not (10 <= len(direccion) <= 200):
        return direccion, "Ingresa una dirección de entrega completa (entre 10 y 200 caracteres)."
    return direccion, None


def referencia_en_uso(db, referencia, excepto_pedido=0):
    """Un mismo número de operación no puede usarse en dos pedidos activos."""
    fila = db.execute(
        "SELECT 1 FROM pedidos WHERE referencia_pago = ? AND id != ? "
        "AND estado NOT IN ('cancelado', 'pago_rechazado')", (referencia, excepto_pedido)).fetchone()
    return fila is not None


def registrar_historial(db, pedido_id, estado, usuario_id, nota=None):
    db.execute(
        "INSERT INTO historial_pedido (pedido_id, estado, usuario_id, nota, fecha) VALUES (?,?,?,?,?)",
        (pedido_id, estado, usuario_id, nota, ahora()))


def estado_actual(db, pedido_id):
    fila = db.execute("SELECT estado FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()
    if fila is None:
        raise ErrorNegocio("El pedido no existe.")
    return fila["estado"]


def cambiar_estado(db, pedido_id, estado_origen, estado_nuevo, usuario_id, nota=None):
    """Único punto por donde cambia el estado de un pedido: valida la transición y deja historial."""
    if estado_nuevo not in TRANSICIONES.get(estado_origen, set()):
        raise ErrorNegocio(
            f"No se puede pasar de «{filtro_estado_texto(estado_origen)}» "
            f"a «{filtro_estado_texto(estado_nuevo)}».")
    cur = db.execute("UPDATE pedidos SET estado = ? WHERE id = ? AND estado = ?",
                     (estado_nuevo, pedido_id, estado_origen))
    if cur.rowcount != 1:
        raise ErrorNegocio("El pedido cambió de estado mientras lo procesabas. Recarga la página.")
    registrar_historial(db, pedido_id, estado_nuevo, usuario_id, nota)


def devolver_stock(db, pedido_id, usuario_id):
    for d in db.execute("SELECT producto_id, cantidad FROM detalle_pedido WHERE pedido_id = ?",
                        (pedido_id,)).fetchall():
        db.execute("UPDATE productos SET stock = stock + ? WHERE id = ?",
                   (d["cantidad"], d["producto_id"]))
        registrar_movimiento(db, d["producto_id"], "devolucion", d["cantidad"], usuario_id,
                             pedido_id, f"Cancelación del pedido {filtro_codigo_pedido(pedido_id)}")


def obtener_pedido_autorizado(db, pedido_id):
    """Un cliente solo puede ver SUS pedidos. Si intenta ver otro, responde 404
    (así no se revela qué números de pedido existen)."""
    pedido = db.execute(
        "SELECT p.*, u.nombre AS cliente_nombre, c.nombre AS campana_nombre FROM pedidos p "
        "JOIN usuarios u ON u.id = p.cliente_id "
        "LEFT JOIN campanas c ON c.id=p.campana_id WHERE p.id = ?", (pedido_id,)).fetchone()
    if pedido is None:
        abort(404)
    if session.get("rol") == "cliente" and pedido["cliente_id"] != session.get("usuario_id"):
        log_seguridad.warning("Intento de ver pedido ajeno | usuario_id=%s | pedido=%s",
                              session.get("usuario_id"), pedido_id)
        abort(404)
    return pedido


@app.route("/pedido/confirmar", methods=["POST"])
@rol_requerido("cliente")
def pedido_confirmar():
    db = get_db()
    carrito_actual = leer_carrito()
    if not carrito_actual:
        flash("Tu carrito está vacío.", "warning")
        return redirect(url_for("carrito"))

    metodo, referencia, error = leer_pago_formulario()
    direccion, error_direccion = leer_direccion()
    try:
        imagen = leer_comprobante()
    except ErrorNegocio as e:
        imagen = None
        error = str(e)
    if error or error_direccion:
        flash(error or error_direccion, "danger")
        return mostrar_carrito({"metodo_pago": metodo, "referencia_pago": referencia,
                                "direccion_envio": direccion})

    archivo_nuevo = None
    campana_id = session.get("campana_carrito_id")
    campana = campana_por_id(db, int(campana_id)) if str(campana_id).isdigit() else campana_predeterminada(db)
    if not campana:
        flash("No hay una campaña publicada para este carrito.", "danger")
        return redirect(url_for("carrito"))
    try:
        with transaccion(db):
            if referencia_en_uso(db, referencia):
                raise ErrorNegocio("Ese número de operación ya fue registrado en otro pedido.")

            lineas, total = [], 0.0
            for producto_id, cantidad in carrito_actual.items():
                p = db.execute("SELECT * FROM productos WHERE id = ?", (producto_id,)).fetchone()
                if p is None:
                    raise ErrorNegocio("Un producto de tu carrito ya no existe.")
                if not publicado_producto(db, producto_id, campana["id"]):
                    raise ErrorNegocio("Un producto del carrito ya no pertenece a la campaña publicada.")
                # Descuento atómico: solo se descuenta si todavía hay stock suficiente
                cur = db.execute(
                    "UPDATE productos SET stock = stock - ? WHERE id = ? AND stock >= ?",
                    (cantidad, producto_id, cantidad))
                if cur.rowcount != 1:
                    raise ErrorNegocio(f"Stock insuficiente para «{p['nombre']}».")
                total += round(p["precio"] * cantidad, 2)
                lineas.append((p, cantidad))

            cur = db.execute(
                "INSERT INTO pedidos (cliente_id, fecha, estado, total, metodo_pago, referencia_pago, "
                "direccion_envio, campana_id) VALUES (?,?,?,?,?,?,?,?)",
                (session["usuario_id"], ahora(), "por_validar", round(total, 2), metodo, referencia,
                 direccion, campana["id"]))
            pedido_id = cur.lastrowid
            for p, cantidad in lineas:
                db.execute(
                    "INSERT INTO detalle_pedido (pedido_id, producto_id, codigo_producto, "
                    "nombre_producto, cantidad, precio_unitario) VALUES (?,?,?,?,?,?)",
                    (pedido_id, p["id"], p["codigo"], p["nombre"], cantidad, p["precio"]))
                registrar_movimiento(db, p["id"], "salida_pedido", -cantidad, session["usuario_id"],
                                     pedido_id, f"Pedido {filtro_codigo_pedido(pedido_id)}")
            archivo_nuevo = guardar_comprobante(imagen)
            if archivo_nuevo:
                db.execute("UPDATE pedidos SET comprobante_pago=? WHERE id=?",
                           (archivo_nuevo, pedido_id))
            registrar_historial(db, pedido_id, "por_validar", session["usuario_id"],
                                f"Pedido recibido. Pago declarado: {metodo} (operación {referencia})")
            avisar_vendedores(db, f"Pedido {filtro_codigo_pedido(pedido_id)} pendiente de validar.", pedido_id)
    except (ErrorNegocio, OSError) as e:
        eliminar_comprobante(archivo_nuevo)
        flash(str(e) if isinstance(e, ErrorNegocio) else "No se pudo guardar el comprobante.", "danger")
        return redirect(url_for("carrito"))

    session.pop("carrito", None)
    session.pop("campana_carrito_id", None)
    emails_vendedores = [f["id"] for f in db.execute("SELECT id FROM usuarios WHERE rol='vendedor'")]
    email_avisos(emails_vendedores, "WEB-Cántabra: nuevo pedido",
                 f"El pedido {filtro_codigo_pedido(pedido_id)} está pendiente de validar.")
    log_seguridad.info("Pedido creado | pedido=%s | cliente=%s | total=%.2f",
                       pedido_id, session["usuario_id"], total)
    flash("Pedido registrado. Un vendedor validará tu pago.", "success")
    return redirect(url_for("pedido_detalle", pedido_id=pedido_id))


@app.route("/mis-pedidos")
@rol_requerido("cliente")
def mis_pedidos():
    filas = get_db().execute(
        "SELECT p.*, u.nombre AS cliente_nombre FROM pedidos p "
        "JOIN usuarios u ON u.id = p.cliente_id WHERE p.cliente_id = ? ORDER BY p.id DESC",
        (session["usuario_id"],)).fetchall()
    return render_template("pedidos_lista.html", titulo="Mis pedidos", pedidos=filas,
                           mostrar_cliente=False, estados=ESTADOS, filtro=None)


@app.route("/pedidos")
@rol_requerido("vendedor", "almacen")
def pedidos():
    filtro = request.args.get("estado")
    if filtro is None:
        filtro = "por_validar" if session["rol"] == "vendedor" else "pago_validado"
    if filtro != "todos" and filtro not in ESTADOS:
        filtro = "todos"

    sql = ("SELECT p.*, u.nombre AS cliente_nombre FROM pedidos p "
           "JOIN usuarios u ON u.id = p.cliente_id")
    params = []
    if filtro != "todos":
        sql += " WHERE p.estado = ?"
        params.append(filtro)
    sql += " ORDER BY p.id DESC"
    db = get_db()
    filas = db.execute(sql, params).fetchall()
    resumen = {f["estado"]: f["n"] for f in db.execute(
        "SELECT estado, COUNT(*) AS n FROM pedidos GROUP BY estado")}
    return render_template("pedidos_lista.html", titulo="Pedidos", pedidos=filas,
                           mostrar_cliente=True, estados=ESTADOS, filtro=filtro, resumen=resumen)


@app.route("/pedido/<int:pedido_id>")
@login_requerido
def pedido_detalle(pedido_id):
    db = get_db()
    pedido = obtener_pedido_autorizado(db, pedido_id)
    items = db.execute("SELECT * FROM detalle_pedido WHERE pedido_id = ? ORDER BY id",
                       (pedido_id,)).fetchall()
    historial = db.execute(
        "SELECT h.*, u.nombre AS usuario_nombre, u.rol AS usuario_rol FROM historial_pedido h "
        "JOIN usuarios u ON u.id = h.usuario_id WHERE h.pedido_id = ? ORDER BY h.id",
        (pedido_id,)).fetchall()
    return render_template("pedido_detalle.html", pedido=pedido, items=items,
                           historial=historial, metodos=METODOS_PAGO,
                           transportistas=TRANSPORTISTAS, etapas=linea_de_tiempo(pedido["estado"]))


@app.route("/pedido/<int:pedido_id>/validar-pago", methods=["POST"])
@rol_requerido("vendedor")
def pedido_validar_pago(pedido_id):
    db = get_db()
    obtener_pedido_autorizado(db, pedido_id)
    accion = request.form.get("accion", "")
    motivo = request.form.get("motivo", "").strip()

    if accion not in ("aprobar", "rechazar"):
        flash("Acción no válida.", "danger")
        return redirect(url_for("pedido_detalle", pedido_id=pedido_id))
    if accion == "rechazar" and not (5 <= len(motivo) <= 200):
        flash("Indica el motivo del rechazo (entre 5 y 200 caracteres).", "danger")
        return redirect(url_for("pedido_detalle", pedido_id=pedido_id))

    try:
        with transaccion(db):
            actual = estado_actual(db, pedido_id)
            if accion == "aprobar":
                cambiar_estado(db, pedido_id, actual, "pago_validado", session["usuario_id"],
                               "Pago verificado correctamente")
                db.execute("UPDATE pedidos SET motivo_rechazo = NULL WHERE id = ?", (pedido_id,))
            else:
                cambiar_estado(db, pedido_id, actual, "pago_rechazado", session["usuario_id"], motivo)
                db.execute("UPDATE pedidos SET motivo_rechazo = ? WHERE id = ?", (motivo, pedido_id))
            cliente_id = db.execute("SELECT cliente_id FROM pedidos WHERE id=?",
                                    (pedido_id,)).fetchone()["cliente_id"]
            registrar_notificacion(db, cliente_id,
                f"Pago {'aprobado' if accion == 'aprobar' else 'rechazado'} para {filtro_codigo_pedido(pedido_id)}.",
                pedido_id)
    except ErrorNegocio as e:
        flash(str(e), "danger")
        return redirect(url_for("pedido_detalle", pedido_id=pedido_id))

    log_seguridad.info("Pago %s | pedido=%s | vendedor=%s", "aprobado" if accion == "aprobar" else "rechazado",
                       pedido_id, session["usuario_id"])
    email_avisos([cliente_id], "WEB-Cántabra: estado del pago",
                 f"El pago de {filtro_codigo_pedido(pedido_id)} fue "
                 f"{'aprobado' if accion == 'aprobar' else 'rechazado'}. "
                 "Revisa el detalle de tu pedido en el sistema.")
    flash("Pago validado." if accion == "aprobar" else "Pago rechazado. El cliente fue notificado en su pedido.",
          "success" if accion == "aprobar" else "warning")
    return redirect(url_for("pedido_detalle", pedido_id=pedido_id))


@app.route("/pedido/<int:pedido_id>/reintentar-pago", methods=["POST"])
@rol_requerido("cliente")
def pedido_reintentar_pago(pedido_id):
    db = get_db()
    obtener_pedido_autorizado(db, pedido_id)
    metodo, referencia, error = leer_pago_formulario()
    try:
        imagen = leer_comprobante()
    except ErrorNegocio as e:
        imagen = None
        error = str(e)
    if error:
        flash(error, "danger")
        return redirect(url_for("pedido_detalle", pedido_id=pedido_id))

    anterior_comprobante = None
    nuevo_comprobante = None
    try:
        with transaccion(db):
            actual = estado_actual(db, pedido_id)
            if referencia_en_uso(db, referencia, excepto_pedido=pedido_id):
                raise ErrorNegocio("Ese número de operación ya fue registrado en otro pedido.")
            cambiar_estado(db, pedido_id, actual, "por_validar", session["usuario_id"],
                           f"Pago alternativo registrado: {metodo} (operación {referencia})")
            db.execute("UPDATE pedidos SET metodo_pago = ?, referencia_pago = ?, motivo_rechazo = NULL "
                       "WHERE id = ?", (metodo, referencia, pedido_id))
            if imagen:
                anterior_comprobante = db.execute("SELECT comprobante_pago FROM pedidos WHERE id=?",
                                                 (pedido_id,)).fetchone()["comprobante_pago"]
                nuevo_comprobante = guardar_comprobante(imagen)
                db.execute("UPDATE pedidos SET comprobante_pago=? WHERE id=?",
                           (nuevo_comprobante, pedido_id))
            avisar_vendedores(db, f"Nuevo pago del pedido {filtro_codigo_pedido(pedido_id)}.", pedido_id)
    except (ErrorNegocio, OSError) as e:
        eliminar_comprobante(nuevo_comprobante)
        flash(str(e) if isinstance(e, ErrorNegocio) else "No se pudo guardar el comprobante.", "danger")
        return redirect(url_for("pedido_detalle", pedido_id=pedido_id))

    if nuevo_comprobante:
        eliminar_comprobante(anterior_comprobante)
    email_avisos([f["id"] for f in db.execute("SELECT id FROM usuarios WHERE rol='vendedor'")],
                 "WEB-Cántabra: pago actualizado",
                 f"El cliente registró un nuevo pago del pedido {filtro_codigo_pedido(pedido_id)}.")
    flash("Nuevo pago registrado. Un vendedor lo validará.", "success")
    return redirect(url_for("pedido_detalle", pedido_id=pedido_id))


@app.route("/pedido/<int:pedido_id>/cancelar", methods=["POST"])
@rol_requerido("cliente", "vendedor")
def pedido_cancelar(pedido_id):
    db = get_db()
    obtener_pedido_autorizado(db, pedido_id)
    motivo = request.form.get("motivo", "").strip()[:200]
    quien = "el cliente" if session["rol"] == "cliente" else "el vendedor"
    nota = f"Cancelado por {quien}" + (f": {motivo}" if motivo else "")

    try:
        with transaccion(db):
            actual = estado_actual(db, pedido_id)
            cambiar_estado(db, pedido_id, actual, "cancelado", session["usuario_id"], nota)
            devolver_stock(db, pedido_id, session["usuario_id"])   # los productos reservados vuelven al inventario
            cliente_id = db.execute("SELECT cliente_id FROM pedidos WHERE id=?",
                                    (pedido_id,)).fetchone()["cliente_id"]
            registrar_notificacion(db, cliente_id,
                                   f"El pedido {filtro_codigo_pedido(pedido_id)} fue cancelado.", pedido_id)
    except ErrorNegocio as e:
        flash(str(e), "danger")
        return redirect(url_for("pedido_detalle", pedido_id=pedido_id))

    log_seguridad.info("Pedido cancelado | pedido=%s | usuario=%s", pedido_id, session["usuario_id"])
    email_avisos([cliente_id], "WEB-Cántabra: pedido cancelado",
                 f"El pedido {filtro_codigo_pedido(pedido_id)} fue cancelado.")
    flash("Pedido cancelado. El stock fue devuelto al inventario.", "info")
    return redirect(url_for("pedido_detalle", pedido_id=pedido_id))


# ---------------------------------------------------------------
# SPRINT 3: PREPARACIÓN, ENVÍO, ENTREGA Y CONTROL DE STOCK
# ---------------------------------------------------------------
ETAPAS = [("por_validar", "Recibido"), ("pago_validado", "Pago validado"),
          ("en_preparacion", "En preparación"), ("enviado", "Enviado"), ("entregado", "Entregado")]


def linea_de_tiempo(estado):
    """Arma la barra de progreso del pedido: etapas cumplidas, etapa actual y etapas pendientes."""
    codigos = [c for c, _ in ETAPAS]
    posicion = codigos.index(estado) if estado in codigos else (0 if estado == "pago_rechazado" else -1)
    etapas = []
    for i, (codigo, texto) in enumerate(ETAPAS):
        if posicion == -1:                       # pedido cancelado
            clase = "light"
        elif i < posicion:
            clase = "success"
        elif i == posicion:
            clase = "success" if estado == "entregado" else ("danger" if estado == "pago_rechazado" else "primary")
        else:
            clase = "light"
        etapas.append({"texto": texto, "clase": clase})
    return etapas


def avanzar_pedido(pedido_id, estado_nuevo, nota, mensaje_ok, sql_extra=None, params_extra=()):
    """Cambia el pedido de etapa de forma segura (transacción + validación de la máquina de estados)."""
    db = get_db()
    obtener_pedido_autorizado(db, pedido_id)
    try:
        with transaccion(db):
            actual = estado_actual(db, pedido_id)
            cambiar_estado(db, pedido_id, actual, estado_nuevo, session["usuario_id"], nota)
            if sql_extra:
                db.execute(sql_extra, params_extra)
            if estado_nuevo in ("enviado", "entregado"):
                cliente_id = db.execute("SELECT cliente_id FROM pedidos WHERE id=?",
                                        (pedido_id,)).fetchone()["cliente_id"]
                registrar_notificacion(db, cliente_id,
                                       f"Pedido {filtro_codigo_pedido(pedido_id)}: {filtro_estado_texto(estado_nuevo)}.",
                                       pedido_id)
    except ErrorNegocio as e:
        flash(str(e), "danger")
        return redirect(url_for("pedido_detalle", pedido_id=pedido_id))
    log_seguridad.info("Pedido %s -> %s | usuario=%s", pedido_id, estado_nuevo, session["usuario_id"])
    if estado_nuevo in ("enviado", "entregado"):
        email_avisos([cliente_id], "WEB-Cántabra: seguimiento de tu pedido",
                     f"El pedido {filtro_codigo_pedido(pedido_id)} está {filtro_estado_texto(estado_nuevo).lower()}.")
    flash(mensaje_ok, "success")
    return redirect(url_for("pedido_detalle", pedido_id=pedido_id))


@app.route("/pedido/<int:pedido_id>/preparar", methods=["POST"])
@rol_requerido("almacen")
def pedido_preparar(pedido_id):
    return avanzar_pedido(pedido_id, "en_preparacion", "Preparación iniciada por el almacén",
                          "El pedido pasó a preparación.")


@app.route("/pedido/<int:pedido_id>/enviar", methods=["POST"])
@rol_requerido("almacen")
def pedido_enviar(pedido_id):
    obtener_pedido_autorizado(get_db(), pedido_id)
    transportista = request.form.get("transportista", "").strip()
    guia = request.form.get("guia_envio", "").strip().upper()
    if transportista not in TRANSPORTISTAS:
        flash("Selecciona un transportista válido.", "danger")
        return redirect(url_for("pedido_detalle", pedido_id=pedido_id))
    if not re.fullmatch(r"[A-Z0-9\-]{5,25}", guia):
        flash("La guía de envío debe tener entre 5 y 25 letras, números o guiones.", "danger")
        return redirect(url_for("pedido_detalle", pedido_id=pedido_id))
    return avanzar_pedido(
        pedido_id, "enviado", f"Enviado con {transportista} (guía {guia})", "Envío registrado correctamente.",
        "UPDATE pedidos SET transportista = ?, guia_envio = ?, fecha_envio = ? WHERE id = ?",
        (transportista, guia, ahora(), pedido_id))


@app.route("/pedido/<int:pedido_id>/entregar", methods=["POST"])
@rol_requerido("cliente", "almacen")
def pedido_entregar(pedido_id):
    quien = "el cliente" if session["rol"] == "cliente" else "el almacén"
    return avanzar_pedido(
        pedido_id, "entregado", f"Entrega confirmada por {quien}", "Entrega confirmada. ¡Pedido completado!",
        "UPDATE pedidos SET fecha_entrega = ? WHERE id = ?", (ahora(), pedido_id))


@app.route("/stock")
@rol_requerido("almacen", "vendedor")
def inventario():
    db = get_db()
    productos = db.execute("SELECT * FROM productos ORDER BY stock ASC, nombre").fetchall()
    movimientos = db.execute(
        "SELECT m.*, p.nombre AS producto_nombre, p.codigo AS producto_codigo, u.nombre AS usuario_nombre "
        "FROM movimientos_stock m JOIN productos p ON p.id = m.producto_id "
        "JOIN usuarios u ON u.id = m.usuario_id ORDER BY m.id DESC LIMIT 30").fetchall()
    bajos = sum(1 for p in productos if p["stock"] < UMBRAL_STOCK_BAJO)
    return render_template("stock.html", productos=productos, movimientos=movimientos,
                           bajos=bajos, umbral=UMBRAL_STOCK_BAJO, max_stock=MAX_STOCK)


@app.route("/stock/<int:producto_id>/reponer", methods=["POST"])
@rol_requerido("almacen")
def stock_reponer(producto_id):
    try:
        cantidad = int(request.form.get("cantidad", ""))
    except ValueError:
        flash("La cantidad debe ser un número entero.", "danger")
        return redirect(url_for("inventario"))
    if not 1 <= cantidad <= MAX_STOCK:
        flash(f"La cantidad debe estar entre 1 y {MAX_STOCK}.", "danger")
        return redirect(url_for("inventario"))
    nota = request.form.get("nota", "").strip()[:200] or "Reposición de inventario"

    db = get_db()
    try:
        with transaccion(db):
            fila = db.execute("SELECT stock FROM productos WHERE id = ?", (producto_id,)).fetchone()
            if fila is None:
                raise ErrorNegocio("El producto no existe.")
            if fila["stock"] + cantidad > MAX_STOCK:
                raise ErrorNegocio(f"El stock no puede superar las {MAX_STOCK} unidades.")
            db.execute("UPDATE productos SET stock = stock + ? WHERE id = ?", (cantidad, producto_id))
            registrar_movimiento(db, producto_id, "ingreso", cantidad, session["usuario_id"], nota=nota)
    except ErrorNegocio as e:
        flash(str(e), "danger")
        return redirect(url_for("inventario"))
    flash("Stock repuesto correctamente.", "success")
    return redirect(url_for("inventario"))


# ---------------------------------------------------------------
# SPRINT 4: CALIFICACIÓN DEL CLIENTE Y REPORTES DE EFICIENCIA
# ---------------------------------------------------------------
PERIODOS = {"7": 7, "30": 30, "90": 90, "todos": 0}


def leer_periodo():
    """Solo se aceptan periodos de una lista fija (cualquier otro valor se ignora)."""
    clave = request.args.get("dias", "30")
    if clave not in PERIODOS:
        clave = "30"
    return clave, PERIODOS[clave]


@app.route("/pedido/<int:pedido_id>/calificar", methods=["POST"])
@rol_requerido("cliente")
def pedido_calificar(pedido_id):
    db = get_db()
    obtener_pedido_autorizado(db, pedido_id)
    try:
        nota = int(request.form.get("calificacion", ""))
    except ValueError:
        nota = 0
    if not 1 <= nota <= 5:
        flash("Elige una calificación de 1 a 5 estrellas.", "danger")
        return redirect(url_for("pedido_detalle", pedido_id=pedido_id))
    comentario = " ".join(request.form.get("comentario", "").split())[:300]

    try:
        with transaccion(db):
            fila = db.execute("SELECT estado, calificacion FROM pedidos WHERE id = ?",
                              (pedido_id,)).fetchone()
            if fila["estado"] != "entregado":
                raise ErrorNegocio("Solo puedes calificar pedidos que ya fueron entregados.")
            if fila["calificacion"] is not None:
                raise ErrorNegocio("Este pedido ya fue calificado.")
            db.execute("UPDATE pedidos SET calificacion = ?, comentario = ?, fecha_calificacion = ? "
                       "WHERE id = ?", (nota, comentario or None, ahora(), pedido_id))
    except ErrorNegocio as e:
        flash(str(e), "danger")
        return redirect(url_for("pedido_detalle", pedido_id=pedido_id))

    flash("¡Gracias por tu calificación!", "success")
    return redirect(url_for("pedido_detalle", pedido_id=pedido_id))


@app.route("/reportes")
@rol_requerido("vendedor", "almacen")
def reportes_panel():
    clave, dias = leer_periodo()
    datos = modulo_reportes.calcular(get_db(), dias)
    return render_template("reportes.html", r=datos, periodo=clave, estados=ESTADOS)


@app.route("/reportes/reporte.xlsx")
@rol_requerido("vendedor")
def reportes_excel():
    clave, dias = leer_periodo()
    try:
        contenido = modulo_reportes.exportar_excel(
            get_db(), dias, etiquetas={k: v[0] for k, v in ESTADOS.items()})
    except ImportError:
        flash("Falta instalar la librería de Excel. En el terminal escribe: pip install openpyxl", "danger")
        return redirect(url_for("reportes_panel", dias=clave))
    log_seguridad.info("Exportación Excel | usuario=%s | periodo=%s", session["usuario_id"], clave)
    respuesta = Response(contenido, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    respuesta.headers["Content-Disposition"] = f"attachment; filename=reporte_WEB-Cantabra_{clave}.xlsx"
    return respuesta


@app.route("/reportes/pedidos.csv")
@rol_requerido("vendedor")
def reportes_csv():
    clave, dias = leer_periodo()
    contenido = modulo_reportes.exportar_pedidos_csv(get_db(), dias)
    log_seguridad.info("Exportación CSV | usuario=%s | periodo=%s", session["usuario_id"], clave)
    respuesta = Response("\ufeff" + contenido, mimetype="text/csv")   # \ufeff = BOM para que Excel lea tildes
    respuesta.headers["Content-Disposition"] = f"attachment; filename=pedidos_{clave}.csv"
    return respuesta


# ---------------------------------------------------------------
# INICIO DE LA APLICACIÓN
# ---------------------------------------------------------------
init_db()

if __name__ == "__main__":
    # Sin depurador interactivo (es un riesgo de seguridad); solo escucha en tu propio equipo.
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=True)
