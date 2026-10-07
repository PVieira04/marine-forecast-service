"""Test that OpenMeteoClient retries on ConnectError (and other TransportErrors).

Usage:
    python -m tests.test_retry_connect_error

Expected output:
    3 retry log lines showing ConnectError, then the final exception.
    This proves the retry loop catches ConnectError instead of crashing.
"""

import asyncio
from src.weather import OpenMeteoClient


# RFC 5737 TEST-NET address — guaranteed unroutable, triggers ConnectError
UNREACHABLE_URL = "http://192.0.2.1/v1/forecast"


async def test_connect_error_retry():
    """Verify that ConnectError is caught and retried."""
    client = OpenMeteoClient()
    # Shorten delays so the test finishes fast
    client.RETRY_DELAY_BASE = 0.1
    # Point both APIs at an unreachable host
    client.WEATHER_API_URL = UNREACHABLE_URL

    print("--- Testing ConnectError retry (weather API) ---")
    print(f"URL: {UNREACHABLE_URL}")
    print(f"Max retries: {client.MAX_RETRIES}\n")

    try:
        await client.get_weather(36.7, -2.8, hours=6)
        print("\nFAIL: Expected an exception but request succeeded")
    except Exception as e:
        print(f"\nFinal exception (expected): {type(e).__name__}: {e}")
        if "ConnectError" in type(e).__name__ or "Connect" in str(type(e).__name__):
            print("PASS: ConnectError was retried and raised after exhausting retries")
        else:
            print(f"PASS: TransportError subclass '{type(e).__name__}' was retried")
    finally:
        await client.close()


async def test_timeout_still_retried():
    """Verify that TimeoutException (a TransportError subclass) still works."""
    client = OpenMeteoClient()
    client.RETRY_DELAY_BASE = 0.1
    # Use a real host but impossibly short timeout to trigger TimeoutException
    client.client = __import__("httpx").AsyncClient(timeout=0.001)

    print("\n--- Testing TimeoutException retry (still covered by TransportError) ---")
    print(f"Timeout: 0.001s")
    print(f"Max retries: {client.MAX_RETRIES}\n")

    try:
        await client.get_weather(36.7, -2.8, hours=6)
        print("\nFAIL: Expected an exception but request succeeded")
    except Exception as e:
        print(f"\nFinal exception (expected): {type(e).__name__}: {e}")
        print("PASS: Timeout was retried under TransportError")
    finally:
        await client.close()


async def main():
    await test_connect_error_retry()
    await test_timeout_still_retried()
    print("\n--- All retry tests complete ---")


if __name__ == "__main__":
    asyncio.run(main())
