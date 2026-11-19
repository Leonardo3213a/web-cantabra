"""
Pruebas automáticas de la descarga en Excel (Sprint 4)
Ejecutar desde la carpeta Proyecto:
    python -m unittest discover -s tests -v
"""
import io
import sqlite3
import sys
import unittest
from datetime import datetime
from unittest import mock

from openpyxl import load_workbook

from test_sprint4 import AHORA, Base3, consulta, datos_principales, insertar, webapp

import datos_demo
import reportes

ETIQUETAS = {k: v[0] for k, v in webapp.ESTADOS.items()}


def generar(dias=30, ahora=AHORA):
    db = sqlite3.connect(webapp.DB_PATH)
    db.row_factory = sqlite3.Row
    try:
        return reportes.exportar_excel(db, dias, ahora, etiquetas=ETIQUETAS)
    finally:
        db.close()


def abrir(dias=30, ahora=AHORA):
    return load_workbook(io.BytesIO(generar(dias, ahora)))


def buscar(hoja, texto, columna_valor=2):
    """Devuelve el valor que está a la derecha de una etiqueta de la hoja."""
    for fila in hoja.iter_rows():
        if fila[0].value == texto:
            return fila[columna_valor - 1].value
    raise AssertionError(f"No se encontró «{texto}»")


def formulas(libro):
    return [(h.title, c.coordinate, c.value) for h in libro.worksheets for f in h.iter_rows()
            for c in f if c.data_type == "f"]


class TestContenidoDelExcel(Base3):
    def setUp(self):
        super().setUp()
        datos_principales()

    def test_hojas_cabeceras_y_filtros(self):
        libro = abrir()
        self.assertEqual(libro.sheetnames, ["Resumen", "Pedidos", "Ventas", "Stock"])
        p = libro["Pedidos"]
        self.assertEqual([c.value for c in p[1]], ["Pedido", "Fecha", "Cliente", "Estado", "Total", "Método de pago",
                                                  "Transportista", "Guía de envío", "Fecha de envío",
                                                  "Fecha de entrega", "Calificación"])
        self.assertEqual(p.freeze_panes, "A2")
        self.assertEqual(p.auto_filter.ref, "A1:K6")

    def test_cada_dato_queda_en_su_propia_celda(self):
        """El problema del CSV: todo en una sola columna. Aquí cada dato va separado."""
        p = abrir()["Pedidos"]
        fila = [c.value for c in p[2]]
        self.assertEqual(fila[0], "PED-00001")
        self.assertEqual(fila[2], "Carlos Mendoza")
        self.assertEqual(fila[3], "Entregado")
        self.assertEqual(fila[5], "Yape")
        for valor in fila:
            if isinstance(valor, str):
                self.assertNotIn(",", valor)
                self.assertNotIn(";", valor)

    def test_fechas_y_montos_son_valores_reales(self):
        db = sqlite3.connect(webapp.DB_PATH)
        db.execute("UPDATE pedidos SET fecha_envio = '2026-10-30 16:00:00', fecha_entrega = '2026-10-31 10:00:00', "
                   "transportista = 'Olva Courier', guia_envio = 'OLV-1' WHERE id = 1")
        db.commit(); db.close()
        p = abrir()["Pedidos"]
        self.assertEqual(p["B2"].value, datetime(2026, 10, 30, 8, 0, 0))
        self.assertEqual(p["I2"].value, datetime(2026, 10, 30, 16, 0, 0))
        self.assertEqual(p["J2"].value, datetime(2026, 10, 31, 10, 0, 0))
        self.assertEqual((p["G2"].value, p["H2"].value), ("Olva Courier", "OLV-1"))
        self.assertEqual(p["E2"].value, 100.0)
        self.assertIn("S/", p["E2"].number_format)
        self.assertEqual(p["K2"].value, 5)
        self.assertIsNone(p["K3"].value)                 # sin calificar: celda vacía, no "None"

    def test_los_estados_tienen_color_propio(self):
        p = abrir()["Pedidos"]
        colores = {p.cell(row=f, column=4).value: p.cell(row=f, column=4).fill.fgColor.rgb[-6:] for f in range(2, 7)}
        self.assertEqual(colores["Entregado"], "A9D08E")
        self.assertEqual(colores["Cancelado"], "D9D9D9")
        self.assertEqual(len(set(colores.values())), 5)

    def test_totales_con_formulas_que_respetan_el_filtro(self):
        libro = abrir()
        self.assertEqual(formulas(libro), [("Pedidos", "D8", "=SUBTOTAL(103,A2:A6)"),
                                           ("Pedidos", "E8", "=SUBTOTAL(109,E2:E6)")])

    def test_el_resumen_coincide_con_los_calculos(self):
        r = abrir()["Resumen"]
        self.assertEqual(buscar(r, "Periodo"), "Últimos 30 días")
        self.assertEqual(buscar(r, "Pedidos registrados"), 5)
        self.assertEqual(buscar(r, "Ventas confirmadas"), 150.0)
        self.assertEqual(buscar(r, "Ticket promedio"), 75.0)
        self.assertEqual(buscar(r, "Tasa de cancelación"), 0.2)
        self.assertEqual(buscar(r, "Satisfacción promedio (sobre 5)"), 5.0)
        self.assertEqual(buscar(r, "Validación del pago", 4), 0.917)              # reducción frente al proceso manual
        self.assertEqual(buscar(r, "Validación del pago", 3), 2.0)
        self.assertEqual(buscar(r, "Pagos sin validar por más de 24 h"), 1)

    def test_periodo_todo_el_historial(self):
        self.assertEqual(buscar(abrir(0)["Resumen"], "Periodo"), "Todo el historial")

    def test_hoja_de_ventas_y_graficos(self):
        libro = abrir()
        v = libro["Ventas"]
        self.assertEqual([v.cell(row=3, column=c).value for c in (1, 3, 4)], ["TEC-001", 3, 90.0])
        self.assertEqual(len(v._charts), 2)
        self.assertEqual(len(libro["Resumen"]._charts), 1)           # distribución de calificaciones

    def test_hoja_de_stock_ordenada_de_menor_a_mayor(self):
        s = abrir()["Stock"]
        self.assertEqual(s.max_row, 11)                               # encabezado + 10 productos
        self.assertEqual((s["B2"].value, s["F2"].value, s["G2"].value), ("Termo de acero 1L", 4, "Stock bajo"))
        stocks = [s.cell(row=f, column=6).value for f in range(2, 12)]
        self.assertEqual(stocks, sorted(stocks))
        self.assertEqual(s["G11"].value, "Normal")

    def test_usa_una_fuente_profesional(self):
        libro = abrir()
        self.assertEqual(libro["Pedidos"]["A2"].font.name, "Arial")
        self.assertEqual(libro["Resumen"]["A6"].font.name, "Arial")


class TestSeguridadDelExcel(Base3):
    def test_un_texto_con_formula_nunca_se_ejecuta(self):
        datos_principales()
        malo = '=HYPERLINK("http://malo.example","clic")'
        db = sqlite3.connect(webapp.DB_PATH)
        db.execute("UPDATE usuarios SET nombre = ? WHERE usuario = 'cliente1'", (malo,))
        db.commit(); db.close()
        libro = abrir()
        celda = libro["Pedidos"]["C2"]
        self.assertEqual(celda.value, malo)
        self.assertEqual(celda.data_type, "s")                        # texto, no fórmula
        self.assertEqual([f[1] for f in formulas(libro)], ["D8", "E8"])    # solo nuestros 2 totales

    def test_otros_prefijos_peligrosos_quedan_como_texto(self):
        datos_principales()
        for nombre in ("+cmd|' /C calc'!A0", "-2+3", "@SUM(1+1)"):
            db = sqlite3.connect(webapp.DB_PATH)
            db.execute("UPDATE usuarios SET nombre = ? WHERE usuario = 'cliente1'", (nombre,))
            db.commit(); db.close()
            celda = abrir()["Pedidos"]["C2"]
            self.assertEqual((celda.value, celda.data_type), (nombre, "s"))

    def test_caracteres_de_control_no_rompen_la_descarga(self):
        datos_principales()
        db = sqlite3.connect(webapp.DB_PATH)
        db.execute("UPDATE usuarios SET nombre = ? WHERE usuario = 'cliente1'", ("Carlos\x01\x08 Mendoza",))
        db.commit(); db.close()
        self.assertEqual(abrir()["Pedidos"]["C2"].value, "Carlos Mendoza")


class TestExcelCasosLimite(Base3):
    def test_sin_pedidos_igual_funciona(self):
        libro = abrir()
        self.assertEqual(libro["Pedidos"].max_row, 1)
        self.assertEqual(formulas(libro), [])                         # sin datos no hay totales
        self.assertEqual(len(libro["Ventas"]._charts), 0)
        self.assertEqual(len(libro["Resumen"]._charts), 0)
        self.assertEqual(buscar(libro["Resumen"], "Ticket promedio"), "—")
        self.assertEqual(libro["Stock"].max_row, 11)

    def test_con_datos_de_demostracion(self):
        datos_demo.generar(webapp.DB_PATH, 60, 3, AHORA)
        libro = abrir(0)
        filas = [f[0].value for f in libro["Pedidos"].iter_rows(min_row=2) if str(f[0].value).startswith("PED-")]
        self.assertEqual(len(filas), 60)                           # una fila por pedido (el resto es la fila de totales)
        self.assertEqual(buscar(libro["Resumen"], "Pedidos registrados"), 60)
        self.assertEqual(len(libro["Ventas"]._charts), 2)

    def test_pedidos_en_proceso_dejan_celdas_vacias(self):
        insertar("por_validar", "2026-10-31 09:00:00", 10.0)
        p = abrir()["Pedidos"]
        self.assertEqual(p["D2"].value, "Pago por validar")
        for columna in "GHIJK":
            self.assertIsNone(p[f"{columna}2"].value)

    def test_texto_de_duracion(self):
        self.assertEqual(reportes.texto_duracion(None), "—")
        self.assertEqual(reportes.texto_duracion(0.5), "30 min")
        self.assertEqual(reportes.texto_duracion(3.5), "3.5 h")
        self.assertEqual(reportes.texto_duracion(72), "3.0 días")


class TestDescargaDelExcel(Base3):
    URL = "/reportes/reporte.xlsx"

    def test_el_vendedor_descarga_un_excel_valido(self):
        pid = self.enviado()
        self.cliente.post(f"/pedido/{pid}/entregar")
        r = self.vendedor.c.get(self.URL)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertEqual(r.headers["Content-Disposition"], "attachment; filename=reporte_WEB-Cantabra_30.xlsx")
        self.assertTrue(r.data.startswith(b"PK"))                    # los .xlsx son archivos zip
        libro = load_workbook(io.BytesIO(r.data))
        self.assertEqual(libro["Pedidos"]["E2"].value, 338.8)        # 2 x 89.90 + 1 x 159.00
        self.assertEqual(libro["Pedidos"]["D2"].value, "Entregado")

    def test_permisos(self):
        for s in (self.cliente, self.almacen):
            self.assertIn("No tienes permiso", s.html(self.URL))
        r = webapp.app.test_client().get(self.URL)
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.location)

    def test_el_periodo_no_se_puede_manipular(self):
        r = self.vendedor.c.get(self.URL + '?dias=x"; filename=malo.exe')
        self.assertEqual(r.headers["Content-Disposition"], "attachment; filename=reporte_WEB-Cantabra_30.xlsx")
        r = self.vendedor.c.get(self.URL + "?dias=todos")
        self.assertIn("reporte_WEB-Cantabra_todos.xlsx", r.headers["Content-Disposition"])

    def test_el_boton_solo_lo_ve_el_vendedor(self):
        self.assertIn("Descargar Excel", self.vendedor.html("/reportes"))
        self.assertNotIn("Descargar Excel", self.almacen.html("/reportes"))

    def test_si_falta_openpyxl_la_pagina_explica_que_hacer(self):
        with mock.patch.dict(sys.modules, {"openpyxl": None}):
            r = self.vendedor.get(self.URL)
        self.assertIn("pip install openpyxl", r.get_data(as_text=True))
        self.assertEqual(self.vendedor.get("/reportes").status_code, 200)     # el resto del sistema sigue funcionando

    def test_se_registra_la_exportacion(self):
        self.vendedor.c.get(self.URL)
        with open(webapp.log_seguridad.handlers[0].baseFilename, encoding="utf-8") as f:
            self.assertIn("Exportación Excel", f.read())


if __name__ == "__main__":
    unittest.main(verbosity=2)
