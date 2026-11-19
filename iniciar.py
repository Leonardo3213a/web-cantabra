"""Iniciador para WEB-Cántabra: prepara .venv y ejecuta app.py.

Ejecutar: python iniciar.py (en terminal o con el botón ▶ de VS Code).
No modifica instalaciones globales ni la base SQLite.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

PROYECTO = Path(__file__).resolve().parent
ENTORNO = PROYECTO / ".venv"
PYTHON_ENTORNO = ENTORNO / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def comando(argumentos: list[str], *, accion: str) -> None:
    """Ejecuta un proceso y muestra la causa concreta si falla."""
    try:
        subprocess.run(argumentos, cwd=PROYECTO, check=True)
    except FileNotFoundError:
        raise SystemExit(f"ERROR: No se encuentra el programa para {accion}.") from None
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            f"\nERROR al {accion} (código {exc.returncode}). "
            "Verifica tu conexión a internet, Python y los mensajes anteriores."
        ) from None


def main() -> None:
    os.chdir(PROYECTO)
    print("=" * 51, flush=True)
    print("  WEB-CÁNTABRA | Inicio con dependencias", flush=True)
    print("=" * 51, flush=True)
    print(f"Carpeta: {PROYECTO}", flush=True)

    if not PYTHON_ENTORNO.is_file():
        print("[1/3] Creando un entorno virtual aislado...", flush=True)
        comando([sys.executable, "-m", "venv", str(ENTORNO)], accion="crear el entorno virtual")
    else:
        print("[1/3] Entorno virtual encontrado.", flush=True)

    print("[2/3] Instalando/verificando Flask, Pillow y demás librerías...", flush=True)
    comando([str(PYTHON_ENTORNO), "-m", "pip", "install", "-r", "requirements.txt"],
            accion="instalar dependencias")
    print("[3/3] Ejecutando la aplicación...", flush=True)
    print("Abre: http://127.0.0.1:5000", flush=True)
    print("Para detener el servidor: Ctrl+C", flush=True)
    comando([str(PYTHON_ENTORNO), "app.py"], accion="ejecutar WEB-Cántabra")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nServidor detenido.")
