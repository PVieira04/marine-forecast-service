"""Email sender using Resend API."""

import asyncio
import base64
import html
import logging
import os
from typing import Optional
import resend

logger = logging.getLogger(__name__)


class EmailSender:
    """Send emails via Resend API."""

    def __init__(self):
        resend.api_key = os.environ.get("RESEND_API_KEY", "")
        self.from_email = os.environ.get("RESEND_FROM_EMAIL", "onboarding@resend.dev")

    async def send(
        self,
        to: str,
        subject: str,
        html_content: str,
        chart_na_b64: Optional[str] = None,
        chart_eu_b64: Optional[str] = None,
        bcc: Optional[str] = None,
    ) -> Optional[str]:
        """
        Send an HTML email with optional embedded chart images.

        Args:
            to: Recipient email address
            subject: Email subject
            html_content: HTML body content
            chart_na_b64: Base64-encoded North Atlantic chart for CID embedding
            chart_eu_b64: Base64-encoded Western Europe chart for CID embedding
            bcc: Optional BCC recipient

        Returns:
            Resend email id if sent successfully, None otherwise
        """
        try:
            attachments = []

            # Add NA chart as CID attachment if provided
            if chart_na_b64:
                chart_bytes = base64.b64decode(chart_na_b64)
                attachments.append({
                    "filename": "chart_na.jpg",
                    "content": list(chart_bytes),
                    "content_id": "chart_na",
                })
                logger.debug("NA chart attached as CID: %d bytes", len(chart_bytes))

            # Add EU chart as CID attachment if provided
            if chart_eu_b64:
                chart_bytes = base64.b64decode(chart_eu_b64)
                attachments.append({
                    "filename": "chart_eu.jpg",
                    "content": list(chart_bytes),
                    "content_id": "chart_eu",
                })
                logger.debug("EU chart attached as CID: %d bytes", len(chart_bytes))

            params = {
                "from": f"Marine Weather <{self.from_email}>",
                "to": [to],
                "subject": subject,
                "html": html_content,
            }

            if bcc:
                params["bcc"] = [bcc]

            if attachments:
                params["attachments"] = attachments

            # Send via Resend (sync API, run in executor for async)
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: resend.Emails.send(params)
            )

            email_id = result.get('id', 'unknown')
            logger.info("Email sent to %s: %s (id: %s)", to, subject, email_id)
            return email_id

        except Exception as e:
            logger.error("Failed to send email: %s", e)
            return None

    async def send_with_retry(
        self,
        to: str,
        subject: str,
        html_content: str,
        chart_na_b64: Optional[str] = None,
        chart_eu_b64: Optional[str] = None,
        bcc: Optional[str] = None,
        max_retries: int = 3,
        retry_delay: int = 10,
    ) -> Optional[str]:
        """
        Send email with retry logic.

        Returns:
            Resend email id if eventually successful, None if all retries failed
        """
        for attempt in range(max_retries):
            email_id = await self.send(
                to=to,
                subject=subject,
                html_content=html_content,
                chart_na_b64=chart_na_b64,
                chart_eu_b64=chart_eu_b64,
                bcc=bcc,
            )
            if email_id:
                return email_id

            if attempt < max_retries - 1:
                logger.warning("Retry %d/%d in %ds...", attempt + 1, max_retries, retry_delay)
                await asyncio.sleep(retry_delay)

        return None

    async def send_failure_notification(
        self,
        to: str,
        error_message: str,
    ) -> Optional[str]:
        """Send the technical failure details to the maintainer."""
        subject = "[Marine Weather Agent] Forecast failed"
        html_content = f"""
        <html>
        <body>
        <h2>Forecast Generation Failed</h2>
        <p><strong>Error:</strong> {html.escape(error_message)}</p>
        <p>The recipient was sent a short "forecast delayed" notice. If this was
        the Worker-dispatched run, the late GitHub schedule fallback will retry
        the slot; if it was the fallback, nothing retries until the next slot.</p>
        <p>Runs: the forecast workflow's run history in GitHub Actions</p>
        </body>
        </html>
        """
        return await self.send(to, subject, html_content)

    async def send_delay_notice(self, to: str) -> Optional[str]:
        """Tell the recipient, in plain terms, that this forecast is delayed."""
        subject = "Marine Weather - forecast delayed"
        html_content = """
        <html>
        <body>
        <p>This forecast couldn't be prepared on time. It will be retried
        automatically; if it doesn't arrive, the next scheduled forecast will
        come as normal.</p>
        <p style="color: #666; font-size: 12px;">Marine Weather Agent</p>
        </body>
        </html>
        """
        return await self.send(to, subject, html_content)
