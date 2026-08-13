import os
import uuid
import json
import shutil
import subprocess
import threading
import asyncio
import urllib.request
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory
import edge_tts
from faster_whisper import WhisperModel


# ============================================================
# APP
# ============================================================

app = Flask(__name__)

app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024


# ============================================================
# DIRECTORIOS
# ============================================================

BASE = Path("/tmp/innovax")
BASE.mkdir(parents=True, exist_ok=True)

AUDIO_DIR = BASE / "audio"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

VIDEO_DIR = BASE / "video"
VIDEO_DIR.mkdir(parents=True, exist_ok=True)

JOBS_DIR = BASE / "jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# CONFIGURACIÓN
# ============================================================

PORT = int(os.environ.get("PORT", "10000"))

PUBLIC_BASE_URL = os.environ.get(
    "PUBLIC_BASE_URL",
    ""
).rstrip("/")

WHISPER_MODEL_SIZE = os.environ.get(
    "WHISPER_MODEL",
    "tiny"
)


# ============================================================
# LOCKS
# ============================================================

RENDER_LOCK = threading.Lock()
WHISPER_LOCK = threading.Lock()

WHISPER_MODEL = None


# ============================================================
# URL PÚBLICA
# ============================================================

def get_public_base_url():

    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL

    hostname = os.environ.get(
        "RENDER_EXTERNAL_HOSTNAME"
    )

    if hostname:
        return f"https://{hostname}"

    return request.host_url.rstrip("/")


def public_url(path):

    base = get_public_base_url()

    if not path.startswith("/"):
        path = "/" + path

    return base + path


# ============================================================
# JOBS
# ============================================================

def job_file(job_id):

    return JOBS_DIR / f"{job_id}.json"


def save_job(job):

    path = job_file(job["id"])

    with open(path, "w", encoding="utf-8") as f:

        json.dump(
            job,
            f,
            ensure_ascii=False,
            indent=2
        )


def load_job(job_id):

    path = job_file(job_id)

    if not path.exists():
        return None

    try:

        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception:

        return None


# ============================================================
# FFMPEG
# ============================================================

def run_ffmpeg(command):

    print()
    print("[FFMPEG] Ejecutando:")
    print(" ".join(str(x) for x in command))
    print()

    result = subprocess.run(

        command,

        stdout=subprocess.DEVNULL,

        stderr=subprocess.PIPE,

        text=True

    )

    if result.returncode != 0:

        print("[FFMPEG ERROR]")
        print(result.stderr)

        raise RuntimeError(
            result.stderr[-6000:]
        )

    return result


# ============================================================
# DESCARGAR ARCHIVO HTTP
# ============================================================

def download_file(url, destination):

    print()
    print("[DOWNLOAD]")
    print(url)
    print()

    request_obj = urllib.request.Request(

        url,

        headers={
            "User-Agent": "Mozilla/5.0"
        }

    )

    try:

        with urllib.request.urlopen(
            request_obj,
            timeout=120
        ) as response:

            with open(
                destination,
                "wb"
            ) as output:

                while True:

                    chunk = response.read(
                        1024 * 1024
                    )

                    if not chunk:
                        break

                    output.write(chunk)

    except Exception as e:

        raise RuntimeError(
            f"No se pudo descargar el archivo desde URL: {e}"
        )


# ============================================================
# WHISPER
# ============================================================

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
# TTS
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
# RENDER DE UNA ESCENA
# ============================================================

def render_scene(
    scene,
    index,
    job_dir
):

    video_url = (
        scene.get("video_url")
        or scene.get("src")
        or scene.get("video_src")
    )

    audio_url = (
        scene.get("audio_url")
        or scene.get("audio_src")
    )

    if not video_url:

        raise ValueError(
            f"La escena {index + 1} no tiene video_url."
        )

    duration = scene.get(
        "duration",
        7
    )

    try:

        duration = float(duration)

    except Exception:

        duration = 7

    if duration < 1:
        duration = 1

    if duration > 60:
        duration = 60


    # --------------------------------------------------------
    # ARCHIVO LOCAL DEL VÍDEO
    # --------------------------------------------------------

    video_input = (
        job_dir /
        f"input_video_{index}.mp4"
    )

    print()
    print(
        f"[SCENE {index + 1}] "
        f"Descargando vídeo..."
    )

    download_file(
        video_url,
        video_input
    )


    # --------------------------------------------------------
    # ARCHIVO LOCAL DEL AUDIO
    # --------------------------------------------------------

    audio_input = None

    if audio_url:

        audio_input = (
            job_dir /
            f"input_audio_{index}.mp3"
        )

        print(
            f"[SCENE {index + 1}] "
            f"Descargando audio..."
        )

        download_file(
            audio_url,
            audio_input
        )


    # --------------------------------------------------------
    # SALIDA
    # --------------------------------------------------------

    output_file = (
        job_dir /
        f"scene_{index}.mp4"
    )


    # --------------------------------------------------------
    # CON AUDIO
    # --------------------------------------------------------

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

            str(output_file)

        ]


    # --------------------------------------------------------
    # SIN AUDIO
    # --------------------------------------------------------

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

            str(output_file)

        ]


    run_ffmpeg(command)


    if not output_file.exists():

        raise RuntimeError(
            f"No se creó la escena {index + 1}"
        )


    if output_file.stat().st_size < 1000:

        raise RuntimeError(
            f"La escena {index + 1} "
            f"se creó pero está vacía."
        )


    print(
        f"[SCENE {index + 1}] OK"
    )

    return output_file


# ============================================================
# CONCATENAR ESCENAS
# ============================================================

def concat_scenes(
    scene_files,
    output_file
):

    concat_file = (
        output_file.parent /
        "concat.txt"
    )

    lines = []

    for file in scene_files:

        escaped = (
            str(file)
            .replace("'", "'\\''")
        )

        lines.append(
            f"file '{escaped}'\n"
        )

    concat_file.write_text(
        "".join(lines),
        encoding="utf-8"
    )


    command = [

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

        str(output_file)

    ]


    run_ffmpeg(command)


    if not output_file.exists():

        raise RuntimeError(
            "No se creó el vídeo final."
        )


    if output_file.stat().st_size < 1000:

        raise RuntimeError(
            "El vídeo final está vacío."
        )


    concat_file.unlink(
        missing_ok=True
    )


# ============================================================
# TRABAJO DE RENDER
# ============================================================

def run_render_job(
    job_id,
    scenes
):

    with RENDER_LOCK:

        job = load_job(job_id)

        if not job:
            return

        job["status"] = "rendering"

        save_job(job)


        job_dir = (
            BASE /
            "renders" /
            job_id
        )

        job_dir.mkdir(
            parents=True,
            exist_ok=True
        )


        try:

            print()
            print(
                "======================================"
            )
            print(
                f"[RENDER] INICIO {job_id}"
            )
            print(
                "======================================"
            )


            scene_files = []


            # ------------------------------------------------
            # ESCENAS
            # ------------------------------------------------

            for index, scene in enumerate(
                scenes
            ):

                scene_file = render_scene(

                    scene,

                    index,

                    job_dir

                )

                scene_files.append(
                    scene_file
                )


            # ------------------------------------------------
            # FINAL
            # ------------------------------------------------

            final_file = (
                job_dir /
                "final.mp4"
            )


            concat_scenes(

                scene_files,

                final_file

            )


            # ------------------------------------------------
            # COMPROBAR FINAL
            # ------------------------------------------------

            if not final_file.exists():

                raise RuntimeError(
                    "FFmpeg terminó pero "
                    "final.mp4 no existe."
                )


            size = final_file.stat().st_size


            if size < 1000:

                raise RuntimeError(
                    "final.mp4 está vacío."
                )


            final_path = (
                f"/files/render/"
                f"{job_id}/final.mp4"
            )


            final_public_url = public_url(
                final_path
            )


            # ------------------------------------------------
            # ÉXITO
            # ------------------------------------------------

            job = load_job(job_id) or {
                "id": job_id
            }


            job.update({

                "status":
                    "succeeded",

                "url":
                    final_public_url,

                "public_url":
                    final_public_url,

                "filename":
                    "final.mp4",

                "size":
                    size,

                "scene_count":
                    len(scene_files)

            })


            save_job(job)


            print()
            print(
                "[RENDER] COMPLETADO"
            )

            print(
                f"[RENDER] URL: "
                f"{final_public_url}"
            )

            print()


        except Exception as e:

            print()
            print(
                "[RENDER ERROR]"
            )

            print(
                str(e)
            )

            print()


            job = load_job(job_id) or {
                "id": job_id
            }


            job.update({

                "status":
                    "failed",

                "error":
                    str(e),

                "url":
                    None,

                "public_url":
                    None

            })


            save_job(job)


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():

    return jsonify({

        "ok":
            True,

        "service":
            "n8n-free-ffmpeg-renderer",

        "version":
            10,

        "tts":
            "edge-tts",

        "transcription":
            True,

        "video_upload":
            True,

        "render":
            True,

        "whisper_model":
            WHISPER_MODEL_SIZE

    })


# ============================================================
# ROOT
# ============================================================

@app.get("/")
def root():

    return jsonify({

        "ok":
            True,

        "service":
            "n8n-free-ffmpeg-renderer",

        "version":
            10

    })


# ============================================================
# TTS
# ============================================================

@app.post("/tts")
def tts():

    try:

        data = (
            request.get_json(
                silent=True
            )
            or {}
        )


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

            return jsonify({

                "ok":
                    False,

                "error":
                    "No text supplied."

            }), 400


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
            f"/files/audio/"
            f"{filename}"
        )


        return jsonify({

            "ok":
                True,

            "id":
                audio_id,

            "voice":
                voice,

            "filename":
                filename,

            "url":
                path,

            "public_url":
                public_url(path)

        })


    except Exception as e:

        return jsonify({

            "ok":
                False,

            "error":
                str(e)

        }), 500


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

            return jsonify({

                "ok":
                    False,

                "error":
                    "No file supplied."

            }), 400


        original_name = (
            video.filename
            or
            "video.mp4"
        )


        extension = (
            Path(
                original_name
            ).suffix.lower()
        )


        allowed = {

            ".mp4",
            ".mov",
            ".mkv",
            ".webm",
            ".avi",
            ".m4v"

        }


        if extension not in allowed:

            extension = ".mp4"


        video_id = uuid.uuid4().hex


        filename = (
            f"{video_id}"
            f"{extension}"
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


        size = (
            output_file.stat().st_size
        )


        path = (
            f"/files/video/"
            f"{filename}"
        )


        return jsonify({

            "ok":
                True,

            "id":
                video_id,

            "filename":
                filename,

            "original_filename":
                original_name,

            "size":
                size,

            "url":
                path,

            "public_url":
                public_url(path)

        })


    except Exception as e:

        return jsonify({

            "ok":
                False,

            "error":
                str(e)

        }), 500


# ============================================================
# SERVIR VIDEOS SUBIDOS
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
# SERVIR AUDIO
# ============================================================

@app.get(
    "/files/audio/<filename>"
)
def audio_file(filename):

    return send_from_directory(

        AUDIO_DIR,

        filename,

        as_attachment=False

    )


# ============================================================
# TRANSCRIBE
# ============================================================

@app.post("/transcribe")
def transcribe():

    temp_video = None
    temp_audio = None

    try:

        data = (
            request.get_json(
                silent=True
            )
            or {}
        )


        video_url = (

            data.get(
                "video_url"
            )

            or

            data.get(
                "url"
            )

        )


        if video_url:

            video_url = str(
                video_url
            ).strip()


        if not video_url:

            return jsonify({

                "ok":
                    False,

                "error":
                    "Falta video_url."

            }), 400


        if video_url.startswith("="):

            video_url = (
                video_url[1:]
                .strip()
            )


        if (

            video_url.startswith('"')
            and
            video_url.endswith('"')

        ):

            video_url = video_url[1:-1]


        if (

            video_url.startswith("'")
            and
            video_url.endswith("'")

        ):

            video_url = video_url[1:-1]


        if not (

            video_url.startswith(
                "http://"
            )

            or

            video_url.startswith(
                "https://"
            )

        ):

            raise ValueError(

                "video_url no es una URL "
                "HTTP/HTTPS válida."

            )


        temp_id = uuid.uuid4().hex


        temp_video = (
            BASE /
            f"transcribe_{temp_id}.mp4"
        )


        temp_audio = (
            BASE /
            f"transcribe_{temp_id}.wav"
        )


        print()
        print(
            "[WHISPER] "
            "Descargando vídeo:"
        )

        print(
            video_url
        )


        # ----------------------------------------------------
        # DESCARGAR
        # ----------------------------------------------------

        download_file(

            video_url,

            temp_video

        )


        # ----------------------------------------------------
        # EXTRAER AUDIO
        # ----------------------------------------------------

        print(
            "[WHISPER] "
            "Extrayendo audio..."
        )


        run_ffmpeg([

            "ffmpeg",

            "-y",

            "-threads",
            "1",

            "-i",
            str(temp_video),

            "-map",
            "0:a:0?",

            "-vn",

            "-ac",
            "1",

            "-ar",
            "16000",

            "-c:a",
            "pcm_s16le",

            str(temp_audio)

        ])


        if not temp_audio.exists():

            raise RuntimeError(
                "No se pudo crear el audio."
            )


        # ----------------------------------------------------
        # WHISPER
        # ----------------------------------------------------

        model = get_whisper_model()


        print(
            "[WHISPER] Transcribiendo..."
        )


        segments, info = model.transcribe(

            str(temp_audio),

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


            words = []


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


                    words.append(
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
                    words

            })


        detected_language = (
            info.language
            if info
            else "es"
        )


        probability = None


        if info:

            try:

                probability = round(

                    float(
                        info.language_probability
                    ),

                    4

                )

            except Exception:

                probability = None


        print(
            "[WHISPER] "
            "Transcripción terminada."
        )


        return jsonify({

            "ok":
                True,

            "language":
                detected_language,

            "language_probability":
                probability,

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

        print()
        print(
            "[WHISPER ERROR]"
        )

        print(
            str(e)
        )


        return jsonify({

            "ok":
                False,

            "error":
                str(e)

        }), 500


    finally:

        if temp_video:

            temp_video.unlink(
                missing_ok=True
            )


        if temp_audio:

            temp_audio.unlink(
                missing_ok=True
            )


# ============================================================
# RENDER
# ============================================================

@app.post("/render")
def render():

    try:

        data = (
            request.get_json(
                silent=True
            )
            or {}
        )


        scenes = data.get(
            "scenes"
        )


        if not isinstance(
            scenes,
            list
        ):

            return jsonify({

                "ok":
                    False,

                "error":
                    "El campo scenes "
                    "debe ser una lista."

            }), 400


        if len(scenes) == 0:

            return jsonify({

                "ok":
                    False,

                "error":
                    "Se necesita al menos "
                    "una escena."

            }), 400


        normalized = []


        for index, scene in enumerate(
            scenes
        ):

            if not isinstance(
                scene,
                dict
            ):

                continue


            video_url = (

                scene.get(
                    "video_url"
                )

                or

                scene.get(
                    "src"
                )

            )


            audio_url = (

                scene.get(
                    "audio_url"
                )

                or

                scene.get(
                    "audio_src"
                )

            )


            duration = scene.get(
                "duration",
                7
            )


            normalized.append({

                "index":
                    index,

                "video_url":
                    video_url,

                "audio_url":
                    audio_url,

                "duration":
                    duration

            })


        if not normalized:

            return jsonify({

                "ok":
                    False,

                "error":
                    "No hay escenas válidas."

            }), 400


        job_id = uuid.uuid4().hex


        job = {

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


        save_job(job)


        thread = threading.Thread(

            target=run_render_job,

            args=(
                job_id,
                normalized
            ),

            daemon=True

        )


        thread.start()


        return jsonify(job)


    except Exception as e:

        return jsonify({

            "ok":
                False,

            "error":
                str(e)

        }), 500


# ============================================================
# STATUS
# ============================================================

@app.get(
    "/status/<job_id>"
)
def status(job_id):

    job = load_job(
        job_id
    )


    if not job:

        return jsonify({

            "ok":
                False,

            "error":
                "No render was found "
                "with that ID."

        }), 404


    return jsonify(job)


# ============================================================
# ARCHIVOS DE RENDER
# ============================================================

@app.get(
    "/files/render/<job_id>/<filename>"
)
def render_file(
    job_id,
    filename
):

    render_dir = (
        BASE /
        "renders" /
        job_id
    )


    return send_from_directory(

        render_dir,

        filename,

        as_attachment=False

    )


# ============================================================
# 413
# ============================================================

@app.errorhandler(413)
def too_large(error):

    return jsonify({

        "ok":
            False,

        "error":
            "El archivo supera "
            "el límite máximo de 1 GB."

    }), 413


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    print()
    print(
        "======================================"
    )

    print(
        "INNOVAX API"
    )

    print(
        "======================================"
    )

    print(
        f"Whisper: "
        f"{WHISPER_MODEL_SIZE}"
    )

    print(
        f"Port: "
        f"{PORT}"
    )

    print()


    app.run(

        host="0.0.0.0",

        port=PORT

    )
