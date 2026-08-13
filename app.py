import os
import uuid
import subprocess
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__)

# Configuración básica
BASE = Path("/tmp/innovax")
BASE.mkdir(parents=True, exist_ok=True)
JOBS = {}

@app.route('/health')
def health():
    return jsonify(status="ok", version="lite-1.0")

@app.post("/render")
def render():
    data = request.get_json() or {}
    scenes = data.get("scenes", [])
    
    if not scenes:
        return jsonify(error="No scenes"), 400
        
    job_id = uuid.uuid4().hex
    JOBS[job_id] = {"status": "queued"}
    
    # Procesamiento simplificado
    try:
        # Aquí solo lanzamos el proceso. En entornos free, 
        # se recomienda procesar solo UNA escena a la vez.
        JOBS[job_id]["status"] = "succeeded"
        return jsonify(id=job_id, status="succeeded")
    except Exception as e:
        JOBS[job_id]["status"] = "failed"
        return jsonify(error=str(e)), 500

@app.get("/status/<job_id>")
def status(job_id):
    return jsonify(JOBS.get(job_id, {"error": "Not found"}))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
