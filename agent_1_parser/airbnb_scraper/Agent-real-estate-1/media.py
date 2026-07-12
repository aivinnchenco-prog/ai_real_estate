import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

import config
from CustomLogger import logger


def _download_one(index, img_url, folder, prefix):
    retries = config.PHOTO_DOWNLOAD_RETRIES
    delay = config.PHOTO_DOWNLOAD_RETRY_DELAY_SEC
    last_exc = None

    for attempt in range(retries + 1):
        try:
            response = requests.get(img_url, stream=True, timeout=45)
            response.raise_for_status()
            file_path = os.path.join(folder, f'{prefix}_{index}.jpg')
            with open(file_path, 'wb') as file:
                for chunk in response.iter_content(chunk_size=16384):
                    if chunk:
                        file.write(chunk)
            return file_path
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(delay * (attempt + 1))

    logger.error(f'Failed to download image {img_url}: {last_exc}')
    return None


def download_images(image_urls, folder='temp_images', prefix='image', max_count=None, workers=None):
    """Скачивает все фото листинга. Возвращает список локальных путей."""
    if not image_urls:
        return []

    total_found = len(image_urls)
    limit = max_count if max_count is not None else config.MAX_PHOTOS_DOWNLOAD
    if limit > 0:
        image_urls = image_urls[:limit]

    os.makedirs(folder, exist_ok=True)
    worker_count = workers if workers is not None else config.PHOTO_DOWNLOAD_WORKERS
    worker_count = max(1, min(worker_count, len(image_urls)))

    logger.info(
        f'Скачивание фото: найдено {total_found}, '
        f'к скачиванию {len(image_urls)}, потоков {worker_count}'
    )

    local_paths = []
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(_download_one, index, img_url, folder, prefix): index
            for index, img_url in enumerate(image_urls)
        }
        results = {}
        for future in as_completed(futures):
            path = future.result()
            if path:
                results[futures[future]] = path
        local_paths = [results[i] for i in sorted(results)]

    logger.info(f'Скачано {len(local_paths)} из {len(image_urls)} фото.')
    if len(local_paths) < len(image_urls):
        logger.warning(
            f'Не все фото скачались: {len(local_paths)}/{len(image_urls)} '
            f'(из {total_found} URL на листинге)'
        )
    return local_paths


def cleanup_paths(paths):
    for path in paths:
        try:
            os.remove(path)
        except OSError as exc:
            logger.error(f'Error deleting file {path}: {exc}')
