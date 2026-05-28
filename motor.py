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
import matplotlib.pyplot as plt
import contextily as ctx
from matplotlib_scalebar.scalebar import ScaleBar
from rasterio.plot import show
import subprocess
import matplotlib.patches as mpatches
import gc  # NUEVO: El recolector de basura para liberar RAM

def ejecutar_pipeline(ruta_shp, carpeta_salida, rinde_min, rinde_max, lote, establecimiento, cultivo):
    print("--- INICIANDO PIPELINE AGRONÓMICO (MODO BAJO CONSUMO) ---")
    columna_rinde = 'VRYIELDMAS'
    
    # 1. CARGA
    print("1/8 Cargando archivo crudo...")
    mapa_crudo = gpd.read_file(ruta_shp)
    
    if mapa_crudo.crs is None:
        mapa_crudo.set_crs("EPSG:4326", inplace=True)
    elif mapa_crudo.crs != "EPSG:4326":
        mapa_crudo = mapa_crudo.to_crs("EPSG:4326")
        
    # 2. GRILLA H3
    print("2/8 Calculando grilla H3...")
    RESOLUCION = 13
    mapa_crudo['hex_id'] = mapa_crudo.geometry.apply(lambda geom: h3.latlng_to_cell(geom.y, geom.x, RESOLUCION))
    mapa_crudo[columna_rinde] = pd.to_numeric(mapa_crudo[columna_rinde], errors='coerce')
    
    grilla_agrupada = mapa_crudo.groupby('hex_id')[columna_rinde].mean().reset_index()
    grilla_agrupada['geometry'] = grilla_agrupada['hex_id'].apply(lambda hid: Polygon([(lon, lat) for lat, lon in h3.cell_to_boundary(hid)]))
    mapa_hex = gpd.GeoDataFrame(grilla_agrupada, geometry='geometry', crs="EPSG:4326")
    
    # --- DIETA RAM 1: Borramos el mapa crudo gigante que ya no usamos ---
    del mapa_crudo
    gc.collect()

    # 3. FILTRO Y ESTADÍSTICAS
    print(f"3/8 Filtrando valores entre {rinde_min} y {rinde_max}...")
    mapa_limpio = mapa_hex[(mapa_hex[columna_rinde] >= rinde_min) & (mapa_hex[columna_rinde] <= rinde_max)]
    
    if len(mapa_limpio) == 0:
        raise ValueError(f"¡El mapa quedó vacío tras el filtro!")

    media = mapa_limpio[columna_rinde].mean()
    std = mapa_limpio[columna_rinde].std()
    cv = (std / media) * 100 if media > 0 else 0
    min_real = mapa_limpio[columna_rinde].min()
    max_real = mapa_limpio[columna_rinde].max()

    ruta_txt = os.path.join(carpeta_salida, "estadisticas.txt")
    with open(ruta_txt, "w", encoding="utf-8") as f:
        f.write("--- REPORTE DE LOTE PROCESADO ---\n")
        f.write(f"Rinde Promedio: {media:.2f} t/ha\n")
        f.write(f"Mínimo real: {min_real:.2f} t/ha\n")
        f.write(f"Máximo real: {max_real:.2f} t/ha\n")
        f.write(f"Coef. Variación (CV): {cv:.2f}%\n")
        f.write(f"Hexágonos útiles: {len(mapa_limpio)}\n")

    # 4. INTERPOLACIÓN Y SUAVIZADO
    print("4/8 Interpolando...")
    crs_metros = mapa_limpio.estimate_utm_crs()
    mapa_limpio = mapa_limpio.to_crs(crs_metros)
    puntos = np.array([(geom.centroid.x, geom.centroid.y) for geom in mapa_limpio.geometry])
    valores = mapa_limpio[columna_rinde].values
    
    # --- DIETA RAM 2: Resolución a 10 metros en vez de 5 ---
    # Esto reduce el peso de la matriz en un 75%
    res = 10 
    min_x, min_y, max_x, max_y = mapa_limpio.total_bounds
    grid_x, grid_y = np.meshgrid(np.arange(min_x, max_x, res), np.arange(max_y, min_y, -res))
    
    sup = griddata(puntos, valores, (grid_x, grid_y), method='linear')
    sup = np.where(np.isnan(sup), griddata(puntos, valores, (grid_x, grid_y), method='nearest'), sup)
    sup = gaussian_filter(sup, sigma=2.0)
    
    contorno_suave = mapa_limpio.geometry.unary_union.buffer(15, join_style=1).buffer(-15, join_style=1)
    transform = from_origin(min_x, max_y, res, res)
    mascara = geometry_mask([contorno_suave], transform=transform, invert=True, out_shape=sup.shape)
    sup[~mascara] = np.nan

    # --- DIETA RAM 3: Limpiamos variables pesadas ---
    del puntos, valores, grid_x, grid_y, mascara
    gc.collect()

    # 5. COLORES
    print("5/8 Renderizando colores RGB...")
    datos_validos = sup[~np.isnan(sup)]
    limites = np.percentile(datos_validos, [0, 20, 40, 60, 80, 100])
    colores_hex = ['#d7191c', '#ffb101', '#ffff01', '#17ae00', '#015800']
    cmap = mcolors.ListedColormap(colores_hex)
    norm = mcolors.BoundaryNorm(limites, cmap.N)
    
    imagen_coloreada = cmap(norm(sup))
    imagen_coloreada[np.isnan(sup), 3] = 0.0 
    imagen = (imagen_coloreada * 255).astype(np.uint8)
    
    del sup, datos_validos, imagen_coloreada
    gc.collect()
    
    # 6. GUARDAR GEOTIFF
    print("6/8 Guardando GeoTIFF final...")
    ruta_final_tif = os.path.join(carpeta_salida, "resultado.tif")
    with rasterio.open(
        ruta_final_tif, 'w', driver='GTiff', 
        height=imagen.shape[0], width=imagen.shape[1], 
        count=4, dtype='uint8', crs=crs_metros, transform=transform,
        nodata=0 
    ) as dst:
        for i in range(4): dst.write(imagen[:, :, i], i+1)
            
    # 7. GENERAR GEOPDF
    print("7/8 Compilando GeoPDF georreferenciado...")
    ruta_final_pdf = os.path.join(carpeta_salida, "mapa_campo.pdf")
    try:
        subprocess.run(["gdal_translate", "-of", "PDF", ruta_final_tif, ruta_final_pdf], check=True)
    except Exception as e:
        ruta_final_pdf = None

    # 8. COMPOSICIÓN FINAL
    print("8/8 Armando composición cartográfica final profesional...")
    
    fig = plt.figure(figsize=(11.69, 8.27))
    ax_mapa = fig.add_axes([0, 0, 0.75, 1]) 
    ax_info = fig.add_axes([0.78, 0.05, 0.20, 0.90]) 
    ax_mapa.set_axis_off()
    ax_info.set_axis_off()

    src = rasterio.open(ruta_final_tif)
    banda_datos = src.read(1)
    minx, miny, maxx, maxy = src.bounds
    
    margen_encuadre = 100
    
    # --- ARREGLO DE SUPERPOSICIÓN ---
    # zorder=0 manda el satélite al fondo, zorder=1 trae el mapa de rinde adelante
    try:
        # Limitamos el zoom del satélite a 15 para no saturar la memoria
        ctx.add_basemap(ax_mapa, crs=src.crs.to_string(), source=ctx.providers.Esri.WorldImagery, zoom=15, zorder=0)
        ax_mapa.set_xlim(minx - margen_encuadre, maxx + margen_encuadre)
        ax_mapa.set_ylim(miny - margen_encuadre, maxy + margen_encuadre)
    except Exception as e:
        print(f"   [Advertencia] Sin satélite de fondo: {e}")

    show(banda_datos, transform=src.transform, ax=ax_mapa, cmap=cmap, norm=norm, alpha=0.8, zorder=1)

    escala = ScaleBar(1, location='lower left', pad=0.5, color='black', box_color='white', box_alpha=0.8)
    ax_mapa.add_artist(escala)

    ax_mapa.annotate('N', xy=(0.03, 0.97), xytext=(0.03, 0.90),
                arrowprops=dict(facecolor='black', width=3, headwidth=10),
                ha='center', va='center', fontsize=18, fontweight='bold',
                xycoords='axes fraction', textcoords='axes fraction',
                bbox=dict(boxstyle='round,pad=0.3', facecolor='white', alpha=0.8, edgecolor='none'))

    texto_titulo = f"REPORTE DE RINDE\n\nCultivo:\n{cultivo.upper()}\n\nEstablecimiento:\n{establecimiento.upper()}\n\nLote:\n{lote.upper()}"
    ax_info.text(0, 1.0, texto_titulo, transform=ax_info.transAxes, 
                 fontsize=15, verticalalignment='top', fontweight='bold', color='#1a202c')

    etiquetas = [f"<= {limites[1]:.2f}", 
                 f"{limites[1]:.2f} - {limites[2]:.2f}", 
                 f"{limites[2]:.2f} - {limites[3]:.2f}", 
                 f"{limites[3]:.2f} - {limites[4]:.2f}", 
                 f"> {limites[4]:.2f}"]
    
    parches = [mpatches.Patch(color=colores_hex[i], label=etiquetas[i]) for i in range(5)]
    leyenda = ax_info.legend(handles=parches, loc='center left', title='Referencias (t/ha)', 
                             frameon=False, fontsize=12, title_fontsize=14)
    leyenda.get_title().set_fontweight('bold')

    ruta_png_final = os.path.join(carpeta_salida, f"Mapa_{lote}_final.png")
    ruta_pdf_final = os.path.join(carpeta_salida, f"Mapa_{lote}_final.pdf")
    
    plt.savefig(ruta_png_final, dpi=300, facecolor='white', bbox_inches=None, pad_inches=0)
    plt.savefig(ruta_pdf_final, dpi=300, facecolor='white', bbox_inches=None, pad_inches=0)
    
    # --- DIETA RAM FINAL ---
    plt.close('all')
    gc.collect()

    return ruta_final_tif, ruta_final_pdf, ruta_txt, ruta_png_final, ruta_pdf_final