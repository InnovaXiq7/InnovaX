import os
import uuid
import shutil
import subprocess
import threading
import asyncio
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory
import edge_tts
from faster_whisper import WhisperModel


app = Flask(__name__)


# ============================================================
# CONFIGURACIÓN
# ============================================================

BASE = Path("/tmp/innovax")
BASE.mkdir(parents=True, exist_ok=True)

AUDIO_DIR = BASE / "audio"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

VIDEO_DIR = BASE / "video"
VIDEO_DIR.mkdir(parents=True, exist_ok=True)

JOBS = {}

RENDER_LOCK = threading.Lock()
WHISPER_LOCK = threading.Lock()

# Límite de subida: 1 GB
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024

PUBLIC_BASE_URL = os.environ.get(
    "PUBLIC_BASE_URL",
    ""
).rstrip("/")


# ============================================================
# WHISPER - CONFIGURACIÓN LIGERA PARA RENDER FREE
# ============================================================

# IMPORTANTE:
# tiny consume mucha menos RAM que base.
WHISPER_MODEL_SIZE = os.environ.get(
    "WHISPER_MODEL",
    "tiny"
)

WHISPER_MODEL = None


def get_whisper_model():

    global WHISPER_MODEL

    if WHISPER_MODEL is None:

        with WHISPER_LOCK:

            if WHISPER_MODEL is None:

                print(
                    f"[WHISPER] Cargando modelo ligero: "
                    f"{WHISPER_MODEL_SIZE}"
                )

                WHISPER_MODEL = WhisperModel(
                    WHISPER_MODEL_SIZE,
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=1,
                    num_workers=1
                )

                print(
                    "[WHISPER] Modelo cargado correctamente"
                )

    return WHISPER_MODEL


# ============================================================
# URL PÚBLICA
# ============================================================

def public_url(path):

    base = PUBLIC_BASE_URL

    if not base:

        hostname = os.environ.get(
            "RENDER_EXTERNAL_HOSTNAME"
        )

        if hostname:

            base = f"https://{hostname}"

        else:

            base = request.host_url.rstrip("/")

    return f"{base}{path}"


# ============================================================
# LIMPIAR JOB
# ============================================================

def clean_job(job_id):

    job_dir = BASE / job_id

    if job_dir.exists():

        shutil.rmtree(
            job_dir,
            ignore_errors=True
        )


# ============================================================
# LIMPIAR ARCHIVOS TEMPORALES ANTIGUOS
# ============================================================

def cleanup_old_files():

    try:

        # Limpiar WAV temporales
        for file in BASE.glob("transcribe_*.wav"):

            try:
                file.unlink()
            except Exception:
                pass

    except Exception as e:

        print(
            "[CLEANUP ERROR]",
            str(e)
        )


# ============================================================
# DESCARGAR / EXTRAER AUDIO
# ============================================================

def download_audio(
    url,
    output_file
):

    result = subprocess.run(

        [
            "ffmpeg",
            "-y",
            "-threads",
            "1",
            "-i",
            url,
            "-vn",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            str(output_file)
        ],

        stdout=subprocess.DEVNULL,

        stderr=subprocess.PIPE,

        text=True
    )

    if result.returncode != 0:

        raise RuntimeError(
            "Error descargando audio:\n"
            + result.stderr[-3000:]
        )


# ============================================================
# EDGE TTS
# ============================================================

async def generate_tts_async(
    text,
    voice,
    output_file
):

    communicate = edge_tts.Communicate(

        text=text,

        voice=voice,

        rate="+0%",

        volume="+0%",

        pitch="+0Hz"
    )

    await communicate.save(
        str(output_file)
    )


def generate_tts(
    text,
    voice,
    output_file
):

    asyncio.run(

        generate_tts_async(

            text,

            voice,

            output_file
        )
    )


# ============================================================
# RENDER JOB
# ============================================================

def run_job(
    job_id,
    scenes
):

    with RENDER_LOCK:

        job_dir = BASE / job_id

        job_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        JOBS[job_id]["status"] = "rendering"

        try:

            inputs = []

            # =================================================
            # PROCESAR ESCENAS
            # =================================================

            for i, scene in enumerate(scenes):

                src = (
                    scene.get("video_url")
                    or scene.get("src")
                    or scene.get("video_src")
                )

                if not src:

                    raise ValueError(
                        f"Scene {i + 1} has no video source"
                    )

                audio_url = (
                    scene.get("audio_url")
                    or scene.get("audio_src")
                )

                duration = scene.get(
                    "duration",
                    7
                )

                try:

                    duration = float(
                        duration
                    )

                except Exception:

                    duration = 7

                if duration < 1:
                    duration = 1

                video_out = (
                    job_dir /
                    f"scene_{i}.mp4"
                )

                audio_out = None

                # =================================================
                # CON AUDIO
                # =================================================

                if audio_url:

                    audio_out = (
                        job_dir /
                        f"audio_{i}.m4a"
                    )

                    download_audio(
                        audio_url,
                        audio_out
                    )

                    command = [

                        "ffmpeg",

                        "-y",

                        "-threads",
                        "1",

                        "-i",
                        src,

                        "-i",
                        str(audio_out),

                        "-t",
                        str(duration),

                        "-vf",

                        (
                            "scale=720:1280:"
                            "force_original_aspect_ratio=increase,"
                            "crop=720:1280,"
                            "fps=24"
                        ),

                        "-map",
                        "0:v:0",

                        "-map",
                        "1:a:0",

                        "-c:v",
                        "libx264",

                        "-preset",
                        "ultrafast",

                        "-crf",
                        "28",

                        "-c:a",
                        "aac",

                        "-b:a",
                        "96k",

                        "-shortest",

                        "-pix_fmt",
                        "yuv420p",

                        str(video_out)
                    ]

                # =================================================
                # SIN AUDIO
                # =================================================

                else:

                    command = [

                        "ffmpeg",

                        "-y",

                        "-threads",
                        "1",

                        "-i",
                        src,

                        "-t",
                        str(duration),

                        "-vf",

                        (
                            "scale=720:1280:"
                            "force_original_aspect_ratio=increase,"
                            "crop=720:1280,"
                            "fps=24"
                        ),

                        "-c:v",
                        "libx264",

                        "-preset",
                        "ultrafast",

                        "-crf",
                        "28",

                        "-pix_fmt",
                        "yuv420p",

                        "-an",

                        str(video_out)
                    ]

                # =================================================
                # EJECUTAR FFMPEG
                # =================================================

                print(
                    f"[RENDER] Procesando escena {i + 1}"
                )

                result = subprocess.run(

                    command,

                    stdout=subprocess.DEVNULL,

                    stderr=subprocess.PIPE,

                    text=True
                )

                if result.returncode != 0:

                    raise RuntimeError(

                        f"Error rendering scene {i + 1}:\n"
                        f"{result.stderr[-4000:]}"
                    )

                if not video_out.exists():

                    raise RuntimeError(
                        f"No se creó la escena {i + 1}"
                    )

                inputs.append(
                    video_out
                )

                if (
                    audio_out
                    and audio_out.exists()
                ):

                    audio_out.unlink(
                        missing_ok=True
                    )

            # =================================================
            # CONCATENAR ESCENAS
            # =================================================

            concat_file = (
                job_dir /
                "concat.txt"
            )

            concat_lines = []

            for video_file in inputs:

                concat_lines.append(
                    f"file '{video_file.as_posix()}'\n"
                )

            concat_file.write_text(
                "".join(concat_lines),
                encoding="utf-8"
            )

            final_file = (
                job_dir /
                "final.mp4"
            )

            concat_command = [

                "ffmpeg",

                "-y",

                "-threads",
                "1",

                "-f",
                "concat",

                "-safe",
                "0",

                "-i",
                str(concat_file),

                "-c",
                "copy",

                "-movflags",
                "+faststart",

                str(final_file)
            ]

            result = subprocess.run(

                concat_command,

                stdout=subprocess.DEVNULL,

                stderr=subprocess.PIPE,

                text=True
            )

            if result.returncode != 0:

                raise RuntimeError(

                    "Error concatenating scenes:\n"
                    + result.stderr[-4000:]
                )

            if not final_file.exists():

                raise RuntimeError(
                    "El vídeo final no fue creado."
                )

            # =================================================
            # JOB COMPLETADO
            # =================================================

            JOBS[job_id].update(

                status="succeeded",

                url=public_url(
                    f"/files/{job_id}/final.mp4"
                ),

                local_url=(
                    f"/files/{job_id}/final.mp4"
                )
            )

            print(
                f"[RENDER] Job completado: {job_id}"
            )

            # =================================================
            # LIMPIEZA DE ESCENAS
            # =================================================

            for video_file in inputs:

                video_file.unlink(
                    missing_ok=True
                )

            concat_file.unlink(
                missing_ok=True
            )

        except Exception as e:

            print(
                f"[RENDER ERROR] {job_id}"
            )

            print(
                str(e)
            )

            JOBS[job_id].update(

                status="failed",

                error=str(e)
            )

            clean_job(
                job_id
            )


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():

    return jsonify(

        ok=True,

        service="n8n-free-ffmpeg-renderer",

        version=9,

        tts="edge-tts",

        video_upload=True,

        transcription=True,

        whisper_model=WHISPER_MODEL_SIZE
    )


# ============================================================
# ROOT
# ============================================================

@app.get("/")
def root():

    return jsonify(

        ok=True,

        service="n8n-free-ffmpeg-renderer",

        version=9,

        tts="edge-tts",

        video_upload=True,

        transcription=True,

        whisper_model=WHISPER_MODEL_SIZE
    )


# ============================================================
# TTS
# ============================================================

@app.post("/tts")
def tts():

    try:

        data = request.get_json(
            silent=True
        ) or {}

        text = str(
            data.get(
                "text",
                ""
            )
        ).strip()

        voice = str(
            data.get(
                "voice",
                "es-ES-AlvaroNeural"
            )
        ).strip()

        if not text:

            return jsonify(
                error="No text supplied."
            ), 400

        if not voice:

            voice = "es-ES-AlvaroNeural"

        audio_id = uuid.uuid4().hex

        filename = (
            f"{audio_id}.mp3"
        )

        output_file = (
            AUDIO_DIR /
            filename
        )

        generate_tts(
            text,
            voice,
            output_file
        )

        if not output_file.exists():

            raise RuntimeError(
                "TTS did not create an audio file."
            )

        path = (
            f"/files/audio/{filename}"
        )

        return jsonify(

            id=audio_id,

            voice=voice,

            url=path,

            public_url=public_url(
                path
            )
        )

    except Exception as e:

        return jsonify(
            error=str(e)
        ), 500


# ============================================================
# UPLOAD AUDIO
# ============================================================

@app.post("/upload-audio")
def upload_audio():

    try:

        audio = (
            request.files.get("file")
            or
            request.files.get("data")
        )

        if not audio:

            return jsonify(

                error=(
                    "No audio file supplied. "
                    "Use multipart field "
                    "'file' or 'data'."
                )

            ), 400

        audio_id = uuid.uuid4().hex

        filename = (
            f"{audio_id}.mp3"
        )

        output_file = (
            AUDIO_DIR /
            filename
        )

        audio.save(
            output_file
        )

        path = (
            f"/files/audio/{filename}"
        )

        return jsonify(

            id=audio_id,

            url=path,

            public_url=public_url(
                path
            )
        )

    except Exception as e:

        return jsonify(
            error=str(e)
        ), 500


# ============================================================
# UPLOAD VIDEO
# ============================================================

@app.post("/upload-video")
def upload_video():

    try:

        video = (
            request.files.get("file")
            or
            request.files.get("data")
            or
            request.files.get("video")
        )

        if not video:

            return jsonify(

                error=(
                    "No video file supplied. "
                    "Use multipart field "
                    "'file', 'data' or 'video'."
                )

            ), 400

        original_name = (
            video.filename
            or "video.mp4"
        )

        extension = (
            Path(
                original_name
            ).suffix.lower()
        )

        allowed_extensions = {

            ".mp4",
            ".mov",
            ".mkv",
            ".webm",
            ".avi",
            ".m4v"
        }

        if extension not in allowed_extensions:

            extension = ".mp4"

        video_id = uuid.uuid4().hex

        filename = (
            f"{video_id}{extension}"
        )

        output_file = (
            VIDEO_DIR /
            filename
        )

        video.save(
            output_file
        )

        if not output_file.exists():

            raise RuntimeError(
                "El vídeo no se pudo guardar."
            )

        file_size = (
            output_file.stat().st_size
        )

        path = (
            f"/files/video/{filename}"
        )

        return jsonify(

            ok=True,

            id=video_id,

            filename=filename,

            original_filename=original_name,

            size=file_size,

            url=path,

            public_url=public_url(
                path
            )
        )

    except Exception as e:

        return jsonify(
            error=str(e)
        ), 500


# ============================================================
# SERVIR VIDEOS
# ============================================================

@app.get(
    "/files/video/<filename>"
)
def video_file(filename):

    return send_from_directory(

        VIDEO_DIR,

        filename,

        as_attachment=False
    )


# ============================================================
# TRANSCRIBIR VIDEO CON WHISPER
# ============================================================

@app.post("/transcribe")
def transcribe():

    input_file = None

    try:

        cleanup_old_files()

        data = request.get_json(
            silent=True
        ) or {}

        video_url = (
            data.get("video_url")
            or
            data.get("url")
        )

        # ====================================================
        # LIMPIAR URL
        # ====================================================

        if video_url:

            video_url = str(
                video_url
            ).strip()

            if video_url.startswith("="):

                video_url = (
                    video_url[1:]
                    .strip()
                )

            if (
                len(video_url) >= 2
                and
                (
                    (
                        video_url.startswith('"')
                        and
                        video_url.endswith('"')
                    )
                    or
                    (
                        video_url.startswith("'")
                        and
                        video_url.endswith("'")
                    )
                )
            ):

                video_url = (
                    video_url[1:-1]
                    .strip()
                )

        if not video_url:

            return jsonify(

                ok=False,

                error="Falta video_url."

            ), 400

        print(
            "[WHISPER] Vídeo recibido:"
        )

        print(
            video_url
        )

        # ====================================================
        # VALIDAR URL
        # ====================================================

        if not (
            video_url.startswith("http://")
            or
            video_url.startswith("https://")
        ):

            raise ValueError(
                "video_url no es una URL HTTP/HTTPS válida: "
                + video_url
            )

        # ====================================================
        # TEMPORAL
        # ====================================================

        temp_id = uuid.uuid4().hex

        input_file = (
            BASE /
            f"transcribe_{temp_id}.wav"
        )

        # ====================================================
        # EXTRAER AUDIO
        # ====================================================

        print(
            "[WHISPER] Extrayendo audio..."
        )

        result = subprocess.run(

            [

                "ffmpeg",

                "-y",

                "-threads",
                "1",

                "-i",
                video_url,

                "-map",
                "0:a:0?",

                "-vn",

                "-ac",
                "1",

                "-ar",
                "16000",

                "-c:a",
                "pcm_s16le",

                str(input_file)

            ],

            stdout=subprocess.DEVNULL,

            stderr=subprocess.PIPE,

            text=True
        )

        if result.returncode != 0:

            raise RuntimeError(

                "No se pudo extraer el audio "
                "del vídeo:\n"
                + result.stderr[-4000:]
            )

        if not input_file.exists():

            raise RuntimeError(
                "No se creó el archivo de audio."
            )

        # ====================================================
        # WHISPER
        # ====================================================

        print(
            "[WHISPER] Cargando modelo ligero..."
        )

        model = get_whisper_model()

        print(
            "[WHISPER] Transcribiendo..."
        )

        segments, info = model.transcribe(

            str(input_file),

            language="es",

            beam_size=1,

            word_timestamps=True,

            vad_filter=True,

            condition_on_previous_text=False
        )

        output_segments = []

        output_words = []

        full_text = []

        for segment in segments:

            segment_text = (
                segment.text
                or ""
            ).strip()

            if segment_text:

                full_text.append(
                    segment_text
                )

            segment_words = []

            if segment.words:

                for word in segment.words:

                    word_text = (
                        word.word
                        or ""
                    ).strip()

                    if not word_text:

                        continue

                    word_data = {

                        "word":
                            word_text,

                        "start":
                            round(
                                float(
                                    word.start
                                ),
                                3
                            ),

                        "end":
                            round(
                                float(
                                    word.end
                                ),
                                3
                            )
                    }

                    segment_words.append(
                        word_data
                    )

                    output_words.append(
                        word_data
                    )

            output_segments.append({

                "start":
                    round(
                        float(
                            segment.start
                        ),
                        3
                    ),

                "end":
                    round(
                        float(
                            segment.end
                        ),
                        3
                    ),

                "text":
                    segment_text,

                "words":
                    segment_words
            })

        # ====================================================
        # LIMPIAR WAV
        # ====================================================

        input_file.unlink(
            missing_ok=True
        )

        # ====================================================
        # IDIOMA
        # ====================================================

        detected_language = (
            info.language
            if info
            else "es"
        )

        language_probability = None

        if info:

            try:

                language_probability = round(

                    float(
                        info.language_probability
                    ),

                    4
                )

            except Exception:

                language_probability = None

        print(
            "[WHISPER] Transcripción terminada."
        )

        # ====================================================
        # RESULTADO
        # ====================================================

        return jsonify({

            "ok": True,

            "language":
                detected_language,

            "language_probability":
                language_probability,

            "text":
                " ".join(
                    full_text
                ),

            "segments":
                output_segments,

            "words":
                output_words
        })

    except Exception as e:

        print(
            "[WHISPER ERROR]"
        )

        print(
            str(e)
        )

        if input_file:

            try:

                input_file.unlink(
                    missing_ok=True
                )

            except Exception:

                pass

        return jsonify(

            ok=False,

            error=str(e)

        ), 500


# ============================================================
# RENDER
# ============================================================

@app.post("/render")
def render():

    data = request.get_json(
        silent=True
    ) or {}

    scenes = data.get(
        "scenes"
    )

    if not isinstance(
        scenes,
        list
    ):

        return jsonify(

            error=(
                "El campo scenes "
                "debe ser una lista."
            ),

            id=None,

            status="failed",

            url=None

        ), 400

    if len(scenes) < 1:

        return jsonify(

            error=(
                "Se requiere "
                "al menos una escena."
            ),

            id=None,

            status="failed",

            url=None

        ), 400

    job_id = uuid.uuid4().hex

    JOBS[job_id] = {

        "id":
            job_id,

        "status":
            "queued",

        "url":
            None,

        "error":
            None
    }

    normalized = []

    for index, scene in enumerate(
        scenes
    ):

        if not isinstance(
            scene,
            dict
        ):

            continue

        src = (
            scene.get("video_url")
            or
            scene.get("src")
        )

        audio_url = (
            scene.get("audio_url")
            or
            scene.get("audio_src")
        )

        duration = scene.get(
            "duration",
            7
        )

        normalized.append({

            "index":
                index,

            "video_url":
                src,

            "audio_url":
                audio_url,

            "duration":
                duration
        })

    if not normalized:

        JOBS[job_id].update(

            status="failed",

            error="No valid scenes."
        )

        return jsonify(
            JOBS[job_id]
        ), 400

    threading.Thread(

        target=run_job,

        args=(
            job_id,
            normalized
        ),

        daemon=True

    ).start()

    return jsonify(
        JOBS[job_id]
    )


# ============================================================
# STATUS
# ============================================================

@app.get(
    "/status/<job_id>"
)
def status(job_id):

    job = JOBS.get(
        job_id
    )

    if not job:

        return jsonify(

            error=(
                "No render was found "
                "with that ID."
            )

        ), 404

    return jsonify(
        job
    )


# ============================================================
# ARCHIVOS DE RENDER
# ============================================================

@app.get(
    "/files/<job_id>/<filename>"
)
def render_file(
    job_id,
    filename
):

    return send_from_directory(

        BASE / job_id,

        filename,

        as_attachment=False
    )


# ============================================================
# ARCHIVOS DE AUDIO
# ============================================================

@app.get(
    "/files/audio/<filename>"
)
def audio_file(
    filename
):

    return send_from_directory(

        AUDIO_DIR,

        filename,

        as_attachment=False
    )


# ============================================================
# ERROR: ARCHIVO DEMASIADO GRANDE
# ============================================================

@app.errorhandler(413)
def request_entity_too_large(error):

    return jsonify(

        ok=False,

        error=(
            "El archivo supera "
            "el límite máximo de 1 GB."
        )

    ), 413


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    port = int(

        os.environ.get(
            "PORT",
            10000
        )
    )

    app.run(

        host="0.0.0.0",

        port=port
    )
