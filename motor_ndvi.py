import ee
import json
import os
import geopandas as gpd
import requests
import zipfile
import io
import numpy as np
import rasterio
import matplotlib.colors as mcolors
import subprocess
import gc
import shutil
from google.oauth2 import service_account

def conectar_satelite():
    # ==========================================
# 1. CONEXIÓN CON GOOGLE EARTH ENGINE
# ==========================================
def conectar_satelite():
    key_path = '/etc/secrets/gee_key.json'
    if not os.path.exists(key_path):
        print("ERROR: No se encuentra la llave secreta de GEE.")
        return False
    try:
        # 1. Leemos la llave con el método moderno de Google Auth
        credentials = service_account.Credentials.from_service_account_file(key_path)
        
        # 2. Le inyectamos explícitamente el permiso (scope) de Earth Engine
        scoped_credentials = credentials.with_scopes(['https://www.googleapis.com/auth/earthengine'])
        
        # 3. Inicializamos forzando el proyecto
        ee.Initialize(scoped_credentials, project='nicasio-mc') 
        
        print("Módulo NDVI: ¡Conexión con Earth Engine OK!")
        return True
    except Exception as e:
        print(f"Módulo NDVI: Error de conexión: {e}")
        return False

def procesar_lote_gee(ruta_shp, fecha_inicio, fecha_fin, carpeta_salida):
    conectar_satelite()
    # 1. Leer el polígono y pasarlo a Lat/Lon
    gdf = gpd.read_file(ruta_shp)
    gdf_4326 = gdf.to_crs(epsg=4326)
    
    geom = gdf_4326.geometry.iloc[0]
    if geom.geom_type == 'Polygon':
        coords = [[c[0], c[1]] for c in list(geom.exterior.coords)]
    elif geom.geom_type == 'MultiPolygon':
        coords = [[c[0], c[1]] for c in list(geom.geoms[0].exterior.coords)]
        
    zona_interes = ee.Geometry.Polygon([coords])

    # 2. Consultar Sentinel-2 y armar el compuesto libre de nubes
    coleccion = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
                .filterBounds(zona_interes)
                .filterDate(fecha_inicio, fecha_fin)
                .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20)))
    
    imagen_limpia = coleccion.median().clip(zona_interes)
    
    # 3. Cálculo de bandas
    ndvi = imagen_limpia.normalizedDifference(['B8', 'B4']).rename('NDVI')
    ndmi = imagen_limpia.normalizedDifference(['B8', 'B11']).rename('NDMI')
    ndre = imagen_limpia.normalizedDifference(['B8', 'B5']).rename('NDRE')
    
    imagen_final = ndvi.addBands([ndmi, ndre])
    
    # 4. Descarga del TIFF
    url = imagen_final.getDownloadURL({
        'scale': 10,
        'crs': 'EPSG:4326',
        'region': zona_interes,
        'format': 'GEO_TIFF'
    })
    
    respuesta = requests.get(url)
    
    print(f"DEBUG GEE: status={respuesta.status_code}, content_type={respuesta.headers.get('content-type')}, size={len(respuesta.content)}")
    print(f"DEBUG GEE primeros bytes: {respuesta.content[:200]}")
    
    if respuesta.status_code != 200:
        raise ValueError(f"GEE respondió con error {respuesta.status_code}: {respuesta.text[:300]}")
    
    if not respuesta.content[:4] == b'PK\x03\x04':
        raise ValueError(f"GEE no devolvió un ZIP. Respuesta: {respuesta.content[:300]}")
    
    with zipfile.ZipFile(io.BytesIO(respuesta.content)) as z:
        z.extractall(carpeta_salida)
    
    # 5. Buscar el TIF descargado
    archivos_en_carpeta = os.listdir(carpeta_salida)
    tif_descargado = [f for f in archivos_en_carpeta if f.endswith('.tif')][0]
    ruta_cruda_tif = os.path.join(carpeta_salida, tif_descargado)
    
    # 6. Procesamiento de Matrices y Estadísticas
    with rasterio.open(ruta_cruda_tif) as src:
        matriz_ndvi = src.read(1)
        transform = src.transform
        crs_original = src.crs
        
    mascara_validos = (~np.isnan(matriz_ndvi)) & (matriz_ndvi >= -1) & (matriz_ndvi <= 1)
    datos_filtrados = matriz_ndvi[mascara_validos]
    
    if len(datos_filtrados) == 0:
        raise ValueError("El archivo satelital no contiene píxeles válidos dentro del lote.")
        
    ndvi_min = float(np.min(datos_filtrados))
    ndvi_max = float(np.max(datos_filtrados))
    ndvi_prom = float(np.mean(datos_filtrados))
    ndvi_std = float(np.std(datos_filtrados))
    
    ruta_txt = os.path.join(carpeta_salida, "estadisticas_ndvi.txt")
    with open(ruta_txt, 'w') as f:
        f.write(f"REPORTE DE VIGOR VEGETATIVO (NDVI)\n")
        f.write(f"Periodo analizado: {fecha_inicio} al {fecha_fin}\n")
        f.write(f"---------------------------------------\n")
        f.write(f"NDVI Minimo: {ndvi_min:.4f}\n")
        f.write(f"NDVI Maximo: {ndvi_max:.4f}\n")
        f.write(f"NDVI Promedio: {ndvi_prom:.4f}\n")
        f.write(f"Desviacion Estandar: {ndvi_std:.4f}\n")

    # 7. Renderizar colores RGB
    limites = np.percentile(datos_filtrados, [0, 20, 40, 60, 80, 100])
    colores_hex = ['#d7191c', '#ffb101', '#ffff01', '#17ae01', '#015801']
    cmap = mcolors.ListedColormap(colores_hex)
    norm = mcolors.BoundaryNorm(limites, cmap.N)
    
    imagen_coloreada = cmap(norm(matriz_ndvi))
    imagen_coloreada[~mascara_validos] = [0.0, 0.0, 0.0, 0.0]
    imagen_rgb = (imagen_coloreada * 255).astype(np.uint8)
    
    del matriz_ndvi, datos_filtrados, imagen_coloreada
    gc.collect()
    
    ruta_color_tif = os.path.join(carpeta_salida, "mapa_ndvi_color.tif")
    with rasterio.open(
        ruta_color_tif, 'w', driver='GTiff',
        height=imagen_rgb.shape[0], width=imagen_rgb.shape[1],
        count=4, dtype='uint8', crs=crs_original, transform=transform,
        compress='lzw'
    ) as dst:
        for i in range(4):
            dst.write(imagen_rgb[:, :, i], i+1)
            
    del imagen_rgb
    gc.collect()
    
    # 8. Generar el GeoPDF
    ruta_pdf = os.path.join(carpeta_salida, "mapa_avenza_ndvi.pdf")
    cmd_gdal = [
        "gdal_translate", "-of", "PDF",
        "-co", "GEO=ON", "-co", "DPI=150",
        ruta_color_tif, ruta_pdf
    ]
    subprocess.run(cmd_gdal, check=True)
    
    os.remove(ruta_cruda_tif)
    
    ruta_zip = os.path.join(carpeta_salida, "resultado_ndvi.zip")
    with zipfile.ZipFile(ruta_zip, 'w') as zipf:
        zipf.write(ruta_color_tif, arcname="mapa_ndvi_color.tif")
        zipf.write(ruta_pdf, arcname="mapa_avenza_ndvi.pdf")
        zipf.write(ruta_txt, arcname="estadisticas_ndvi.txt")
        
    return ruta_zip