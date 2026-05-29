from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import os
import shutil
import zipfile
import uuid
import traceback
import motor_ndvi 
from motor import ejecutar_pipeline
from typing import List

app = FastAPI(title="Motor de Rinde LoteLimpio")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

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
        # 1. Guardar archivos
        ruta_shp = None
        for archivo in archivos:
            ruta_destino = os.path.join(carpeta_trabajo, archivo.filename)
            with open(ruta_destino, "wb") as buffer:
                shutil.copyfileobj(archivo.file, buffer)
            if archivo.filename.lower().endswith('.shp'): 
                ruta_shp = ruta_destino

        # 2. EJECUTAR MOTOR (Limpio y directo)
        tif_resultado, pdf_avenza, txt_resultado = ejecutar_pipeline(
            ruta_shp, carpeta_trabajo, rinde_min, rinde_max, lote, establecimiento, cultivo
        )

        # 3. EMPAQUETADO FINAL EN ZIP
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
# ENDPOINT 2: PROCESAMIENTO SATELITAL (NDVI)
# ==========================================

# ==========================================
# ENDPOINT 2: PROCESAMIENTO SATELITAL (NDVI)
# ==========================================
# ==========================================
# ENDPOINT 2: PROCESAMIENTO SATELITAL (NDVI)
# ==========================================
@app.post("/procesar-ndvi/")
async def procesar_ndvi(
    fecha_inicio: str = Form(...),
    fecha_fin: str = Form(...),
    file: UploadFile = File(...)
):
    id_sesion = str(uuid.uuid4())
    carpeta_trabajo = f"temp_{id_sesion}"
    os.makedirs(carpeta_trabajo, exist_ok=True)
    
    try:
        # 1. Leer el contenido completo en memoria primero
        contenido = await file.read()
        
        # 2. Validar que realmente sea un ZIP antes de guardar
        # Agregamos que nos muestre qué bytes recibió si falla
        if not contenido[:4] == b'PK\x03\x04':
            raise ValueError(f"El archivo recibido no es un ZIP válido. Tamaño: {len(contenido)} bytes. Inicia con: {contenido[:4]}")
        
        # 3. Guardar el ZIP
        ruta_zip_entrada = os.path.join(carpeta_trabajo, file.filename)
        with open(ruta_zip_entrada, "wb") as buffer:
            buffer.write(contenido)
            
        # 4. Descomprimir
        with zipfile.ZipFile(ruta_zip_entrada, 'r') as zip_ref:
            zip_ref.extractall(carpeta_trabajo)
            
        # 5. Buscar el .shp
        archivos = os.listdir(carpeta_trabajo)
        shp_detectado = [f for f in archivos if f.endswith('.shp')]
        
        if not shp_detectado:
            raise ValueError("El archivo ZIP no contiene ningún archivo .shp válido.")
            
        ruta_shp_final = os.path.join(carpeta_trabajo, shp_detectado[0])
        
        # 6. Invocar al motor satelital
        ruta_resultado_zip = motor_ndvi.procesar_lote_gee(
            ruta_shp=ruta_shp_final,
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin,
            carpeta_salida=carpeta_trabajo
        )
        
        # 7. Devolver el mapa procesado
        return FileResponse(
            path=ruta_resultado_zip, 
            filename="resultado_ndvi.zip", 
            media_type="application/zip"
        )
        
    except Exception as e:
        return {"status": "error", "message": f"Error procesando datos NDVI: {str(e)}"}