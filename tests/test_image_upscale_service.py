from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Barrier
from types import SimpleNamespace

from PIL import Image

from services import image_upscale_service


def _png_bytes(width: int = 8, height: int = 4) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(output, format="PNG")
    return output.getvalue()


def test_failed_upscale_returns_original_with_persistent_event_metadata(monkeypatch) -> None:
    source = _png_bytes()
    monkeypatch.setattr(
        image_upscale_service,
        "config",
        SimpleNamespace(image_upscale_enabled=True, image_upscale_engine="pillow_lanczos"),
    )
    monkeypatch.setattr(
        image_upscale_service,
        "_pillow_lanczos",
        lambda data, target: (_ for _ in ()).throw(RuntimeError("resize failed")),
    )

    outcome = image_upscale_service.upscale_image_with_status(source, "16x12")

    assert outcome.payload == source
    assert outcome.event_type == "upscale_fallback_original"
    assert outcome.event_data["error"] == "resize failed"


def test_upscale_pool_is_not_serialized_by_a_global_lock(monkeypatch) -> None:
    source = _png_bytes(8, 8)
    barrier = Barrier(2, timeout=2)

    def concurrent_upscale(image_data, target):
        barrier.wait()
        return image_data

    monkeypatch.setattr(
        image_upscale_service,
        "config",
        SimpleNamespace(image_upscale_enabled=True, image_upscale_engine="pillow_lanczos"),
    )
    monkeypatch.setattr(image_upscale_service, "_pillow_lanczos", concurrent_upscale)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(image_upscale_service.upscale_image_with_status, source, "16x16")
            for _ in range(2)
        ]
        outcomes = [future.result(timeout=3) for future in futures]

    assert [item.payload for item in outcomes] == [source, source]
