"""Tests for Scheduler & Reminder System."""

from datetime import datetime, timedelta, timezone

import pytest

from src.scheduling.scheduler import Scheduler, detect_reminder_intent, parse_reminder_time


class TestParseReminderTime:
    """Test natural language time parsing (Vietnamese + English)."""

    def test_sau_phut(self):
        result = parse_reminder_time("sau 30 phút uống nước")
        assert result is not None
        desc, run_at = result
        assert "uống nước" in desc
        assert run_at > datetime.now(timezone.utc)

    def test_sau_tieng(self):
        result = parse_reminder_time("sau 2 tiếng họp team")
        assert result is not None
        desc, run_at = result
        assert "họp team" in desc

    def test_sau_gio(self):
        result = parse_reminder_time("sau 1 giờ ăn trưa")
        assert result is not None
        desc, run_at = result
        assert "ăn trưa" in desc

    def test_sau_ngay(self):
        result = parse_reminder_time("sau 3 ngày nộp báo cáo")
        assert result is not None
        desc, run_at = result
        assert "nộp báo cáo" in desc

    def test_sau_tuan(self):
        result = parse_reminder_time("sau 1 tuần check project")
        assert result is not None
        _, run_at = result
        # Should be ~7 days later
        diff = run_at - datetime.now(timezone.utc)
        assert diff.days >= 6

    def test_in_minutes(self):
        result = parse_reminder_time("in 15 minutes check email")
        assert result is not None
        desc, run_at = result
        assert "check email" in desc

    def test_in_hours(self):
        result = parse_reminder_time("in 2 hours meeting")
        assert result is not None
        desc, _ = result
        assert "meeting" in desc

    def test_luc_time(self):
        result = parse_reminder_time("lúc 15:30 gọi khách")
        assert result is not None
        desc, run_at = result
        assert "gọi khách" in desc
        assert run_at.minute == 30

    def test_at_time(self):
        result = parse_reminder_time("at 14:00 standup")
        assert result is not None
        desc, run_at = result
        assert "standup" in desc

    def test_at_pm(self):
        result = parse_reminder_time("at 3pm meeting")
        assert result is not None
        desc, run_at = result
        assert "meeting" in desc

    def test_default_description_vi(self):
        result = parse_reminder_time("sau 5 phút")
        assert result is not None
        desc, _ = result
        assert desc == "Nhắc nhở"

    def test_default_description_en(self):
        result = parse_reminder_time("in 5 minutes")
        assert result is not None
        desc, _ = result
        assert desc == "Reminder"

    def test_unparseable(self):
        result = parse_reminder_time("hello world")
        assert result is None

    def test_no_time(self):
        result = parse_reminder_time("nhắc tao uống nước")
        assert result is None


class TestScheduler:
    @pytest.fixture
    def scheduler(self):
        s = Scheduler()
        return s

    def test_add_reminder(self, scheduler):
        run_at = datetime.now(timezone.utc) + timedelta(hours=1)
        job_id = scheduler.add_reminder("user1", "test reminder", run_at)
        assert job_id
        assert len(job_id) == 8

    def test_get_pending(self, scheduler):
        run_at = datetime.now(timezone.utc) + timedelta(hours=1)
        scheduler.add_reminder("user1", "pending test", run_at)
        pending = scheduler.get_pending("user1")
        assert len(pending) >= 1
        assert any("pending test" in p["description"] for p in pending)

    def test_cancel_job(self, scheduler):
        run_at = datetime.now(timezone.utc) + timedelta(hours=1)
        job_id = scheduler.add_reminder("user1", "to cancel", run_at)
        result = scheduler.cancel_job(job_id)
        assert result is True

    def test_cancel_nonexistent(self, scheduler):
        result = scheduler.cancel_job("nonexistent")
        assert result is False

    def test_get_stats(self, scheduler):
        stats = scheduler.get_stats()
        assert "pending" in stats
        assert "completed" in stats
        assert "running" in stats

    def test_add_recurring(self, scheduler):
        job_id = scheduler.add_recurring("system", "health check", 3600)
        assert job_id
        assert len(job_id) == 8

    @pytest.mark.asyncio
    async def test_start_stop(self, scheduler):
        await scheduler.start()
        assert scheduler._running
        await scheduler.stop()
        assert not scheduler._running

    def test_set_notification_callback(self, scheduler):
        async def dummy(user_id, msg):
            pass
        scheduler.set_notification_callback(dummy)
        assert scheduler._notification_callback is not None


class TestDetectReminderIntent:
    """Test natural language reminder detection in conversation."""

    def test_nhac_tao_sau(self):
        result = detect_reminder_intent("nhắc tao sau 30 phút uống nước")
        assert result is not None
        desc, run_at = result
        assert run_at > datetime.now(timezone.utc)

    def test_nho_nhac(self):
        result = detect_reminder_intent("nhớ nhắc tao sau 1 tiếng họp team")
        assert result is not None

    def test_dung_quen_nhac(self):
        result = detect_reminder_intent("đừng quên nhắc tao sau 2 tiếng gọi điện")
        assert result is not None

    def test_remind_me(self):
        result = detect_reminder_intent("remind me in 30 minutes to check email")
        assert result is not None
        desc, _ = result
        assert "check email" in desc

    def test_dont_forget(self):
        result = detect_reminder_intent("don't forget to remind me in 1 hour")
        assert result is not None

    def test_set_reminder(self):
        result = detect_reminder_intent("set a reminder in 2 hours for meeting")
        assert result is not None

    def test_no_trigger(self):
        """Normal messages should not be detected as reminders."""
        result = detect_reminder_intent("hôm nay trời đẹp quá")
        assert result is None

    def test_trigger_but_no_time(self):
        """Has trigger word but no parseable time."""
        result = detect_reminder_intent("nhắc tao uống nước")
        assert result is None

    def test_hen_tao(self):
        result = detect_reminder_intent("hẹn tao sau 15 phút")
        assert result is not None
