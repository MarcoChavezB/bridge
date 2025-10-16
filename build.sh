#!/usr/bin/env bash
# build.sh - automatiza pipeline para usb_printer_bridge (Linux)
set -e
PROJECT_NAME=usb_printer_bridge
VENV_DIR=venv
ARTIFACT_DIR=artifacts
PY_FILE=usb_printer_bridge.py

echo "1) Crear virtualenv e instalar dependencias..."
python3 -m venv ${VENV_DIR}
source ${VENV_DIR}/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "2) Ejecutar chequeo rápido (sin necesidad de impresora):"
python3 - <<'PY'
import sys
print("Chequeo de sintaxis del script:")
import importlib.util, os
spec = importlib.util.spec_from_file_location("mod", "usb_printer_bridge.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
print("OK: módulo cargado")
PY

echo "3) Crear ejecutable con pyinstaller (onefile)..."
pip install pyinstaller
pyinstaller --onefile ${PY_FILE} --name ${PROJECT_NAME}

echo "4) Preparar artefactos..."
rm -rf ${ARTIFACT_DIR}
mkdir -p ${ARTIFACT_DIR}
cp dist/${PROJECT_NAME} ${ARTIFACT_DIR}/
cp ${PY_FILE} ${ARTIFACT_DIR}/
cp requirements.txt ${ARTIFACT_DIR}/

echo "5) Empaquetar artefactos (tar.gz)..."
tar -czf ${PROJECT_NAME}_artifact.tar.gz -C ${ARTIFACT_DIR} .
echo "Artefacto creado: ${PROJECT_NAME}_artifact.tar.gz"

echo "DONE"
