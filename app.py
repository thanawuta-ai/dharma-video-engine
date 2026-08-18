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
        for idx, url in enumerate(req.image_urls):
            img_path = f"img_{task_id}_{idx}.jpg"
            r_img = requests.get(url, timeout=60)
            with open(img_path, "wb") as f:
                f.write(r_img.content)
            img_files.append(img_path)

        # 3. เรนเดอร์ทีละคลิปสั้นๆ เพื่อประหยัด RAM (ใช้ RAM ไม่เกิน 150MB)
        concat_list_file = f"concat_{task_id}.txt"
        with open(concat_list_file, "w") as f_list:
            for idx, img in enumerate(img_files):
                clip_path = f"clip_{task_id}_{idx}.mp4"
                # ซูมเข้าและซูมออกสลับกัน
                zoom_expr = "min(zoom+0.0015,1.15)" if idx % 2 == 0 else "if(lte(zoom,1.0),1.15,max(1.0,zoom-0.0015))"
                
                cmd_clip = [
                    "ffmpeg", "-y",
                    "-loop", "1", "-t", "8", "-i", img,
                    "-filter_complex", f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,zoompan=z='{zoom_expr}':d=200:s=1080x1920:fps=25",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    clip_path
                ]
                subprocess.run(cmd_clip, check=True)
                clip_files.append(clip_path)
                f_list.write(f"file '{clip_path}'\n")

        # 4. รวมคลิปและใส่เสียงธรรมะ
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
            "video_url": f"/outputs/final_{task_id}.mp4"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        # เคลียร์ไฟล์ขยะทันทีเพื่อคืน RAM
        for temp_f in [audio_file, f"concat_{task_id}.txt"] + img_files + clip_files:
            if os.path.exists(temp_f):
                os.remove(temp_f)
