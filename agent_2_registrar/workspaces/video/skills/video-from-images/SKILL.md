---
name: video-from-images
description: Creates vertical property showcase videos from listing photos using ffmpeg slideshow. Use when generating Reels/TikTok/Shorts video from property images or when video agent is invoked.
metadata: {"openclaw": {"requires": {"bins": ["ffmpeg"]}}}
---

# Video from Images

## Defaults

- Format: MP4 H.264
- Resolution: 1080x1920 (vertical)
- Duration: ~3 sec per photo, max 60 sec total
- Transition: fade 0.5s

## Slideshow script

```bash
LISTING_ID="{listing_id}"
PHOTOS_DIR="data/listings/${LISTING_ID}/photos"
OUT_DIR="data/media/${LISTING_ID}"
mkdir -p "$OUT_DIR"

# Create concat list (3 sec each)
> /tmp/photos.txt
for f in $(ls "$PHOTOS_DIR"/*.{jpg,jpeg,png,webp} 2>/dev/null | head -20); do
  echo "file '$f'" >> /tmp/photos.txt
  echo "duration 3" >> /tmp/photos.txt
done

${FFMPEG_PATH:-ffmpeg} -y -f concat -safe 0 -i /tmp/photos.txt \
  -vf "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,format=yuv420p" \
  -c:v libx264 -pix_fmt yuv420p -r 30 \
  "$OUT_DIR/video.mp4"
```

## With title overlay (optional)

```bash
TITLE=$(jq -r '.title' "data/listings/${LISTING_ID}/raw.json")
PRICE=$(jq -r '.price.amount' "data/listings/${LISTING_ID}/raw.json")

${FFMPEG_PATH:-ffmpeg} -y -i "$OUT_DIR/video.mp4" \
  -vf "drawtext=text='${TITLE}':fontsize=48:fontcolor=white:x=(w-text_w)/2:y=h-120:box=1:boxcolor=black@0.5" \
  "$OUT_DIR/video_final.mp4"
mv "$OUT_DIR/video_final.mp4" "$OUT_DIR/video.mp4"
```

## Validation

- [ ] Минимум 3 фото
- [ ] video.mp4 существует и > 100KB
- [ ] meta.json записан

## Errors

- Нет ffmpeg → сообщи установить: `brew install ffmpeg` / `apt install ffmpeg`
- < 3 фото → верни ошибку, не генерируй пустое видео
