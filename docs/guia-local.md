# WEB-Cántabra — Sistema de pedidos (Sprint 5 mejorado)

**Aplicación de demostración académica** desarrollada en Python, Flask, SQLite, Bootstrap, Jinja2 y openpyxl. Se conserva el proyecto de los sprints 1 a 4 y se añaden campañas semestrales, seguridad de cuenta, comprobantes privados y avisos.

## Iniciar en Windows 10 u 11

1. Descomprime el ZIP y abre la carpeta `Proyecto`.
2. Instala **Python 3.11 o superior** desde python.org. Activa la opción *Add Python to PATH*.
3. Haz doble clic en `iniciar_windows.bat` **o abre `iniciar.py` y pulsa ▶ en Visual Studio Code**. Ambas opciones crean `.venv` e instalan automáticamente las dependencias con internet; no instalan paquetes globalmente.
4. Abre `http://127.0.0.1:5000` en el navegador.
5. Inicia sesión con una cuenta demostrativa y cambia inmediatamente la contraseña desde **Mi cuenta**.

Si prefieres iniciar manualmente desde PowerShell:

```powershell
cd Proyecto
python iniciar.py
```

Para ejecutar `app.py` directamente, instala primero las dependencias en el **mismo intérprete** que lo ejecutará. Es más sencillo abrir `iniciar.py` con el botón ▶ o usar el archivo `.bat`.

**Cuentas de demostración (solo pruebas):** `cliente1`, `cliente2` (clientes), `vendedor1` (vendedor), `almacen1` (almacén). Contraseña inicial: `1234`. Los usuarios de prueba no deben conservarse con estas claves si se publica el sistema.

## Qué funcionalidades incluye

| Parte | Funciones preservadas / nuevas |
| --- | --- |
| Sprint 1 | Login por roles, creación y edición de productos, búsqueda y filtros. |
| Sprint 2 | Carrito, pedidos, validación o rechazo de pagos, reintentos, cancelaciones, devolución de stock y seguimiento. |
| Sprint 3 | Preparación, registro de transportista y guía de envío, entrega, inventario y kardex. |
| Sprint 4 | Calificación de pedidos entregados, métricas y exportación a CSV y Excel. |
| Sprint 5 | Catálogos por año y semestre, publicación de campañas, asignación y reutilización de productos, asociación de cada pedido a su campaña, cambio de contraseña, correo de contacto, carga privada de comprobantes, avisos internos y correo opcional vía SMTP; nuevo diseño institucional y logo original. |

### Campañas semestrales

Ingresa como **vendedor** → `Campañas`. Crea `2027`, semestre `I` (enero a junio) o `II` (julio a diciembre). Las campañas se crean **en borrador**. Abre **Asignar productos**, selecciona los que se mostrarán y pulsa **Publicar**. El cliente verá únicamente campañas publicadas. Puedes volver a ocultarlas. Las fichas de producto son reutilizables entre ediciones y comparten el mismo inventario. Al completar el pedido se guarda la campaña seleccionada; los pedidos históricos conservan sus datos.

La primera vez que se abre esta versión, se crea automáticamente una campaña del semestre actual con todos los productos anteriores, sin borrar productos, usuarios, pedidos ni reportes. Cuando llegue el siguiente semestre, crea y publica su campaña. Las campañas anteriores no se eliminan.

### Seguridad de contraseñas

En **Mi cuenta** el usuario indica contraseña actual, nueva y confirmación. La nueva exige **10–128 caracteres, al menos una mayúscula, una minúscula y un número**; se guarda en hash, y se solicita iniciar sesión otra vez. El login histórico y la protección CSRF se mantienen. La cuenta demo aún comienza con `1234` por compatibilidad con las pruebas originales.

### Comprobantes de pago

El cliente puede adjuntar opcionalmente **PNG, JPG o WEBP de máximo 2 MB** al confirmar un pedido o reintentar un pago rechazado. El archivo se valida y queda fuera del directorio `static`, en `comprobantes_privados`. Solo el propietario del pedido y el personal autorizado pueden acceder al enlace desde **Detalle del pedido**. El vendedor debe **verificar manualmente el pago real**; adjuntar un comprobante no implica aprobación.

### Notificaciones internas y correo SMTP opcional

Se generan avisos al vendedor cuando aparece un pedido o un pago nuevo y al cliente cuando se valida/rechaza un pago, se envía/entrega o cancela un pedido. En **Avisos** puedes verlos y marcarlos como leídos.

Para habilitar además el envío por correo, cada usuario guarda su dirección en **Mi cuenta** y el administrador define las variables de entorno:

```powershell
$env:SMTP_HOST="smtp.example.com"
$env:SMTP_PORT="587"
$env:SMTP_TLS="1"
$env:SMTP_USER="usuario@example.com"
$env:SMTP_PASSWORD="CLAVE_DE_APLICACION"
$env:SMTP_FROM="avisos@example.com"
.\.venv\Scripts\python.exe app.py
```

El ejemplo es ilustrativo: sustituir por datos reales de un SMTP autorizado. **Sin SMTP configurado, los avisos dentro de la aplicación siguen funcionando**; no se envían correos. La aplicación intenta enviarlos directamente y no incluye reintentos ni una cola de entregas garantizadas. Nunca guardes contraseñas SMTP en el código ni en un repositorio.

## Base de datos y copia de seguridad

- Base SQLite: `webcantabra.db` (se crea automáticamente, no se distribuye en el ZIP).
- Código principal: `app.py`; lógica de reportes: `reportes.py`.
- Privados: `secret.key`, `comprobantes_privados/`, `seguridad.log`.
- Para migrar un proyecto previo, **haz primero una copia de `webcantabra.db`**, coloca el archivo antiguo en `Proyecto/` y arranca la nueva versión. La migración agrega tablas y columnas sin eliminar información antigua.
- Para respaldar pedidos y comprobantes **conserva tanto la base de datos como la carpeta de comprobantes**.

## Pruebas

Desde la carpeta `Proyecto`, con dependencias instaladas:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Incluye las pruebas originales de los 4 sprints y nuevas pruebas del Sprint 5. Las pruebas usan una base temporal, no la base real.

## Publicación en internet

La ejecución incluida utiliza `127.0.0.1:5000` para **demostración local**. No constituye un despliegue público; para internet se necesita un servidor WSGI, HTTPS, almacenamiento persistente para SQLite y comprobantes, copias de seguridad, cuentas fuertes y configuración del SMTP si se usa correo. No expongas el servidor de desarrollo de Flask directamente a internet.

**Diseño:** identidad gráfica original azul/celeste de estilo institucional. No utiliza el logotipo oficial de SENATI ni implica afiliación oficial.

## Error: `ModuleNotFoundError: No module named 'PIL'`

`PIL` es el módulo que instala el paquete **Pillow**. El error aparece si VS Code ejecuta `app.py` con un Python sin las dependencias instaladas (por ejemplo, Python 3.14 global) en vez de utilizar el entorno de este proyecto.

**Solución recomendada:** abre `iniciar.py` en VS Code y pulsa ▶ o haz doble clic en `iniciar_windows.bat`. El lanzador crea `.venv` y ejecuta `app.py` con ese entorno.

**Si deseas seguir ejecutando `app.py` directamente desde VS Code:** selecciona `Ctrl+Shift+P` → `Python: Select Interpreter` → `Enter interpreter path` → `Proyecto\.venv\Scripts\python.exe` (después de haber corrido el iniciador una vez). Como alternativa, en PowerShell: `python -m pip install -r requirements.txt` siempre que `python` sea exactamente el intérprete seleccionado.

Para verificarlo: ` .\.venv\Scripts\python.exe -c "from PIL import Image; import flask; print('OK')" `. No borres `webcantabra.db`, `secret.key` ni `comprobantes_privados` al reparar el entorno.
