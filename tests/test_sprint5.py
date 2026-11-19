"""Pruebas de regresión para las mejoras del Sprint 5.

Ejecutar junto con los tests originales:
    python -m unittest discover -s tests -v
"""
from io import BytesIO
import secrets
from PIL import Image
from werkzeug.security import generate_password_hash
from test_sprint1 import Base, webapp


def referencia():
    return "OP" + secrets.token_hex(5).upper()


def png_ejemplo():
    salida = BytesIO()
    Image.new("RGB", (12, 12), (10, 100, 160)).save(salida, format="PNG")
    salida.seek(0)
    return salida


class TestCampanasSemestrales(Base):
    def test_crear_borrador_asignar_y_publicar(self):
        self.entrar("vendedor1")
        # Se elige un año libre a partir de los ya creados, sin alterar campañas reales.
        with webapp.app.app_context():
            anio = webapp.get_db().execute("SELECT COALESCE(MAX(anio), 2026)+1 FROM campanas").fetchone()[0]
        r = self.post("/campanas/nueva", {
            "anio": str(anio), "semestre": "1", "nombre": f"Catálogo {anio} I",
            "descripcion": "Pruebas del primer semestre"})
        self.assertIn("Campaña creada", r.get_data(as_text=True))
        with webapp.app.app_context():
            db = webapp.get_db()
            campana_id = db.execute("SELECT id FROM campanas WHERE anio=? AND semestre=1",
                                    (anio,)).fetchone()[0]
            producto_id = db.execute("SELECT id FROM productos ORDER BY id LIMIT 1").fetchone()[0]
        self.post(f"/campanas/{campana_id}/productos", {"producto_id": producto_id, "accion": "agregar"})
        r = self.post(f"/campanas/{campana_id}/publicacion", {"accion": "publicar"})
        self.assertIn("Campaña publicada", r.get_data(as_text=True))
        with webapp.app.app_context():
            db = webapp.get_db()
            self.assertEqual(db.execute("SELECT publicada FROM campanas WHERE id=?",
                                        (campana_id,)).fetchone()[0], 1)
            self.assertIsNotNone(db.execute(
                "SELECT 1 FROM campana_productos WHERE campana_id=? AND producto_id=?",
                (campana_id, producto_id)).fetchone())
        self.entrar("cliente1")
        r = self.c.get(f"/catalogo?campana={campana_id}")
        self.assertIn(f"Catálogo {anio} I", r.get_data(as_text=True))
        self.assertEqual(self.c.get("/campanas").status_code, 302)


class TestSeguridadCuenta(Base):
    def test_cambio_de_clave_requiere_actual_y_politica_fuerte(self):
        self.entrar("cliente2")
        try:
            r = self.post("/mi-cuenta", {"accion": "clave", "clave_actual": "1234",
                         "clave_nueva": "debil", "clave_confirmar": "debil"})
            self.assertIn("10 a 128 caracteres", r.get_data(as_text=True))
            clave_nueva = "NuevaClave" + secrets.token_hex(5).upper()
            r = self.post("/mi-cuenta", {"accion": "clave", "clave_actual": "1234",
                         "clave_nueva": clave_nueva, "clave_confirmar": clave_nueva})
            self.assertIn("Contraseña actualizada", r.get_data(as_text=True))
            self.assertEqual(self.entrar("cliente2", "1234").status_code, 200)
            self.assertIn("incorrectos", self.entrar("cliente2", "1234").get_data(as_text=True))
            self.assertIn("Catálogo de productos", self.entrar("cliente2", clave_nueva).get_data(as_text=True))
        finally:
            # Restablece la clave de DEMO para que el resto de los tests sean independientes.
            with webapp.app.app_context():
                db = webapp.get_db()
                db.execute("UPDATE usuarios SET clave=? WHERE usuario='cliente2'",
                           (generate_password_hash("1234"),))
                db.commit()


class TestComprobantesNotificaciones(Base):
    def test_comprobante_privado_y_aviso_a_vendedor(self):
        self.entrar("cliente1")
        with webapp.app.app_context():
            db = webapp.get_db()
            producto_id = db.execute(
                "SELECT p.id FROM productos p JOIN campana_productos cp ON cp.producto_id=p.id "
                "JOIN campanas c ON c.id=cp.campana_id WHERE c.publicada=1 AND p.stock>0 "
                "LIMIT 1").fetchone()[0]
        self.post(f"/carrito/agregar/{producto_id}", {"cantidad": "1"})
        datos = {
            "csrf_token": self.token("/carrito"), "metodo_pago": "Yape",
            "referencia_pago": referencia(), "direccion_envio": "Calle del Comercio 123, Lima",
            "comprobante": (png_ejemplo(), "pago.png"),
        }
        r = self.c.post("/pedido/confirmar", data=datos,
                        content_type="multipart/form-data", follow_redirects=True)
        self.assertIn("Pedido registrado", r.get_data(as_text=True))
        with webapp.app.app_context():
            pedido = webapp.get_db().execute(
                "SELECT id, comprobante_pago, campana_id FROM pedidos ORDER BY id DESC LIMIT 1").fetchone()
            pedido_id, comprobante, campana_id = pedido
            self.assertTrue(comprobante.endswith(".png"))
            self.assertIsNotNone(campana_id)
        self.assertEqual(self.c.get(f"/pedido/{pedido_id}/comprobante").status_code, 200)
        self.entrar("cliente2")
        self.assertEqual(self.c.get(f"/pedido/{pedido_id}/comprobante").status_code, 404)
        self.entrar("vendedor1")
        r = self.c.get("/notificaciones")
        self.assertIn(f"PED-{pedido_id:05d}", r.get_data(as_text=True))
        r = self.post("/notificaciones/leidas", {})
        self.assertIn("marcadas como leídas", r.get_data(as_text=True))

    def test_rechaza_archivos_falsamente_renombrados(self):
        self.entrar("cliente1")
        with webapp.app.app_context():
            db = webapp.get_db()
            producto_id = db.execute(
                "SELECT p.id FROM productos p JOIN campana_productos cp ON cp.producto_id=p.id "
                "JOIN campanas c ON c.id=cp.campana_id WHERE c.publicada=1 AND p.stock>0 LIMIT 1").fetchone()[0]
        self.post(f"/carrito/agregar/{producto_id}", {"cantidad": "1"})
        r = self.c.post("/pedido/confirmar", data={
            "csrf_token": self.token("/carrito"), "metodo_pago": "Yape",
            "referencia_pago": referencia(), "direccion_envio": "Calle del Comercio 123, Lima",
            "comprobante": (BytesIO(b"NO ES UNA IMAGEN"), "falso.png")},
            content_type="multipart/form-data", follow_redirects=True)
        self.assertIn("imagen PNG, JPG o WEBP", r.get_data(as_text=True))
        self.post("/carrito/vaciar", {})
