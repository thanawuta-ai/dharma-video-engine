from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import subprocess
import os
import uuid
import requests
from fastapi.staticfiles import StaticFiles

app = FastAPI()
os.makedirs("outputs", exist_ok=True)
app.mount("/outputs", StaticFiles(directory="outputs"), name="outputs")

class RenderRequest(BaseModel):
    audio_url: str
    image_urls: list[str]

@app.get("/")
def read_root():
    return {"status": "ok", "service": "dharma-video-engine"}

@app.post("/render")
def render_video(req: RenderRequest):
    task_id = str(uuid.uuid4())[:8]
    audio_file = f"audio_{task_id}.mp3"
    output_file = f"outputs/final_{task_id}.mp4"
    img_files = []
    clip_files = []

    try:
        # 1. โหลดไฟล์เสียง
        r_audio = requests.get(req.audio_url, timeout=60)
        with open(audio_file, "wb") as f:
            f.write(r_audio.content)

        # 2. โหลดรูปภาพ
        headers = {'User-Agent': 'Mozilla/5.0'}
        for idx, url in enumerate(req.image_urls):
            img_path = f"img_{task_id}_{idx}.jpg"
            r_img = requests.get(url, headers=headers, timeout=60)
            with open(img_path, "wb") as f:
                f.write(r_img.content)
            img_files.append(img_path)

        # 3. เรนเดอร์รวดเร็วพิเศษ (ใช้ RAM ต่ำ + Fast Encoding)
        concat_list_file = f"concat_{task_id}.txt"
        with open(concat_list_file, "w") as f_list:
            for idx, img in enumerate(img_files):
                clip_path = f"clip_{task_id}_{idx}.mp4"
                
                cmd_clip = [
                    "ffmpeg", "-y",
                    "-loop", "1", "-t", "8", "-i", img,
                    "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,format=yuv420p",
                    "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage",
                    clip_path
                ]
                subprocess.run(cmd_clip, check=True)
                clip_files.append(clip_path)
                f_list.write(f"file '{clip_path}'\n")

        # 4. ประกอบเสียงและตัดจบตามความยาวเสียงจริง
        cmd_final = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", concat_list_file,
            "-i", audio_file,
            "-c:v", "copy", "-c:a", "aac",
            "-shortest", output_file
        ]
        subprocess.run(cmd_final, check=True)

        return {
            "status": "success",
            "video_url": f"https://dharma-video-engine.onrender.com/{output_file}"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        for temp_f in [audio_file, f"concat_{task_id}.txt"] + img_files + clip_files:
            if os.path.exists(temp_f):
                os.remove(temp_f)
