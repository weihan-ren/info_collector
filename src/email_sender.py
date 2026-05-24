import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from src.analyzer import Briefing
from src.config_loader import EmailConfig


class EmailSender:
    def __init__(self, config: EmailConfig):
        self.config = config

    def send(self, briefing: Briefing, dry_run: bool = False) -> bool:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = briefing.title
        msg["From"] = self.config.sender
        msg["To"] = ", ".join(self.config.recipients)

        msg.attach(MIMEText(briefing.to_plain_text(), "plain", "utf-8"))
        msg.attach(MIMEText(briefing.to_html(), "html", "utf-8"))

        if dry_run:
            print(f"[DRY RUN] Would send email to: {self.config.recipients}")
            print(f"[DRY RUN] Subject: {briefing.title}")
            return True

        try:
            if self.config.use_tls:
                server = smtplib.SMTP(self.config.smtp_host, self.config.smtp_port, timeout=30)
                server.starttls()
            else:
                server = smtplib.SMTP_SSL(self.config.smtp_host, self.config.smtp_port, timeout=30)

            server.login(self.config.sender, self.config.password)
            server.sendmail(
                self.config.sender, self.config.recipients, msg.as_string()
            )
            server.quit()
            return True
        except Exception as e:
            print(f"Email send failed: {e}")
            return False
