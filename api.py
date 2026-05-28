from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import os
import shutil
import zipfile
import uuid
from motor import ejecutar_pipeline # Importamos el motor que creamos

app = FastAPI(title="Motor de Rinde Databaler")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.post("/procesar-mapa/")
async def procesar_mapa(archivos: list[UploadFile] = File(...)):
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
            if archivo.filename.lower().endswith('.shp'): ruta_shp = ruta_destino

        # 2. EJECUTAR EL MOTOR (LA MAGIA AGRONÓMICA)
        # Esto genera el .tif coloreado en la carpeta
        # Ejecutamos el motor (ahora recibe 2 variables)
        tif_resultado, pdf_resultado = ejecutar_pipeline(ruta_shp, carpeta_trabajo)

        # Comprimimos ambos en el mismo ZIP
        archivo_zip = os.path.join(carpeta_trabajo, "resultado_final.zip")
        with zipfile.ZipFile(archivo_zip, 'w') as zipf:
            if os.path.exists(tif_resultado):
                zipf.write(tif_resultado, "mapa_rinde_final.tif")
            # Agregamos el PDF al ZIP si se generó bien
            if pdf_resultado and os.path.exists(pdf_resultado):
                zipf.write(pdf_resultado, "MAPA_PARA_AVENZA.pdf")

        return FileResponse(archivo_zip, media_type='application/zip', filename="mapa_procesado.zip")

    except Exception as e:
        import traceback
        print("--- ESTE ES EL ERROR QUE ME TENÉS QUE PASAR ---")
        traceback.print_exc()
        print("------------------------------------------------")
        raise HTTPException(status_code=500, detail=str(e))