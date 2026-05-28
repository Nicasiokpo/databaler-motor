import ee
import json
import os

def conectar_satelite():
    # Vamos a verificar si el archivo existe ANTES de intentar abrirlo
    key_path = '/etc/secrets/gee_key.json'
    print(f"DEBUG: Buscando llave en {key_path}")
    
    if not os.path.exists(key_path):
        print("ERROR CRÍTICO: ¡No encuentro el archivo de llave en la ruta esperada!")
        return False

    try:
        with open(key_path, 'r') as f:
            service_account_info = json.load(f)
            
        credentials = ee.ServiceAccountCredentials(
            service_account_info['client_email'], 
            key_path
        )
        
        ee.Initialize(credentials)
        print("Módulo NDVI: ¡Conexión con Earth Engine OK!")
        return True
    except Exception as e:
        print(f"Módulo NDVI: Falló la inicialización. Detalle: {str(e)}")
        return False

# Ejecutamos la función
conectar_satelite()