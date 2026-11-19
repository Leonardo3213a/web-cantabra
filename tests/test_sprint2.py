"""
Pruebas automáticas del Sprint 2 - Carrito, pedidos y validación de pago
Ejecutar desde la carpeta Proyecto:
    python -m unittest discover -s tests -v
Usan una base de datos temporal: NO tocan tu webcantabra.db real.
"""
import os
import sqlite3
import unittest

from test_sprint1 import webapp, sacar_token   # reutiliza la base temporal del Sprint 1

# Productos de la carga inicial (id, precio, stock): 1=Auriculares 89.90/40, 2=Teclado 159.00/25,
# 4=Monitor 599.00/12, 9=Termo 55.00/4
PAGO = {"metodo_pago": "Yape", "referencia_pago": "OP123456",
        "direccion_envio": "Av. Los Olivos 123, San Martín de Porres, Lima"}


class Sesion:
    """Un navegador de prueba con su propia sesión (para tener cliente y vendedor a la vez)."""

    def __init__(self, usuario):
        self.c = webapp.app.test_client()
        tok = sacar_token(self.c.get("/login").get_data(as_text=True))
        r = self.c.post("/login", data={"usuario": usuario, "clave": "1234", "csrf_token": tok})
        assert r.status_code == 302, f"No pudo iniciar sesión como {usuario}"

    def get(self, ruta):
        return self.c.get(ruta, follow_redirects=True)

    def html(self, ruta):
        return self.get(ruta).get_data(as_text=True)

    def post(self, ruta, datos=None, seguir=True):
        tok = sacar_token(self.c.get("/catalogo").get_data(as_text=True))
        datos = dict(datos or {}, csrf_token=tok)
        return self.c.post(ruta, data=datos, follow_redirects=seguir)


def consulta(sql, params=()):
    db = sqlite3.connect(webapp.DB_PATH)
    try:
        return db.execute(sql, params).fetchall()
    finally:
        db.close()


def stock(producto_id):
    return consulta("SELECT stock FROM productos WHERE id = ?", (producto_id,))[0][0]


class Base2(unittest.TestCase):
    def setUp(self):
        webapp.app.config["TESTING"] = True
        webapp.INTENTOS_FALLIDOS.clear()
        if os.path.exists(webapp.DB_PATH):          # base limpia para cada prueba
            os.remove(webapp.DB_PATH)
        webapp.init_db()
        self.cliente = Sesion("cliente1")
        self.cliente2 = Sesion("cliente2")
        self.vendedor = Sesion("vendedor1")
        self.almacen = Sesion("almacen1")

    def crear_pedido(self, sesion=None, items=((1, 2), (2, 1)), pago=None):
        """Agrega productos al carrito, confirma y devuelve el id del pedido."""
        sesion = sesion or self.cliente
        for pid, cant in items:
            sesion.post(f"/carrito/agregar/{pid}", {"cantidad": cant})
        sesion.post("/pedido/confirmar", pago or PAGO)
        return consulta("SELECT MAX(id) FROM pedidos")[0][0]


class TestCarrito(Base2):
    def test_agregar_y_ver_total(self):
        self.cliente.post("/carrito/agregar/1", {"cantidad": 2})
        self.cliente.post("/carrito/agregar/2", {"cantidad": 1})
        html = self.cliente.html("/carrito")
        self.assertIn("Auriculares Bluetooth", html)
        self.assertIn("S/ 338.80", html)       # 2 x 89.90 + 1 x 159.00

    def test_sumar_cantidad_del_mismo_producto(self):
        self.cliente.post("/carrito/agregar/1", {"cantidad": 2})
        self.cliente.post("/carrito/agregar/1", {"cantidad": 3})
        self.assertIn("S/ 449.50", self.cliente.html("/carrito"))   # 5 x 89.90

    def test_no_se_puede_superar_el_stock(self):
        r = self.cliente.post("/carrito/agregar/9", {"cantidad": 5})   # solo hay 4
        self.assertIn("Solo hay 4", r.get_data(as_text=True))
        self.assertIn("vacío", self.cliente.html("/carrito"))

    def test_cantidades_invalidas(self):
        for cant in ("0", "-3", "abc", "1.5", "100000", ""):
            self.cliente.post("/carrito/agregar/1", {"cantidad": cant})
        self.assertIn("vacío", self.cliente.html("/carrito"))

    def test_producto_inexistente(self):
        r = self.cliente.post("/carrito/agregar/9999", {"cantidad": 1})
        self.assertIn("no existe", r.get_data(as_text=True))

    def test_quitar_y_vaciar(self):
        self.cliente.post("/carrito/agregar/1", {"cantidad": 1})
        self.cliente.post("/carrito/agregar/2", {"cantidad": 1})
        self.cliente.post("/carrito/quitar/1")
        html = self.cliente.html("/carrito")
        self.assertNotIn("Auriculares", html)
        self.assertIn("Teclado", html)
        self.cliente.post("/carrito/vaciar")
        self.assertIn("vacío", self.cliente.html("/carrito"))

    def test_vendedor_y_almacen_no_tienen_carrito(self):
        for s in (self.vendedor, self.almacen):
            self.assertIn("No tienes permiso", s.html("/carrito"))
            r = s.post("/carrito/agregar/1", {"cantidad": 1})
            self.assertIn("No tienes permiso", r.get_data(as_text=True))

    def test_el_carrito_de_un_cliente_no_se_mezcla_con_otro(self):
        self.cliente.post("/carrito/agregar/1", {"cantidad": 1})
        self.assertIn("vacío", self.cliente2.html("/carrito"))


class TestCrearPedido(Base2):
    def test_pedido_correcto(self):
        pid = self.crear_pedido()
        fila = consulta("SELECT estado, total, metodo_pago, referencia_pago FROM pedidos WHERE id=?", (pid,))[0]
        self.assertEqual(fila, ("por_validar", 338.80, "Yape", "OP123456"))
        self.assertEqual(len(consulta("SELECT 1 FROM detalle_pedido WHERE pedido_id=?", (pid,))), 2)
        self.assertEqual(len(consulta("SELECT 1 FROM historial_pedido WHERE pedido_id=?", (pid,))), 1)

    def test_descuenta_stock_y_vacia_carrito(self):
        self.crear_pedido()
        self.assertEqual(stock(1), 38)
        self.assertEqual(stock(2), 24)
        self.assertIn("vacío", self.cliente.html("/carrito"))

    def test_el_precio_lo_decide_el_servidor(self):
        """Aunque el cliente intente mandar un precio falso, se usa el de la base de datos."""
        self.cliente.post("/carrito/agregar/4", {"cantidad": 1, "precio": "0.01"})
        self.cliente.post("/pedido/confirmar", dict(PAGO, total="0.01", precio="0.01"))
        self.assertEqual(consulta("SELECT total FROM pedidos")[0][0], 599.00)

    def test_carrito_vacio_no_crea_pedido(self):
        r = self.cliente.post("/pedido/confirmar", PAGO)
        self.assertIn("vacío", r.get_data(as_text=True))
        self.assertEqual(consulta("SELECT COUNT(*) FROM pedidos")[0][0], 0)

    def test_metodo_o_operacion_invalidos(self):
        self.cliente.post("/carrito/agregar/1", {"cantidad": 1})
        casos = [dict(PAGO, metodo_pago="Bitcoin"), dict(PAGO, metodo_pago=""),
                 dict(PAGO, referencia_pago="123"), dict(PAGO, referencia_pago="A" * 30),
                 dict(PAGO, referencia_pago="AB 12 34"), dict(PAGO, referencia_pago="'; DROP--")]
        for datos in casos:
            self.cliente.post("/pedido/confirmar", datos)
        self.assertEqual(consulta("SELECT COUNT(*) FROM pedidos")[0][0], 0)
        self.assertEqual(stock(1), 40)

    def test_numero_de_operacion_repetido(self):
        self.crear_pedido()
        self.cliente2.post("/carrito/agregar/3", {"cantidad": 1})
        r = self.cliente2.post("/pedido/confirmar", PAGO)
        self.assertIn("ya fue registrado", r.get_data(as_text=True))
        self.assertEqual(consulta("SELECT COUNT(*) FROM pedidos")[0][0], 1)

    def test_stock_insuficiente_no_deja_pedidos_a_medias(self):
        """Si el segundo producto ya no tiene stock, el primero tampoco se descuenta (todo o nada)."""
        self.cliente.post("/carrito/agregar/1", {"cantidad": 5})
        self.cliente.post("/carrito/agregar/9", {"cantidad": 4})
        db = sqlite3.connect(webapp.DB_PATH)                    # otro cliente compró casi todo
        db.execute("UPDATE productos SET stock = 1 WHERE id = 9")
        db.commit(); db.close()
        r = self.cliente.post("/pedido/confirmar", PAGO)
        self.assertIn("Stock insuficiente", r.get_data(as_text=True))
        self.assertEqual(stock(1), 40)                           # sin cambios
        self.assertEqual(stock(9), 1)
        self.assertEqual(consulta("SELECT COUNT(*) FROM pedidos")[0][0], 0)

    def test_no_se_puede_comprar_dos_veces_el_ultimo_producto(self):
        self.crear_pedido(items=((9, 4),), pago=dict(PAGO, referencia_pago="AAA111"))
        self.assertEqual(stock(9), 0)
        r = self.cliente2.post("/carrito/agregar/9", {"cantidad": 1})
        self.assertIn("Solo hay 0", r.get_data(as_text=True))


class TestValidacionPago(Base2):
    def test_vendedor_aprueba(self):
        pid = self.crear_pedido()
        self.vendedor.post(f"/pedido/{pid}/validar-pago", {"accion": "aprobar"})
        self.assertEqual(consulta("SELECT estado FROM pedidos WHERE id=?", (pid,))[0][0], "pago_validado")
        self.assertEqual(len(consulta("SELECT 1 FROM historial_pedido WHERE pedido_id=?", (pid,))), 2)

    def test_rechazo_exige_motivo(self):
        pid = self.crear_pedido()
        r = self.vendedor.post(f"/pedido/{pid}/validar-pago", {"accion": "rechazar", "motivo": ""})
        self.assertIn("motivo del rechazo", r.get_data(as_text=True))
        self.assertEqual(consulta("SELECT estado FROM pedidos WHERE id=?", (pid,))[0][0], "por_validar")

    def test_rechazo_con_motivo_y_cliente_lo_ve(self):
        pid = self.crear_pedido()
        self.vendedor.post(f"/pedido/{pid}/validar-pago", {"accion": "rechazar", "motivo": "Operación no encontrada"})
        html = self.cliente.html(f"/pedido/{pid}")
        self.assertIn("Pago rechazado", html)
        self.assertIn("Operación no encontrada", html)

    def test_no_se_puede_validar_dos_veces(self):
        pid = self.crear_pedido()
        self.vendedor.post(f"/pedido/{pid}/validar-pago", {"accion": "aprobar"})
        r = self.vendedor.post(f"/pedido/{pid}/validar-pago", {"accion": "rechazar", "motivo": "Ya no sirve"})
        self.assertIn("No se puede pasar", r.get_data(as_text=True))
        self.assertEqual(consulta("SELECT estado FROM pedidos WHERE id=?", (pid,))[0][0], "pago_validado")

    def test_accion_desconocida(self):
        pid = self.crear_pedido()
        r = self.vendedor.post(f"/pedido/{pid}/validar-pago", {"accion": "regalar"})
        self.assertIn("Acción no válida", r.get_data(as_text=True))

    def test_cliente_y_almacen_no_pueden_validar_pagos(self):
        pid = self.crear_pedido()
        for s in (self.cliente, self.almacen):
            r = s.post(f"/pedido/{pid}/validar-pago", {"accion": "aprobar"})
            self.assertIn("No tienes permiso", r.get_data(as_text=True))
        self.assertEqual(consulta("SELECT estado FROM pedidos WHERE id=?", (pid,))[0][0], "por_validar")

    def test_reintento_de_pago_tras_rechazo(self):
        pid = self.crear_pedido()
        self.vendedor.post(f"/pedido/{pid}/validar-pago", {"accion": "rechazar", "motivo": "Monto incorrecto"})
        self.cliente.post(f"/pedido/{pid}/reintentar-pago", {"metodo_pago": "Plin", "referencia_pago": "NUEVO7788"})
        fila = consulta("SELECT estado, metodo_pago, referencia_pago, motivo_rechazo FROM pedidos WHERE id=?", (pid,))[0]
        self.assertEqual(fila, ("por_validar", "Plin", "NUEVO7788", None))

    def test_no_se_reintenta_si_el_pago_no_fue_rechazado(self):
        pid = self.crear_pedido()
        r = self.cliente.post(f"/pedido/{pid}/reintentar-pago", {"metodo_pago": "Plin", "referencia_pago": "NUEVO7788"})
        self.assertIn("No se puede pasar", r.get_data(as_text=True))

    def test_xss_en_motivo_de_rechazo(self):
        pid = self.crear_pedido()
        malo = "<script>alert(1)</script>"
        self.vendedor.post(f"/pedido/{pid}/validar-pago", {"accion": "rechazar", "motivo": malo})
        html = self.cliente.html(f"/pedido/{pid}")
        self.assertNotIn(malo, html)
        self.assertIn("&lt;script&gt;", html)


class TestCancelacion(Base2):
    def test_cliente_cancela_y_el_stock_vuelve(self):
        pid = self.crear_pedido()
        self.assertEqual(stock(1), 38)
        self.cliente.post(f"/pedido/{pid}/cancelar", {"motivo": "Me equivoqué"})
        self.assertEqual(consulta("SELECT estado FROM pedidos WHERE id=?", (pid,))[0][0], "cancelado")
        self.assertEqual(stock(1), 40)
        self.assertEqual(stock(2), 25)

    def test_vendedor_puede_cancelar(self):
        pid = self.crear_pedido()
        self.vendedor.post(f"/pedido/{pid}/cancelar", {})
        self.assertEqual(consulta("SELECT estado FROM pedidos WHERE id=?", (pid,))[0][0], "cancelado")

    def test_no_se_cancela_dos_veces_ni_devuelve_stock_doble(self):
        pid = self.crear_pedido()
        self.cliente.post(f"/pedido/{pid}/cancelar", {})
        self.cliente.post(f"/pedido/{pid}/cancelar", {})
        self.assertEqual(stock(1), 40)                 # no 42

    def test_no_se_cancela_un_pedido_con_pago_validado(self):
        pid = self.crear_pedido()
        self.vendedor.post(f"/pedido/{pid}/validar-pago", {"accion": "aprobar"})
        r = self.cliente.post(f"/pedido/{pid}/cancelar", {})
        self.assertIn("No se puede pasar", r.get_data(as_text=True))
        self.assertEqual(stock(1), 38)

    def test_almacen_no_cancela(self):
        pid = self.crear_pedido()
        r = self.almacen.post(f"/pedido/{pid}/cancelar", {})
        self.assertIn("No tienes permiso", r.get_data(as_text=True))

    def test_cliente_no_cancela_pedido_ajeno(self):
        pid = self.crear_pedido()
        r = self.cliente2.post(f"/pedido/{pid}/cancelar", {}, seguir=False)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(consulta("SELECT estado FROM pedidos WHERE id=?", (pid,))[0][0], "por_validar")

    def test_numero_de_operacion_se_libera_al_cancelar(self):
        pid = self.crear_pedido()
        self.cliente.post(f"/pedido/{pid}/cancelar", {})
        pid2 = self.crear_pedido(sesion=self.cliente2, items=((3, 1),))   # misma operación OP123456
        self.assertNotEqual(pid, pid2)


class TestAccesoAPedidos(Base2):
    def test_cliente_no_ve_pedidos_de_otro_cliente(self):
        pid = self.crear_pedido()
        r = self.cliente2.get(f"/pedido/{pid}")
        self.assertEqual(r.status_code, 404)
        self.assertNotIn("Auriculares", r.get_data(as_text=True))

    def test_cliente_dueno_si_ve_su_pedido(self):
        pid = self.crear_pedido()
        self.assertIn("PED-0000%d" % pid, self.cliente.html(f"/pedido/{pid}"))

    def test_vendedor_y_almacen_ven_cualquier_pedido(self):
        pid = self.crear_pedido()
        for s in (self.vendedor, self.almacen):
            self.assertIn("Carlos Mendoza", s.html(f"/pedido/{pid}"))

    def test_pedido_inexistente(self):
        self.assertEqual(self.vendedor.get("/pedido/99999").status_code, 404)

    def test_mis_pedidos_solo_muestra_los_propios(self):
        self.crear_pedido(sesion=self.cliente)
        self.crear_pedido(sesion=self.cliente2, items=((3, 1),), pago=dict(PAGO, referencia_pago="ZZZ99999"))
        html = self.cliente.html("/mis-pedidos")
        self.assertIn("PED-00001", html)
        self.assertNotIn("PED-00002", html)

    def test_lista_de_pedidos_para_vendedor_con_filtro(self):
        pid = self.crear_pedido()
        self.assertIn("Pago por validar", self.vendedor.html("/pedidos"))
        self.vendedor.post(f"/pedido/{pid}/validar-pago", {"accion": "aprobar"})
        self.assertNotIn("PED-00001", self.vendedor.html("/pedidos"))              # ya no está pendiente
        self.assertIn("PED-00001", self.vendedor.html("/pedidos?estado=todos"))
        self.assertIn("PED-00001", self.almacen.html("/pedidos"))                   # almacén ve los validados

    def test_cliente_no_entra_a_la_lista_general(self):
        self.assertIn("No tienes permiso", self.cliente.html("/pedidos"))

    def test_sin_login_todo_redirige(self):
        anonimo = webapp.app.test_client()
        for ruta in ("/carrito", "/mis-pedidos", "/pedidos", "/pedido/1"):
            r = anonimo.get(ruta)
            self.assertEqual(r.status_code, 302, ruta)
            self.assertIn("/login", r.location)


class TestSeguridadSprint2(Base2):
    def test_post_sin_token_csrf_rechazado(self):
        r = self.cliente.c.post("/carrito/agregar/1", data={"cantidad": 1})
        self.assertEqual(r.status_code, 400)
        pid = self.crear_pedido()
        r = self.vendedor.c.post(f"/pedido/{pid}/validar-pago", data={"accion": "aprobar"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(consulta("SELECT estado FROM pedidos WHERE id=?", (pid,))[0][0], "por_validar")

    def test_get_no_ejecuta_acciones(self):
        pid = self.crear_pedido()
        for ruta in (f"/pedido/{pid}/cancelar", f"/pedido/{pid}/validar-pago", "/pedido/confirmar", "/carrito/vaciar"):
            self.assertEqual(self.vendedor.c.get(ruta).status_code, 405, ruta)

    def test_sql_injection_en_numero_de_pedido_y_pago(self):
        self.assertEqual(self.vendedor.get("/pedido/1%20OR%201=1").status_code, 404)
        self.cliente.post("/carrito/agregar/1", {"cantidad": 1})
        self.cliente.post("/pedido/confirmar", dict(PAGO, referencia_pago="1' OR '1'='1"))
        self.assertEqual(consulta("SELECT COUNT(*) FROM pedidos")[0][0], 0)

    def test_no_hay_estilos_ni_scripts_en_linea_que_bloquee_el_csp(self):
        carpeta = os.path.join(os.path.dirname(webapp.__file__), "templates")
        for nombre in os.listdir(carpeta):
            texto = open(os.path.join(carpeta, nombre), encoding="utf-8").read()
            self.assertNotIn(' style="', texto, nombre)
            self.assertNotIn(" onclick=", texto, nombre)


class TestMaquinaDeEstados(unittest.TestCase):
    def test_estados_finales_no_tienen_salida(self):
        self.assertEqual(webapp.TRANSICIONES["entregado"], set())
        self.assertEqual(webapp.TRANSICIONES["cancelado"], set())

    def test_todas_las_transiciones_apuntan_a_estados_validos(self):
        for origen, destinos in webapp.TRANSICIONES.items():
            self.assertIn(origen, webapp.ESTADOS)
            for d in destinos:
                self.assertIn(d, webapp.ESTADOS)

    def test_no_se_puede_saltar_etapas(self):
        self.assertNotIn("enviado", webapp.TRANSICIONES["por_validar"])
        self.assertNotIn("en_preparacion", webapp.TRANSICIONES["por_validar"])
        self.assertNotIn("pago_validado", webapp.TRANSICIONES["cancelado"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
