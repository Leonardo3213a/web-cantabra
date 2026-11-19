"""
WEB-Cántabra - Indicadores de eficiencia (Sprint 4)

Este módulo SOLO LEE la base de datos: calcula tiempos del proceso, ventas,
cancelaciones, satisfacción del cliente y alertas. No modifica nada.
"""
import csv
import io
from datetime import datetime, timedelta

FORMATO = "%Y-%m-%d %H:%M:%S"

# Pedidos que cuentan como "venta confirmada" (el pago ya fue validado)
ESTADOS_VENTA = ("pago_validado", "en_preparacion", "enviado", "entregado")

# LÍNEA BASE del proceso manual actual (por correo, llamadas y Excel).
# Son valores SUPUESTOS de referencia (datos ficticios permitidos por el enunciado).
# Cámbialos si tienes datos reales de la empresa. Se expresan en horas.
BASE_MANUAL_HORAS = {
    "validacion": 24.0,       # recibir el pedido y confirmar el pago
    "procesamiento": 72.0,    # desde que llega el pedido hasta que sale enviado
}

UMBRAL_PAGO_ATRASADO_H = 24         # pago sin validar por más de 24 h = alerta
UMBRAL_PREPARACION_ATRASADA_H = 48  # pedido en preparación por más de 48 h = alerta
UMBRAL_STOCK_BAJO = 10


# ---------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------
def _fecha(texto):
    return datetime.strptime(texto, FORMATO) if texto else None


def _horas(inicio, fin):
    """Horas entre dos fechas de texto; None si falta alguna."""
    a, b = _fecha(inicio), _fecha(fin)
    if a is None or b is None:
        return None
    return max((b - a).total_seconds() / 3600, 0.0)


def _promedio(valores):
    validos = [v for v in valores if v is not None]
    return round(sum(validos) / len(validos), 2) if validos else None


def _porcentaje(parte, total):
    return round(parte * 100 / total, 1) if total else None


def _desde(dias, ahora):
    """Fecha inicial del periodo (None = sin límite)."""
    return None if not dias else (ahora - timedelta(days=dias)).strftime(FORMATO)


def _comparar(nombre, clave, actual):
    base = BASE_MANUAL_HORAS[clave]
    reduccion = round((base - actual) * 100 / base, 1) if actual is not None else None
    return {"nombre": nombre, "base": base, "actual": actual, "reduccion": reduccion}


# ---------------------------------------------------------------
# Cálculo principal
# ---------------------------------------------------------------
def calcular(db, dias=30, ahora=None):
    """Devuelve todos los indicadores del periodo (dias=0 significa 'todo el historial')."""
    ahora = (ahora or datetime.now()).replace(microsecond=0)
    desde = _desde(dias, ahora)
    filtro = (desde, desde)          # para: (? IS NULL OR p.fecha >= ?)

    def momento(estado):
        return ("(SELECT MIN(h.fecha) FROM historial_pedido h "
                f"WHERE h.pedido_id = p.id AND h.estado = '{estado}')")

    filas = db.execute(
        "SELECT p.id, p.estado, p.total, p.fecha, p.calificacion, "
        f"{momento('pago_validado')} AS t_validado, "
        f"{momento('enviado')} AS t_enviado, "
        f"{momento('entregado')} AS t_entregado, "
        "(SELECT COUNT(*) FROM historial_pedido h WHERE h.pedido_id = p.id "
        " AND h.estado = 'pago_rechazado') AS rechazos "
        "FROM pedidos p WHERE (? IS NULL OR p.fecha >= ?)", filtro).fetchall()

    total = len(filas)
    por_estado = {}
    for f in filas:
        por_estado[f["estado"]] = por_estado.get(f["estado"], 0) + 1

    ventas = [f for f in filas if f["estado"] in ESTADOS_VENTA]
    monto = round(sum(f["total"] for f in ventas), 2)

    tiempos = {
        "validacion":    _promedio(_horas(f["fecha"], f["t_validado"]) for f in filas),
        "preparacion":   _promedio(_horas(f["t_validado"], f["t_enviado"]) for f in filas),
        "procesamiento": _promedio(_horas(f["fecha"], f["t_enviado"]) for f in filas),
        "entrega":       _promedio(_horas(f["t_enviado"], f["t_entregado"]) for f in filas),
        "ciclo":         _promedio(_horas(f["fecha"], f["t_entregado"]) for f in filas),
    }

    calificados = [f["calificacion"] for f in filas if f["calificacion"]]
    distribucion = {n: calificados.count(n) for n in (5, 4, 3, 2, 1)}

    productos = [dict(r) for r in db.execute(
        "SELECT d.codigo_producto AS codigo, d.nombre_producto AS nombre, "
        "SUM(d.cantidad) AS unidades, ROUND(SUM(d.cantidad * d.precio_unitario), 2) AS monto "
        "FROM detalle_pedido d JOIN pedidos p ON p.id = d.pedido_id "
        f"WHERE p.estado IN ({','.join('?' * len(ESTADOS_VENTA))}) AND (? IS NULL OR p.fecha >= ?) "
        "GROUP BY d.producto_id, d.codigo_producto, d.nombre_producto "
        "ORDER BY unidades DESC, monto DESC LIMIT 5", (*ESTADOS_VENTA, *filtro)).fetchall()]

    categorias = [dict(r) for r in db.execute(
        "SELECT pr.categoria AS categoria, SUM(d.cantidad) AS unidades, "
        "ROUND(SUM(d.cantidad * d.precio_unitario), 2) AS monto "
        "FROM detalle_pedido d JOIN pedidos p ON p.id = d.pedido_id "
        "JOIN productos pr ON pr.id = d.producto_id "
        f"WHERE p.estado IN ({','.join('?' * len(ESTADOS_VENTA))}) AND (? IS NULL OR p.fecha >= ?) "
        "GROUP BY pr.categoria ORDER BY monto DESC", (*ESTADOS_VENTA, *filtro)).fetchall()]

    # Alertas: miran la situación ACTUAL (no dependen del periodo elegido)
    limite_pago = (ahora - timedelta(hours=UMBRAL_PAGO_ATRASADO_H)).strftime(FORMATO)
    limite_prep = (ahora - timedelta(hours=UMBRAL_PREPARACION_ATRASADA_H)).strftime(FORMATO)
    alertas = {
        "pagos_atrasados": db.execute(
            "SELECT COUNT(*) FROM pedidos WHERE estado = 'por_validar' AND fecha <= ?",
            (limite_pago,)).fetchone()[0],
        "preparacion_atrasada": db.execute(
            "SELECT COUNT(*) FROM pedidos p WHERE p.estado = 'en_preparacion' AND "
            "(SELECT MIN(h.fecha) FROM historial_pedido h WHERE h.pedido_id = p.id "
            " AND h.estado = 'en_preparacion') <= ?", (limite_prep,)).fetchone()[0],
        "stock_bajo": db.execute(
            "SELECT COUNT(*) FROM productos WHERE stock < ?", (UMBRAL_STOCK_BAJO,)).fetchone()[0],
    }

    return {
        "dias": dias,
        "total_pedidos": total,
        "por_estado": por_estado,
        "ventas": {"pedidos": len(ventas), "monto": monto,
                   "ticket": round(monto / len(ventas), 2) if ventas else None},
        "tasa_cancelacion": _porcentaje(por_estado.get("cancelado", 0), total),
        "tasa_rechazo_pago": _porcentaje(sum(1 for f in filas if f["rechazos"]), total),
        "tiempos": tiempos,
        "comparativo": [
            _comparar("Validación del pago", "validacion", tiempos["validacion"]),
            _comparar("Procesamiento del pedido (recibido → enviado)", "procesamiento", tiempos["procesamiento"]),
        ],
        "satisfaccion": {
            "promedio": round(sum(calificados) / len(calificados), 2) if calificados else None,
            "n": len(calificados),
            "distribucion": distribucion,
        },
        "top_productos": productos,
        "por_categoria": categorias,
        "alertas": alertas,
    }


# ---------------------------------------------------------------
# Exportación a CSV (con protección contra "inyección de fórmulas")
# ---------------------------------------------------------------
def neutralizar_celda(valor):
    """Excel ejecuta como fórmula cualquier texto que empiece con = + - @.
    Si un usuario escribe '=cmd|...' en un campo, al abrir el CSV podría ejecutarse.
    Se evita anteponiendo una comilla simple."""
    if valor is None:
        return ""
    texto = str(valor)
    if texto and texto[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + texto
    return texto


def exportar_pedidos_csv(db, dias=30, ahora=None):
    ahora = (ahora or datetime.now()).replace(microsecond=0)
    desde = _desde(dias, ahora)
    filas = db.execute(
        "SELECT p.id, p.fecha, u.nombre AS cliente, p.estado, p.total, p.metodo_pago, "
        "p.transportista, p.guia_envio, p.fecha_envio, p.fecha_entrega, p.calificacion "
        "FROM pedidos p JOIN usuarios u ON u.id = p.cliente_id "
        "WHERE (? IS NULL OR p.fecha >= ?) ORDER BY p.id", (desde, desde)).fetchall()

    salida = io.StringIO()
    escritor = csv.writer(salida)
    escritor.writerow(["pedido", "fecha", "cliente", "estado", "total", "metodo_pago",
                       "transportista", "guia_envio", "fecha_envio", "fecha_entrega", "calificacion"])
    for f in filas:
        escritor.writerow([neutralizar_celda(f["id"] and f"PED-{f['id']:05d}"),
                           neutralizar_celda(f["fecha"]), neutralizar_celda(f["cliente"]),
                           neutralizar_celda(f["estado"]), f"{f['total']:.2f}",
                           neutralizar_celda(f["metodo_pago"]), neutralizar_celda(f["transportista"]),
                           neutralizar_celda(f["guia_envio"]), neutralizar_celda(f["fecha_envio"]),
                           neutralizar_celda(f["fecha_entrega"]), neutralizar_celda(f["calificacion"])])
    return salida.getvalue()


# ---------------------------------------------------------------
# Texto legible para duraciones (también lo usan las pantallas)
# ---------------------------------------------------------------
def texto_duracion(horas):
    """Convierte horas en texto fácil de leer: minutos, horas o días."""
    if horas is None:
        return "—"
    if horas < 1:
        return f"{round(horas * 60)} min"
    if horas < 48:
        return f"{horas:.1f} h"
    return f"{horas / 24:.1f} días"


# ---------------------------------------------------------------
# Exportación a EXCEL (.xlsx): columnas separadas, formato y gráficos listos
# ---------------------------------------------------------------
# Un archivo .xlsx guarda cada dato en su propia celda, así que se ve ordenado en
# cualquier Excel, sin importar la configuración regional (a diferencia del CSV,
# que depende del separador de listas de cada computadora).
ETIQUETAS_ESTADO = {
    "por_validar": "Pago por validar", "pago_rechazado": "Pago rechazado", "pago_validado": "Pago validado",
    "en_preparacion": "En preparación", "enviado": "Enviado", "entregado": "Entregado", "cancelado": "Cancelado",
}
COLORES_ESTADO = {   # colores suaves de relleno para la columna Estado
    "por_validar": "FFF2CC", "pago_rechazado": "F8CBAD", "pago_validado": "C6E0B4",
    "en_preparacion": "BDD7EE", "enviado": "DDEBF7", "entregado": "A9D08E", "cancelado": "D9D9D9",
}
FORMATO_SOLES = '"S/ "#,##0.00'
FORMATO_FECHA = "dd/mm/yyyy hh:mm"
CARACTERES_ILEGALES = "".join(chr(c) for c in list(range(0, 9)) + [11, 12] + list(range(14, 32)))


def _limpiar_texto(valor):
    """Quita caracteres de control que Excel no admite (evita errores al guardar)."""
    return "".join(c for c in valor if c not in CARACTERES_ILEGALES)


def exportar_excel(db, dias=30, ahora=None, etiquetas=None):
    """Genera el reporte completo en Excel y devuelve los bytes del archivo .xlsx."""
    from openpyxl import Workbook                                   # se importa aquí: si falta, la app sigue funcionando
    from openpyxl.chart import BarChart, Reference
    from openpyxl.formatting.rule import CellIsRule, FormulaRule
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    ahora = (ahora or datetime.now()).replace(microsecond=0)
    etiquetas = {**ETIQUETAS_ESTADO, **(etiquetas or {})}
    r = calcular(db, dias, ahora)

    azul, azul_claro, gris = "1F4E79", "D9E2F3", "F2F2F2"
    fuente = Font(name="Arial", size=10)
    negrita = Font(name="Arial", size=10, bold=True)
    fuente_cabecera = Font(name="Arial", size=10, bold=True, color="FFFFFF")
    relleno_cabecera = PatternFill("solid", fgColor=azul)
    relleno_seccion = PatternFill("solid", fgColor=azul_claro)
    linea = Side(style="thin", color="BFBFBF")
    borde = Border(left=linea, right=linea, top=linea, bottom=linea)

    def poner(ws, fila, columna, valor, formato=None, estilo=None, relleno=None, alinear=None, con_borde=True):
        celda = ws.cell(row=fila, column=columna)
        if isinstance(valor, str):
            valor = _limpiar_texto(valor)
        celda.value = valor
        if isinstance(valor, str):
            celda.data_type = "s"          # SIEMPRE texto: nunca se interpreta como fórmula
        celda.font = estilo or fuente
        if formato:
            celda.number_format = formato
        if relleno:
            celda.fill = relleno
        if alinear:
            celda.alignment = alinear
        if con_borde:
            celda.border = borde
        return celda

    def cabecera(ws, fila, titulos):
        for i, titulo in enumerate(titulos, start=1):
            poner(ws, fila, i, titulo, estilo=fuente_cabecera, relleno=relleno_cabecera,
                  alinear=Alignment(horizontal="center", vertical="center", wrap_text=True))
        ws.row_dimensions[fila].height = 30

    def anchos(ws, lista):
        for i, ancho in enumerate(lista, start=1):
            ws.column_dimensions[chr(64 + i)].width = ancho

    def seccion(ws, fila, texto, columnas):
        for c in range(1, columnas + 1):
            poner(ws, fila, c, texto if c == 1 else None, estilo=negrita, relleno=relleno_seccion)

    def preparar_impresion(ws):
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True

    def pct(valor):
        return None if valor is None else valor / 100          # Excel guarda los porcentajes como fracción

    def numero_o_guion(valor):
        return "—" if valor is None else valor

    wb = Workbook()

    # ------------------------------------------------------------ Hoja 1: Resumen
    ws = wb.active
    ws.title = "Resumen"
    ws.sheet_view.showGridLines = False
    anchos(ws, [48, 24, 24, 20])
    ws["A1"].value = "WEB-Cántabra — Reporte de eficiencia"
    ws["A1"].font = Font(name="Arial", size=16, bold=True, color=azul)
    ws.merge_cells("A1:D1")
    periodo = "Todo el historial" if not dias else f"Últimos {dias} días"
    poner(ws, 2, 1, "Periodo", estilo=negrita, relleno=PatternFill("solid", fgColor=gris))
    poner(ws, 2, 2, periodo)
    poner(ws, 3, 1, "Generado el", estilo=negrita, relleno=PatternFill("solid", fgColor=gris))
    poner(ws, 3, 2, ahora, formato=FORMATO_FECHA, alinear=Alignment(horizontal="left"))

    fila = 5
    seccion(ws, fila, "Indicadores generales", 4)
    indicadores = [
        ("Pedidos registrados", r["total_pedidos"], "0"),
        ("Ventas confirmadas", r["ventas"]["monto"], FORMATO_SOLES),
        ("Pedidos con venta confirmada", r["ventas"]["pedidos"], "0"),
        ("Ticket promedio", numero_o_guion(r["ventas"]["ticket"]), FORMATO_SOLES),
        ("Tasa de cancelación", numero_o_guion(pct(r["tasa_cancelacion"])), "0.0%"),
        ("Pagos rechazados (% de pedidos)", numero_o_guion(pct(r["tasa_rechazo_pago"])), "0.0%"),
        ("Satisfacción promedio (sobre 5)", numero_o_guion(r["satisfaccion"]["promedio"]), '0.00" ★"'),
        ("Calificaciones recibidas", r["satisfaccion"]["n"], "0"),
    ]
    for etiqueta, valor, formato in indicadores:
        fila += 1
        poner(ws, fila, 1, etiqueta)
        poner(ws, fila, 2, valor, formato=formato, estilo=negrita, alinear=Alignment(horizontal="right"))

    fila += 2
    seccion(ws, fila, "Tiempo promedio por etapa", 4)
    fila += 1
    for i, titulo in enumerate(["Etapa", "Horas", "Equivale a"], start=1):
        poner(ws, fila, i, titulo, estilo=negrita, alinear=Alignment(horizontal="center"))
    etapas = [("Recibido → pago validado", "validacion"), ("Pago validado → enviado", "preparacion"),
              ("Recibido → enviado (procesamiento interno)", "procesamiento"),
              ("Enviado → entregado", "entrega"), ("Ciclo completo (recibido → entregado)", "ciclo")]
    for nombre, clave in etapas:
        fila += 1
        horas = r["tiempos"][clave]
        poner(ws, fila, 1, nombre)
        poner(ws, fila, 2, numero_o_guion(horas), formato='0.0" h"', alinear=Alignment(horizontal="right"))
        poner(ws, fila, 3, texto_duracion(horas), alinear=Alignment(horizontal="right"))

    fila += 2
    seccion(ws, fila, "Mejora frente al proceso manual", 4)
    fila += 1
    for i, titulo in enumerate(["Indicador", "Proceso manual (supuesto)", "Con el sistema", "Reducción"], start=1):
        poner(ws, fila, i, titulo, estilo=fuente_cabecera, relleno=relleno_cabecera,
              alinear=Alignment(horizontal="center", wrap_text=True))
    for c in r["comparativo"]:
        fila += 1
        poner(ws, fila, 1, c["nombre"])
        poner(ws, fila, 2, c["base"], formato='0.0" h"', alinear=Alignment(horizontal="right"))
        poner(ws, fila, 3, numero_o_guion(c["actual"]), formato='0.0" h"', estilo=negrita, alinear=Alignment(horizontal="right"))
        poner(ws, fila, 4, numero_o_guion(pct(c["reduccion"])), formato="0.0%;[Red]-0.0%",
              estilo=negrita, alinear=Alignment(horizontal="right"))
    fila += 1
    ws.cell(row=fila, column=1).value = ("Nota: el «proceso manual» es una línea base SUPUESTA (correo, llamadas y hojas de cálculo), "
                                         "definida en reportes.py. «Con el sistema» se calcula con los pedidos registrados.")
    ws.cell(row=fila, column=1).font = Font(name="Arial", size=9, italic=True, color="595959")
    ws.cell(row=fila, column=1).alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells(start_row=fila, start_column=1, end_row=fila, end_column=4)
    ws.row_dimensions[fila].height = 38

    fila += 2
    seccion(ws, fila, "Alertas actuales", 4)
    for etiqueta, clave in [("Pagos sin validar por más de 24 h", "pagos_atrasados"),
                            ("Pedidos en preparación por más de 48 h", "preparacion_atrasada"),
                            ("Productos con stock bajo", "stock_bajo")]:
        fila += 1
        poner(ws, fila, 1, etiqueta)
        poner(ws, fila, 2, r["alertas"][clave], formato="0", estilo=negrita, alinear=Alignment(horizontal="right"))
    primera_alerta = fila - 2
    ws.conditional_formatting.add(f"B{primera_alerta}:B{fila}", CellIsRule(
        operator="greaterThan", formula=["0"], fill=PatternFill("solid", bgColor="F8CBAD", fgColor="F8CBAD")))

    fila += 2
    seccion(ws, fila, "Satisfacción del cliente", 4)
    fila += 1
    poner(ws, fila, 1, "Calificación", estilo=negrita, alinear=Alignment(horizontal="center"))
    poner(ws, fila, 2, "Cantidad", estilo=negrita, alinear=Alignment(horizontal="center"))
    inicio_dist = fila + 1
    for estrellas in (5, 4, 3, 2, 1):
        fila += 1
        poner(ws, fila, 1, f"{estrellas} estrella" + ("s" if estrellas > 1 else ""))
        poner(ws, fila, 2, r["satisfaccion"]["distribucion"][estrellas], formato="0", alinear=Alignment(horizontal="right"))
    if r["satisfaccion"]["n"]:
        grafico = BarChart()
        grafico.type = "bar"
        grafico.title = "Distribución de calificaciones"
        grafico.legend = None
        grafico.add_data(Reference(ws, min_col=2, min_row=inicio_dist, max_row=fila), titles_from_data=False)
        grafico.set_categories(Reference(ws, min_col=1, min_row=inicio_dist, max_row=fila))
        grafico.y_axis.title = "Cantidad"
        grafico.height, grafico.width = 7, 14
        ws.add_chart(grafico, f"F{inicio_dist - 2}")
    preparar_impresion(ws)

    # ------------------------------------------------------------ Hoja 2: Pedidos
    wp = wb.create_sheet("Pedidos")
    titulos = ["Pedido", "Fecha", "Cliente", "Estado", "Total", "Método de pago", "Transportista",
               "Guía de envío", "Fecha de envío", "Fecha de entrega", "Calificación"]
    cabecera(wp, 1, titulos)
    anchos(wp, [13, 17, 22, 18, 14, 25, 20, 18, 17, 17, 13])
    desde = _desde(dias, ahora)
    pedidos = db.execute(
        "SELECT p.id, p.fecha, u.nombre AS cliente, p.estado, p.total, p.metodo_pago, p.transportista, "
        "p.guia_envio, p.fecha_envio, p.fecha_entrega, p.calificacion "
        "FROM pedidos p JOIN usuarios u ON u.id = p.cliente_id "
        "WHERE (? IS NULL OR p.fecha >= ?) ORDER BY p.id", (desde, desde)).fetchall()
    centro = Alignment(horizontal="center")
    for i, p in enumerate(pedidos, start=2):
        poner(wp, i, 1, f"PED-{p['id']:05d}", alinear=centro)
        poner(wp, i, 2, _fecha(p["fecha"]), formato=FORMATO_FECHA, alinear=centro)
        poner(wp, i, 3, p["cliente"], alinear=Alignment(indent=1))
        poner(wp, i, 4, etiquetas.get(p["estado"], p["estado"]), alinear=centro,
              relleno=PatternFill("solid", fgColor=COLORES_ESTADO.get(p["estado"], "FFFFFF")))
        poner(wp, i, 5, p["total"], formato=FORMATO_SOLES, alinear=Alignment(horizontal="right"))
        poner(wp, i, 6, p["metodo_pago"], alinear=Alignment(indent=1))
        poner(wp, i, 7, p["transportista"], alinear=Alignment(indent=1))
        poner(wp, i, 8, p["guia_envio"], alinear=centro)
        poner(wp, i, 9, _fecha(p["fecha_envio"]), formato=FORMATO_FECHA, alinear=centro)
        poner(wp, i, 10, _fecha(p["fecha_entrega"]), formato=FORMATO_FECHA, alinear=centro)
        poner(wp, i, 11, p["calificacion"], formato='0" ★"', alinear=centro)
    ultima = len(pedidos) + 1
    wp.freeze_panes = "A2"
    wp.auto_filter.ref = f"A1:K{ultima}"
    if pedidos:
        fila_total = ultima + 2
        poner(wp, fila_total, 3, "Totales (según el filtro aplicado)", estilo=negrita, con_borde=False)
        poner(wp, fila_total, 4, f"=SUBTOTAL(103,A2:A{ultima})", formato='0" pedidos"', estilo=negrita,
              alinear=centro, con_borde=False)
        poner(wp, fila_total, 5, f"=SUBTOTAL(109,E2:E{ultima})", formato=FORMATO_SOLES, estilo=negrita, con_borde=False)
        wp.cell(row=fila_total, column=4).data_type = "f"
        wp.cell(row=fila_total, column=5).data_type = "f"
        for rango in (f"A2:C{ultima}", f"E2:K{ultima}"):        # la columna Estado conserva su propio color
            wp.conditional_formatting.add(rango, FormulaRule(
                formula=["MOD(ROW(),2)=0"], fill=PatternFill("solid", bgColor=gris, fgColor=gris), stopIfTrue=False))
    preparar_impresion(wp)

    # ------------------------------------------------------------ Hoja 3: Ventas
    wv = wb.create_sheet("Ventas")
    anchos(wv, [16, 36, 14, 16])
    wv["A1"].value = "Productos más vendidos (ventas confirmadas)"
    wv["A1"].font = Font(name="Arial", size=12, bold=True, color=azul)
    cabecera(wv, 2, ["Código", "Producto", "Unidades", "Monto"])
    for i, p in enumerate(r["top_productos"], start=3):
        poner(wv, i, 1, p["codigo"], alinear=centro)
        poner(wv, i, 2, p["nombre"])
        poner(wv, i, 3, p["unidades"], formato="0")
        poner(wv, i, 4, p["monto"], formato=FORMATO_SOLES)
    fin_top = 2 + len(r["top_productos"])
    inicio_cat = max(fin_top, 2) + 3
    wv.cell(row=inicio_cat - 1, column=1).value = "Ventas por categoría"
    wv.cell(row=inicio_cat - 1, column=1).font = Font(name="Arial", size=12, bold=True, color=azul)
    cabecera(wv, inicio_cat, ["Categoría", "", "Unidades", "Monto"])
    for i, c in enumerate(r["por_categoria"], start=inicio_cat + 1):
        poner(wv, i, 1, c["categoria"])
        poner(wv, i, 2, None)
        poner(wv, i, 3, c["unidades"], formato="0")
        poner(wv, i, 4, c["monto"], formato=FORMATO_SOLES)
    fin_cat = inicio_cat + len(r["por_categoria"])
    if r["por_categoria"]:
        g1 = BarChart()
        g1.type = "col"
        g1.title = "Ventas por categoría (S/)"
        g1.legend = None
        g1.add_data(Reference(wv, min_col=4, min_row=inicio_cat + 1, max_row=fin_cat), titles_from_data=False)
        g1.set_categories(Reference(wv, min_col=1, min_row=inicio_cat + 1, max_row=fin_cat))
        g1.height, g1.width = 8, 15
        wv.add_chart(g1, "F2")
    if r["top_productos"]:
        g2 = BarChart()
        g2.type = "bar"
        g2.title = "Unidades vendidas por producto"
        g2.legend = None
        g2.add_data(Reference(wv, min_col=3, min_row=3, max_row=fin_top), titles_from_data=False)
        g2.set_categories(Reference(wv, min_col=2, min_row=3, max_row=fin_top))
        g2.height, g2.width = 8, 15
        wv.add_chart(g2, "F19")
    preparar_impresion(wv)

    # ------------------------------------------------------------ Hoja 4: Stock
    wst = wb.create_sheet("Stock")
    cabecera(wst, 1, ["Código", "Producto", "Categoría", "Proveedor", "Precio", "Stock", "Situación"])
    anchos(wst, [12, 32, 16, 20, 14, 10, 14])
    productos = db.execute("SELECT * FROM productos ORDER BY stock ASC, nombre").fetchall()
    for i, p in enumerate(productos, start=2):
        situacion = "Agotado" if p["stock"] == 0 else ("Stock bajo" if p["stock"] < UMBRAL_STOCK_BAJO else "Normal")
        poner(wst, i, 1, p["codigo"], alinear=centro)
        poner(wst, i, 2, p["nombre"])
        poner(wst, i, 3, p["categoria"])
        poner(wst, i, 4, p["proveedor"])
        poner(wst, i, 5, p["precio"], formato=FORMATO_SOLES)
        poner(wst, i, 6, p["stock"], formato="0", alinear=centro)
        poner(wst, i, 7, situacion, alinear=centro)
    ultimo_stock = len(productos) + 1
    wst.freeze_panes = "A2"
    wst.auto_filter.ref = f"A1:G{ultimo_stock}"
    if productos:
        rango = f"G2:G{ultimo_stock}"
        for texto, color in (("Agotado", "F8CBAD"), ("Stock bajo", "FFF2CC"), ("Normal", "C6E0B4")):
            wst.conditional_formatting.add(rango, CellIsRule(
                operator="equal", formula=[f'"{texto}"'], fill=PatternFill("solid", bgColor=color, fgColor=color)))
    preparar_impresion(wst)

    wb.calculation.fullCalcOnLoad = True        # Excel recalcula los totales al abrir
    salida = io.BytesIO()
    wb.save(salida)
    return salida.getvalue()
