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

def conectar_satelite():
    key_path = '/etc/secrets/gee_key.json'
    if not os.path.exists(key_path):
        print("ERROR: No se encuentra la llave secreta de GEE.")
        return False
    try:
        with open(key_path, 'r') as f:
            service_account_info = json.load(f)
        credentials = ee.ServiceAccountCredentials(service_account_info['client_email'], key_path)
        ee.Initialize(credentials)
        print("Módulo NDVI: ¡Conexión con Earth Engine OK!")
        return True
    except Exception as e:
        print(f"Módulo NDVI: Error de conexión: {e}")
        return False

def procesar_lote_gee(ruta_shp, fecha_inicio, fecha_fin, carpeta_salida):
    # 1. Leer el polígono y pasarlo a Lat/Lon
    gdf = gpd.read_file(ruta_shp)
    gdf_4326 = gdf.to_crs(epsg=4326)
    
    geom = gdf_4326.geometry.iloc[0]
    if geom.geom_type == 'Polygon':
        coords = list(geom.exterior.coords)
    elif geom.geom_type == 'MultiPolygon':
        coords = list(geom.geoms[0].exterior.coords)
        
    zona_interes = ee.Geometry.Polygon(coords)

    # 2. Consultar Sentinel-2 y armar el compuesto libre de nubes
    coleccion = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
                .filterBounds(zona_interes)
                .filterDate(fecha_inicio, fecha_fin)
                .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20)))
    
    # Si pedís un solo día, la mediana devuelve esa única imagen intacta
    imagen_limpia = coleccion.median().clip(zona_interes)
    
    # 3. Cálculo de bandas del perfil fisiológico
    ndvi = imagen_limpia.normalizedDifference(['B8', 'B4']).rename('NDVI')
    ndmi = imagen_limpia.normalizedDifference(['B8', 'B11']).rename('NDMI')
    ndre = imagen_limpia.normalizedDifference(['B8', 'B5']).rename('NDRE')
    
    imagen_final = ndvi.addBands([ndmi, ndre])
    
    # 4. Descarga del TIFF crudo multipropósito
    url = imagen_final.getDownloadURL({
        'scale': 10,
        'crs': 'EPSG:4326',
        'region': zona_interes,
        'format': 'GEO_TIFF'
    })
    
    respuesta = requests.get(url)
    with zipfile.ZipFile(io.BytesIO(respuesta.content)) as z:
        z.extractall(carpeta_salida)
    
    # GEE suele descargar el archivo con un nombre largo predeterminado, buscamos el .tif
    archivos_en_carpeta = os.listdir(carpeta_salida)
    tif_descargado = [f for f in archivos_en_carpeta if f.endswith('.tif')][0]
    ruta_cruda_tif = os.path.join(carpeta_salida, tif_descargado)
    
    # 5. Procesamiento de Matrices y Estadísticas (Cuidado de RAM)
    with rasterio.open(ruta_cruda_tif) as src:
        # Banda 1 es NDVI
        matriz_ndvi = src.read(1)
        transform = src.transform
        crs_original = src.crs
        
    # Filtrar valores inválidos de fondo
    mascara_validos = (~np.isnan(matriz_ndvi)) & (matriz_ndvi >= -1) & (matriz_ndvi <= 1)
    datos_filtrados = matriz_ndvi[mascara_validos]
    
    if len(datos_filtrados) == 0:
        raise ValueError("El archivo satelital no contiene píxeles válidos dentro del lote.")
        
    # Calcular estadísticas zonales reales
    ndvi_min = float(np.min(datos_filtrados))
    ndvi_max = float(np.max(datos_filtrados))
    ndvi_prom = float(np.mean(datos_filtrados))
    ndvi_std = float(np.std(datos_filtrados))
    
    # Guardar reporte de estadísticas en .txt usando punto como separador decimal estándar
    ruta_txt = os.path.join(carpeta_salida, "estadisticas_ndvi.txt")
    with open(ruta_txt, 'w') as f:
        f.write(f"REPORTE DE VIGOR VEGETATIVO (NDVI)\n")
        f.write(f"Periodo analizado: {fecha_inicio} al {fecha_fin}\n")
        f.write(f"---------------------------------------\n")
        f.write(f"NDVI Minimo: {ndvi_min:.4f}\n")
        f.write(f"NDVI Maximo: {ndvi_max:.4f}\n")
        f.write(f"NDVI Promedio: {ndvi_prom:.4f}\n")
        f.write(f"Desviacion Estandar: {ndvi_std:.4f}\n")

    # 6. Renderizar colores RGB (Rampa Agronómica clásica)
    # Dividimos el lote en 5 categorías de vigor usando percentiles reales del lote
    limites = np.percentile(datos_filtrados, [0, 20, 40, 60, 80, 100])
    
    # Aplicamos la corrección terminando en 01 para que los verdes no se hagan transparentes
    colores_hex = ['#d7191c', '#ffb101', '#ffff01', '#17ae01', '#015801']
    cmap = mcolors.ListedColormap(colores_hex)
    norm = mcolors.BoundaryNorm(limites, cmap.N)
    
    imagen_coloreada = cmap(norm(matriz_ndvi))
    imagen_coloreada[~mascara_validos] = [0.0, 0.0, 0.0, 0.0] # Transparente fuera del lote
    imagen_rgb = (imagen_coloreada * 255).astype(np.uint8)
    
    # Liberar memoria intermedia inmediatamente
    del matriz_ndvi, datos_filtrados, imagen_coloreada
    gc.collect()
    
    # 7. Guardar el mapa de visualización coloreado (GeoTIFF de 4 bandas: R, G, B, Alfa)
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
    
    # 8. Generar el GeoPDF de alta resolución para Avenza Maps usando GDAL
    ruta_pdf = os.path.join(carpeta_salida, "mapa_avenza_ndvi.pdf")
    cmd_gdal = [
        "gdal_translate", "-of", "PDF",
        "-co", "GEO=ON", "-co", "DPI=150",
        ruta_color_tif, ruta_pdf
    ]
    subprocess.run(cmd_gdal, check=True)
    
    # 9. Limpiar archivos intermedios y empaquetar el ZIP de descarga
    os.remove(ruta_cruda_tif) # Borramos el pesado de Google para no ocupar espacio
    
    ruta_zip = os.path.join(carpeta_salida, "resultado_ndvi.zip")
    with zipfile.ZipFile(ruta_zip, 'w') as zipf:
        zipf.write(ruta_color_tif, arcname="mapa_ndvi_color.tif")
        zipf.write(ruta_pdf, arcname="mapa_avenza_ndvi.pdf")
        zipf.write(ruta_txt, arcname="estadisticas_ndvi.txt")
        
    return ruta_zip

# Intentar conectar al cargar el archivo
conectar_satelite()