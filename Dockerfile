FROM python:3.12-slim

# Evita que Python escriba archivos .pyc y fuerza la salida de logs sin búfer
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

# Instalar FFmpeg y dependencias del sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copiar e instalar requerimientos de Python en una capa separada
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar la aplicación
COPY app.py .

# Indicar el puerto expuesto
EXPOSE 8080

CMD ["python", "app.py"]
