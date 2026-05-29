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
@app.post("/procesar-ndvi/")
async def procesar_ndvi(
    fecha_inicio: str = Form(...),
    fecha_fin: str = Form(...),
    file: UploadFile = File(...)
):
    # Crear un identificador único para no mezclar peticiones de diferentes usuarios
    id_sesion = str(uuid.uuid4())
    carpeta_trabajo = f"temp_{id_sesion}"
    os.makedirs(carpeta_trabajo, exist_ok=True)
    
    try:
        # Guardar el archivo recibido (asumiendo que viene un .zip con los componentes del .shp)
        ruta_zip_entrada = os.path.join(carpeta_trabajo, file.filename)
        with open(ruta_zip_entrada, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        # Descomprimir el shapefile adentro de la carpeta temporal
        with zipfile.ZipFile(ruta_zip_entrada, 'r') as zip_ref:
            zip_ref.extractall(carpeta_trabajo)
            
        # Buscar el archivo .shp principal desempacado
        archivos = os.listdir(carpeta_trabajo)
        shp_detectado = [f for f in archivos if f.endswith('.shp')][0]
        ruta_shp_final = os.path.join(carpeta_trabajo, shp_detectado)
        
        # Invocar a nuestro nuevo motor satelital de GEE
        ruta_resultado_zip = motor_ndvi.procesar_lote_gee(
            ruta_shp=ruta_shp_final,
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin,
            carpeta_salida=carpeta_trabajo
        )
        
        # Retornar el archivo ZIP al usuario en la web
        return FileResponse(
            path=ruta_resultado_zip, 
            filename="resultado_ndvi.zip", 
            media_type="application/zip"
        )
        
    except Exception as e:
        return {"status": "error", "message": f"Error procesando datos NDVI: {str(e)}"}