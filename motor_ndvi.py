import ee
import json
import os

def inicializar_gee():
    try:
        # Render monta los archivos secretos aquí
        key_path = '/etc/secrets/gee_key.json'
        
        # Leemos la key desde el archivo secreto de Render
        with open(key_path, 'r') as f:
            service_account_info = json.load(f)
            
        credentials = ee.ServiceAccountCredentials(
            service_account_info['client_email'], 
            key_path
        )
        
        ee.Initialize(credentials)
        return True
    except Exception as e:
        print(f"Error crítico conectando con GEE: {e}")
        return False

# Probamos la conexión apenas se importa el módulo
if inicializar_gee():
    print("Módulo NDVI: Conexión con Earth Engine OK")
else:
    print("Módulo NDVI: Falló la conexión")