# ============================================================
# RENDER JOB OPTIMIZADO PARA BAJA MEMORIA
# ============================================================

def run_job(job_id, scenes):
    with RENDER_LOCK:
        job_dir = BASE / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        JOBS[job_id]["status"] = "rendering"

        try:
            inputs = []

            for i, scene in enumerate(scenes):
                src = scene.get("video_url") or scene.get("src") or scene.get("video_src")
                if not src:
                    raise ValueError(f"Scene {i + 1} has no video source")

                audio_url = scene.get("audio_url") or scene.get("audio_src")
                duration = float(scene.get("duration", 7))
                if duration < 1:
                    duration = 1

                video_out = job_dir / f"scene_{i}.mp4"
                audio_out = None

                if audio_url:
                    audio_out = job_dir / f"audio_{i}.m4a"
                    download_audio(audio_url, audio_out)
                    command = [
                        "ffmpeg", "-y", "-threads", "1", "-i", src, "-i", str(audio_out),
                        "-t", str(duration),
                        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=24",
                        "-map", "0:v:0", "-map", "1:a:0",
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                        "-c:a", "aac", "-b:a", "128k", "-shortest", "-pix_fmt", "yuv420p",
                        str(video_out)
                    ]
                else:
                    command = [
                        "ffmpeg", "-y", "-threads", "1", "-i", src,
                        "-t", str(duration),
                        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=24",
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                        "-pix_fmt", "yuv420p", "-an",
                        str(video_out)
                    ]

                result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
                if result.returncode != 0:
                    raise RuntimeError(f"Error rendering scene {i + 1}:\n{result.stderr[-2000:]}")

                inputs.append(video_out)
                if audio_out and audio_out.exists():
                    audio_out.unlink(missing_ok=True)

            # Concatenar
            concat_file = job_dir / "concat.txt"
            concat_lines = [f"file '{vf.as_posix()}'\n" for vf in inputs]
            concat_file.write_text("".join(concat_lines), encoding="utf-8")

            final_file = job_dir / "final.mp4"
            concat_command = [
                "ffmpeg", "-y", "-threads", "1", "-f", "concat", "-safe", "0",
                "-i", str(concat_file), "-c", "copy", "-movflags", "+faststart", str(final_file)
            ]

            result = subprocess.run(concat_command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"Error concatenating scenes:\n{result.stderr[-2000:]}")

            JOBS[job_id].update(
                status="succeeded",
                url=public_url(f"/files/{job_id}/final.mp4"),
                local_url=f"/files/{job_id}/final.mp4"
            )

            # Limpiar ficheros individuales de escena para liberar disco/memoria RAM inmediatamente
            for video_file in inputs:
                video_file.unlink(missing_ok=True)
            concat_file.unlink(missing_ok=True)

        except Exception as e:
            JOBS[job_id].update(status="failed", error=str(e))
            clean_job(job_id)
