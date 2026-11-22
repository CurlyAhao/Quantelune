"""Local Whisper transcription worker. The caller supplies local files and a local model."""

import json
import os
import subprocess
import sys
from pathlib import Path


def main(request_path):
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    model_path = Path(request["model"]).resolve()
    if not model_path.is_dir():
        raise RuntimeError("未找到本地转录模型，请检查桌面版模型配置。")

    output = Path(request["output"])
    audio = output / "audio.wav"
    cmd = [
        request["ffmpeg"],
        "-nostdin",
        "-v",
        "error",
        "-y",
        "-ss",
        str(request["start"]),
        "-i",
        request["source"],
    ]
    if request["end"] is not None:
        cmd += ["-t", str(request["end"] - request["start"])]
    cmd += ["-map", "0:a:0", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(audio)]
    print(json.dumps({"progress": "正在读取音频"}), flush=True)
    subprocess.run(
        cmd, check=True, capture_output=True, creationflags=0x08000000 if os.name == "nt" else 0
    )

    import soundfile as sf
    import torch
    from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline

    info = sf.info(str(audio))
    duration = info.frames / info.samplerate
    if duration <= 0:
        raise ValueError("选中的时间范围没有音频。")
    torch.set_num_threads(4)
    print(json.dumps({"progress": "正在转录"}), flush=True)
    processor = AutoProcessor.from_pretrained(str(model_path), local_files_only=True)
    model = AutoModelForSpeechSeq2Seq.from_pretrained(str(model_path), local_files_only=True)
    model.eval()
    recognizer = pipeline(
        "automatic-speech-recognition",
        model=model,
        tokenizer=processor.tokenizer,
        feature_extractor=processor.feature_extractor,
        device=-1,
        chunk_length_s=30,
        stride_length_s=5,
    )
    generation = {"task": "transcribe"}
    if request["language"] != "auto":
        generation["language"] = request["language"]
    waveform, sample_rate = sf.read(str(audio), dtype="float32")
    if sample_rate != 16000 or waveform.ndim != 1:
        raise ValueError("转录音频格式无法读取。")
    result = recognizer(waveform, return_timestamps=True, generate_kwargs=generation)
    if not result.get("chunks") and str(result.get("text") or "").strip():
        raise RuntimeError("转录文字没有时间位置，请重试。")
    segments = []
    for item in result.get("chunks", []):
        timestamp = item.get("timestamp") or (None, None)
        if timestamp[0] is None:
            continue
        start = max(0.0, min(duration, float(timestamp[0])))
        end = duration if timestamp[1] is None else max(start, min(duration, float(timestamp[1])))
        text = str(item.get("text") or "").strip()
        if text and end > start:
            segments.append(
                {
                    "id": len(segments),
                    "start": request["start"] + start,
                    "end": request["start"] + end,
                    "text": text,
                }
            )
    payload = {
        "segments": segments,
        "duration": duration,
        "transformers": __import__("transformers").__version__,
        "torch": torch.__version__,
    }
    (output / "result.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1])
