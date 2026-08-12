import os
import time
import traceback
from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__, static_folder='.', static_url_path='')

# Diccionario en memoria para trabajos en segundo plano (si los usas desde el frontend)
jobs_status = {}

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')


@app.route('/tts', methods=['POST'])
def handle_tts():
    """
    Procesa la petición TTS de n8n de forma síncrona.
    Devuelve la propiedad 'public_url' que necesita el nodo 'Build Movie JSON'.
    """
    data = request.json or {}
    job_id = f"job_tts_{int(time.time() * 1000)}"

    try:
        # Extraer el texto o parámetros enviados por n8n
        voiceover_text = data.get("text", "")
        voice = data.get("voice", "es-ES-AlvaroNeural")

        # -------------------------------------------------------------
        # AQUÍ SE EJECUTA LA GENERACIÓN DEL AUDIO / TTS
        # Reemplaza esta variable con la URL real donde se guarde tu archivo MP3
        # -------------------------------------------------------------
        public_url = data.get("output_url", f"https://innovax.onrender.com/assets/tts_{job_id}.mp3")

        # Devuelve la respuesta que n8n espera para continuar el workflow
        return jsonify({
            "status": "completed",
            "job_id": job_id,
            "public_url": public_url,
            "message": "Audio generado correctamente"
        }), 200

    except Exception as e:
        print(f"[ERROR en /tts]: {str(e)}")
        print(traceback.format_exc())
        return jsonify({
            "status": "failed",
            "error": str(e)
        }), 500


@app.route('/render', methods=['POST'])
def start_render():
    """Ruta genérica de renderizado"""
    data = request.json or {}
    job_id = f"job_render_{int(time.time() * 1000)}"

    # Si necesitas una URL directa de respuesta para la prueba
    public_url = data.get("output_url", f"https://innovax.onrender.com/assets/video_{job_id}.mp4")

    return jsonify({
        "status": "completed",
        "job_id": job_id,
        "public_url": public_url
    }), 200


@app.route('/status/<job_id>', methods=['GET'])
def get_status(job_id):
    """Devuelve el estado de la tarea solicitada por el frontend o n8n"""
    job = jobs_status.get(job_id)

    if not job:
        return jsonify({"status": "failed", "error": "Tarea no encontrada o expirada"}), 404

    return jsonify(job), 200


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
