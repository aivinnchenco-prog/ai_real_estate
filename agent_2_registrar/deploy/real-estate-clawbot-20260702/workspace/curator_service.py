"""
curator_service.py — локальный отбор лучших кадров объекта (0 LLM-токенов).
CPU-only сборка под VPS: 2 vCPU, без GPU.

Запуск (внутри .venv-curator):
 uvicorn curator_service:app --host 127.0.0.1 --port 8077 --workers 1

ВАЖНО про --workers 1: CLIP грузится один раз в память процесса. Несколько
воркеров = несколько копий модели в RAM одновременно — на 2 vCPU без GPU
это лишний расход памяти и борьба за CPU. Не увеличивать.
"""

import os
# Ограничиваем потоки ДО импорта torch — иначе он попытается забрать все ядра
os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("MKL_NUM_THREADS", "2")

import io
import numpy as np
import cv2
import requests
import torch
import open_clip
from PIL import Image
from fastapi import FastAPI
from pydantic import BaseModel

torch.set_num_threads(2) # должно совпадать с nproc на этом VPS

DEVICE = "cpu" # на этом VPS GPU нет — фиксируем явно, без auto-detect
MODEL_NAME = "ViT-B-32" # сознательно НЕ ViT-L-14: та модель рассчитана
 # на GPU, на 2 vCPU будет в разы медленнее без пользы
PRETRAINED = "laion2b_s34b_b79k"

# ── технические пороги (тюнятся под твой парсинг) ──
MIN_SHORT_SIDE = 700 # px
BLUR_MIN_VAR = 80.0 # ниже = размыто
BRIGHTNESS_RANGE = (40, 225) # средняя яркость 0..255

# ── классы содержимого: (промпт, категория, junk?) ──
PROMPTS = [
 ("a wide photo of a spacious furnished living room", "living", False),
 ("a modern kitchen interior, real estate photo", "kitchen", False),
 ("a bright clean bedroom interior", "bedroom", False),
 ("a clean modern bathroom interior", "bathroom", False),
 ("the exterior facade of a residential building", "exterior", False),
 ("a balcony or terrace with a city or nature view", "view", False),
 ("a dining area with a table", "dining", False),
 # --- junk: то, что парсер тащит лишнего ---
 ("a close-up of a hair dryer", "junk", True),
 ("a close-up of plates, dishes or cutlery", "junk", True),
 ("a close-up of a power outlet, switch or faucet", "junk", True),
 ("a cluttered messy room with clutter", "junk", True),
 ("a photo of documents, paper or a contract", "junk", True),
 ("a screenshot or a map or a floor plan drawing", "junk", True),
 ("a dark blurry low quality photo", "junk", True),
]
AESTHETIC_PROMPTS = [
 "a professional real estate photo, magazine quality, wide angle, well lit",
 "a beautiful interior design photo, bright, airy, stylish",
]

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_NAME}


print(f"[curator] loading CLIP {MODEL_NAME} on {DEVICE} ...")
clip_model, _, preprocess = open_clip.create_model_and_transforms(
 MODEL_NAME, pretrained=PRETRAINED, device=DEVICE
)
clip_model.eval()
tokenizer = open_clip.get_tokenizer(MODEL_NAME)


def _encode_text(prompts):
 with torch.no_grad():
  t = tokenizer(prompts).to(DEVICE)
  f = clip_model.encode_text(t)
  return f / f.norm(dim=-1, keepdim=True)


CLASS_FEATS = _encode_text([p[0] for p in PROMPTS])
AEST_FEATS = _encode_text(AESTHETIC_PROMPTS)


class ImgIn(BaseModel):
 key: str
 url: str


class SelectIn(BaseModel):
 images: list[ImgIn]
 top_k: int = 6


def _tech_gate(bgr) -> str | None:
 h, w = bgr.shape[:2]
 if min(h, w) < MIN_SHORT_SIDE:
  return f"too_small({min(h, w)}px)"
 gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
 if cv2.Laplacian(gray, cv2.CV_64F).var() < BLUR_MIN_VAR:
  return "blurry"
 b = gray.mean()
 if not (BRIGHTNESS_RANGE[0] <= b <= BRIGHTNESS_RANGE[1]):
  return f"bad_brightness({b:.0f})"
 return None


def _score(pil):
 with torch.no_grad():
  x = preprocess(pil).unsqueeze(0).to(DEVICE)
  f = clip_model.encode_image(x)
  f = f / f.norm(dim=-1, keepdim=True)
  cls = (100.0 * f @ CLASS_FEATS.T).softmax(dim=-1)[0]
  idx = int(cls.argmax())
  prompt, category, is_junk = PROMPTS[idx]
  aest = float((f @ AEST_FEATS.T).mean())
  return category, is_junk, aest, float(cls[idx])


@app.post("/select")
def select(req: SelectIn):
 survivors = []
 rejected = []
 for item in req.images:
  try:
   raw = requests.get(item.url, timeout=30).content
   pil = Image.open(io.BytesIO(raw)).convert("RGB")
  except Exception as e:
   rejected.append({"key": item.key, "reason": f"download_failed:{e}"})
   continue

  bgr = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
  why = _tech_gate(bgr)
  if why:
   rejected.append({"key": item.key, "reason": why})
   continue

  category, is_junk, aest, conf = _score(pil)
  if is_junk:
   rejected.append({"key": item.key, "reason": f"content:{category}({conf:.2f})"})
   continue
  survivors.append((item.key, category, aest))

 survivors.sort(key=lambda t: t[2], reverse=True)

 order = ["exterior", "view", "living", "dining", "kitchen", "bedroom", "bathroom"]
 by_cat: dict[str, list] = {}
 for k, c, s in survivors:
  by_cat.setdefault(c, []).append((k, c, s))

 selected = []
 for c in order:
  if by_cat.get(c):
   selected.append(by_cat[c].pop(0))
   if len(selected) >= req.top_k:
    break
 if len(selected) < req.top_k:
  rest = [x for lst in by_cat.values() for x in lst]
  rest.sort(key=lambda t: t[2], reverse=True)
  selected += rest[: req.top_k - len(selected)]

 return {
  "selected": [
   {"key": k, "category": c, "score": round(s, 4)} for k, c, s in selected
  ],
  "rejected": rejected,
 }
