"""Focused regression tests for route failover, diagnostics, and health parsing."""

from __future__ import annotations

import errno
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import ouc_common
import proxy_chain
import zen_proxy


class DataRootTests(unittest.TestCase):
    def test_windows_uses_local_appdata(self) -> None:
        with mock.patch.object(ouc_common.sys, "platform", "win32"), mock.patch.dict(
            os.environ, {"LOCALAPPDATA": r"C:\Users\tester\AppData\Local"}, clear=False
        ):
            self.assertEqual(
                ouc_common._default_data_root(),
                Path(r"C:\Users\tester\AppData\Local"),
            )

    def test_linux_uses_xdg_data_home(self) -> None:
        with mock.patch.object(ouc_common.sys, "platform", "linux"), mock.patch.dict(
            os.environ, {"XDG_DATA_HOME": "/srv/tester/.local/share"}, clear=False
        ):
            self.assertEqual(
                ouc_common._default_data_root(), Path("/srv/tester/.local/share")
            )

    def test_linux_falls_back_to_local_share(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "XDG_DATA_HOME"}
        with mock.patch.object(ouc_common.sys, "platform", "linux"), mock.patch.dict(
            os.environ, env, clear=True
        ), mock.patch.object(
            ouc_common.Path, "home", return_value=Path("/home/tester")
        ):
            self.assertEqual(
                ouc_common._default_data_root(), Path("/home/tester/.local/share")
            )


class RunHiddenTests(unittest.TestCase):
    def test_windows_suppresses_the_console_window(self) -> None:
        completed = mock.Mock()
        with mock.patch.object(
            ouc_common.sys, "platform", "win32"
        ), mock.patch.object(
            ouc_common.subprocess, "run", return_value=completed
        ) as run:
            result = ouc_common.run_hidden(["warp-cli", "status"], timeout=3.0)

        self.assertIs(result, completed)
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["warp-cli", "status"])
        self.assertEqual(kwargs["creationflags"], ouc_common.CREATE_NO_WINDOW)
        self.assertTrue(kwargs["capture_output"])
        self.assertTrue(kwargs["text"])
        self.assertEqual(kwargs["timeout"], 3.0)

    def test_linux_does_not_pass_windows_creation_flags(self) -> None:
        with mock.patch.object(
            ouc_common.sys, "platform", "linux"
        ), mock.patch.object(ouc_common.subprocess, "run") as run:
            ouc_common.run_hidden(["warp-cli", "status"])

        _, kwargs = run.call_args
        self.assertNotIn("creationflags", kwargs)



class EdgeFailoverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self._saved = (
            zen_proxy.LANE_DIR,
            zen_proxy.PROXY_STATUS_FILE,
            zen_proxy.FALLBACK_FLAG,
            zen_proxy.PROXY_LOG_FILE,
        )
        zen_proxy.LANE_DIR = self.dir
        zen_proxy.PROXY_STATUS_FILE = self.dir / "proxy_status.json"
        zen_proxy.FALLBACK_FLAG = self.dir / "fallback.flag"
        zen_proxy.PROXY_LOG_FILE = self.dir / "proxy.log"
        zen_proxy._LAST_STREAM_RESET_NOTE = 0.0

    def tearDown(self) -> None:
        (
            zen_proxy.LANE_DIR,
            zen_proxy.PROXY_STATUS_FILE,
            zen_proxy.FALLBACK_FLAG,
            zen_proxy.PROXY_LOG_FILE,
        ) = self._saved
        self.tmp.cleanup()

    def test_stale_warp_address_is_refreshed_once(self) -> None:
        state = zen_proxy._WarpState(["edge-a"], "edge-a")
        calls: list[tuple[str, str]] = []
        fresh = object()

        def fake_open(edge, port, local_v6, timeout=0.0):
            calls.append((edge, local_v6))
            if local_v6 == "2001:db8::1":
                raise OSError(errno.EADDRNOTAVAIL, "address not available")
            return fresh

        with mock.patch.object(
            zen_proxy, "get_warp_local_v6", side_effect=["2001:db8::1", "2001:db8::2"]
        ), mock.patch.object(zen_proxy, "_open_pinned_v6", side_effect=fake_open):
            sock, edge = zen_proxy._open_pinned_any("opencode.ai", 443, state)

        self.assertIs(sock, fresh)
        self.assertEqual(edge, "edge-a")
        self.assertEqual(
            calls, [("edge-a", "2001:db8::1"), ("edge-a", "2001:db8::2")]
        )

    def test_primary_edge_is_first_and_failed_edge_is_skipped(self) -> None:
        state = zen_proxy._WarpState(["edge-a", "edge-b", "edge-c"], "edge-a")
        self.assertEqual(state.edges_to_try()[0], "edge-a")

        state.mark_edge_failed("edge-a")
        choices = state.edges_to_try()

        self.assertNotIn("edge-a", choices)
        self.assertLessEqual(len(choices), zen_proxy.EDGE_TRY_LIMIT)
        self.assertNotEqual(state.get(wait_if_rotating=False)[1], "edge-a")

    def test_success_clears_fallback_and_switches_primary(self) -> None:
        state = zen_proxy._WarpState(["edge-a", "edge-b"], "edge-a")
        zen_proxy.FALLBACK_FLAG.write_text("1", encoding="utf-8")

        state.mark_edge_ok("edge-b")

        self.assertFalse(zen_proxy.FALLBACK_FLAG.exists())
        self.assertEqual(state.get(wait_if_rotating=False)[1], "edge-b")
        self.assertEqual(
            (self.dir / "last_edge.txt").read_text(encoding="utf-8").strip(),
            "edge-b",
        )

    def test_status_counts_errors_and_fallbacks(self) -> None:
        zen_proxy._record_event("reset", "ConnectionResetError", "opencode.ai", "edge-a")
        zen_proxy._record_event("fallback", "direct route used", "opencode.ai")
        status = zen_proxy.proxy_status()

        self.assertEqual(status["errors_5m"], 1)
        self.assertEqual(status["fallbacks_5m"], 1)
        self.assertIsNotNone(status["fallback_age"])
        self.assertLessEqual(status["fallback_age"], 2)
        log = (self.dir / "proxy.log").read_text(encoding="utf-8")
        self.assertIn("reset:", log)
        self.assertIn("fallback:", log)

    def test_mid_stream_reset_retires_edge_only_after_a_second_cut(self) -> None:
        state = zen_proxy._WarpState(["edge-a", "edge-b"], "edge-a")

        zen_proxy._note_stream_reset(
            "opencode.ai", "ConnectionResetError after response", state, "edge-a"
        )
        self.assertEqual(state.current_edge(), "edge-a")
        self.assertEqual(zen_proxy.proxy_status()["stream_resets_5m"], 1)

        zen_proxy._LAST_STREAM_RESET_NOTE = 0.0
        zen_proxy._note_stream_reset(
            "opencode.ai", "ConnectionResetError after response", state, "edge-a"
        )
        self.assertEqual(state.current_edge(), "edge-b")
        self.assertEqual(zen_proxy.proxy_status()["stream_resets_5m"], 2)
        log = (self.dir / "proxy.log").read_text(encoding="utf-8")
        self.assertIn("stream_reset:", log)
        self.assertIn("moved to edge-b", log)

    def test_isolated_start_skips_the_pinned_edge_pool_fetch(self) -> None:
        with mock.patch.object(
            zen_proxy, "_active_warp_scope", return_value="isolated"
        ), mock.patch.object(
            zen_proxy,
            "fetch_opencode_aaaa_pool",
            side_effect=AssertionError("isolated startup must not resolve edges"),
        ), mock.patch.object(
            zen_proxy, "ensure_dirs", lambda: None
        ):
            server, port, edge = zen_proxy.start_proxy_server(0)

        try:
            self.assertGreater(port, 0)
            self.assertEqual(edge, "")
            self.assertEqual(zen_proxy.proxy_status()["last_event"]["kind"], "start")
        finally:
            server.shutdown()
            server.server_close()


class BodyReadTests(unittest.TestCase):
    def test_unbuffered_short_reads_are_reassembled(self) -> None:
        class ShortReadStream:
            def __init__(self, data: bytes) -> None:
                self.data = data

            def read(self, size: int) -> bytes:
                chunk = self.data[: min(size, 3)]
                self.data = self.data[len(chunk) :]
                return chunk

        payload = b'{"model":"deepseek-v4.1","stream":true}'
        self.assertEqual(
            zen_proxy._read_exact(ShortReadStream(payload), len(payload)), payload
        )

    def test_eof_before_the_declared_length_returns_what_arrived(self) -> None:
        class TruncatedStream:
            def __init__(self) -> None:
                self.calls = 0

            def read(self, size: int) -> bytes:
                self.calls += 1
                return b"abc" if self.calls == 1 else b""

        self.assertEqual(zen_proxy._read_exact(TruncatedStream(), 10), b"abc")


class StreamResetReportingTests(unittest.TestCase):
    class _FakeSocket:
        def __init__(self, recv_events: list[object]) -> None:
            self.recv_events = list(recv_events)
            self.sent: list[bytes] = []
            self.shutdowns: list[int] = []

        def setsockopt(self, *args) -> None:
            pass

        def ioctl(self, *args) -> None:
            pass

        def recv(self, size: int, flags: int = 0) -> bytes:
            event = self.recv_events.pop(0)
            if isinstance(event, Exception):
                raise event
            return event

        def sendall(self, data: bytes) -> None:
            self.sent.append(data)

        def shutdown(self, how: int) -> None:
            self.shutdowns.append(how)

    def test_reset_after_the_client_closed_is_not_a_cut_stream(self) -> None:
        client = self._FakeSocket([b""])
        upstream = self._FakeSocket(
            [b"HTTP/1.1 200 OK\r\n\r\n", ConnectionResetError("cut")]
        )
        with mock.patch.object(
            zen_proxy.select,
            "select",
            side_effect=[
                ([upstream], [], []),
                ([client], [], []),
                ([upstream], [], []),
            ],
        ), mock.patch.object(
            zen_proxy, "_drain_client"
        ), mock.patch.object(
            zen_proxy, "_note_stream_reset"
        ) as note, mock.patch.object(
            zen_proxy, "_record_reset"
        ) as reset:
            zen_proxy._relay(client, upstream, "opencode.ai", None, "edge-a")

        self.assertFalse(note.called)
        self.assertFalse(reset.called)

    def test_reset_while_the_client_waits_is_reported(self) -> None:
        client = self._FakeSocket([])
        upstream = self._FakeSocket(
            [b"HTTP/1.1 200 OK\r\n\r\n", ConnectionResetError("cut")]
        )
        with mock.patch.object(
            zen_proxy.select,
            "select",
            side_effect=[
                ([upstream], [], []),
                ([upstream], [], []),
                ([], [], []),
            ],
        ), mock.patch.object(
            zen_proxy, "_drain_client"
        ), mock.patch.object(
            zen_proxy, "_note_stream_reset"
        ) as note:
            zen_proxy._relay(client, upstream, "opencode.ai", None, "edge-a")

        note.assert_called_once()
        host, detail, state, edge = note.call_args.args
        self.assertEqual(host, "opencode.ai")
        self.assertIn("after response", detail)
        self.assertIsNone(state)
        self.assertEqual(edge, "edge-a")

    def test_client_gone_detects_a_pending_close(self) -> None:
        left, right = socket.socketpair()
        try:
            self.assertFalse(zen_proxy._client_gone(left, set(), seconds=0.02))
            right.close()
            self.assertTrue(zen_proxy._client_gone(left, set(), seconds=0.5))
        finally:
            left.close()





class CustomProxyFailoverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self._saved = (
            proxy_chain.EGRESS_CONFIG_FILE,
            proxy_chain.PROXY_INDEX_FILE,
            proxy_chain.ensure_dirs,
        )
        proxy_chain.EGRESS_CONFIG_FILE = self.dir / "egress_mode.json"
        proxy_chain.PROXY_INDEX_FILE = self.dir / "custom_proxy_index.txt"
        proxy_chain.ensure_dirs = lambda: None
        proxy_chain.EGRESS_CONFIG_FILE.write_text(
            json.dumps(
                {
                    "mode": "custom",
                    "warp_scope": "tun",
                    "custom_proxy": "10.0.0.1:1080,10.0.0.2:1080",
                    "custom_type": "socks5",
                }
            ),
            encoding="utf-8",
        )
        proxy_chain.PROXY_INDEX_FILE.write_text("0", encoding="utf-8")

    def tearDown(self) -> None:
        (
            proxy_chain.EGRESS_CONFIG_FILE,
            proxy_chain.PROXY_INDEX_FILE,
            proxy_chain.ensure_dirs,
        ) = self._saved
        self.tmp.cleanup()

    def test_failover_advances_without_blocking_verification(self) -> None:
        def warp_down(host: str, port: int):
            raise ConnectionError("warp down")

        with mock.patch.object(
            proxy_chain, "open_socks5_tunnel", side_effect=OSError("node down")
        ), mock.patch.object(
            proxy_chain, "open_http_proxy_tunnel", side_effect=OSError("node down")
        ), mock.patch.object(
            proxy_chain,
            "rotate_custom_proxy",
            side_effect=AssertionError("request path must not verify proxy nodes"),
        ), mock.patch.object(
            proxy_chain, "advance_custom_proxy", wraps=proxy_chain.advance_custom_proxy
        ) as advance:
            with self.assertRaises(ConnectionError):
                proxy_chain.open_multi_egress_upstream("opencode.ai", 443, warp_down)

        self.assertGreaterEqual(advance.call_count, 1)
        self.assertEqual(proxy_chain.PROXY_INDEX_FILE.read_text(encoding="utf-8"), "0")



if __name__ == "__main__":
    unittest.main()
