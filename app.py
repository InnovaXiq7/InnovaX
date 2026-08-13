import os
import uuid
import shutil
import subprocess
import threading
import asyncio
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory
import requests
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

app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024

PUBLIC_BASE_URL = os.environ.get(
    "PUBLIC_BASE_URL",
    "https://innovax.onrender.com"
).rstrip("/")

# ============================================================
# MÚSICA DE FONDO
# ============================================================

# El archivo está en:
# assets/ssstik.io_1786530745642.mp3

MUSIC_FILE = (
    Path(__file__).resolve().parent
    / "assets"
    / "ssstik.io_1786530745642.mp3"
)

# Volumen de la música.
# 0.12 = 12%
MUSIC_VOLUME = 0.12


# ============================================================
# WHISPER
# ============================================================

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
                    f"[WHISPER] Cargando modelo: "
                    f"{WHISPER_MODEL_SIZE}"
                )

                WHISPER_MODEL = WhisperModel(
                    WHISPER_MODEL_SIZE,
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=2,
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

    if not path:
        return None

    path = str(path).strip()

    if path.startswith("="):
        path = path[1:].strip()

    if (
        path.startswith("http://")
        or
        path.startswith("https://")
    ):
        return path

    if not path.startswith("/"):
        path = "/" + path

    return f"{PUBLIC_BASE_URL}{path}"


# ============================================================
# NORMALIZAR URL
# ============================================================

def normalize_url(value):

    if not value:
        return None

    value = str(value).strip()

    if value.startswith("="):
        value = value[1:].strip()

    if len(value) >= 2:

        if (
            (
                value.startswith('"')
                and
                value.endswith('"')
            )
            or
            (
                value.startswith("'")
                and
                value.endswith("'")
            )
        ):

            value = value[1:-1].strip()

    return public_url(value)


# ============================================================
# DESCARGAR ARCHIVO
# ============================================================

def download_file(url, output_file):

    url = normalize_url(url)

    if not url:

        raise ValueError(
            "URL vacía."
        )

    print(
        "[DOWNLOAD] Descargando:"
    )

    print(url)

    print(
        "[DOWNLOAD] Destino:"
    )

    print(output_file)

    try:

        response = requests.get(
            url,
            stream=True,
            timeout=(30, 300),
            headers={
                "User-Agent": "Innovax/1.0"
            }
        )

        response.raise_for_status()

        output_file.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        with open(
            output_file,
            "wb"
        ) as f:

            for chunk in response.iter_content(
                chunk_size=1024 * 1024
            ):

                if chunk:
                    f.write(chunk)

    except Exception as e:

        raise RuntimeError(
            "No se pudo descargar el archivo desde la URL: "
            + url
            + "\n"
            + str(e)
        )

    if not output_file.exists():

        raise RuntimeError(
            "La descarga terminó pero el archivo no existe."
        )

    size = output_file.stat().st_size

    if size <= 0:

        raise RuntimeError(
            "La descarga creó un archivo vacío."
        )

    print(
        f"[DOWNLOAD] OK - {size} bytes"
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
# AÑADIR MÚSICA DE FONDO
# ============================================================

def add_background_music(
    final_file,
    job_dir
):

    if not MUSIC_FILE.exists():

        raise RuntimeError(
            "No se encontró la música de fondo: "
            + str(MUSIC_FILE)
        )

    mixed_file = (
        job_dir /
        "final_mixed.mp4"
    )

    print(
        "[MUSIC] Añadiendo música de fondo..."
    )

    print(
        f"[MUSIC] Archivo: {MUSIC_FILE}"
    )

    print(
        f"[MUSIC] Volumen: {MUSIC_VOLUME}"
    )

    command = [

        "ffmpeg",

        "-y",

        "-threads",
        "1",

        # Vídeo ya renderizado
        "-i",
        str(final_file),

        # Repetir música indefinidamente
        "-stream_loop",
        "-1",

        "-i",
        str(MUSIC_FILE),

        # Bajar música y mezclarla con la voz
        "-filter_complex",

        (
            f"[1:a]"
            f"volume={MUSIC_VOLUME}"
            f"[music];"
            f"[0:a]"
            f"[music]"
            f"amix=inputs=2:"
            f"duration=first:"
            f"dropout_transition=2"
            f"[a]"
        ),

        "-map",
        "0:v:0",

        "-map",
        "[a]",

        "-c:v",
        "copy",

        "-c:a",
        "aac",

        "-b:a",
        "160k",

        "-shortest",

        "-movflags",
        "+faststart",

        str(mixed_file)
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:

        raise RuntimeError(
            "Error añadiendo música de fondo:\n"
            + result.stderr[-5000:]
        )

    if not mixed_file.exists():

        raise RuntimeError(
            "FFmpeg no creó el vídeo con música."
        )

    final_file.unlink(
        missing_ok=True
    )

    mixed_file.rename(
        final_file
    )

    print(
        "[MUSIC] Música añadida correctamente."
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

            print(
                f"[RENDER] Job: {job_id}"
            )

            print(
                f"[RENDER] Escenas: {len(scenes)}"
            )

            # =================================================
            # PROCESAR ESCENAS
            # =================================================

            for i, scene in enumerate(scenes):

                print(
                    f"[RENDER] Procesando escena {i + 1}"
                )

                src = (
                    scene.get("video_url")
                    or
                    scene.get("src")
                    or
                    scene.get("video_src")
                )

                if not src:

                    raise ValueError(
                        f"Scene {i + 1} no tiene video_url."
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

                try:

                    duration = float(
                        duration
                    )

                except Exception:

                    duration = 7

                if duration < 1:
                    duration = 1

                # =================================================
                # VIDEO
                # =================================================

                video_input = (
                    job_dir /
                    f"input_video_{i}.mp4"
                )

                download_file(
                    src,
                    video_input
                )

                # =================================================
                # AUDIO / VOZ
                # =================================================

                audio_input = None

                if audio_url:

                    audio_input = (
                        job_dir /
                        f"input_audio_{i}.mp3"
                    )

                    download_file(
                        audio_url,
                        audio_input
                    )

                # =================================================
                # SALIDA ESCENA
                # =================================================

                video_out = (
                    job_dir /
                    f"scene_{i}.mp4"
                )

                # =================================================
                # FFMPEG CON AUDIO
                # =================================================

                if audio_input:

                    command = [

                        "ffmpeg",

                        "-y",

                        "-threads",
                        "1",

                        "-i",
                        str(video_input),

                        "-i",
                        str(audio_input),

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
                        "128k",

                        "-shortest",

                        "-pix_fmt",
                        "yuv420p",

                        "-movflags",
                        "+faststart",

                        str(video_out)
                    ]

                # =================================================
                # FFMPEG SIN AUDIO
                # =================================================

                else:

                    command = [

                        "ffmpeg",

                        "-y",

                        "-threads",
                        "1",

                        "-i",
                        str(video_input),

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

                        "-movflags",
                        "+faststart",

                        str(video_out)
                    ]

                print(
                    "[FFMPEG] Renderizando escena..."
                )

                result = subprocess.run(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True
                )

                if result.returncode != 0:

                    raise RuntimeError(
                        f"Error renderizando escena {i + 1}:\n"
                        f"{result.stderr[-5000:]}"
                    )

                if not video_out.exists():

                    raise RuntimeError(
                        f"FFmpeg terminó pero no creó "
                        f"scene_{i}.mp4"
                    )

                print(
                    f"[RENDER] Escena {i + 1} OK"
                )

                inputs.append(
                    video_out
                )

                video_input.unlink(
                    missing_ok=True
                )

                if audio_input:

                    audio_input.unlink(
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

            print(
                "[RENDER] Concatenando escenas..."
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
                    "Error concatenando escenas:\n"
                    + result.stderr[-5000:]
                )

            if not final_file.exists():

                raise RuntimeError(
                    "FFmpeg terminó pero no creó final.mp4"
                )

            # =================================================
            # MÚSICA DE FONDO
            # =================================================

            add_background_music(
                final_file,
                job_dir
            )

            # =================================================
            # COPIAR RESULTADO A VIDEO_DIR
            # =================================================

            public_filename = (
                f"{job_id}.mp4"
            )

            public_file = (
                VIDEO_DIR /
                public_filename
            )

            shutil.copy2(
                final_file,
                public_file
            )

            if not public_file.exists():

                raise RuntimeError(
                    "No se pudo crear el archivo público."
                )

            public_path = (
                f"/files/video/{public_filename}"
            )

            final_url = public_url(
                public_path
            )

            file_size = (
                public_file.stat().st_size
            )

            print(
                "[RENDER] FINALIZADO"
            )

            print(
                final_url
            )

            print(
                f"[RENDER] Tamaño: {file_size} bytes"
            )

            # =================================================
            # ÉXITO
            # =================================================

            JOBS[job_id].update(

                status="succeeded",

                url=final_url,

                public_url=final_url,

                filename=public_filename,

                size=file_size,

                scene_count=len(scenes)
            )

            # =================================================
            # LIMPIAR TEMPORALES
            # =================================================

            for video_file in inputs:

                video_file.unlink(
                    missing_ok=True
                )

            concat_file.unlink(
                missing_ok=True
            )

            final_file.unlink(
                missing_ok=True
            )

            # VIDEO_DIR se conserva porque
            # n8n descargará el MP4 desde ahí.

        except Exception as e:

            print(
                "[RENDER ERROR]"
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

        render=True,

        transcription=True,

        tts="edge-tts",

        service="n8n-free-ffmpeg-renderer",

        version=12,

        video_upload=True,

        whisper_model=WHISPER_MODEL_SIZE,

        background_music=MUSIC_FILE.exists()
    )


# ============================================================
# ROOT
# ============================================================

@app.get("/")
def root():

    return jsonify(

        ok=True,

        render=True,

        transcription=True,

        tts="edge-tts",

        service="n8n-free-ffmpeg-renderer",

        version=12,

        video_upload=True,

        whisper_model=WHISPER_MODEL_SIZE,

        background_music=MUSIC_FILE.exists()
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
                "TTS no creó el archivo."
            )

        path = (
            f"/files/audio/{filename}"
        )

        return jsonify(

            ok=True,

            id=audio_id,

            voice=voice,

            url=path,

            public_url=public_url(
                path
            )
        )

    except Exception as e:

        return jsonify(

            ok=False,

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

                ok=False,

                error=(
                    "No se recibió ningún vídeo."
                )

            ), 400

        original_name = (
            video.filename
            or
            "video.mp4"
        )

        extension = Path(
            original_name
        ).suffix.lower()

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

            ok=False,

            error=str(e)

        ), 500


# ============================================================
# SERVIR VIDEOS
# ============================================================

@app.get("/files/video/<filename>")
def video_file(filename):

    return send_from_directory(

        VIDEO_DIR,

        filename,

        as_attachment=False
    )


# ============================================================
# SERVIR AUDIO
# ============================================================

@app.get("/files/audio/<filename>")
def audio_file(filename):

    return send_from_directory(

        AUDIO_DIR,

        filename,

        as_attachment=False
    )


# ============================================================
# TRANSCRIBIR
# ============================================================

@app.post("/transcribe")
def transcribe():

    input_file = None
    video_file = None

    try:

        data = request.get_json(
            silent=True
        ) or {}

        video_url = (

            data.get("video_url")

            or

            data.get("url")
        )

        if not video_url:

            return jsonify(

                ok=False,

                error="Falta video_url."

            ), 400

        video_url = normalize_url(
            video_url
        )

        print(
            "[WHISPER] Descargando vídeo desde URL:"
        )

        print(video_url)

        temp_id = uuid.uuid4().hex

        video_file = (
            BASE /
            f"transcribe_{temp_id}.mp4"
        )

        input_file = (
            BASE /
            f"transcribe_{temp_id}.wav"
        )

        # ====================================================
        # DESCARGAR VIDEO
        # ====================================================

        download_file(
            video_url,
            video_file
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
                str(video_file),

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
            "[WHISPER] Cargando modelo..."
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
                or
                ""

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
                        or
                        ""

                    ).strip()

                    if not word_text:
                        continue

                    word_data = {

                        "word":
                            word_text,

                        "start":
                            round(
                                float(word.start),
                                3
                            ),

                        "end":
                            round(
                                float(word.end),
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
                        float(segment.start),
                        3
                    ),

                "end":
                    round(
                        float(segment.end),
                        3
                    ),

                "text":
                    segment_text,

                "words":
                    segment_words
            })

        detected_language = (

            info.language
            if info
            else
            "es"
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

                pass

        print(
            "[WHISPER] Transcripción terminada."
        )

        return jsonify({

            "ok":
                True,

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

        return jsonify(

            ok=False,

            error=str(e)

        ), 500

    finally:

        if input_file:

            input_file.unlink(
                missing_ok=True
            )

        if video_file:

            video_file.unlink(
                missing_ok=True
            )


# ============================================================
# RENDER
# ============================================================

@app.post("/render")
def render():

    try:

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

                ok=False,

                error=(
                    "El campo scenes "
                    "debe ser una lista."
                )

            ), 400

        if len(scenes) < 1:

            return jsonify(

                ok=False,

                error=(
                    "Se necesita "
                    "al menos una escena."
                )

            ), 400

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

                or

                scene.get("video_src")
            )

            if not src:
                continue

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

            return jsonify(

                ok=False,

                error="No hay escenas válidas."

            ), 400

        job_id = uuid.uuid4().hex

        JOBS[job_id] = {

            "id":
                job_id,

            "status":
                "queued",

            "url":
                None,

            "public_url":
                None,

            "error":
                None,

            "scene_count":
                len(normalized)
        }

        print(
            f"[RENDER] Nuevo job: {job_id}"
        )

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

    except Exception as e:

        return jsonify(

            ok=False,

            error=str(e)

        ), 500


# ============================================================
# STATUS
# ============================================================

@app.get("/status/<job_id>")
def status(job_id):

    job = JOBS.get(
        job_id
    )

    if not job:

        return jsonify(

            error=(
                "No se encontró el render."
            )

        ), 404

    return jsonify(
        job
    )


# ============================================================
# ERROR ARCHIVO GRANDE
# ============================================================

@app.errorhandler(413)
def request_entity_too_large(error):

    return jsonify(

        ok=False,

        error=(
            "El archivo supera "
            "el límite de 1 GB."
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
