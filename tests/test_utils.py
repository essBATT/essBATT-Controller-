"""Tests for utils module, especially RepeatedTimer (Step 1).

Uses pytest-mock to test timer behavior without real threading delays.
"""

import time
from unittest.mock import patch, call

from utils import RepeatedTimer


def test_repeated_timer_initialization(mocker):
    """Test that RepeatedTimer sets up correctly and starts the timer."""
    mock_timer = mocker.patch("threading.Timer")
    mock_function = mocker.Mock()

    rt = RepeatedTimer(5.0, mock_function, "arg1", kwarg="value")

    assert rt.interval == 5.0
    assert rt.function == mock_function
    assert rt.args == ("arg1",)
    assert rt.kwargs == {"kwarg": "value"}
    assert rt.is_running is True
    mock_timer.assert_called_once()  # Timer was started


def test_repeated_timer_stop(mocker):
    """Test stop method cancels the timer."""
    mock_timer_instance = mocker.Mock()
    mocker.patch("threading.Timer", return_value=mock_timer_instance)

    rt = RepeatedTimer(1.0, lambda: None)
    rt.stop()

    mock_timer_instance.cancel.assert_called_once()
    assert rt.is_running is False


def test_repeated_timer_callback(mocker):
    """Test that the timer actually calls the function after interval (mocked time)."""
    mock_function = mocker.Mock()
    mocker.patch("time.time", return_value=100.0)  # Fixed time for determinism

    with patch("threading.Timer") as mock_timer:
        rt = RepeatedTimer(10.0, mock_function)
        # Simulate the _run being called by the timer
        rt._run()  # Direct call for test
        mock_function.assert_called_once_with()


def test_repeated_timer_skips_overlapping_run(mocker):
    """A tick that fires while the previous call is still running is skipped."""
    release = []

    def blocking_fn():
        # Re-enter _run while this invocation still holds the lock
        if not release:
            release.append(True)
            rt._run()

    mocker.patch("threading.Timer")
    mocker.patch("time.time", return_value=100.0)
    rt = RepeatedTimer(10.0, blocking_fn)
    rt._run()

    assert rt.overlap_skips == 1
