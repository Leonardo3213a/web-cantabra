"""
Pruebas automáticas del Sprint 4 - Reportes, calificación del cliente y datos de demostración
Ejecutar desde la carpeta Proyecto:
    python -m unittest discover -s tests -v
Usan una base de datos temporal: NO tocan tu webcantabra.db real.
"""
import csv
import io
import itertools
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime

from test_sprint3 import Base3, ENVIO, PAGO, consulta, stock, webapp

import datos_demo
import reportes

AHORA = datetime(2026, 10, 31, 12, 0, 0)
_refs = itertools.count(1)


def uid(usuario):
    return consulta("SELECT id FROM usuarios WHERE usuario = ?", (usuario,))[0][0]


def insertar(estado, fecha, total, historial=(), detalle=(), calificacion=None, cliente="cliente1"):
    """Inserta un pedido con fechas exactas para poder comprobar los cálculos a mano."""
    db = sqlite3.connect(webapp.DB_PATH)
    cur = db.execute(
        "INSERT INTO pedidos (cliente_id, fecha, estado, total, metodo_pago, referencia_pago, "
        "direccion_envio, calificacion) VALUES (?,?,?,?,?,?,?,?)",
        (uid(cliente), fecha, estado, total, "Yape", f"T{next(_refs):08d}", "Calle de prueba 123", calificacion))
    pid = cur.lastrowid
    for estado_h, fecha_h in historial:
        db.execute("INSERT INTO historial_pedido (pedido_id, estado, usuario_id, nota, fecha) VALUES (?,?,?,?,?)",
                   (pid, estado_h, uid("vendedor1"), None, fecha_h))
    for producto_id, cantidad, precio in detalle:
        codigo, nombre = db.execute("SELECT codigo, nombre FROM productos WHERE id = ?", (producto_id,)).fetchone()
        db.execute("INSERT INTO detalle_pedido (pedido_id, producto_id, codigo_producto, nombre_producto, "
                   "cantidad, precio_unitario) VALUES (?,?,?,?,?,?)", (pid, producto_id, codigo, nombre, cantidad, precio))
    db.commit()
    db.close()
    return pid


def calcular(dias=30, ahora=AHORA):
    db = sqlite3.connect(webapp.DB_PATH)
    db.row_factory = sqlite3.Row
    try:
        return reportes.calcular(db, dias, ahora)
    finally:
        db.close()


def datos_principales():
    """Pedidos con tiempos conocidos (ver los cálculos esperados en cada prueba)."""
    insertar("entregado", "2026-10-30 08:00:00", 100.0, [
        ("pago_validado", "2026-10-30 11:00:00"), ("en_preparacion", "2026-10-30 12:00:00"),
        ("enviado", "2026-10-30 16:00:00"), ("entregado", "2026-10-31 10:00:00")],
        [(1, 2, 30.0), (5, 1, 40.0)], calificacion=5)                              # A
    insertar("enviado", "2026-10-30 09:00:00", 50.0, [
        ("pago_validado", "2026-10-30 10:00:00"), ("en_preparacion", "2026-10-30 10:30:00"),
        ("enviado", "2026-10-30 14:00:00")], [(1, 1, 30.0), (8, 1, 20.0)])         # B
    insertar("cancelado", "2026-10-30 10:00:00", 50.0, [("cancelado", "2026-10-30 11:00:00")],
             [(2, 5, 10.0)])                                                        # C
    insertar("pago_rechazado", "2026-10-31 08:00:00", 20.0, [("pago_rechazado", "2026-10-31 09:00:00")])   # D
    insertar("por_validar", "2026-10-29 10:00:00", 10.0)                            # E


class TestCalculoDeIndicadores(Base3):
    def test_sin_datos_no_falla(self):
        r = calcular()
        self.assertEqual(r["total_pedidos"], 0)
        self.assertIsNone(r["tasa_cancelacion"])
        self.assertIsNone(r["ventas"]["ticket"])
        self.assertIsNone(r["tiempos"]["ciclo"])
        self.assertIsNone(r["satisfaccion"]["promedio"])
        self.assertEqual(r["top_productos"], [])
        self.assertTrue(all(c["reduccion"] is None for c in r["comparativo"]))

    def test_conteos_y_ventas(self):
        datos_principales()
        r = calcular()
        self.assertEqual(r["total_pedidos"], 5)
        self.assertEqual(r["por_estado"], {"entregado": 1, "enviado": 1, "cancelado": 1,
                                           "pago_rechazado": 1, "por_validar": 1})
        self.assertEqual(r["ventas"], {"pedidos": 2, "monto": 150.0, "ticket": 75.0})   # cancelados y rechazados no suman

    def test_tasas(self):
        datos_principales()
        r = calcular()
        self.assertEqual(r["tasa_cancelacion"], 20.0)       # 1 de 5
        self.assertEqual(r["tasa_rechazo_pago"], 20.0)      # 1 de 5

    def test_tiempos_promedio(self):
        datos_principales()
        t = calcular()["tiempos"]
        self.assertEqual(t["validacion"], 2.0)       # A: 3 h, B: 1 h
        self.assertEqual(t["preparacion"], 4.5)      # A: 5 h, B: 4 h  (pago validado -> enviado)
        self.assertEqual(t["procesamiento"], 6.5)    # A: 8 h, B: 5 h  (recibido -> enviado)
        self.assertEqual(t["entrega"], 18.0)         # solo A: 16:00 -> 10:00 del día siguiente
        self.assertEqual(t["ciclo"], 26.0)           # solo A: 08:00 -> 10:00 del día siguiente

    def test_comparativo_contra_el_proceso_manual(self):
        datos_principales()
        validacion, procesamiento = calcular()["comparativo"]
        self.assertEqual((validacion["base"], validacion["actual"], validacion["reduccion"]), (24.0, 2.0, 91.7))
        self.assertEqual((procesamiento["base"], procesamiento["actual"], procesamiento["reduccion"]), (72.0, 6.5, 91.0))

    def test_si_el_sistema_fuera_mas_lento_la_reduccion_es_negativa(self):
        insertar("enviado", "2026-10-30 08:00:00", 10.0,
                 [("pago_validado", "2026-10-31 08:00:00"), ("enviado", "2026-10-31 11:00:00")])
        validacion = calcular()["comparativo"][0]
        self.assertEqual(validacion["actual"], 24.0)
        self.assertEqual(validacion["reduccion"], 0.0)
        insertar("enviado", "2026-10-30 08:00:00", 10.0,
                 [("pago_validado", "2026-10-31 20:00:00"), ("enviado", "2026-10-31 21:00:00")])
        self.assertLess(calcular()["comparativo"][0]["reduccion"], 0)

    def test_productos_mas_vendidos_solo_cuentan_ventas_confirmadas(self):
        datos_principales()
        top = calcular()["top_productos"]
        self.assertEqual([(p["codigo"], p["unidades"], p["monto"]) for p in top],
                         [("TEC-001", 3, 90.0), ("OFI-001", 1, 40.0), ("HOG-001", 1, 20.0)])
        self.assertNotIn("TEC-002", [p["codigo"] for p in top])      # el pedido cancelado no cuenta

    def test_ventas_por_categoria(self):
        datos_principales()
        cat = calcular()["por_categoria"]
        self.assertEqual([(c["categoria"], c["monto"]) for c in cat],
                         [("Tecnología", 90.0), ("Oficina", 40.0), ("Hogar", 20.0)])

    def test_filtro_por_periodo(self):
        datos_principales()
        insertar("entregado", "2026-09-01 10:00:00", 1000.0, [
            ("pago_validado", "2026-09-01 11:00:00"), ("enviado", "2026-09-01 20:00:00"),
            ("entregado", "2026-09-03 12:00:00")], [(4, 1, 1000.0)])
        self.assertEqual(calcular(30)["total_pedidos"], 5)
        self.assertEqual(calcular(7)["total_pedidos"], 5)
        self.assertEqual(calcular(90)["total_pedidos"], 6)
        r = calcular(0)                                         # 0 = todo el historial
        self.assertEqual(r["total_pedidos"], 6)
        self.assertEqual(r["ventas"]["monto"], 1150.0)

    def test_satisfaccion(self):
        for nota in (5, 4, 4):
            insertar("entregado", "2026-10-30 08:00:00", 10.0, calificacion=nota)
        insertar("entregado", "2026-10-30 08:00:00", 10.0)             # sin calificar: no cuenta
        s = calcular()["satisfaccion"]
        self.assertEqual(s["n"], 3)
        self.assertEqual(s["promedio"], 4.33)
        self.assertEqual(s["distribucion"], {5: 1, 4: 2, 3: 0, 2: 0, 1: 0})

    def test_alertas(self):
        insertar("por_validar", "2026-10-29 10:00:00", 10.0)                      # 50 h sin validar -> alerta
        insertar("por_validar", "2026-10-31 11:00:00", 10.0)                      # 1 h -> normal
        insertar("en_preparacion", "2026-10-27 10:00:00", 10.0, [("en_preparacion", "2026-10-28 12:00:00")])   # 72 h -> alerta
        insertar("en_preparacion", "2026-10-31 07:00:00", 10.0, [("en_preparacion", "2026-10-31 08:00:00")])   # 4 h -> normal
        a = calcular()["alertas"]
        self.assertEqual(a["pagos_atrasados"], 1)
        self.assertEqual(a["preparacion_atrasada"], 1)
        self.assertEqual(a["stock_bajo"], 3)      # Silla (8), Escritorio (6) y Termo (4) de la carga inicial

    def test_inyeccion_en_el_periodo_no_es_posible(self):
        datos_principales()
        with self.assertRaises(Exception):
            calcular(dias="30; DROP TABLE pedidos")      # tipo inválido: se rechaza, no se ejecuta
        self.assertEqual(consulta("SELECT COUNT(*) FROM pedidos")[0][0], 5)


class TestFormatosDeLaInterfaz(unittest.TestCase):
    def test_duracion(self):
        f = webapp.filtro_duracion
        self.assertEqual(f(None), "—")
        self.assertEqual(f(0.5), "30 min")
        self.assertEqual(f(3.5), "3.5 h")
        self.assertEqual(f(47.9), "47.9 h")
        self.assertEqual(f(72), "3.0 días")

    def test_porcentaje(self):
        self.assertEqual(webapp.filtro_porcentaje(None), "—")
        self.assertEqual(webapp.filtro_porcentaje(12.5), "12.5 %")


class TestExportacionCSV(Base3):
    def leer(self, texto):
        return list(csv.reader(io.StringIO(texto)))

    def test_estructura(self):
        datos_principales()
        db = sqlite3.connect(webapp.DB_PATH); db.row_factory = sqlite3.Row
        filas = self.leer(reportes.exportar_pedidos_csv(db, 30, AHORA)); db.close()
        self.assertEqual(filas[0][:4], ["pedido", "fecha", "cliente", "estado"])
        self.assertEqual(len(filas), 6)                          # encabezado + 5 pedidos
        self.assertEqual(filas[1][0], "PED-00001")
        self.assertEqual(filas[1][4], "100.00")

    def test_neutraliza_formulas_peligrosas(self):
        """Un nombre como =HYPERLINK(...) podría ejecutarse al abrir el CSV en Excel."""
        datos_principales()
        db = sqlite3.connect(webapp.DB_PATH)
        db.execute("UPDATE usuarios SET nombre = ? WHERE usuario = 'cliente1'", ('=HYPERLINK("http://malo.example","clic")',))
        db.commit(); db.row_factory = sqlite3.Row
        filas = self.leer(reportes.exportar_pedidos_csv(db, 30, AHORA)); db.close()
        self.assertTrue(filas[1][2].startswith("'="))
        for fila in filas:
            for celda in fila:
                self.assertNotIn(celda[:1], ("=", "+", "-", "@"), celda)

    def test_neutralizar_celda(self):
        n = reportes.neutralizar_celda
        self.assertEqual(n("=1+1"), "'=1+1")
        self.assertEqual(n("+51999"), "'+51999")
        self.assertEqual(n("-5"), "'-5")
        self.assertEqual(n("@cmd"), "'@cmd")
        self.assertEqual(n("Carlos"), "Carlos")
        self.assertEqual(n(None), "")
        self.assertEqual(n(5), "5")


class TestPaginasDeReportes(Base3):
    def test_permisos(self):
        self.assertIn("No tienes permiso", self.cliente.html("/reportes"))
        for s in (self.vendedor, self.almacen):
            self.assertIn("Reportes de eficiencia", s.html("/reportes"))
        r = webapp.app.test_client().get("/reportes")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.location)

    def test_la_pagina_funciona_sin_datos(self):
        self.assertEqual(self.vendedor.get("/reportes").status_code, 200)
        self.assertIn("Aún no hay ventas", self.vendedor.html("/reportes"))

    def test_periodos_invalidos_se_ignoran(self):
        for dias in ("abc", "30' OR 1=1 --", "-5", "999999", "", "<script>"):
            r = self.vendedor.get(f"/reportes?dias={dias}")
            self.assertEqual(r.status_code, 200, dias)
        for dias in ("7", "30", "90", "todos"):
            self.assertEqual(self.vendedor.get(f"/reportes?dias={dias}").status_code, 200)

    def test_csv_solo_para_el_vendedor(self):
        r = self.vendedor.c.get("/reportes/pedidos.csv")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, "text/csv")
        self.assertIn("attachment", r.headers["Content-Disposition"])
        self.assertEqual(r.headers["Content-Disposition"], "attachment; filename=pedidos_30.csv")
        self.assertTrue(r.get_data(as_text=True).startswith("\ufeffpedido,"))
        for s in (self.cliente, self.almacen):
            self.assertIn("No tienes permiso", s.html("/reportes/pedidos.csv"))
        self.assertEqual(webapp.app.test_client().get("/reportes/pedidos.csv").status_code, 302)

    def test_nombre_de_archivo_no_se_puede_manipular(self):
        r = self.vendedor.c.get('/reportes/pedidos.csv?dias=x"; filename=malo.exe')
        self.assertEqual(r.headers["Content-Disposition"], "attachment; filename=pedidos_30.csv")

    def test_los_botones_de_descarga_solo_los_ve_el_vendedor(self):
        self.assertIn("/reportes/pedidos.csv", self.vendedor.html("/reportes"))
        self.assertNotIn("/reportes/pedidos.csv", self.almacen.html("/reportes"))

    def test_los_reportes_reflejan_un_pedido_real_de_principio_a_fin(self):
        pid = self.enviado()
        self.cliente.post(f"/pedido/{pid}/entregar")
        html = self.vendedor.html("/reportes?dias=7")
        self.assertIn("S/ 338.80", html)                       # 2 x 89.90 + 1 x 159.00
        self.assertIn("Auriculares Bluetooth", html)
        self.assertIn("Mejora frente al proceso manual", html)

    def test_enlaces_de_la_navegacion(self):
        for s in (self.vendedor, self.almacen):
            self.assertIn('href="/reportes"', s.html("/catalogo"))
        self.assertNotIn('href="/reportes"', self.cliente.html("/catalogo"))


class TestCalificacion(Base3):
    def entregado(self):
        pid = self.enviado()
        self.cliente.post(f"/pedido/{pid}/entregar")
        return pid

    def nota(self, pid):
        return consulta("SELECT calificacion, comentario FROM pedidos WHERE id = ?", (pid,))[0]

    def test_cliente_califica_su_pedido_entregado(self):
        pid = self.entregado()
        self.assertIn("Califica tu pedido", self.cliente.html(f"/pedido/{pid}"))
        self.cliente.post(f"/pedido/{pid}/calificar", {"calificacion": "5", "comentario": "Todo perfecto"})
        self.assertEqual(self.nota(pid), (5, "Todo perfecto"))
        html = self.cliente.html(f"/pedido/{pid}")
        self.assertIn("★★★★★", html)
        self.assertNotIn("Califica tu pedido", html)
        self.assertIn("★★★★★", self.vendedor.html(f"/pedido/{pid}"))        # el personal también la ve

    def test_el_comentario_es_opcional(self):
        pid = self.entregado()
        self.cliente.post(f"/pedido/{pid}/calificar", {"calificacion": "3", "comentario": ""})
        self.assertEqual(self.nota(pid), (3, None))

    def test_valores_invalidos(self):
        pid = self.entregado()
        for valor in ("0", "6", "abc", "", "-1", "3.5", "99999999999999999999"):
            self.cliente.post(f"/pedido/{pid}/calificar", {"calificacion": valor})
        self.assertEqual(self.nota(pid), (None, None))

    def test_solo_pedidos_entregados(self):
        pid = self.enviado()
        r = self.cliente.post(f"/pedido/{pid}/calificar", {"calificacion": "5"})
        self.assertIn("Solo puedes calificar", r.get_data(as_text=True))
        self.assertEqual(self.nota(pid), (None, None))
        self.assertNotIn("Califica tu pedido", self.cliente.html(f"/pedido/{pid}"))

    def test_no_se_puede_calificar_dos_veces(self):
        pid = self.entregado()
        self.cliente.post(f"/pedido/{pid}/calificar", {"calificacion": "5"})
        r = self.cliente.post(f"/pedido/{pid}/calificar", {"calificacion": "1", "comentario": "cambié de opinión"})
        self.assertIn("ya fue calificado", r.get_data(as_text=True))
        self.assertEqual(self.nota(pid), (5, None))

    def test_otro_cliente_no_puede_calificar(self):
        pid = self.entregado()
        r = self.cliente2.post(f"/pedido/{pid}/calificar", {"calificacion": "1"}, seguir=False)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.nota(pid), (None, None))

    def test_el_personal_no_califica(self):
        pid = self.entregado()
        for s in (self.vendedor, self.almacen):
            r = s.post(f"/pedido/{pid}/calificar", {"calificacion": "5"})
            self.assertIn("No tienes permiso", r.get_data(as_text=True))
        self.assertEqual(self.nota(pid), (None, None))
        self.assertNotIn("Califica tu pedido", self.vendedor.html(f"/pedido/{pid}"))

    def test_csrf_obligatorio(self):
        pid = self.entregado()
        r = self.cliente.c.post(f"/pedido/{pid}/calificar", data={"calificacion": "5"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.cliente.c.get(f"/pedido/{pid}/calificar").status_code, 405)

    def test_comentario_malicioso_se_escapa(self):
        pid = self.entregado()
        malo = "<script>alert(1)</script>"
        self.cliente.post(f"/pedido/{pid}/calificar", {"calificacion": "4", "comentario": malo})
        for s in (self.cliente, self.vendedor):
            html = s.html(f"/pedido/{pid}")
            self.assertNotIn(malo, html)
            self.assertIn("&lt;script&gt;", html)

    def test_comentario_se_limpia_y_se_recorta(self):
        pid = self.entregado()
        self.cliente.post(f"/pedido/{pid}/calificar", {"calificacion": "4", "comentario": "  hola    mundo  " + "a" * 400})
        guardado = self.nota(pid)[1]
        self.assertTrue(guardado.startswith("hola mundo"))
        self.assertEqual(len(guardado), 300)

    def test_la_satisfaccion_aparece_en_los_reportes(self):
        pid = self.entregado()
        self.cliente.post(f"/pedido/{pid}/calificar", {"calificacion": "5"})
        self.assertIn("5.0 ★", self.vendedor.html("/reportes?dias=7"))


class TestDatosDeDemostracion(Base3):
    def crear(self, cantidad=40, semilla=7):
        return datos_demo.generar(webapp.DB_PATH, cantidad, semilla, AHORA)

    def test_crea_la_cantidad_pedida_y_no_toca_el_stock(self):
        stock_antes = consulta("SELECT SUM(stock) FROM productos")[0][0]
        self.assertEqual(self.crear(40), 40)
        self.assertEqual(consulta("SELECT COUNT(*) FROM pedidos")[0][0], 40)
        self.assertEqual(consulta("SELECT SUM(stock) FROM productos")[0][0], stock_antes)
        self.assertEqual(consulta("SELECT COUNT(*) FROM movimientos_stock")[0][0], 0)
        self.assertEqual(consulta("SELECT COUNT(*) FROM pedidos WHERE direccion_envio NOT LIKE '[DEMO]%'")[0][0], 0)

    def test_las_fechas_son_coherentes(self):
        self.crear(60)
        limite = AHORA.strftime("%Y-%m-%d %H:%M:%S")
        self.assertLessEqual(consulta("SELECT MAX(fecha) FROM historial_pedido")[0][0], limite)
        self.assertLessEqual(consulta("SELECT MAX(fecha) FROM pedidos")[0][0], limite)
        for (pid,) in consulta("SELECT id FROM pedidos"):
            fechas = [f[0] for f in consulta("SELECT fecha FROM historial_pedido WHERE pedido_id=? ORDER BY id", (pid,))]
            self.assertEqual(fechas, sorted(fechas), f"historial desordenado en el pedido {pid}")
            self.assertGreaterEqual(fechas[0], consulta("SELECT fecha FROM pedidos WHERE id=?", (pid,))[0][0])

    def test_todos_los_pedidos_pasan_por_estados_validos(self):
        self.crear(60)
        for (pid, final) in consulta("SELECT id, estado FROM pedidos"):
            historial = [f[0] for f in consulta("SELECT estado FROM historial_pedido WHERE pedido_id=? ORDER BY id", (pid,))]
            self.assertEqual(historial[-1], final)
            for antes, despues in zip(historial, historial[1:]):
                self.assertIn(despues, webapp.TRANSICIONES[antes], f"{antes} -> {despues}")

    def test_los_reportes_con_datos_de_demostracion_son_realistas(self):
        self.crear(80)
        r = calcular(0)
        self.assertEqual(r["total_pedidos"], 80)
        self.assertGreater(r["por_estado"]["entregado"], 30)
        self.assertTrue(1 <= r["tiempos"]["validacion"] <= 12, r["tiempos"])
        self.assertTrue(5 <= r["tiempos"]["procesamiento"] <= 48, r["tiempos"])
        self.assertGreater(r["tiempos"]["ciclo"], r["tiempos"]["procesamiento"])
        self.assertGreater(r["satisfaccion"]["n"], 10)
        self.assertTrue(1 <= r["satisfaccion"]["promedio"] <= 5)
        self.assertTrue(all(c["reduccion"] > 0 for c in r["comparativo"]))

    def test_la_pagina_de_reportes_muestra_los_datos_de_demostracion(self):
        datos_demo.generar(webapp.DB_PATH, 60, 3)        # con la fecha real de hoy
        html = self.vendedor.html("/reportes?dias=30")
        self.assertIn("Mejora frente al proceso manual", html)
        self.assertNotIn("Aún no hay ventas", html)

    def test_es_repetible_con_la_misma_semilla(self):
        self.crear(30, semilla=5)
        a = consulta("SELECT estado, total, fecha FROM pedidos ORDER BY id")
        datos_demo.borrar(webapp.DB_PATH)
        self.crear(30, semilla=5)
        self.assertEqual(a, consulta("SELECT estado, total, fecha FROM pedidos ORDER BY id"))

    def test_no_se_pueden_generar_dos_veces(self):
        self.crear(10)
        with self.assertRaises(ValueError):
            self.crear(10)
        self.assertEqual(consulta("SELECT COUNT(*) FROM pedidos")[0][0], 10)

    def test_cantidades_fuera_de_rango(self):
        for cantidad in (0, -1, 501):
            with self.assertRaises(ValueError):
                self.crear(cantidad)

    def test_borrar_solo_elimina_los_pedidos_ficticios(self):
        real = self.crear_pedido()                                          # pedido real
        otro = self.crear_pedido(sesion=self.cliente2, items=((3, 1),),     # real, pero con operación "DEMO..."
                                 pago=dict(PAGO, referencia_pago="DEMO999999"))
        self.crear(20)
        self.assertEqual(consulta("SELECT COUNT(*) FROM pedidos")[0][0], 22)
        self.assertEqual(datos_demo.borrar(webapp.DB_PATH), 20)
        self.assertEqual([f[0] for f in consulta("SELECT id FROM pedidos ORDER BY id")], [real, otro])
        self.assertEqual(consulta("SELECT COUNT(*) FROM historial_pedido")[0][0], 2)
        self.assertEqual(consulta("SELECT COUNT(*) FROM detalle_pedido")[0][0], 3)     # 2 líneas + 1 línea
        self.assertEqual(consulta("SELECT COUNT(*) FROM movimientos_stock")[0][0], 3)   # los del stock real
        self.assertEqual(datos_demo.borrar(webapp.DB_PATH), 0)                          # repetir no hace daño

    def test_base_desactualizada_da_un_mensaje_claro(self):
        ruta = tempfile.mktemp(suffix=".db")
        db = sqlite3.connect(ruta)
        db.execute("CREATE TABLE pedidos (id INTEGER PRIMARY KEY, estado TEXT)")
        db.commit(); db.close()
        try:
            with self.assertRaises(SystemExit):
                datos_demo.generar(ruta, 5)
        finally:
            os.unlink(ruta)


class TestActualizacionDesdeSprint3(unittest.TestCase):
    def test_base_del_sprint_3_recibe_las_columnas_de_calificacion(self):
        ruta = tempfile.mktemp(suffix=".db")
        db = sqlite3.connect(ruta)
        db.executescript("""
            CREATE TABLE usuarios (id INTEGER PRIMARY KEY AUTOINCREMENT, usuario TEXT UNIQUE NOT NULL,
                nombre TEXT NOT NULL, clave TEXT NOT NULL, rol TEXT NOT NULL);
            CREATE TABLE productos (id INTEGER PRIMARY KEY AUTOINCREMENT, codigo TEXT UNIQUE NOT NULL,
                nombre TEXT NOT NULL, categoria TEXT NOT NULL, proveedor TEXT NOT NULL,
                precio REAL NOT NULL, stock INTEGER NOT NULL);
            CREATE TABLE pedidos (id INTEGER PRIMARY KEY AUTOINCREMENT, cliente_id INTEGER NOT NULL,
                fecha TEXT NOT NULL, estado TEXT NOT NULL, total REAL NOT NULL, metodo_pago TEXT NOT NULL,
                referencia_pago TEXT NOT NULL, motivo_rechazo TEXT, direccion_envio TEXT, transportista TEXT,
                guia_envio TEXT, fecha_envio TEXT, fecha_entrega TEXT);
            INSERT INTO usuarios (usuario, nombre, clave, rol) VALUES ('cliente1','Carlos','x','cliente');
            INSERT INTO pedidos (cliente_id, fecha, estado, total, metodo_pago, referencia_pago, direccion_envio)
                VALUES (1, '2026-10-30 10:00:00', 'entregado', 100.0, 'Yape', 'VIEJO12345', 'Mi casa 123');
        """)
        db.commit(); db.close()
        original = webapp.DB_PATH
        webapp.DB_PATH = ruta
        try:
            webapp.init_db()
            db = sqlite3.connect(ruta)
            columnas = {f[1] for f in db.execute("PRAGMA table_info(pedidos)")}
            self.assertTrue({"calificacion", "comentario", "fecha_calificacion"} <= columnas)
            self.assertEqual(db.execute("SELECT referencia_pago, calificacion FROM pedidos").fetchone(), ("VIEJO12345", None))
            db.close()
        finally:
            webapp.DB_PATH = original
            os.unlink(ruta)


if __name__ == "__main__":
    unittest.main(verbosity=2)
