"""DWD synoptic chart fetcher."""

import httpx
import logging
from typing import Optional
import base64
from io import BytesIO
from PIL import Image

logger = logging.getLogger(__name__)


# DWD Open Data LATEST endpoints
CHART_URLS = {
    "na": "https://opendata.dwd.de/weather/charts/analysis/Z__C_EDZW_LATEST_tka01,ana_bwkman_dwdna_O_000000_000000_LATEST_WV12.png",
    "eu": "https://opendata.dwd.de/weather/charts/analysis/Z__C_EDZW_LATEST_tka01,ana_bwkman_dwdc_O_000000_000000_LATEST_WV12.png",
}


class DWDChartFetcher:
    """Fetches synoptic charts from DWD Open Data."""

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=30.0)

    async def close(self):
        await self.client.aclose()

    def _compress_to_b64(
        self, image_bytes: bytes, max_width: int = 600, quality: int = 70
    ) -> str:
        """Compress image to resized JPEG and return base64 string."""
        img = Image.open(BytesIO(image_bytes))

        # Resize maintaining aspect ratio
        if img.width > max_width:
            ratio = max_width / img.width
            new_size = (max_width, int(img.height * ratio))
            img = img.resize(new_size, Image.Resampling.LANCZOS)

        # Convert to RGB if necessary (for JPEG)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        # Save as compressed JPEG
        buffer = BytesIO()
        img.save(buffer, format="JPEG", quality=quality, optimize=True)
        compressed_bytes = buffer.getvalue()

        logger.debug("Compressed: %d -> %d bytes", len(image_bytes), len(compressed_bytes))
        return base64.b64encode(compressed_bytes).decode("utf-8")

    async def get_chart_base64(self, chart_type: str = "na") -> Optional[str]:
        """
        Fetch chart from DWD, compress, and return as base64 string.

        Returns:
            Base64-encoded image string or None on error.
        """
        url = CHART_URLS.get(chart_type)
        if not url:
            logger.warning("Unknown chart type: %s", chart_type)
            return None

        try:
            response = await self.client.get(url)
            response.raise_for_status()
            return self._compress_to_b64(response.content)
        except Exception as e:
            logger.error("Chart fetch failed for %s: %s", chart_type, e)
            return None


# Module-level test
if __name__ == "__main__":
    import asyncio

    async def test():
        fetcher = DWDChartFetcher()
        try:
            for chart_type in CHART_URLS:
                b64 = await fetcher.get_chart_base64(chart_type)
                if b64:
                    print(f"{chart_type}: {len(b64)} base64 chars")
                else:
                    print(f"{chart_type}: fetch failed")
        finally:
            await fetcher.close()

    asyncio.run(test())
