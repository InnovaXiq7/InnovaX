import os
import uuid
import shutil
import subprocess
import threading
import asyncio
import re
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory
import edge_tts


# ============================================================
# APP
# ============================================================

app = Flask(__name__)


# ============================================================
# DIRECTORIOS
# ============================================================

BASE = Path("/tmp/innovax")
BASE.mkdir(parents=True, exist_ok=True)

AUDIO_DIR = BASE / "audio"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# JOBS
# ============================================================

JOBS = {}

RENDER_LOCK = threading.Lock()


# ============================================================
# PUBLIC URL
# ============================================================

PUBLIC_BASE_URL = os.environ.get(
    "PUBLIC_BASE_URL",
    ""
).rstrip("/")


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
# UTILIDADES
# ============================================================

def safe_float(value, default=7.0):

    try:

        value = float(value)

        if not value > 0:
            return default

        return value

    except Exception:

        return default


def format_ass_time(seconds):

    seconds = max(
        0,
        float(seconds)
    )

    hours = int(seconds // 3600)

    minutes = int(
        (seconds % 3600) // 60
    )

    secs = seconds % 60

    whole = int(secs)

    centiseconds = int(
        round(
            (secs - whole) * 100
        )
    )

    if centiseconds >= 100:

        whole += 1
        centiseconds = 0

    if whole >= 60:

        minutes += whole // 60
        whole %= 60

    if minutes >= 60:

        hours += minutes // 60
        minutes %= 60

    return (
        f"{hours}:"
        f"{minutes:02d}:"
        f"{whole:02d}."
        f"{centiseconds:02d}"
    )


def clean_text(text):

    text = str(
        text or ""
    )

    text = text.replace(
        "\n",
        " "
    )

    text = text.replace(
        "\r",
        " "
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


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
# DESCARGAR AUDIO
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
            "128k",

            str(output_file)
        ],

        stdout=subprocess.DEVNULL,

        stderr=subprocess.PIPE,

        text=True
    )


    if result.returncode != 0:

        raise RuntimeError(

            "Error descargando audio:\n"
            +
            result.stderr[-4000:]
        )


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
# CREAR BLOQUES DE SUBTITULOS
# ============================================================

def make_caption_chunks(text):

    text = clean_text(text)

    if not text:
        return []

    # --------------------------------------------------------
    # Quitamos signos para decidir cómo dividir las palabras.
    # Los signos se mantienen posteriormente si forman parte
    # de la palabra original.
    # --------------------------------------------------------

    words = text.split()

    if not words:
        return []


    chunks = []

    current = []


    for word in words:

        current.append(word)

        # ----------------------------------------------------
        # Estilo tipo TikTok:
        #
        # 1 palabra
        # o
        # 2 palabras cortas
        #
        # Evitamos frases enormes.
        # ----------------------------------------------------

        current_text = " ".join(
            current
        )

        clean_current = re.sub(
            r"[^\wáéíóúüñÁÉÍÓÚÜÑ]",
            "",
            current_text
        )


        if len(current) >= 2:

            # Si ya tenemos dos palabras,
            # normalmente cerramos el bloque.

            if len(clean_current) >= 8:

                chunks.append(
                    " ".join(current)
                )

                current = []

            else:

                # Dos palabras muy cortas pueden quedarse juntas.

                continue


        elif len(current) == 1:

            # Palabras largas aparecen solas.

            clean_word = re.sub(
                r"[^\wáéíóúüñÁÉÍÓÚÜÑ]",
                "",
                word
            )

            if len(clean_word) >= 8:

                chunks.append(
                    word
                )

                current = []


    if current:

        chunks.append(
            " ".join(current)
        )


    return chunks


# ============================================================
# CREAR SUBTITULOS ASS
# ============================================================

def create_ass_subtitles(
    text,
    duration,
    output_file
):

    text = clean_text(text)

    if not text:
        return False


    chunks = make_caption_chunks(
        text
    )

    if not chunks:
        return False


    # --------------------------------------------------------
    # Distribución del tiempo.
    #
    # Se pondera por longitud de cada bloque para que las
    # palabras largas permanezcan un poco más.
    # --------------------------------------------------------

    weights = []

    for chunk in chunks:

        letters = len(
            re.sub(
                r"\s+",
                "",
                chunk
            )
        )

        weights.append(
            max(
                1,
                letters
            )
        )


    total_weight = sum(
        weights
    )


    # --------------------------------------------------------
    # ASS
    # --------------------------------------------------------

    ass = []

    ass.append(
        "[Script Info]\n"
    )

    ass.append(
        "ScriptType: v4.00+\n"
    )

    ass.append(
        "PlayResX: 720\n"
    )

    ass.append(
        "PlayResY: 1280\n"
    )

    ass.append(
        "ScaledBorderAndShadow: yes\n"
    )

    ass.append(
        "WrapStyle: 2\n"
    )

    ass.append(
        "Collisions: Normal\n"
    )

    ass.append(
        "\n"
    )


    # --------------------------------------------------------
    # Estilo
    #
    # Blanco
    # Contorno negro
    # Sin caja
    # Negrita
    # Centrado
    # --------------------------------------------------------

    ass.append(
        "[V4+ Styles]\n"
    )

    ass.append(
        "Format: "
        "Name,Fontname,Fontsize,"
        "PrimaryColour,SecondaryColour,"
        "OutlineColour,BackColour,"
        "Bold,Italic,Underline,StrikeOut,"
        "ScaleX,ScaleY,Spacing,Angle,"
        "BorderStyle,Outline,Shadow,"
        "Alignment,MarginL,MarginR,MarginV,"
        "Encoding\n"
    )


    ass.append(
        "Style: "
        "TikTok,"
        "DejaVu Sans,"
        "48,"
        "&H00FFFFFF,"
        "&H00000000,"
        "&H00000000,"
        "&H00000000,"
        "-1,0,0,0,"
        "100,100,0,0,"
        "1,3,0,"
        "5,0,0,0,"
        "1\n"
    )


    ass.append(
        "\n"
    )


    # --------------------------------------------------------
    # Eventos
    # --------------------------------------------------------

    ass.append(
        "[Events]\n"
    )

    ass.append(
        "Format: "
        "Layer,Start,End,Style,"
        "Name,MarginL,MarginR,MarginV,"
        "Effect,Text\n"
    )


    current_time = 0.0


    for index, chunk in enumerate(
        chunks
    ):

        portion = (
            weights[index]
            /
            total_weight
        )


        chunk_duration = (
            duration
            *
            portion
        )


        start = current_time

        end = (
            current_time
            +
            chunk_duration
        )


        # ----------------------------------------------------
        # Evitar subtítulos demasiado rápidos.
        # ----------------------------------------------------

        if (
            end - start
            <
            0.35
        ):

            end = (
                start
                +
                0.35
            )


        # ----------------------------------------------------
        # Posición:
        #
        # 720 x 1280
        #
        # Centro X = 360
        #
        # Y ≈ 850
        #
        # Esto queda aproximadamente donde está el TikTok
        # de referencia.
        # ----------------------------------------------------

        ass_text = (
            r"{\an5\pos(360,850)}"
            +
            chunk.replace(
                "{",
                r"\{"
            ).replace(
                "}",
                r"\}"
            )
        )


        ass.append(

            "Dialogue: "
            f"0,"
            f"{format_ass_time(start)},"
            f"{format_ass_time(min(end, duration))},"
            "TikTok,"
            ",0,0,0,"
            ","
            f"{ass_text}\n"
        )


        current_time = end


    output_file.write_text(
        "".join(ass),
        encoding="utf-8"
    )


    return True


# ============================================================
# RENDER DE ESCENAS
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


        JOBS[job_id][
            "status"
        ] = "rendering"


        try:

            inputs = []


            # =================================================
            # ESCENAS
            # =================================================

            for i, scene in enumerate(
                scenes
            ):


                # ------------------------------------------------
                # VIDEO
                # ------------------------------------------------

                src = (

                    scene.get(
                        "video_url"
                    )

                    or

                    scene.get(
                        "src"
                    )

                    or

                    scene.get(
                        "video_src"
                    )
                )


                if not src:

                    raise ValueError(

                        f"La escena "
                        f"{i + 1} no tiene "
                        "video_url."
                    )


                # ------------------------------------------------
                # AUDIO
                # ------------------------------------------------

                audio_url = (

                    scene.get(
                        "audio_url"
                    )

                    or

                    scene.get(
                        "audio_src"
                    )
                )


                # ------------------------------------------------
                # SUBTITULOS
                # ------------------------------------------------

                subtitle = (

                    scene.get(
                        "subtitle"
                    )

                    or

                    scene.get(
                        "voiceover"
                    )

                    or

                    scene.get(
                        "text"
                    )

                    or

                    ""
                )


                subtitle = clean_text(
                    subtitle
                )


                # ------------------------------------------------
                # DURACION
                # ------------------------------------------------

                duration = safe_float(

                    scene.get(
                        "duration",
                        7
                    ),

                    7
                )


                # ------------------------------------------------
                # ARCHIVOS
                # ------------------------------------------------

                scene_video = (

                    job_dir
                    /
                    f"scene_{i}.mp4"
                )


                scene_audio = None


                ass_file = (

                    job_dir
                    /
                    f"subtitle_{i}.ass"
                )


                # ------------------------------------------------
                # AUDIO
                # ------------------------------------------------

                if audio_url:

                    scene_audio = (

                        job_dir
                        /
                        f"audio_{i}.m4a"
                    )


                    download_audio(

                        audio_url,

                        scene_audio
                    )


                # ------------------------------------------------
                # SUBTITULOS ASS
                # ------------------------------------------------

                has_subtitles = False


                if subtitle:

                    has_subtitles = (
                        create_ass_subtitles(

                            subtitle,

                            duration,

                            ass_file

                        )
                    )


                # ------------------------------------------------
                # FILTRO VIDEO
                # ------------------------------------------------

                video_filter = (

                    "scale=720:1280:"
                    "force_original_aspect_ratio=increase,"
                    "crop=720:1280,"
                    "fps=30"
                )


                # ------------------------------------------------
                # INCRUSTAR SUBTITULOS
                # ------------------------------------------------

                if has_subtitles:

                    ass_path = (
                        ass_file
                        .as_posix()
                        .replace(
                            "\\",
                            "/"
                        )
                    )


                    # ------------------------------------------------
                    # FFmpeg subtitles filter
                    # ------------------------------------------------

                    video_filter += (

                        ",subtitles="
                        f"'{ass_path}'"
                    )


                # =================================================
                # CON AUDIO
                # =================================================

                if scene_audio:


                    command = [

                        "ffmpeg",

                        "-y",

                        "-threads",
                        "1",

                        "-i",
                        src,

                        "-i",
                        str(scene_audio),

                        "-t",
                        str(duration),

                        "-vf",
                        video_filter,

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

                        "-pix_fmt",
                        "yuv420p",

                        "-c:a",
                        "aac",

                        "-b:a",
                        "128k",

                        "-shortest",

                        "-movflags",
                        "+faststart",

                        str(scene_video)
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
                        video_filter,

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

                        str(scene_video)
                    ]


                # =================================================
                # EJECUTAR FFMPEG
                # =================================================

                result = subprocess.run(

                    command,

                    stdout=subprocess.DEVNULL,

                    stderr=subprocess.PIPE,

                    text=True
                )


                if result.returncode != 0:

                    raise RuntimeError(

                        f"Error renderizando "
                        f"escena {i + 1}:\n"
                        +
                        result.stderr[-5000:]
                    )


                inputs.append(
                    scene_video
                )


                # ------------------------------------------------
                # LIMPIEZA
                # ------------------------------------------------

                if (
                    scene_audio
                    and
                    scene_audio.exists()
                ):

                    scene_audio.unlink(
                        missing_ok=True
                    )


                if ass_file.exists():

                    ass_file.unlink(
                        missing_ok=True
                    )


            # ====================================================
            # CONCATENAR
            # ====================================================

            concat_file = (

                job_dir
                /
                "concat.txt"
            )


            concat_lines = []


            for video_file in inputs:

                concat_lines.append(

                    "file '"
                    +
                    video_file.as_posix()
                    +
                    "'\n"
                )


            concat_file.write_text(

                "".join(
                    concat_lines
                ),

                encoding="utf-8"
            )


            final_file = (

                job_dir
                /
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

                    "Error concatenando "
                    "las escenas:\n"
                    +
                    result.stderr[-5000:]
                )


            # ====================================================
            # COMPLETADO
            # ====================================================

            JOBS[job_id].update(

                status="succeeded",

                url=public_url(

                    f"/files/"
                    f"{job_id}/"
                    f"final.mp4"
                ),

                local_url=(

                    f"/files/"
                    f"{job_id}/"
                    f"final.mp4"
                )
            )


            # ====================================================
            # LIMPIEZA
            # ====================================================

            for video_file in inputs:

                video_file.unlink(
                    missing_ok=True
                )


            concat_file.unlink(
                missing_ok=True
            )


        except Exception as e:


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

        service=(
            "n8n-free-ffmpeg-renderer"
        ),

        version=7,

        tts="edge-tts",

        subtitles=True,

        subtitle_style=(
            "tiktok-word-captions"
        )
    )


# ============================================================
# ROOT
# ============================================================

@app.get("/")
def root():

    return jsonify(

        ok=True,

        service=(
            "n8n-free-ffmpeg-renderer"
        ),

        version=7,

        tts="edge-tts",

        subtitles=True
    )


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
            or
            {}
        )


        text = clean_text(

            data.get(
                "text",
                ""
            )
        )


        voice = str(

            data.get(
                "voice",
                "es-ES-AlvaroNeural"
            )
        ).strip()


        if not text:

            return jsonify(

                error=(
                    "No text supplied."
                )

            ), 400


        if not voice:

            voice = "es-ES-AlvaroNeural"


        audio_id = uuid.uuid4().hex

        filename = (
            f"{audio_id}.mp3"
        )


        output_file = (

            AUDIO_DIR
            /
            filename
        )


        generate_tts(

            text,

            voice,

            output_file
        )


        if not output_file.exists():

            raise RuntimeError(

                "TTS no creó "
                "el archivo de audio."
            )


        path = (

            f"/files/audio/"
            f"{filename}"
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

            request.files.get(
                "file"
            )

            or

            request.files.get(
                "data"
            )
        )


        if not audio:

            return jsonify(

                error=(
                    "No audio file supplied. "
                    "Usa el campo multipart "
                    "'file' o 'data'."
                )

            ), 400


        audio_id = uuid.uuid4().hex

        filename = (
            f"{audio_id}.mp3"
        )


        output_file = (

            AUDIO_DIR
            /
            filename
        )


        audio.save(
            output_file
        )


        path = (

            f"/files/audio/"
            f"{filename}"
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
# RENDER
# ============================================================

@app.post("/render")
def render():

    data = (

        request.get_json(
            silent=True
        )
        or
        {}
    )


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
                "Se necesita "
                "al menos una escena."
            ),

            id=None,

            status="failed",

            url=None

        ), 400


    job_id = uuid.uuid4().hex


    JOBS[job_id] = {

        "id": job_id,

        "status": "queued",

        "url": None,

        "error": None
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

            scene.get(
                "video_url"
            )

            or

            scene.get(
                "src"
            )

            or

            scene.get(
                "video_src"
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


        # --------------------------------------------------------
        # IMPORTANTE
        #
        # Aceptamos subtitle, voiceover o text.
        # Esto hace que no tengas que cambiar n8n.
        # --------------------------------------------------------

        subtitle = (

            scene.get(
                "subtitle"
            )

            or

            scene.get(
                "voiceover"
            )

            or

            scene.get(
                "text"
            )

            or
            ""
        )


        duration = safe_float(

            scene.get(
                "duration",
                7
            ),

            7
        )


        normalized.append({

            "index": index,

            "video_url": src,

            "audio_url": audio_url,

            "subtitle": subtitle,

            "duration": duration

        })


    if not normalized:

        JOBS[job_id].update(

            status="failed",

            error=(
                "No hay escenas válidas."
            )
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
                "No existe un render "
                "con ese ID."
            )

        ), 404


    return jsonify(
        job
    )


# ============================================================
# VIDEO FILE
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
# AUDIO FILE
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
