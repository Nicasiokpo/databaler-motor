from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import os
import shutil
import zipfile
import uuid
import traceback
import motor_ndvi
from motor import ejecutar_pipeline
from typing import List, Annotated

app = FastAPI(title="Motor de Rinde LoteLimpio")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

@app.options("/{rest_of_path:path}")
async def preflight_handler(rest_of_path: str):
    return JSONResponse(
        content={},
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        }
    )

# ==========================================
# ENDPOINT 1: PROCESAMIENTO DE MAPAS DE RINDE
# ==========================================
@app.post("/procesar-mapa/")
async def procesar_mapa(
    archivos: list[UploadFile] = File(...),
    rinde_min: float = Form(...),
    rinde_max: float = Form(...),
    lote: str = Form(...),
    establecimiento: str = Form(...),
    cultivo: str = Form(...)
):
    id_proceso = str(uuid.uuid4())
    carpeta_trabajo = os.path.join("temp_uploads", id_proceso)
    os.makedirs(carpeta_trabajo, exist_ok=True)

    try:
        ruta_shp = None
        for archivo in archivos:
            ruta_destino = os.path.join(carpeta_trabajo, archivo.filename)
            with open(ruta_destino, "wb") as buffer:
                shutil.copyfileobj(archivo.file, buffer)
            if archivo.filename.lower().endswith('.shp'):
                ruta_shp = ruta_destino

        tif_resultado, pdf_avenza, txt_resultado = ejecutar_pipeline(
            ruta_shp, carpeta_trabajo, rinde_min, rinde_max, lote, establecimiento, cultivo
        )

        archivo_zip = os.path.join(carpeta_trabajo, f"LoteLimpio_{lote}.zip")
        with zipfile.ZipFile(archivo_zip, 'w') as zipf:
            if os.path.exists(tif_resultado): zipf.write(tif_resultado, "mapa_rinde.tif")
            if pdf_avenza and os.path.exists(pdf_avenza): zipf.write(pdf_avenza, "MAPA_AVENZA.pdf")
            if txt_resultado and os.path.exists(txt_resultado): zipf.write(txt_resultado, "estadisticas_lote.txt")

        return FileResponse(archivo_zip, media_type='application/zip', filename=f"LoteLimpio_{lote}.zip")

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================
# ENDPOINT 2: PROCESAMIENTO SATELITAL
# ==========================================
@app.post("/procesar-ndvi/")
async def procesar_ndvi(
    fecha_inicio: Annotated[str, Form()],
    fecha_fin: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    indice: str = Form(default='NDVI'),
    nubosidad_max: int = Form(default=20)
):
    id_sesion = str(uuid.uuid4())
    carpeta_trabajo = f"temp_{id_sesion}"
    os.makedirs(carpeta_trabajo, exist_ok=True)

    try:
        if indice not in ['NDVI', 'NDMI', 'NDRE']:
            raise ValueError("Indice no valido. Usar: NDVI, NDMI o NDRE.")

        contenido = await file.read()
        if not contenido[:4] == b'PK\x03\x04':
            raise ValueError(f"El archivo recibido no es un ZIP valido. Tamanio: {len(contenido)} bytes.")

        ruta_zip_entrada = os.path.join(carpeta_trabajo, file.filename)
        with open(ruta_zip_entrada, "wb") as buffer:
            buffer.write(contenido)

        with zipfile.ZipFile(ruta_zip_entrada, 'r') as zip_ref:
            zip_ref.extractall(carpeta_trabajo)

        archivos = os.listdir(carpeta_trabajo)
        shp_detectado = [f for f in archivos if f.endswith('.shp')]

        if not shp_detectado:
            raise ValueError("El archivo ZIP no contiene ningun archivo .shp valido.")

        ruta_shp_final = os.path.join(carpeta_trabajo, shp_detectado[0])

        ruta_resultado_zip, nombre_zip = motor_ndvi.procesar_lote_gee(
            ruta_shp=ruta_shp_final,
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin,
            carpeta_salida=carpeta_trabajo,
            indice=indice,
            nubosidad_max=nubosidad_max
        )

        return FileResponse(
            path=ruta_resultado_zip,
            filename=nombre_zip,
            media_type="application/zip"
        )

    except Exception as e:
        return {"status": "error", "message": f"Error procesando datos satelitales: {str(e)}"}