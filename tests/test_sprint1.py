"""
Pruebas automáticas del Sprint 1 - WEB-Cántabra
Ejecutar desde la carpeta Proyecto:
    python -m unittest discover -s tests -v
Usan una base de datos temporal: NO tocan tu webcantabra.db real.
"""
import os
import re
import sys
import tempfile
import unittest

# Base de datos temporal para las pruebas (debe definirse ANTES de importar app)
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["WEBCANTABRA_DB"] = _tmp.name
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as webapp  # noqa: E402


def sacar_token(html):
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    return m.group(1) if m else ""


class Base(unittest.TestCase):
    def setUp(self):
        webapp.app.config["TESTING"] = True
        webapp.INTENTOS_FALLIDOS.clear()
        self.c = webapp.app.test_client()

    def token(self, ruta="/login"):
        return sacar_token(self.c.get(ruta).get_data(as_text=True))

    def entrar(self, usuario, clave="1234"):
        return self.c.post("/login", data={
            "usuario": usuario, "clave": clave, "csrf_token": self.token()},
            follow_redirects=True)

    def post(self, ruta, datos, token_de="/catalogo"):
        datos = dict(datos, csrf_token=self.token(token_de))
        return self.c.post(ruta, data=datos, follow_redirects=True)


PRODUCTO_OK = dict(codigo="TEC-050", nombre="Webcam HD", categoria="Tecnología",
                   proveedor="TecnoSur S.A.", precio="75.50", stock="20")


class TestAutenticacion(Base):
    def test_catalogo_exige_login(self):
        r = self.c.get("/catalogo")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.location)

    def test_login_correcto(self):
        r = self.entrar("cliente1")
        self.assertIn("Catálogo de productos", r.get_data(as_text=True))

    def test_login_clave_incorrecta(self):
        r = self.entrar("cliente1", "mala")
        self.assertIn("incorrectos", r.get_data(as_text=True))

    def test_mensaje_igual_si_usuario_no_existe(self):
        a = self.entrar("noexiste", "x").get_data(as_text=True)
        self.assertIn("Usuario o contraseña incorrectos", a)

    def test_logout_solo_por_post(self):
        self.entrar("cliente1")
        self.assertEqual(self.c.get("/logout").status_code, 405)
        r = self.post("/logout", {})
        self.assertIn("Sesión cerrada", r.get_data(as_text=True))
        self.assertEqual(self.c.get("/catalogo").status_code, 302)

    def test_contrasenas_cifradas_en_base_de_datos(self):
        import sqlite3
        db = sqlite3.connect(webapp.DB_PATH)
        claves = [f[0] for f in db.execute("SELECT clave FROM usuarios")]
        db.close()
        for clave in claves:
            self.assertNotEqual(clave, "1234")
            self.assertTrue(clave.startswith(("scrypt:", "pbkdf2:")))


class TestFuerzaBruta(Base):
    def test_bloqueo_tras_5_intentos(self):
        for _ in range(5):
            self.entrar("cliente1", "mala")
        # Aun con la clave correcta, ya debe estar bloqueado
        r = self.entrar("cliente1", "1234")
        self.assertEqual(r.status_code, 429)
        self.assertIn("Demasiados intentos", r.get_data(as_text=True))


class TestRoles(Base):
    def test_cliente_no_crea_productos(self):
        self.entrar("cliente1")
        r = self.c.get("/producto/nuevo", follow_redirects=True)
        self.assertIn("No tienes permiso", r.get_data(as_text=True))
        r = self.post("/producto/nuevo", PRODUCTO_OK)
        self.assertIn("No tienes permiso", r.get_data(as_text=True))

    def test_almacen_no_edita_productos(self):
        self.entrar("almacen1")
        r = self.c.get("/producto/1/editar", follow_redirects=True)
        self.assertIn("No tienes permiso", r.get_data(as_text=True))

    def test_vendedor_si_crea_producto(self):
        self.entrar("vendedor1")
        r = self.post("/producto/nuevo", PRODUCTO_OK)
        html = r.get_data(as_text=True)
        self.assertIn("registrado correctamente", html)
        self.assertIn("Webcam HD", html)

    def test_cliente_no_ve_boton_nuevo(self):
        r = self.entrar("cliente1")
        self.assertNotIn("Nuevo producto", r.get_data(as_text=True))


class TestCatalogo(Base):
    def test_busqueda_y_filtro(self):
        self.entrar("cliente1")
        r = self.c.get("/catalogo?q=teclado").get_data(as_text=True)
        self.assertIn("Teclado", r)
        self.assertNotIn("Monitor", r)
        r = self.c.get("/catalogo?categoria=Hogar").get_data(as_text=True)
        self.assertIn("Mochila", r)
        self.assertNotIn("Teclado", r)

    def test_edicion_y_alerta_poco_stock(self):
        self.entrar("vendedor1")
        datos = dict(PRODUCTO_OK, codigo="TEC-001", nombre="Auriculares Pro", stock="3")
        r = self.post("/producto/1/editar", datos, token_de="/producto/1/editar")
        html = r.get_data(as_text=True)
        self.assertIn("Auriculares Pro", html)
        self.assertIn("poco stock", html)

    def test_producto_inexistente(self):
        self.entrar("vendedor1")
        r = self.c.get("/producto/9999/editar", follow_redirects=True)
        self.assertIn("no existe", r.get_data(as_text=True))


class TestValidacion(Base):
    def setUp(self):
        super().setUp()
        self.entrar("vendedor1")

    def intentar(self, **cambios):
        return self.post("/producto/nuevo", dict(PRODUCTO_OK, **cambios),
                         token_de="/producto/nuevo").get_data(as_text=True)

    def test_codigo_duplicado(self):
        self.assertIn("Ya existe", self.intentar(codigo="TEC-001"))

    def test_precio_no_numerico(self):
        self.assertIn("números válidos", self.intentar(precio="abc"))

    def test_precio_nan_o_infinito(self):
        self.assertIn("no es válido", self.intentar(precio="nan"))
        self.assertIn("no es válido", self.intentar(precio="inf"))

    def test_negativos(self):
        self.assertIn("negativos", self.intentar(precio="-5"))
        self.assertIn("negativos", self.intentar(stock="-1"))

    def test_fuera_de_rango(self):
        self.assertIn("fuera del rango", self.intentar(precio="99999999"))

    def test_codigo_con_caracteres_raros(self):
        self.assertIn("solo puede tener", self.intentar(codigo="A';DROP TABLE--"))

    def test_texto_demasiado_largo(self):
        self.assertIn("demasiado largo", self.intentar(nombre="x" * 500))

    def test_campos_vacios(self):
        self.assertIn("Completa todos", self.intentar(nombre=""))


class TestAtaquesComunes(Base):
    def test_sql_injection_en_login(self):
        r = self.entrar("' OR '1'='1' --", "' OR '1'='1")
        self.assertIn("incorrectos", r.get_data(as_text=True))

    def test_sql_injection_en_busqueda(self):
        self.entrar("cliente1")
        r = self.c.get("/catalogo?q=' OR 1=1 --").get_data(as_text=True)
        self.assertIn("No se encontraron", r)

    def test_xss_se_escapa(self):
        self.entrar("vendedor1")
        malo = "<script>alert(1)</script>"
        r = self.post("/producto/nuevo", dict(PRODUCTO_OK, codigo="XSS-1", nombre=malo),
                      token_de="/producto/nuevo").get_data(as_text=True)
        self.assertNotIn(malo, r)
        self.assertIn("&lt;script&gt;", r)

    def test_post_sin_token_csrf_rechazado(self):
        self.entrar("vendedor1")
        r = self.c.post("/producto/nuevo", data=PRODUCTO_OK)
        self.assertEqual(r.status_code, 400)

    def test_post_con_token_falso_rechazado(self):
        self.entrar("vendedor1")
        r = self.c.post("/producto/nuevo", data=dict(PRODUCTO_OK, csrf_token="falso"))
        self.assertEqual(r.status_code, 400)

    def test_login_sin_token_csrf_rechazado(self):
        r = self.c.post("/login", data={"usuario": "cliente1", "clave": "1234"})
        self.assertEqual(r.status_code, 400)


class TestConfiguracion(Base):
    def test_cabeceras_de_seguridad(self):
        h = self.c.get("/login").headers
        self.assertEqual(h["X-Frame-Options"], "DENY")
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        self.assertIn("default-src 'self'", h["Content-Security-Policy"])
        self.assertEqual(h["Cache-Control"], "no-store")

    def test_cookie_httponly_y_samesite(self):
        r = self.c.get("/login")
        cookie = r.headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)

    def test_depuracion_desactivada(self):
        self.assertFalse(webapp.app.debug)

    def test_pagina_404_sin_detalles_internos(self):
        r = self.c.get("/ruta-inventada")
        self.assertEqual(r.status_code, 404)
        html = r.get_data(as_text=True)
        self.assertIn("no existe", html)
        self.assertNotIn("Traceback", html)

    def test_clave_secreta_no_esta_en_el_codigo(self):
        codigo = open(webapp.__file__, encoding="utf-8").read()
        self.assertNotIn(webapp.app.secret_key, codigo)


def tearDownModule():
    try:
        os.unlink(_tmp.name)
    except OSError:
        pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
