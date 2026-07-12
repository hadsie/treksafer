"""Tests for operator notifications (app/notify.py)."""

import smtplib
from unittest.mock import MagicMock, patch

import pytest
import responses

from app.config import get_config
from app.notify import notify, notify_email, notify_ntfy


@pytest.fixture
def channels(monkeypatch):
    """Configure both channels for the duration of a test."""
    monitoring = get_config().monitoring
    monkeypatch.setattr(monitoring, 'ntfy_topic', 'test-topic')
    monkeypatch.setattr(monitoring, 'smtp_host', 'smtp.test')
    monkeypatch.setattr(monitoring, 'smtp_user', 'alerts@test')
    monkeypatch.setattr(monitoring, 'alert_email', 'operator@test')
    return monitoring


class TestNotifyNtfy:
    @responses.activate
    def test_posts_to_topic(self, channels):
        responses.add(responses.POST, 'https://ntfy.sh/test-topic', status=200)

        assert notify_ntfy('Title', 'Body') is True
        assert responses.calls[0].request.headers['Title'] == 'Title'
        assert responses.calls[0].request.body == b'Body'

    @responses.activate
    def test_http_error_returns_false(self, channels):
        responses.add(responses.POST, 'https://ntfy.sh/test-topic', status=500)

        assert notify_ntfy('Title', 'Body') is False

    def test_unconfigured_skips(self):
        assert notify_ntfy('Title', 'Body') is False


class TestNotifyEmail:
    def test_sends_message(self, channels):
        with patch('app.notify.smtplib.SMTP') as mock_smtp:
            smtp = mock_smtp.return_value.__enter__.return_value

            assert notify_email('Subject', 'Body') is True

        message = smtp.send_message.call_args[0][0]
        assert message['Subject'] == 'Subject'
        assert message['To'] == 'operator@test'
        smtp.login.assert_called_once()

    def test_smtp_failure_returns_false(self, channels):
        with patch('app.notify.smtplib.SMTP',
                   side_effect=smtplib.SMTPConnectError(421, 'nope')):
            assert notify_email('Subject', 'Body') is False

    def test_unconfigured_skips(self):
        assert notify_email('Subject', 'Body') is False


class TestNotify:
    @responses.activate
    def test_one_channel_failing_does_not_block_the_other(self, channels):
        responses.add(responses.POST, 'https://ntfy.sh/test-topic', status=500)
        with patch('app.notify.smtplib.SMTP') as mock_smtp:
            smtp = mock_smtp.return_value.__enter__.return_value

            assert notify('Title', 'Body') is True

        smtp.send_message.assert_called_once()

    @responses.activate
    def test_all_channels_failing_returns_false(self, channels):
        responses.add(responses.POST, 'https://ntfy.sh/test-topic', status=500)
        with patch('app.notify.smtplib.SMTP',
                   side_effect=smtplib.SMTPConnectError(421, 'nope')):
            assert notify('Title', 'Body') is False
