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