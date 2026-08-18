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

    try:
        # 1. โหลดไฟล์เสียง
        r_audio = requests.get(req.audio_url, timeout=60)
        with open(audio_file, "wb") as f:
            f.write(r_audio.content)

        # 2. โหลดภาพ AI 5 ภาพ
        for idx, url in enumerate(req.image_urls):
            img_path = f"img_{task_id}_{idx}.jpg"
            r_img = requests.get(url, timeout=60)
            with open(img_path, "wb") as f:
                f.write(r_img.content)
            img_files.append(img_path)

        # 3. FFmpeg Ken Burns Effect สลับซูมเข้า/ออก 5 ฉาก 9:16 ความคมชัดสูง
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-t", "10", "-i", img_files[0],
            "-loop", "1", "-t", "10", "-i", img_files[1],
            "-loop", "1", "-t", "10", "-i", img_files[2],
            "-loop", "1", "-t", "10", "-i", img_files[3],
            "-loop", "1", "-t", "10", "-i", img_files[4],
            "-i", audio_file,
            "-filter_complex",
            "[0:v]zoompan=z='min(zoom+0.0015,1.2)':d=300:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920[v0];"
            "[1:v]zoompan=z='if(lte(zoom,1.0),1.2,max(1.0,zoom-0.0015))':d=300:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920[v1];"
            "[2:v]zoompan=z='min(zoom+0.0015,1.2)':d=300:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920[v2];"
            "[3:v]zoompan=z='if(lte(zoom,1.0),1.2,max(1.0,zoom-0.0015))':d=300:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920[v3];"
            "[4:v]zoompan=z='min(zoom+0.0015,1.2)':d=300:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920[v4];"
            "[v0][v1][v2][v3][v4]concat=n=5:v=1:a=0[v_out]",
            "-map", "[v_out]", "-map", "5:a",
            "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p",
            "-shortest", output_file
        ]
        subprocess.run(cmd, check=True)

        return {
            "status": "success",
            "video_url": f"/outputs/final_{task_id}.mp4"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        if os.path.exists(audio_file):
            os.remove(audio_file)
        for f in img_files:
            if os.path.exists(f):
                os.remove(f)
