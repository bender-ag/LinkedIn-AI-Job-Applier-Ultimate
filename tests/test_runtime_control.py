"""Unit tests for the graceful-shutdown state machine in src.utils.runtime_control."""

import asyncio
import signal

import pytest

from src.utils import runtime_control as rc
from src.utils.runtime_control import RuntimeController, ShutdownState


@pytest.fixture(autouse=True)
def _reset_global_controller():
    """Snapshot and restore global process state touched by module-level tests.

    ``runtime_controller`` and the SIGINT handler are process-wide singletons;
    tests that exercise the signal handler or ``sleep_with_shutdown`` mutate them,
    so restore a clean baseline before and after each test.
    """
    controller = rc.runtime_controller
    original_sigint = signal.getsignal(signal.SIGINT)

    def _baseline():
        controller.shutdown_requested.clear()
        controller._browser_lost.clear()
        controller.cleanup_complete.set()
        controller.set_shutdown_state(ShutdownState.RUNNING)

    _baseline()
    yield
    _baseline()
    signal.signal(signal.SIGINT, original_sigint)


# --------------------------------------------------------------------------- state machine
def test_initial_state_is_running():
    controller = RuntimeController()
    assert controller.shutdown_state == ShutdownState.RUNNING
    assert not controller.is_shutdown_requested()


def test_first_request_transitions_to_draining():
    controller = RuntimeController()
    controller.request_shutdown("test")
    assert controller.shutdown_state == ShutdownState.DRAINING
    assert controller.is_shutdown_requested()
    assert controller.should_drain()


def test_second_request_keeps_draining():
    controller = RuntimeController()
    controller.request_shutdown("first")
    controller.request_shutdown("second")
    assert controller.shutdown_state == ShutdownState.DRAINING
    assert controller.is_shutdown_requested()


def test_request_during_cleanup_does_not_regress_to_draining():
    controller = RuntimeController()
    controller.set_shutdown_state(ShutdownState.CLEANUP)
    controller.request_shutdown("test")
    assert controller.shutdown_state == ShutdownState.CLEANUP
    assert controller.is_shutdown_requested()


def test_reset_for_new_run_clears_previous_run_state():
    controller = RuntimeController()
    controller.request_shutdown("test")
    controller.mark_browser_lost()
    controller.set_shutdown_state(ShutdownState.DONE)

    controller.reset_for_new_run()

    assert controller.shutdown_state == ShutdownState.RUNNING
    assert not controller.is_browser_lost()
    # cleanup_complete is cleared so the Windows console handler actually waits.
    assert not controller.cleanup_complete.is_set()
    controller.finish_run()
    assert controller.cleanup_complete.is_set()


# --------------------------------------------------------------------------- job-loop checkpoint
def test_next_job_stop_reason_none_while_running():
    controller = RuntimeController()
    assert controller.next_job_stop_reason() is None


def test_next_job_stop_reason_shutdown_when_draining():
    controller = RuntimeController()
    controller.request_shutdown("test")
    assert controller.next_job_stop_reason() == "Shutdown"


def test_next_job_stop_reason_error_when_browser_lost():
    controller = RuntimeController()
    controller.mark_browser_lost()
    assert controller.next_job_stop_reason() == "Error"


def test_browser_loss_takes_priority_over_shutdown():
    controller = RuntimeController()
    controller.request_shutdown("test")
    controller.mark_browser_lost()
    assert controller.next_job_stop_reason() == "Error"


# --------------------------------------------------------------------------- sleep_with_shutdown
def test_sleep_with_shutdown_completes_full_interval():
    # No shutdown requested: the full (short) interval elapses and returns True.
    assert asyncio.run(rc.sleep_with_shutdown(0.05)) is True


def test_sleep_with_shutdown_returns_false_when_already_requested():
    rc.runtime_controller.request_shutdown("test")
    # A long interval must not actually be waited out once shutdown is set.
    assert asyncio.run(rc.sleep_with_shutdown(30)) is False


def test_sleep_with_shutdown_wakes_early_on_request():
    async def scenario():
        async def request_soon():
            await asyncio.sleep(0.05)
            rc.runtime_controller.request_shutdown("test")

        waiter = asyncio.create_task(rc.sleep_with_shutdown(30))
        asyncio.create_task(request_soon())
        return await asyncio.wait_for(waiter, timeout=5)

    assert asyncio.run(scenario()) is False


# --------------------------------------------------------------------------- signal escalation
def test_first_signal_drains_second_signal_escalates():
    # First interrupt starts a graceful drain, does not raise.
    rc._handle_shutdown_signal(signal.SIGINT, None)
    assert rc.runtime_controller.should_drain()
    assert rc.runtime_controller.is_shutdown_requested()

    # Second interrupt escalates to an immediate abort.
    with pytest.raises(KeyboardInterrupt):
        rc._handle_shutdown_signal(signal.SIGINT, None)
