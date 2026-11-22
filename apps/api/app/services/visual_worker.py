"""Local image encoder for ordinary project images, without model downloads."""

from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import json
import sys
from pathlib import Path

import numpy as np
from media_features import _image_vectors, _visual_model
from PIL import Image, ImageOps


def main(request_path: str) -> None:
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    output = Path(request["output"])
    samples = request["samples"]
    model, transform, weights = _visual_model(request["options"])
    batches = []
    for start in range(0, len(samples), 8):
        batch = samples[start : start + 8]
        pictures = []
        for sample in batch:
            try:
                with Image.open(sample["path"]) as image:
                    pictures.append(np.asarray(ImageOps.exif_transpose(image).convert("RGB")))
            except (OSError, ValueError) as exc:
                raise ValueError(
                    f"无法读取图片：{sample['name']}。请检查格式和文件是否完整。"
                ) from exc
        vectors = _image_vectors(pictures, model, transform)
        batches.append(vectors)
        print(
            json.dumps(
                {"label": f"{start + len(batch)}/{len(samples)} · 图像特征"}, ensure_ascii=True
            ),
            flush=True,
        )
    vectors = np.concatenate(batches, axis=0).astype(np.float32)
    lengths = np.linalg.norm(vectors, axis=1)
    if np.any(~np.isfinite(vectors)) or np.any(lengths <= 0):
        raise ValueError("图像特征无效，请检查图像和模型文件。")
    normalized = vectors / lengths[:, None]
    similarities = normalized @ normalized.T
    np.savez_compressed(output / "vectors.npz", image_vectors=vectors)
    with (output / "summary.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        fields = ["sample_id", "filename", "description", "mean_similarity", "max_similarity"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for index, sample in enumerate(samples):
            others = np.delete(similarities[index], index)
            writer.writerow(
                {
                    "sample_id": sample["id"],
                    "filename": sample["name"],
                    "description": sample.get("description", ""),
                    "mean_similarity": float(others.mean()) if len(others) else "",
                    "max_similarity": float(others.max()) if len(others) else "",
                }
            )
    entries = [
        {key: item[key] for key in ("id", "name", "sha256", "description")} for item in samples
    ]
    (output / "index.json").write_text(
        json.dumps({"entries": entries}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with weights.open("rb") as stream:
        weights_sha = hashlib.file_digest(stream, "sha256").hexdigest()
    parameters = {
        "model": "ResNet18",
        "model_sha256": weights_sha,
        "device": "cpu",
        "feature_dimensions": int(vectors.shape[1]),
        "preprocessing": "ImageNet V1 resize 256, center crop 224, normalize",
        "comparison": "cosine similarity of L2-normalized vectors",
        "torchvision_version": importlib.metadata.version("torchvision"),
    }
    (output / "parameters.json").write_text(
        json.dumps(parameters, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "result.json").write_text(
        json.dumps({"samples": len(samples)}, ensure_ascii=False), encoding="utf-8"
    )


if __name__ == "__main__":
    main(sys.argv[1])
