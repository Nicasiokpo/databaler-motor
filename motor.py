import geopandas as gpd
import pandas as pd
import h3
import numpy as np
from shapely.geometry import Polygon
from scipy.interpolate import griddata
from scipy.ndimage import gaussian_filter
import rasterio
from rasterio.transform import from_origin
from rasterio.features import geometry_mask
import matplotlib.colors as mcolors
import os
import subprocess
import gc

def ejecutar_pipeline(ruta_shp, carpeta_salida, rinde_min, rinde_max, lote, establecimiento, cultivo):
    print("--- INICIANDO PIPELINE AGRONÓMICO ---")
    columna_rinde = 'VRYIELDMAS'
    
    # 1. CARGA
    print("1/7 Cargando archivo crudo...")
    mapa_crudo = gpd.read_file(ruta_shp)
    
    if mapa_crudo.crs is None:
        mapa_crudo.set_crs("EPSG:4326", inplace=True)
    elif mapa_crudo.crs != "EPSG:4326":
        mapa_crudo = mapa_crudo.to_crs("EPSG:4326")
        
    # 2. GRILLA H3
    print("2/7 Calculando grilla H3...")
    RESOLUCION = 13
    mapa_crudo['hex_id'] = mapa_crudo.geometry.apply(lambda geom: h3.latlng_to_cell(geom.y, geom.x, RESOLUCION))
    mapa_crudo[columna_rinde] = pd.to_numeric(mapa_crudo[columna_rinde], errors='coerce')
    
    grilla_agrupada = mapa_crudo.groupby('hex_id')[columna_rinde].mean().reset_index()
    grilla_agrupada['geometry'] = grilla_agrupada['hex_id'].apply(lambda hid: Polygon([(lon, lat) for lat, lon in h3.cell_to_boundary(hid)]))
    mapa_hex = gpd.GeoDataFrame(grilla_agrupada, geometry='geometry', crs="EPSG:4326")
    
    del mapa_crudo
    gc.collect()

    # 3. FILTRO Y ESTADÍSTICAS
    print(f"3/7 Filtrando valores entre {rinde_min} y {rinde_max}...")
    mapa_limpio = mapa_hex[(mapa_hex[columna_rinde] >= rinde_min) & (mapa_hex[columna_rinde] <= rinde_max)]
    
    if len(mapa_limpio) == 0:
        raise ValueError(f"¡El mapa quedó vacío tras el filtro!")

    media = mapa_limpio[columna_rinde].mean()
    std = mapa_limpio[columna_rinde].std()
    cv = (std / media) * 100 if media > 0 else 0
    min_real = mapa_limpio[columna_rinde].min()
    max_real = mapa_limpio[columna_rinde].max()

    ruta_txt = os.path.join(carpeta_salida, "estadisticas_lote.txt")
    with open(ruta_txt, "w", encoding="utf-8") as f:
        f.write("--- REPORTE DE LOTE PROCESADO ---\n")
        f.write(f"Establecimiento: {establecimiento}\n")
        f.write(f"Lote: {lote}\n")
        f.write(f"Cultivo: {cultivo}\n")
        f.write("-" * 35 + "\n")
        f.write(f"Rinde Promedio: {media:.2f} t/ha\n")
        f.write(f"Mínimo real: {min_real:.2f} t/ha\n")
        f.write(f"Máximo real: {max_real:.2f} t/ha\n")
        f.write(f"Coef. Variación (CV): {cv:.2f}%\n")
        f.write(f"Hexágonos útiles: {len(mapa_limpio)}\n")

    # 4. INTERPOLACIÓN (Volvemos a alta resolución 5m para recuperar colores)
    print("4/7 Interpolando...")
    crs_metros = mapa_limpio.estimate_utm_crs()
    mapa_limpio = mapa_limpio.to_crs(crs_metros)
    puntos = np.array([(geom.centroid.x, geom.centroid.y) for geom in mapa_limpio.geometry])
    valores = mapa_limpio[columna_rinde].values
    
    res = 5 
    min_x, min_y, max_x, max_y = mapa_limpio.total_bounds
    grid_x, grid_y = np.meshgrid(np.arange(min_x, max_x, res), np.arange(max_y, min_y, -res))
    
    sup = griddata(puntos, valores, (grid_x, grid_y), method='linear')
    sup = np.where(np.isnan(sup), griddata(puntos, valores, (grid_x, grid_y), method='nearest'), sup)
    sup = gaussian_filter(sup, sigma=2.0)
    
    contorno_suave = mapa_limpio.geometry.unary_union.buffer(10, join_style=1).buffer(-10, join_style=1)
    transform = from_origin(min_x, max_y, res, res)
    mascara = geometry_mask([contorno_suave], transform=transform, invert=True, out_shape=sup.shape)
    sup[~mascara] = np.nan

    del puntos, valores, grid_x, grid_y, mascara
    gc.collect()

    # 5. COLORES
    print("5/7 Renderizando colores RGB...")
    datos_validos = sup[~np.isnan(sup)]
    limites = np.percentile(datos_validos, [0, 20, 40, 60, 80, 100])
    colores_hex = ['#d7191c', '#ffb101', '#ffff01', '#17ae00', '#015800']
    cmap = mcolors.ListedColormap(colores_hex)
    norm = mcolors.BoundaryNorm(limites, cmap.N)
    
    # Creamos la imagen coloreada (4 bandas: RGBA)
    imagen_coloreada = cmap(norm(sup))
    
    # FORZAMOS LA TRANSPARENCIA ABSOLUTA EN LOS NULOS
    # Ponemos todas las bandas (RGB y Alfa) en 0 donde no hay datos
    imagen_coloreada[np.isnan(sup)] = [0.0, 0.0, 0.0, 0.0] 
    
    imagen = (imagen_coloreada * 255).astype(np.uint8)
    
    del sup, datos_validos, imagen_coloreada
    gc.collect()
    
    # 6. GUARDAR GEOTIFF
    print("6/7 Guardando GeoTIFF final...")
    ruta_final_tif = os.path.join(carpeta_salida, "resultado.tif")
    
    # Al poner nodata=0, le decimos explícitamente a los visores GIS que ignoren los ceros
    with rasterio.open(
        ruta_final_tif, 'w', driver='GTiff', 
        height=imagen.shape[0], width=imagen.shape[1], 
        count=4, dtype='uint8', crs=crs_metros, transform=transform,
        nodata=0,
        compress='lzw' # Agregamos compresión ligera para evitar errores de renderizado
    ) as dst:
        for i in range(4): 
            dst.write(imagen[:, :, i], i+1)
            
    # 7. GENERAR GEOPDF
    print("7/7 Compilando GeoPDF georreferenciado...")
    ruta_final_pdf = os.path.join(carpeta_salida, "MAPA_AVENZA.pdf")
    try:
        subprocess.run(["gdal_translate", "-of", "PDF", ruta_final_tif, ruta_final_pdf], check=True)
    except Exception as e:
        ruta_final_pdf = None

    return ruta_final_tif, ruta_final_pdf, ruta_txt