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


def ejecutar_pipeline(ruta_shp, carpeta_salida, rinde_min, rinde_max, lote, establecimiento, cultivo):
    print("--- INICIANDO PIPELINE AGRONÓMICO ---")
    columna_rinde = 'VRYIELDMAS'
    
    # 1. CARGA
    print("1/8 Cargando archivo crudo...")
    mapa_crudo = gpd.read_file(ruta_shp)
    
    # --- ARREGLO DEL CRS ---
    if mapa_crudo.crs is None:
        print("   El mapa no tiene CRS, asumiendo WGS84 (GPS)...")
        mapa_crudo.set_crs("EPSG:4326", inplace=True)
    elif mapa_crudo.crs != "EPSG:4326":
        print("   Reproyectando a WGS84...")
        mapa_crudo = mapa_crudo.to_crs("EPSG:4326")
        
    # 2. GRILLA H3
    print("2/8 Calculando grilla H3...")
    RESOLUCION = 13
    mapa_crudo['hex_id'] = mapa_crudo.geometry.apply(lambda geom: h3.latlng_to_cell(geom.y, geom.x, RESOLUCION))
    
    mapa_crudo[columna_rinde] = pd.to_numeric(mapa_crudo[columna_rinde], errors='coerce')
    
    grilla_agrupada = mapa_crudo.groupby('hex_id')[columna_rinde].mean().reset_index()
    grilla_agrupada['geometry'] = grilla_agrupada['hex_id'].apply(lambda hid: Polygon([(lon, lat) for lat, lon in h3.cell_to_boundary(hid)]))
    mapa_hex = gpd.GeoDataFrame(grilla_agrupada, geometry='geometry', crs="EPSG:4326")
    
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
    
    res = 5
    min_x, min_y, max_x, max_y = mapa_limpio.total_bounds
    grid_x, grid_y = np.meshgrid(np.arange(min_x, max_x, res), np.arange(max_y, min_y, -res))
    
    sup = griddata(puntos, valores, (grid_x, grid_y), method='linear')
    sup = np.where(np.isnan(sup), griddata(puntos, valores, (grid_x, grid_y), method='nearest'), sup)
    sup = gaussian_filter(sup, sigma=2.0)
    
    print("   Recortando contorno...")
    contorno_suave = mapa_limpio.geometry.unary_union.buffer(8, join_style=1).buffer(-8, join_style=1)
    transform = from_origin(min_x, max_y, res, res)
    mascara = geometry_mask([contorno_suave], transform=transform, invert=True, out_shape=sup.shape)
    sup[~mascara] = np.nan

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
            
    # 7. GENERAR GEOPDF PARA AVENZA MAPS
    print("7/8 Compilando GeoPDF georreferenciado...")
    ruta_final_pdf = os.path.join(carpeta_salida, "mapa_campo.pdf")
    try:
        subprocess.run(["gdal_translate", "-of", "PDF", ruta_final_tif, ruta_final_pdf], check=True)
        print("   ¡GeoPDF compilado con éxito!")
    except Exception as e:
        print(f"   [ERROR] GDAL falló: {e}")
        ruta_final_pdf = None

  

    # 8. COMPOSICIÓN FINAL: PANTALLA COMPLETA (Estilo "El Recuerdo")
    print("8/8 Armando composición cartográfica final...")
    
    fig = plt.figure(figsize=(11.69, 8.27))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()

    src = rasterio.open(ruta_final_tif)
    minx, miny, maxx, maxy = src.bounds
    
    # ✅ FIX 1: margen chico para que el lote llene el frame
    margen = 30
    ax.set_xlim(minx - margen, maxx + margen)
    ax.set_ylim(miny - margen, maxy + margen)

    # ✅ FIX 2: satélite PRIMERO (zorder bajo)
    try:
        ctx.add_basemap(ax, crs=src.crs.to_string(), source=ctx.providers.Esri.WorldImagery, zorder=1)
    except Exception as e:
        print(f"   [Advertencia] Sin satélite: {e}")

    # ✅ FIX 3: imshow con la imagen RGBA ya coloreada (no show() con banda cruda)
    extent = [minx, maxx, miny, maxy]
    # Leer las 4 bandas RGBA del GeoTIFF
    rgba = src.read([1, 2, 3, 4])          # shape: (4, H, W)
    rgba = np.moveaxis(rgba, 0, -1)        # shape: (H, W, 4)
    rgba = rgba.astype(np.float32) / 255.0
    ax.imshow(rgba, extent=extent, origin='upper', zorder=2, alpha=0.85,
              aspect='auto', interpolation='nearest')

    # Info box
    texto_info = f"MAPA DE RENDIMIENTO\nCULTIVO: {cultivo.upper()}\nESTABLECIMIENTO: {establecimiento.upper()}\nLOTE: {lote.upper()}"
    ax.text(0.02, 0.98, texto_info, transform=ax.transAxes, fontsize=15,
            verticalalignment='top', fontweight='bold', color='black',
            bbox=dict(boxstyle='square,pad=0.6', facecolor='white', alpha=0.9,
                      edgecolor='black', linewidth=1), zorder=5)

    # Flecha norte
    ax.annotate('N', xy=(0.96, 0.96), xytext=(0.96, 0.89),
                arrowprops=dict(facecolor='black', width=4, headwidth=12),
                ha='center', va='center', fontsize=20, fontweight='bold', color='black',
                xycoords='axes fraction', textcoords='axes fraction',
                bbox=dict(boxstyle='square,pad=0.4', facecolor='white', alpha=0.9,
                          edgecolor='black', linewidth=1), zorder=5)

    # Leyenda
    etiquetas = [f"<= {limites[1]:.2f}", 
                 f"{limites[1]:.2f} - {limites[2]:.2f}", 
                 f"{limites[2]:.2f} - {limites[3]:.2f}", 
                 f"{limites[3]:.2f} - {limites[4]:.2f}", 
                 f"> {limites[4]:.2f}"]
    parches = [mpatches.Patch(color=colores_hex[i], label=etiquetas[i]) for i in range(5)]
    leyenda = ax.legend(handles=parches, loc='lower right', title='Referencias (t/ha)',
                        framealpha=0.9, facecolor='white', edgecolor='black',
                        fontsize=12, title_fontsize=14, bbox_to_anchor=(0.98, 0.02))
    leyenda.get_title().set_fontweight('bold')
    leyenda.get_frame().set_linewidth(1)

    # Escala
    escala = ScaleBar(1, location='lower center', pad=0.5, color='black',
                      box_color='white', box_alpha=0.9)
    ax.add_artist(escala)

    ruta_png_final = os.path.join(carpeta_salida, f"Mapa_{lote}_final.png")
    ruta_pdf_final = os.path.join(carpeta_salida, f"Mapa_{lote}_final.pdf")
    
    plt.savefig(ruta_png_final, dpi=300, bbox_inches='tight', pad_inches=0)
    plt.savefig(ruta_pdf_final, dpi=300, bbox_inches='tight', pad_inches=0)
    plt.close(fig)
    src.close()

    return ruta_final_tif, ruta_final_pdf, ruta_txt, ruta_png_final, ruta_pdf_final