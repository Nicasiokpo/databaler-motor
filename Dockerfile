# 1. Usamos una computadora base con Python
FROM python:3.11-slim

# 2. Instalamos GDAL para poder generar el GeoPDF
RUN apt-get update && apt-get install -y gdal-bin libgdal-dev build-essential

# 3. Copiamos tu lista de librerías y las instalamos
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copiamos tus motores de Python
COPY api.py .
COPY motor.py .

# 5. Prendemos el servidor
CMD uvicorn api:app --host 0.0.0.0 --port ${PORT:-10000}