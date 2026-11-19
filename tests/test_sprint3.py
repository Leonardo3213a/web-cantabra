"""
Pruebas automáticas del Sprint 3 - Preparación, envío, entrega y control de stock
Ejecutar desde la carpeta Proyecto:
    python -m unittest discover -s tests -v
Usan una base de datos temporal: NO tocan tu webcantabra.db real.
"""
import os
import sqlite3
import tempfile
import unittest

from test_sprint2 import Base2, PAGO, consulta, stock, webapp

ENVIO = {"transportista": "Olva Courier", "guia_envio": "OLV-2026-0001"}


def estado(pedido_id):
    return consulta("SELECT estado FROM pedidos WHERE id = ?", (pedido_id,))[0][0]


class Base3(Base2):
    """Atajos para llevar un pedido hasta cada etapa del proceso."""

    def validado(self, **kw):
        pid = self.crear_pedido(**kw)
        self.vendedor.post(f"/pedido/{pid}/validar-pago", {"accion": "aprobar"})
        return pid

    def en_preparacion(self, **kw):
        pid = self.validado(**kw)
        self.almacen.post(f"/pedido/{pid}/preparar")
        return pid

    def enviado(self, **kw):
        pid = self.en_preparacion(**kw)
        self.almacen.post(f"/pedido/{pid}/enviar", ENVIO)
        return pid


class TestDireccionDeEntrega(Base3):
    def intentar(self, direccion):
        self.cliente.post("/carrito/agregar/1", {"cantidad": 1})
        return self.cliente.post("/pedido/confirmar", dict(PAGO, direccion_envio=direccion))

    def test_sin_direccion_no_se_crea_el_pedido(self):
        r = self.intentar("")
        self.assertIn("dirección de entrega", r.get_data(as_text=True))
        self.assertEqual(consulta("SELECT COUNT(*) FROM pedidos")[0][0], 0)
        self.assertEqual(stock(1), 40)

    def test_direccion_demasiado_corta_o_larga(self):
        for direccion in ("Lima", "x" * 300, "         "):
            self.intentar(direccion)
        self.assertEqual(consulta("SELECT COUNT(*) FROM pedidos")[0][0], 0)

    def test_se_guarda_sin_espacios_repetidos(self):
        self.intentar("  Av.   Los   Pinos   456,   Lima  ")
        self.assertEqual(consulta("SELECT direccion_envio FROM pedidos")[0][0], "Av. Los Pinos 456, Lima")

    def test_el_formulario_conserva_lo_escrito_si_hay_error(self):
        r = self.cliente.post("/carrito/agregar/1", {"cantidad": 1})
        r = self.cliente.post("/pedido/confirmar", dict(PAGO, referencia_pago="1", direccion_envio="Calle Mayor 100, Lima"))
        self.assertIn("Calle Mayor 100, Lima", r.get_data(as_text=True))

    def test_el_almacen_ve_la_direccion(self):
        pid = self.validado()
        self.assertIn("Av. Los Olivos 123", self.almacen.html(f"/pedido/{pid}"))

    def test_xss_en_direccion(self):
        malo = "<script>alert(1)</script> Calle 123"
        self.intentar(malo)
        html = self.almacen.html("/pedido/1")
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)


class TestPreparacion(Base3):
    def test_almacen_inicia_la_preparacion(self):
        pid = self.validado()
        self.almacen.post(f"/pedido/{pid}/preparar")
        self.assertEqual(estado(pid), "en_preparacion")
        ultimo = consulta("SELECT estado FROM historial_pedido WHERE pedido_id=? ORDER BY id DESC LIMIT 1", (pid,))[0][0]
        self.assertEqual(ultimo, "en_preparacion")

    def test_no_se_prepara_un_pedido_sin_pago_validado(self):
        pid = self.crear_pedido()                                   # por_validar
        r = self.almacen.post(f"/pedido/{pid}/preparar")
        self.assertIn("No se puede pasar", r.get_data(as_text=True))
        self.vendedor.post(f"/pedido/{pid}/validar-pago", {"accion": "rechazar", "motivo": "Operación inexistente"})
        self.almacen.post(f"/pedido/{pid}/preparar")
        self.assertEqual(estado(pid), "pago_rechazado")
        self.vendedor.post(f"/pedido/{pid}/cancelar", {})
        self.almacen.post(f"/pedido/{pid}/preparar")
        self.assertEqual(estado(pid), "cancelado")

    def test_cliente_y_vendedor_no_pueden_preparar(self):
        pid = self.validado()
        for s in (self.cliente, self.vendedor):
            r = s.post(f"/pedido/{pid}/preparar")
            self.assertIn("No tienes permiso", r.get_data(as_text=True))
        self.assertEqual(estado(pid), "pago_validado")

    def test_doble_clic_no_duplica_el_historial(self):
        pid = self.validado()
        self.almacen.post(f"/pedido/{pid}/preparar")
        r = self.almacen.post(f"/pedido/{pid}/preparar")
        self.assertIn("No se puede pasar", r.get_data(as_text=True))
        n = consulta("SELECT COUNT(*) FROM historial_pedido WHERE pedido_id=? AND estado='en_preparacion'", (pid,))[0][0]
        self.assertEqual(n, 1)

    def test_botones_segun_rol(self):
        pid = self.validado()
        self.assertIn("Iniciar preparación", self.almacen.html(f"/pedido/{pid}"))
        self.assertNotIn("Iniciar preparación", self.cliente.html(f"/pedido/{pid}"))
        self.assertNotIn("Iniciar preparación", self.vendedor.html(f"/pedido/{pid}"))

    def test_no_se_cancela_un_pedido_en_preparacion(self):
        pid = self.en_preparacion()
        stock_antes = stock(1)
        for s in (self.cliente, self.vendedor):
            r = s.post(f"/pedido/{pid}/cancelar", {})
            self.assertIn("No se puede pasar", r.get_data(as_text=True))
        self.assertEqual(estado(pid), "en_preparacion")
        self.assertEqual(stock(1), stock_antes)


class TestEnvio(Base3):
    def test_envio_correcto(self):
        pid = self.en_preparacion()
        self.almacen.post(f"/pedido/{pid}/enviar", dict(ENVIO, guia_envio="olv-2026-0001"))
        fila = consulta("SELECT estado, transportista, guia_envio, fecha_envio FROM pedidos WHERE id=?", (pid,))[0]
        self.assertEqual(fila[:3], ("enviado", "Olva Courier", "OLV-2026-0001"))
        self.assertIsNotNone(fila[3])
        html = self.cliente.html(f"/pedido/{pid}")
        self.assertIn("OLV-2026-0001", html)
        self.assertIn("Olva Courier", html)

    def test_transportista_invalido(self):
        pid = self.en_preparacion()
        r = self.almacen.post(f"/pedido/{pid}/enviar", dict(ENVIO, transportista="Paloma mensajera"))
        self.assertIn("transportista válido", r.get_data(as_text=True))
        self.assertEqual(estado(pid), "en_preparacion")

    def test_guia_invalida(self):
        pid = self.en_preparacion()
        for guia in ("12", "<script>x</script>", "a b c d e f", "x" * 40, ""):
            self.almacen.post(f"/pedido/{pid}/enviar", dict(ENVIO, guia_envio=guia))
        self.assertEqual(estado(pid), "en_preparacion")

    def test_no_se_puede_enviar_sin_preparar(self):
        pid = self.validado()
        r = self.almacen.post(f"/pedido/{pid}/enviar", ENVIO)
        self.assertIn("No se puede pasar", r.get_data(as_text=True))
        self.assertEqual(estado(pid), "pago_validado")

    def test_cliente_y_vendedor_no_pueden_enviar(self):
        pid = self.en_preparacion()
        for s in (self.cliente, self.vendedor):
            r = s.post(f"/pedido/{pid}/enviar", ENVIO)
            self.assertIn("No tienes permiso", r.get_data(as_text=True))
        self.assertEqual(estado(pid), "en_preparacion")


class TestEntrega(Base3):
    def test_cliente_confirma_la_recepcion(self):
        pid = self.enviado()
        self.assertIn("Ya recibí mi pedido", self.cliente.html(f"/pedido/{pid}"))
        self.cliente.post(f"/pedido/{pid}/entregar")
        fila = consulta("SELECT estado, fecha_entrega FROM pedidos WHERE id=?", (pid,))[0]
        self.assertEqual(fila[0], "entregado")
        self.assertIsNotNone(fila[1])

    def test_almacen_tambien_puede_confirmar_la_entrega(self):
        pid = self.enviado()
        self.almacen.post(f"/pedido/{pid}/entregar")
        self.assertEqual(estado(pid), "entregado")

    def test_vendedor_no_confirma_entregas(self):
        pid = self.enviado()
        r = self.vendedor.post(f"/pedido/{pid}/entregar")
        self.assertIn("No tienes permiso", r.get_data(as_text=True))
        self.assertEqual(estado(pid), "enviado")

    def test_otro_cliente_no_puede_confirmar(self):
        pid = self.enviado()
        r = self.cliente2.post(f"/pedido/{pid}/entregar", seguir=False)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(estado(pid), "enviado")

    def test_no_se_entrega_lo_que_aun_no_se_envio(self):
        pid = self.en_preparacion()
        for s in (self.cliente, self.almacen):
            r = s.post(f"/pedido/{pid}/entregar")
            self.assertIn("No se puede pasar", r.get_data(as_text=True))
        self.assertEqual(estado(pid), "en_preparacion")

    def test_el_cliente_no_ve_el_boton_antes_del_envio(self):
        pid = self.en_preparacion()
        self.assertNotIn("Ya recibí mi pedido", self.cliente.html(f"/pedido/{pid}"))

    def test_entregado_es_un_estado_final(self):
        pid = self.enviado()
        self.cliente.post(f"/pedido/{pid}/entregar")
        for ruta, s in ((f"/pedido/{pid}/preparar", self.almacen), (f"/pedido/{pid}/entregar", self.cliente),
                        (f"/pedido/{pid}/cancelar", self.cliente), (f"/pedido/{pid}/enviar", self.almacen)):
            s.post(ruta, ENVIO)
        self.assertEqual(estado(pid), "entregado")

    def test_ciclo_completo_con_historial_ordenado_y_stock_correcto(self):
        pid = self.enviado()                                        # 2 auriculares + 1 teclado
        self.cliente.post(f"/pedido/{pid}/entregar")
        historial = [f[0] for f in consulta("SELECT estado FROM historial_pedido WHERE pedido_id=? ORDER BY id", (pid,))]
        self.assertEqual(historial, ["por_validar", "pago_validado", "en_preparacion", "enviado", "entregado"])
        self.assertEqual((stock(1), stock(2)), (38, 24))            # el stock no vuelve: se vendió
        html = self.cliente.html(f"/pedido/{pid}")
        self.assertIn("Entregado", html)
        self.assertIn("Seguimiento del pedido", html)


class TestLineaDeTiempo(unittest.TestCase):
    def clases(self, estado_):
        return [e["clase"] for e in webapp.linea_de_tiempo(estado_)]

    def test_recien_recibido(self):
        self.assertEqual(self.clases("por_validar"), ["primary", "light", "light", "light", "light"])

    def test_en_camino(self):
        self.assertEqual(self.clases("enviado"), ["success", "success", "success", "primary", "light"])

    def test_entregado_todo_completo(self):
        self.assertEqual(self.clases("entregado"), ["success"] * 5)

    def test_pago_rechazado_marca_la_primera_etapa(self):
        self.assertEqual(self.clases("pago_rechazado")[0], "danger")

    def test_cancelado_no_avanza(self):
        self.assertEqual(self.clases("cancelado"), ["light"] * 5)


class TestInventario(Base3):
    def test_permisos_de_la_pagina_de_stock(self):
        self.assertIn("No tienes permiso", self.cliente.html("/stock"))
        self.assertIn("Control de stock", self.vendedor.html("/stock"))
        self.assertNotIn("Reponer</button>", self.vendedor.html("/stock"))      # el vendedor solo consulta
        self.assertIn("Reponer</button>", self.almacen.html("/stock"))

    def test_reponer_aumenta_el_stock_y_deja_movimiento(self):
        self.almacen.post("/stock/9/reponer", {"cantidad": 20, "nota": "Llegó lote del proveedor"})
        self.assertEqual(stock(9), 24)
        mov = consulta("SELECT tipo, cantidad, usuario_id, nota FROM movimientos_stock WHERE producto_id=9")[0]
        self.assertEqual(mov[:2], ("ingreso", 20))
        self.assertEqual(mov[3], "Llegó lote del proveedor")
        self.assertIn("Jorge Salinas", self.almacen.html("/stock"))

    def test_cantidades_invalidas(self):
        for cant in ("0", "-5", "abc", "1.5", "1000000", ""):
            self.almacen.post("/stock/9/reponer", {"cantidad": cant})
        self.assertEqual(stock(9), 4)
        self.assertEqual(consulta("SELECT COUNT(*) FROM movimientos_stock")[0][0], 0)

    def test_no_supera_el_maximo(self):
        r = self.almacen.post("/stock/1/reponer", {"cantidad": 99990})
        self.assertIn("no puede superar", r.get_data(as_text=True))
        self.assertEqual(stock(1), 40)

    def test_producto_inexistente(self):
        r = self.almacen.post("/stock/9999/reponer", {"cantidad": 5})
        self.assertIn("no existe", r.get_data(as_text=True))

    def test_solo_el_almacen_repone(self):
        for s in (self.cliente, self.vendedor):
            r = s.post("/stock/9/reponer", {"cantidad": 5})
            self.assertIn("No tienes permiso", r.get_data(as_text=True))
        self.assertEqual(stock(9), 4)

    def test_pedido_y_cancelacion_dejan_movimientos(self):
        pid = self.crear_pedido()
        salidas = consulta("SELECT producto_id, cantidad, pedido_id FROM movimientos_stock "
                           "WHERE tipo='salida_pedido' ORDER BY producto_id")
        self.assertEqual(salidas, [(1, -2, pid), (2, -1, pid)])
        self.cliente.post(f"/pedido/{pid}/cancelar", {})
        devs = consulta("SELECT producto_id, cantidad FROM movimientos_stock WHERE tipo='devolucion' ORDER BY producto_id")
        self.assertEqual(devs, [(1, 2), (2, 1)])
        # el kardex cuadra con el stock real
        total = consulta("SELECT SUM(cantidad) FROM movimientos_stock WHERE producto_id=1")[0][0]
        self.assertEqual(40 + total, stock(1))

    def test_editar_producto_registra_el_ajuste(self):
        datos = dict(codigo="TEC-001", nombre="Auriculares Bluetooth", categoria="Tecnología",
                     proveedor="TecnoSur S.A.", precio="89.90", stock="55")
        self.vendedor.post("/producto/1/editar", datos)
        self.assertEqual(stock(1), 55)
        self.assertEqual(consulta("SELECT tipo, cantidad FROM movimientos_stock")[0], ("ajuste", 15))
        self.vendedor.post("/producto/1/editar", dict(datos, precio="95.00"))     # no cambia el stock
        self.assertEqual(consulta("SELECT COUNT(*) FROM movimientos_stock")[0][0], 1)

    def test_producto_nuevo_registra_stock_inicial(self):
        datos = dict(codigo="NUE-001", nombre="Webcam HD", categoria="Tecnología",
                     proveedor="TecnoSur S.A.", precio="75.50", stock="20")
        self.vendedor.post("/producto/nuevo", datos)
        self.assertEqual(consulta("SELECT tipo, cantidad FROM movimientos_stock")[0], ("ingreso", 20))

    def test_alerta_de_stock_bajo_y_orden(self):
        html = self.almacen.html("/stock")
        self.assertIn("producto(s) con stock bajo", html)
        self.assertLess(html.index("Termo de acero"), html.index("Auriculares Bluetooth"))   # los más bajos primero


class TestSeguridadSprint3(Base3):
    RUTAS = ["/pedido/1/preparar", "/pedido/1/enviar", "/pedido/1/entregar", "/stock/1/reponer"]

    def test_post_sin_token_csrf_rechazado(self):
        self.validado()
        for ruta in self.RUTAS:
            r = self.almacen.c.post(ruta, data={"cantidad": 1, **ENVIO})
            self.assertEqual(r.status_code, 400, ruta)
        self.assertEqual(estado(1), "pago_validado")

    def test_get_no_ejecuta_acciones(self):
        self.validado()
        for ruta in self.RUTAS:
            self.assertEqual(self.almacen.c.get(ruta).status_code, 405, ruta)

    def test_resumen_de_pedidos_para_el_almacen(self):
        self.validado()
        html = self.almacen.html("/pedidos")
        self.assertIn("PED-00001", html)                       # por defecto ve lo pendiente de preparar
        self.assertIn("Pago validado", html)

    def test_no_se_puede_saltar_etapas_por_url(self):
        pid = self.crear_pedido()
        for ruta, s in ((f"/pedido/{pid}/enviar", self.almacen), (f"/pedido/{pid}/entregar", self.almacen),
                        (f"/pedido/{pid}/preparar", self.almacen)):
            s.post(ruta, ENVIO)
        self.assertEqual(estado(pid), "por_validar")

    def test_sql_injection_en_guia_y_nota(self):
        pid = self.en_preparacion()
        self.almacen.post(f"/pedido/{pid}/enviar", dict(ENVIO, guia_envio="1'; DROP TABLE pedidos;--"))
        self.almacen.post("/stock/9/reponer", {"cantidad": 1, "nota": "x'); DROP TABLE productos;--"})
        self.assertEqual(consulta("SELECT COUNT(*) FROM pedidos")[0][0], 1)
        self.assertEqual(consulta("SELECT COUNT(*) FROM productos")[0][0], 10)


class TestActualizacionDeBaseDeDatos(unittest.TestCase):
    def test_base_del_sprint_2_se_actualiza_sin_perder_datos(self):
        """Una base creada en el Sprint 2 (sin columnas de envío) debe actualizarse sola."""
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
                referencia_pago TEXT NOT NULL, motivo_rechazo TEXT);
            INSERT INTO usuarios (usuario, nombre, clave, rol) VALUES ('cliente1','Carlos','x','cliente');
            INSERT INTO pedidos (cliente_id, fecha, estado, total, metodo_pago, referencia_pago)
                VALUES (1, '2026-10-30 10:00:00', 'pago_validado', 100.0, 'Yape', 'VIEJO12345');
        """)
        db.commit(); db.close()

        original = webapp.DB_PATH
        webapp.DB_PATH = ruta
        try:
            webapp.init_db()
            webapp.init_db()                                   # repetirlo no debe fallar
            db = sqlite3.connect(ruta)
            columnas = {f[1] for f in db.execute("PRAGMA table_info(pedidos)")}
            self.assertTrue({"direccion_envio", "transportista", "guia_envio", "fecha_envio", "fecha_entrega"} <= columnas)
            self.assertEqual(db.execute("SELECT referencia_pago FROM pedidos").fetchone()[0], "VIEJO12345")
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE name='movimientos_stock'").fetchone()[0],
                             "movimientos_stock")
            db.close()
        finally:
            webapp.DB_PATH = original
            try:
                os.unlink(ruta)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
