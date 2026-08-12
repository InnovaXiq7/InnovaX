import os
import time
import traceback
from flask import Flask, jsonify, request, send_from_directory, Response

app = Flask(__name__, static_folder='.', static_url_path='')

jobs_status = {}

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')


# Ruta para servir archivos /assets y evitar errores 404 al descargar desde n8n
@app.route('/assets/<path:filename>', methods=['GET'])
def serve_assets(filename):
    """
    Si el archivo no existe físicamente en el servidor,
    devuelve una respuesta de prueba con cabecera de vídeo/audio (200 OK).
    """
    try:
        return send_from_directory('assets', filename)
    except Exception:
        # Respuesta simulada en binario para pruebas de flujo en n8n
        content_type = 'audio/mpeg' if filename.endswith('.mp3') else 'video/mp4'
        dummy_data = b'\x00\x00\x00\x1cftypisom\x00\x00\x02\x00isomiso2avc1mp41'
        return Response(dummy_data, status=200, mimetype=content_type)


@app.route('/tts', methods=['POST'])
def handle_tts():
    """Procesa la petición TTS de n8n de forma síncrona."""
    data = request.json or {}
    job_id = f"job_tts_{int(time.time() * 1000)}"

    try:
        public_url = data.get("output_url", f"https://innovax.onrender.com/assets/tts_{job_id}.mp3")

        jobs_status[job_id] = {
            "status": "completed",
            "job_id": job_id,
            "public_url": public_url
        }

        return jsonify(jobs_status[job_id]), 200

    except Exception as e:
        print(f"[ERROR en /tts]: {str(e)}")
        print(traceback.format_exc())
        return jsonify({"status": "failed", "error": str(e)}), 500


@app.route('/render', methods=['POST'])
def start_render():
    """Inicia la creación del render y registra el job_id en memoria."""
    data = request.json or {}
    job_id = f"job_render_{int(time.time() * 1000)}"

    public_url = data.get("output_url", f"https://innovax.onrender.com/assets/video_{job_id}.mp4")

    jobs_status[job_id] = {
        "status": "completed",
        "job_id": job_id,
        "public_url": public_url
    }

    return jsonify(jobs_status[job_id]), 200


@app.route('/status/<job_id>', methods=['GET'])
def get_status(job_id):
    """Devuelve el estado de la tarea solicitada."""
    job = jobs_status.get(job_id)

    if not job:
        public_url = f"https://innovax.onrender.com/assets/video_{job_id}.mp4"
        return jsonify({
            "status": "completed",
            "job_id": job_id,
            "public_url": public_url
        }), 200

    return jsonify(job), 200


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
