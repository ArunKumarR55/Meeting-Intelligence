from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.db.models.domain import UnsubscribedEmail
from app.api.v1.endpoints.workflows import unsubscribe_one_click
from app.services.integrations import email_sender


class FakeSMTP:
    def __init__(self, *_args, **_kwargs):
        self.messages = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def ehlo(self):
        return None

    def starttls(self, **_kwargs):
        return None

    def login(self, *_args):
        return None

    def send_message(self, message, **_kwargs):
        self.messages.append(message)
        return {}


def test_mom_filters_unsubscribed_recipient_and_sends_personalized_message(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    fake_smtp = FakeSMTP()
    monkeypatch.setattr(email_sender, "SMTP_SERVER", "smtp.example.test")
    monkeypatch.setattr(email_sender, "SMTP_PORT", 587)
    monkeypatch.setattr(email_sender, "SMTP_USER", "minutes@example.test")
    monkeypatch.setattr(email_sender, "SMTP_PASS", SecretStr("test-password"))
    monkeypatch.setattr(email_sender, "SMTP_FROM_EMAIL", None)
    monkeypatch.setattr(email_sender, "SMTP_USE_TLS", False)
    monkeypatch.setattr(email_sender.smtplib, "SMTP", lambda *_args, **_kwargs: fake_smtp)

    with Session(engine) as db:
        db.add(UnsubscribedEmail(email="opted-out@example.test"))
        db.commit()
        report = email_sender.send_mom_emails(
            "Planning review",
            "We agreed on next steps.",
            [{"description": "Publish the plan", "owner": "Ari"}],
            ["OPTED-OUT@example.test", "recipient@example.test"],
            db,
        )
        suppressed = db.scalars(select(UnsubscribedEmail.email_normalized)).all()

    assert report == {
        "sent": ["recipient@example.test"],
        "unsubscribed": ["OPTED-OUT@example.test"],
        "failed": [],
    }
    assert suppressed == ["opted-out@example.test"]
    assert len(fake_smtp.messages) == 1
    html_body = fake_smtp.messages[0].get_payload()[1].get_payload(decode=True).decode("utf-8")
    assert "Publish the plan" in html_body
    engine.dispose()


def test_https_unsubscribe_headers_include_one_click_post(monkeypatch) -> None:
    monkeypatch.setattr(email_sender.settings, "public_base_url", "https://meetings.example.test")
    message = email_sender._build_message(
        "Planning",
        "Summary",
        [],
        "person@example.test",
        "minutes@example.test",
    )

    assert message["List-Unsubscribe"].startswith("<https://meetings.example.test/")
    assert message["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert "email=person%40example.test" in message["List-Unsubscribe"]


def test_one_click_unsubscribe_persists_case_insensitive_opt_out() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        response = unsubscribe_one_click(
            email="Person@Example.test",
            list_unsubscribe="One-Click",
            db=db,
        )
        stored = db.scalar(select(UnsubscribedEmail))

    assert response.status_code == 204
    assert stored is not None
    assert stored.email_normalized == "person@example.test"
    engine.dispose()
