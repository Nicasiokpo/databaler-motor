import ee
import json

def conectar_satelite():
    # Esta es la ruta donde Render monta tu archivo JSON secreto
    key_path = '/etc/secrets/gee_key.json'
    
    try:
        with open(key_path, 'r') as f:
            service_account_info = json.load(f)
            
        credentials = ee.ServiceAccountCredentials(
            service_account_info['client_email'], 
            key_path
        )
        
        # Inicializamos la conexión
        ee.Initialize(credentials)
        print("Módulo NDVI: Conexión con Earth Engine OK")
        return True
    except Exception as e:
        print(f"Módulo NDVI: Error conectando con GEE: {e}")
        return False

# Probamos la conexión al importar el módulo
conectar_satelite()