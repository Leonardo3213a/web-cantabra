"""
WEB-Cántabra - Datos FICTICIOS de demostración para los reportes (Sprint 4)

Crea pedidos inventados (con fechas de los últimos 30 días) para que los reportes
muestren información realista. NO modifica el stock ni los productos.

Uso (desde la carpeta Proyecto, con el entorno listo):
    python datos_demo.py                  # crea 60 pedidos ficticios
    python datos_demo.py --cantidad 100   # crea otra cantidad
    python datos_demo.py --borrar         # elimina SOLO los pedidos ficticios

Los pedidos ficticios se reconocen porque su número de operación empieza con DEMO
y su dirección empieza con [DEMO]. Tus pedidos reales nunca se tocan.
"""
import argparse
import os
import random
import sqlite3
import sys
from datetime import datetime, timedelta

FORMATO = "%Y-%m-%d %H:%M:%S"
RUTA_BD = os.environ.get("WEBCANTABRA_DB",
                         os.path.join(os.path.dirname(os.path.abspath(__file__)), "webcantabra.db"))

# Cómo terminan los pedidos ficticios (escenario, peso)
ESCENARIOS = [("entregado", 62), ("entregado_con_rechazo", 8), ("enviado", 7), ("en_preparacion", 4),
              ("pago_validado", 4), ("por_validar", 4), ("pago_rechazado", 3), ("cancelado", 8)]

DIRECCIONES = ["Av. Los Olivos 123, Lima", "Jr. Cusco 456, Ica", "Calle Las Flores 789, Arequipa",
               "Av. Grau 321, Piura", "Jr. Huancavelica 654, Huancayo", "Av. El Sol 987, Cusco",
               "Calle Lima 159, Trujillo", "Av. Bolognesi 753, Tacna"]
TRANSPORTISTAS = ["Olva Courier", "Shalom Empresarial", "Serpost", "Reparto propio"]
METODOS = ["Transferencia bancaria", "Yape", "Plin", "Tarjeta de crédito/débito"]
COMENTARIOS = {5: ["Todo llegó perfecto", "Muy rápido, gracias", "Excelente atención", None, None],
               4: ["Buen servicio", "Llegó bien, solo tardó un poco", None],
               3: ["Regular, esperaba más rapidez", None],
               2: ["Demoró más de lo esperado"],
               1: ["No me gustó la demora"]}


def _fmt(fecha):
    return fecha.strftime(FORMATO)


def _validar_base(db):
    columnas = {f[1] for f in db.execute("PRAGMA table_info(pedidos)")}
    if "calificacion" not in columnas:
        raise SystemExit("La base de datos aún no está actualizada.\n"
                         "Ejecuta primero 'python app.py' una vez, ciérralo (Ctrl+C) y vuelve a intentar.")


def hay_datos_demo(db):
    return db.execute("SELECT COUNT(*) FROM pedidos WHERE referencia_pago LIKE 'DEMO%' "
                      "AND direccion_envio LIKE '[DEMO]%'").fetchone()[0] > 0


def _camino(rnd, escenario):
    """Lista de pasos (estado, rol que lo realiza, nota, horas desde el paso anterior)."""
    def validacion():
        # La mayoría se valida en pocas horas; algunos casos tardan más
        return rnd.uniform(12, 30) if rnd.random() < 0.08 else rnd.triangular(0.3, 9, 2.0)

    pasos = [("por_validar", "cliente", "Pedido recibido (datos de demostración)", 0.0)]
    if escenario in ("entregado_con_rechazo", "pago_rechazado"):
        pasos.append(("pago_rechazado", "vendedor", "Operación no encontrada en el banco", validacion()))
        if escenario == "pago_rechazado":
            return pasos
        pasos.append(("por_validar", "cliente", "Pago alternativo registrado", rnd.uniform(1, 6)))
    if escenario == "cancelado":
        pasos.append(("cancelado", rnd.choice(["cliente", "vendedor"]), "Cancelado (demostración)", rnd.uniform(1, 10)))
        return pasos
    if escenario == "por_validar":
        return pasos

    pasos.append(("pago_validado", "vendedor", "Pago verificado correctamente", validacion()))
    if escenario == "pago_validado":
        return pasos
    pasos.append(("en_preparacion", "almacen", "Preparación iniciada por el almacén", rnd.uniform(0.5, 4)))
    if escenario == "en_preparacion":
        return pasos
    pasos.append(("enviado", "almacen", "Envío registrado", rnd.uniform(2, 20)))
    if escenario == "enviado":
        return pasos
    pasos.append(("entregado", rnd.choice(["cliente", "cliente", "almacen"]),
                  "Entrega confirmada", rnd.uniform(24, 96)))
    return pasos


def generar(ruta=RUTA_BD, cantidad=60, semilla=2026, ahora=None):
    """Crea pedidos ficticios. Devuelve cuántos creó."""
    if not 1 <= cantidad <= 500:
        raise ValueError("La cantidad debe estar entre 1 y 500.")
    rnd = random.Random(semilla)
    ahora = (ahora or datetime.now()).replace(microsecond=0)

    db = sqlite3.connect(ruta)
    db.row_factory = sqlite3.Row
    try:
        _validar_base(db)
        if hay_datos_demo(db):
            raise ValueError("Ya existen datos de demostración. Ejecuta primero: python datos_demo.py --borrar")

        usuarios = {rol: [f["id"] for f in db.execute("SELECT id FROM usuarios WHERE rol = ?", (rol,))]
                    for rol in ("cliente", "vendedor", "almacen")}
        productos = db.execute("SELECT id, codigo, nombre, precio FROM productos").fetchall()
        if not all(usuarios.values()) or not productos:
            raise ValueError("Faltan usuarios o productos. Ejecuta python app.py una vez antes.")

        escenarios, pesos = zip(*ESCENARIOS)
        for i in range(cantidad):
            escenario = rnd.choices(escenarios, weights=pesos)[0]
            pasos = _camino(rnd, escenario)
            duracion = sum(p[3] for p in pasos)
            # Los pedidos terminados empiezan hace entre 7 y 30 días; los demás son recientes
            if escenario.startswith("entregado"):
                holgura = rnd.uniform(0, max(0.0, 30 * 24 - duracion - 7 * 24))
                inicio = ahora - timedelta(hours=duracion + 7 * 24 + holgura)
            elif escenario in ("cancelado", "pago_rechazado"):
                inicio = ahora - timedelta(hours=duracion + rnd.uniform(1, 25 * 24))
            else:
                inicio = ahora - timedelta(hours=duracion + rnd.uniform(0.5, 6))

            cliente = rnd.choice(usuarios["cliente"])
            lineas = []
            for p in rnd.sample(list(productos), rnd.randint(1, 3)):
                lineas.append((p, rnd.randint(1, 3)))
            total = round(sum(p["precio"] * c for p, c in lineas), 2)

            # Momento de cada estado
            momento, instante = {}, inicio
            historial = []
            for estado, rol, nota, horas in pasos:
                instante = instante + timedelta(hours=horas)
                momento[estado] = instante
                historial.append((estado, rnd.choice(usuarios[rol]), nota, _fmt(instante)))

            estado_final = pasos[-1][0]
            transportista = guia = fecha_envio = fecha_entrega = None
            if "enviado" in momento:
                transportista, guia = rnd.choice(TRANSPORTISTAS), f"DEMO-{rnd.randint(10000, 99999)}"
                fecha_envio = _fmt(momento["enviado"])
            if "entregado" in momento:
                fecha_entrega = _fmt(momento["entregado"])

            calificacion = comentario = fecha_calificacion = None
            if estado_final == "entregado" and rnd.random() < 0.75:
                calificacion = rnd.choices([5, 4, 3, 2, 1], weights=[50, 30, 12, 5, 3])[0]
                comentario = rnd.choice(COMENTARIOS[calificacion])
                fecha_calificacion = _fmt(momento["entregado"] + timedelta(hours=rnd.uniform(1, 48)))

            cur = db.execute(
                "INSERT INTO pedidos (cliente_id, fecha, estado, total, metodo_pago, referencia_pago, "
                "motivo_rechazo, direccion_envio, transportista, guia_envio, fecha_envio, fecha_entrega, "
                "calificacion, comentario, fecha_calificacion) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cliente, _fmt(inicio), estado_final, total, rnd.choice(METODOS), f"DEMO{i + 1:06d}",
                 "Operación no encontrada en el banco" if estado_final == "pago_rechazado" else None,
                 "[DEMO] " + rnd.choice(DIRECCIONES), transportista, guia, fecha_envio, fecha_entrega,
                 calificacion, comentario, fecha_calificacion))
            pedido_id = cur.lastrowid
            for p, cantidad_linea in lineas:
                db.execute("INSERT INTO detalle_pedido (pedido_id, producto_id, codigo_producto, "
                           "nombre_producto, cantidad, precio_unitario) VALUES (?,?,?,?,?,?)",
                           (pedido_id, p["id"], p["codigo"], p["nombre"], cantidad_linea, p["precio"]))
            for estado, usuario_id, nota, fecha in historial:
                db.execute("INSERT INTO historial_pedido (pedido_id, estado, usuario_id, nota, fecha) "
                           "VALUES (?,?,?,?,?)", (pedido_id, estado, usuario_id, nota, fecha))
        db.commit()
        return cantidad
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def borrar(ruta=RUTA_BD):
    """Elimina SOLO los pedidos ficticios. Devuelve cuántos eliminó."""
    db = sqlite3.connect(ruta)
    try:
        _validar_base(db)
        ids = [f[0] for f in db.execute(
            "SELECT id FROM pedidos WHERE referencia_pago LIKE 'DEMO%' AND direccion_envio LIKE '[DEMO]%'")]
        for tabla in ("historial_pedido", "detalle_pedido", "movimientos_stock"):
            db.executemany(f"DELETE FROM {tabla} WHERE pedido_id = ?", [(i,) for i in ids])
        db.executemany("DELETE FROM pedidos WHERE id = ?", [(i,) for i in ids])
        db.commit()
        return len(ids)
    finally:
        db.close()


if __name__ == "__main__":
    analizador = argparse.ArgumentParser(description="Datos ficticios de demostración para WEB-Cántabra")
    analizador.add_argument("--cantidad", type=int, default=60, help="cuántos pedidos crear (1 a 500)")
    analizador.add_argument("--borrar", action="store_true", help="eliminar los pedidos ficticios")
    argumentos = analizador.parse_args()
    try:
        if argumentos.borrar:
            print(f"Pedidos ficticios eliminados: {borrar()}")
        else:
            print(f"Pedidos ficticios creados: {generar(cantidad=argumentos.cantidad)}")
            print("Abre http://127.0.0.1:5000/reportes para ver los indicadores.")
    except ValueError as error:
        sys.exit(str(error))
