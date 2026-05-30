import ee
import json
import os
import geopandas as gpd
import requests
import zipfile
import io
import numpy as np
import rasterio
import rasterio.mask
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import subprocess
import gc
from google.oauth2 import service_account

# ==========================================
# CONFIGURACIÓN DE ÍNDICES
# ==========================================
INDICES = {
    'NDVI': {
        'bandas': ['B8', 'B4'],
        'nombre': 'Índice de Vegetación (NDVI)',
        'descripcion': 'Vigor vegetativo general',
        'colores': ['#d7191c', '#ffb101', '#ffff01', '#17ae01', '#015801'],
        'etiquetas': ['Muy bajo', 'Bajo', 'Medio', 'Alto', 'Muy alto'],
    },
    'NDMI': {
        'bandas': ['B8', 'B11'],
        'nombre': 'Índice de Humedad (NDMI)',
        'descripcion': 'Contenido hídrico del cultivo',
        'colores': ['#d7191c', '#fdae61', '#ffffbf', '#74add1', '#2c7bb6'],
        'etiquetas': ['Seco', 'Bajo', 'Medio', 'Húmedo', 'Muy húmedo'],
    },
    'NDRE': {
        'bandas': ['B8', 'B5'],
        'nombre': 'Índice Red Edge (NDRE)',
        'descripcion': 'Contenido de clorofila y nitrógeno',
        'colores': ['#d7191c', '#fdae61', '#ffffbf', '#a6d96a', '#1a9641'],
        'etiquetas': ['Muy bajo', 'Bajo', 'Medio', 'Alto', 'Muy alto'],
    },
}

# ==========================================
# 1. CONEXIÓN CON GOOGLE EARTH ENGINE
# ==========================================
def conectar_satelite():
    key_path = '/etc/secrets/gee_key.json'
    if not os.path.exists(key_path):
        raise RuntimeError("No se encuentra /etc/secrets/gee_key.json")
    try:
        with open(key_path, 'r') as f:
            service_account_info = json.load(f)
        credentials = ee.ServiceAccountCredentials(service_account_info['client_email'], key_file=key_path)
        ee.Initialize(credentials, project='nicasio-mc')
        print("Módulo NDVI: ¡Conexión con Earth Engine OK!")
        return True
    except Exception as e:
        raise RuntimeError(f"Error GEE: {e}")


# ==========================================
# 2. GENERADOR DE LEYENDA PNG
# ==========================================
def generar_leyenda_png(indice, limites, ruta_salida):
    config = INDICES[indice]
    colores = config['colores']
    etiquetas = config['etiquetas']

    fig, ax = plt.subplots(figsize=(4, 2.5))
    ax.set_axis_off()

    ax.text(0.5, 0.97, config['nombre'], transform=ax.transAxes,
            ha='center', va='top', fontsize=9, fontweight='bold', color='#222222')
    ax.text(0.5, 0.86, config['descripcion'], transform=ax.transAxes,
            ha='center', va='top', fontsize=7, color='#555555')

    for i, (color, etiqueta) in enumerate(zip(colores, etiquetas)):
        x = 0.05 + i * 0.19
        ax.add_patch(mpatches.FancyBboxPatch(
            (x, 0.45), 0.17, 0.28,
            boxstyle="round,pad=0.01",
            facecolor=color, edgecolor='white', linewidth=0.5,
            transform=ax.transAxes
        ))
        val_min = f"{limites[i]:.2f}"
        val_max = f"{limites[i+1]:.2f}"
        ax.text(x + 0.085, 0.38, f"{val_min} - {val_max}",
                transform=ax.transAxes, ha='center', va='top',
                fontsize=5.5, color='#333333')
        ax.text(x + 0.085, 0.28, etiqueta,
                transform=ax.transAxes, ha='center', va='top',
                fontsize=6, color='#333333', style='italic')

    ax.text(0.5, 0.1, f"Percentiles 0-20-40-60-80-100 del lote",
            transform=ax.transAxes, ha='center', va='top',
            fontsize=5.5, color='#888888')

    plt.tight_layout(pad=0.3)
    plt.savefig(ruta_salida, dpi=150, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    plt.close()


# ==========================================
# 3. MOTOR PRINCIPAL
# ==========================================
def procesar_lote_gee(ruta_shp, fecha_inicio, fecha_fin, carpeta_salida,
                      indice='NDVI', nubosidad_max=20):

    if indice not in INDICES:
        raise ValueError(f"Indice '{indice}' no valido. Usar: NDVI, NDMI o NDRE.")

    config = INDICES[indice]
    conectar_satelite()

    # Nombre base del lote desde el shapefile
    nombre_lote = os.path.splitext(os.path.basename(ruta_shp))[0]

    # 1. Leer el poligono y pasarlo a Lat/Lon
    gdf = gpd.read_file(ruta_shp)
    gdf_4326 = gdf.to_crs(epsg=4326)

    geom = gdf_4326.geometry.iloc[0]
    if geom.geom_type == 'Polygon':
        coords = [[c[0], c[1]] for c in list(geom.exterior.coords)]
    elif geom.geom_type == 'MultiPolygon':
        coords = [[c[0], c[1]] for c in list(geom.geoms[0].exterior.coords)]

    zona_interes = ee.Geometry.Polygon([coords])

    # 2. Consultar Sentinel-2
    coleccion = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
                 .filterBounds(zona_interes)
                 .filterDate(fecha_inicio, fecha_fin)
                 .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', nubosidad_max)))

    imagen_limpia = coleccion.median().clip(zona_interes)

    # 3. Calcular solo el indice solicitado
    banda_a = config['bandas'][0]
    banda_b = config['bandas'][1]
    imagen_indice = imagen_limpia.normalizedDifference([banda_a, banda_b]).rename(indice)

    # 4. Descarga del TIFF crudo
    url = imagen_indice.getDownloadURL({
        'scale': 10,
        'crs': 'EPSG:4326',
        'region': zona_interes,
        'format': 'GEO_TIFF'
    })

    respuesta = requests.get(url)
    print(f"DEBUG GEE: status={respuesta.status_code}, size={len(respuesta.content)}")

    if respuesta.status_code != 200:
        raise ValueError(f"GEE respondio con error {respuesta.status_code}: {respuesta.text[:300]}")

    # Guardar TIF crudo (para QGIS con valores reales)
    nombre_tif_crudo = f"{nombre_lote}_{indice}_valores.tif"
    ruta_cruda_tif = os.path.join(carpeta_salida, nombre_tif_crudo)
    with open(ruta_cruda_tif, "wb") as f:
        f.write(respuesta.content)

    # 5. Procesamiento con recorte exacto al poligono
    with rasterio.open(ruta_cruda_tif) as src:
        out_image, out_transform = rasterio.mask.mask(src, [geom], crop=True, nodata=np.nan)
        matriz = out_image[0]
        transform = out_transform
        crs_original = src.crs

    mascara_validos = (~np.isnan(matriz)) & (matriz >= -1) & (matriz <= 1)
    datos_filtrados = matriz[mascara_validos]

    if len(datos_filtrados) == 0:
        raise ValueError("El archivo satelital no contiene pixeles validos dentro del lote.")

    # 6. Estadisticas
    val_min = float(np.min(datos_filtrados))
    val_max = float(np.max(datos_filtrados))
    val_prom = float(np.mean(datos_filtrados))
    val_std = float(np.std(datos_filtrados))

    nombre_txt = f"{nombre_lote}_{indice}_estadisticas.txt"
    ruta_txt = os.path.join(carpeta_salida, nombre_txt)
    with open(ruta_txt, 'w') as f:
        f.write(f"REPORTE {config['nombre'].upper()}\n")
        f.write(f"Lote: {nombre_lote}\n")
        f.write(f"Periodo analizado: {fecha_inicio} al {fecha_fin}\n")
        f.write(f"Nubosidad maxima permitida: {nubosidad_max}%\n")
        f.write(f"Indice calculado: {indice} ({config['descripcion']})\n")
        f.write(f"---------------------------------------\n")
        f.write(f"{indice} Minimo:  {val_min:.4f}\n")
        f.write(f"{indice} Maximo:  {val_max:.4f}\n")
        f.write(f"{indice} Promedio: {val_prom:.4f}\n")
        f.write(f"Desviacion Estandar: {val_std:.4f}\n")

    # 7. Renderizar colores RGB
    limites = np.percentile(datos_filtrados, [0, 20, 40, 60, 80, 100])
    cmap = mcolors.ListedColormap(config['colores'])
    norm = mcolors.BoundaryNorm(limites, cmap.N)

    imagen_coloreada = cmap(norm(matriz))
    imagen_coloreada[~mascara_validos] = [0.0, 0.0, 0.0, 0.0]
    imagen_rgb = (imagen_coloreada * 255).astype(np.uint8)

    del imagen_coloreada
    gc.collect()

    nombre_color_tif = f"{nombre_lote}_{indice}_color.tif"
    ruta_color_tif = os.path.join(carpeta_salida, nombre_color_tif)
    with rasterio.open(
        ruta_color_tif, 'w', driver='GTiff',
        height=imagen_rgb.shape[0], width=imagen_rgb.shape[1],
        count=4, dtype='uint8', crs=crs_original, transform=transform,
        compress='lzw', nodata=0
    ) as dst:
        for i in range(4):
            dst.write(imagen_rgb[:, :, i], i + 1)

    del imagen_rgb, datos_filtrados
    gc.collect()

    # 8. Leyenda PNG
    nombre_leyenda = f"{nombre_lote}_{indice}_leyenda.png"
    ruta_leyenda = os.path.join(carpeta_salida, nombre_leyenda)
    generar_leyenda_png(indice, limites, ruta_leyenda)

    # 9. GeoPDF para Avenza
    nombre_pdf = f"{nombre_lote}_{indice}_avenza.pdf"
    ruta_pdf = os.path.join(carpeta_salida, nombre_pdf)
    cmd_gdal = [
        "gdal_translate", "-of", "PDF",
        "-co", "GEO=ON", "-co", "DPI=150",
        ruta_color_tif, ruta_pdf
    ]
    subprocess.run(cmd_gdal, check=True)

    # 10. Empaquetar ZIP con nombre del lote e indice
    nombre_zip = f"{nombre_lote}_{indice}.zip"
    ruta_zip = os.path.join(carpeta_salida, nombre_zip)
    with zipfile.ZipFile(ruta_zip, 'w') as zipf:
        zipf.write(ruta_color_tif, arcname=f"{nombre_lote}_{indice}_color.tif")
        zipf.write(ruta_cruda_tif, arcname=f"{nombre_lote}_{indice}_valores.tif")
        zipf.write(ruta_pdf, arcname=f"{nombre_lote}_{indice}_avenza.pdf")
        zipf.write(ruta_leyenda, arcname=f"{nombre_lote}_{indice}_leyenda.png")
        zipf.write(ruta_txt, arcname=f"{nombre_lote}_{indice}_estadisticas.txt")

    return ruta_zip, nombre_zip