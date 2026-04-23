"""
Live console dashboard for the ingestion pipeline.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

try:
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.progress_bar import ProgressBar

    HAS_RICH = True
except ImportError:  # pragma: no cover
    HAS_RICH = False


# -- Helpers ------------------------------------------------------------------


def _fmt_dur(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m {s:02d}s"


@dataclass
class _Obj:
    expected: int = 0
    fetched: int = 0
    ingested: int = 0
    failed: int = 0
    chunk: int = 0
    status: str = "pending"
    t0: float = 0.0


# -- Dashboard ----------------------------------------------------------------


class IngestionDashboard:
    """Thread-safe live dashboard powered by *rich*.

    Implements ``__rich__`` so ``rich.live.Live`` auto-rebuilds every tick.
    """

    def __init__(self, connector_id: str, sync_mode: str, acl_engine: str, log_file: str, failed_file: str = "") -> None:
        self._cid = connector_id
        self._mode = sync_mode
        self._acl = acl_engine
        self._log = str(log_file)
        self._failed_log = str(failed_file)
        self._objs: dict[str, _Obj] = {}
        self._order: list[str] = []
        self._activity = "Initializing..."
        self._activity_t0 = time.monotonic()
        self._errors: list[str] = []
        self._last_error = ""
        self._total_counts: dict[str, int] = {}
        self._stop_requested = False
        self._t0 = time.monotonic()
        self._rate_window: deque[tuple[float, int]] = deque()
        self._last_ingest_time = 0.0
        self._frozen_rate = 0.0
        self._force_exit = False
        self._last_acl_duration = 0.0  # seconds the last ACL resolution took
        self._lock = threading.Lock()
        self._console = Console()
        self._live: Live | None = None

    def __rich__(self):
        with self._lock:
            return self._build()

    # -- Lifecycle ------------------------------------------------------------

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested

    def start(self) -> None:
        self._live = Live(self, console=self._console, refresh_per_second=4, transient=True, vertical_overflow="visible")
        self._live.start()
        self._start_key_monitor()

    def stop(self) -> None:
        if self._live:
            self._live.stop()
            self._live = None

    # -- Update API -----------------------------------------------------------

    def _obj(self, name: str) -> _Obj:
        if name not in self._objs:
            self._objs[name] = _Obj()
            self._order.append(name)
        return self._objs[name]

    def set_object_types(self, types: list[str]) -> None:
        with self._lock:
            for t in types:
                self._obj(t)

    def set_total_counts(self, counts: dict[str, int]) -> None:
        with self._lock:
            self._total_counts = dict(counts)
            for name, count in counts.items():
                self._obj(name).expected = count

    def chunk_fetched(self, obj_type: str, chunk_idx: int, count: int) -> None:
        with self._lock:
            o = self._obj(obj_type)
            if o.t0 == 0.0:
                o.t0 = time.monotonic()
            o.fetched += count
            o.chunk = chunk_idx
            o.status = "fetching"
            self._activity = f"[{obj_type}] chunk #{chunk_idx} -- fetched {count} records"
            self._activity_t0 = time.monotonic()

    def chunk_skipped(self, obj_type: str, count: int) -> None:
        with self._lock:
            o = self._obj(obj_type)
            o.fetched += count
            o.ingested += count

    def acl_started(self, obj_type: str, chunk_idx: int, count: int) -> None:
        with self._lock:
            o = self._objs.get(obj_type)
            if o:
                o.status = "acl"
                o.chunk = chunk_idx
            self._activity = f"[{obj_type}] chunk #{chunk_idx} -- Resolving ACLs ({count} records)"
            self._activity_t0 = time.monotonic()

    def set_activity(self, msg: str) -> None:
        with self._lock:
            # If we're leaving an ACL phase, record how long it took
            if "Resolving ACLs" in self._activity and "Resolving ACLs" not in msg:
                self._last_acl_duration = time.monotonic() - self._activity_t0
            self._activity = msg
            self._activity_t0 = time.monotonic()

    def chunk_ingested(self, obj_type: str, success: int, failed: int) -> None:
        with self._lock:
            o = self._objs.get(obj_type)
            if o:
                o.ingested += success
                o.failed += failed
                o.status = "ingesting"
            if success > 0:
                self._last_ingest_time = time.monotonic()

    def object_done(self, obj_type: str) -> None:
        with self._lock:
            o = self._objs.get(obj_type)
            if o:
                o.status = "done"

    def add_error(self, msg: str) -> None:
        with self._lock:
            self._errors.append(msg)
            self._last_error = msg
            if len(self._errors) > 8:
                self._errors = self._errors[-8:]

    def _start_key_monitor(self) -> None:
        t = threading.Thread(target=self._key_loop, daemon=True, name="ctrl-x-monitor")
        t.start()

    def _key_loop(self) -> None:
        try:
            import sys
            if sys.platform == "win32":
                import msvcrt
                while True:
                    if msvcrt.kbhit():
                        key = msvcrt.getch()
                        if key == b"\x18":  # Ctrl+X
                            if self._stop_requested:
                                self._do_force_exit()
                                return
                            self._do_stop()
                    time.sleep(0.1)
            else:
                import select, tty, termios
                fd = sys.stdin.fileno()
                old = termios.tcgetattr(fd)
                try:
                    tty.setcbreak(fd)
                    while True:
                        if select.select([sys.stdin], [], [], 0.1)[0]:
                            if sys.stdin.read(1) == "\x18":
                                if self._stop_requested:
                                    self._do_force_exit()
                                    return
                                self._do_stop()
                finally:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:
            pass  # best-effort

    def _do_stop(self) -> None:
        with self._lock:
            self._stop_requested = True
            self._activity = "Ctrl+X pressed -- stopping after current chunk..."
            self._activity_t0 = time.monotonic()

    def _do_force_exit(self) -> None:
        import sys
        with self._lock:
            self._force_exit = True
            self._activity = "Ctrl+X x2 -- ingestion stopped abruptly"
            self._activity_t0 = time.monotonic()
        # Stop dashboard cleanly then exit
        self.stop()
        print("\n  Ingestion stopped abruptly by user (Ctrl+X x2).")
        print("  Progress was checkpointed -- next run will resume.\n")
        sys.exit(1)

    def finish(self) -> None:
        with self._lock:
            for o in self._objs.values():
                if o.fetched > 0:
                    o.status = "done"
            self._activity = "Complete"
            self._activity_t0 = time.monotonic()

    # -- Rendering ------------------------------------------------------------

    def _rolling_rate(self, now: float, tot_i: int) -> float:
        """Items/sec based on a 2-minute sliding window for stable ETA."""
        self._rate_window.append((now, tot_i))
        cutoff = now - 120
        while self._rate_window and self._rate_window[0][0] < cutoff:
            self._rate_window.popleft()
        if len(self._rate_window) >= 2:
            t0, c0 = self._rate_window[0]
            dt = now - t0
            if dt > 1:
                return (tot_i - c0) / dt
        # Fallback to overall rate for the first 2 seconds
        elapsed = now - self._t0
        return tot_i / elapsed if elapsed > 0.5 else 0

    def _build(self) -> Group:
        now = time.monotonic()
        tot_i = sum(o.ingested for o in self._objs.values())
        tot_fail = sum(o.failed for o in self._objs.values())
        elapsed = now - self._t0
        grand_total = sum(self._total_counts.values()) if self._total_counts else sum(o.fetched for o in self._objs.values())

        # Rate freezes during ACL pauses (no items flowing) so ETA stays stable
        idle_secs = (now - self._last_ingest_time) if self._last_ingest_time else 0
        if idle_secs < 10:
            # Items are actively flowing — compute live rate and save it
            self._frozen_rate = self._rolling_rate(now, tot_i)
        # else: keep self._frozen_rate from last active push window
        overall_rate = self._frozen_rate

        # -- Header -----------------------------------------------------------
        tot_fail_any = sum(o.failed for o in self._objs.values())
        header_lines = (
            f"[bold]Connector:[/] {self._cid}  [dim]|[/]  "
            f"[bold]Mode:[/] {self._mode}  [dim]|[/]  "
            f"[bold]ACL:[/] {self._acl}\n"
            f"[dim]Log:     {self._log}[/]"
        )
        if self._failed_log:
            style = "red" if tot_fail_any else "dim"
            header_lines += f"\n[{style}]Errors:  {self._failed_log}[/{style}]"
        header = Panel(
            header_lines,
            title="[bold blue] Salesforce >> Graph Ingestion [/]",
            border_style="blue",
            padding=(0, 1),
        )

        # -- Object table -----------------------------------------------------
        tbl = Table(
            show_header=True, header_style="bold", border_style="dim",
            pad_edge=False, padding=(0, 1), expand=True,
        )
        tbl.add_column("Object", style="cyan", min_width=18, no_wrap=True)
        tbl.add_column("Ingested / Total", justify="right", min_width=18, no_wrap=True)
        tbl.add_column("Failed", justify="right", min_width=8, no_wrap=True)
        tbl.add_column("ETA", justify="right", min_width=10, no_wrap=True)
        tbl.add_column("Status", min_width=14, no_wrap=True)

        for name in self._order:
            o = self._objs[name]
            obj_total = o.expected or o.fetched
            is_pending = o.fetched == 0

            # -- ingested / total --
            if is_pending and o.expected > 0:
                count_cell = f"[dim]- / {o.expected:,}[/]"
            elif is_pending:
                count_cell = "[dim]-[/]"
            else:
                count_cell = f"{o.ingested:,} / {obj_total:,}"

            # -- per-object ETA --
            # Pending objects: show a static estimate (frozen rate), dimmed
            # Active objects: use their own measured rate
            # Done objects: no ETA
            if o.status == "done":
                obj_eta = "[dim]-[/]"
            elif o.status == "pending" and overall_rate > 0 and obj_total > 0:
                obj_eta = f"[dim]~{_fmt_dur(obj_total / overall_rate)}[/]"
            elif o.t0 > 0 and o.ingested > 0 and obj_total > o.ingested:
                obj_rate = o.ingested / (now - o.t0)
                obj_eta = f"~{_fmt_dur((obj_total - o.ingested) / obj_rate)}"
            elif overall_rate > 0 and obj_total > o.ingested:
                obj_eta = f"~{_fmt_dur((obj_total - o.ingested) / overall_rate)}"
            else:
                obj_eta = "[dim]-[/]"

            # -- status --
            if o.status == "ingesting":
                status = f"[green]> Chunk #{o.chunk}[/]"
            elif o.status == "acl":
                status = f"[yellow]~ ACL #{o.chunk}[/]"
            elif o.status == "fetching":
                status = f"[yellow]v Fetching[/]"
            elif o.status == "done":
                skipped = max(0, o.fetched - o.ingested - o.failed)
                if skipped:
                    status = f"[bold green]+ Done[/] [dim]({skipped} skip)[/]"
                else:
                    status = "[bold green]+ Done[/]"
            else:
                status = "[dim]- Pending[/]"

            fail_style = "red" if o.failed else "dim"
            tbl.add_row(
                name,
                count_cell,
                Text(str(o.failed) if not is_pending else "-", style=fail_style),
                obj_eta,
                status,
            )

        # -- Totals --
        tbl.add_section()
        tbl.add_row(
            "[bold]Total[/]",
            f"[bold]{tot_i:,} / {grand_total:,}[/]" if grand_total else f"[bold]{tot_i:,}[/]",
            Text(str(tot_fail), style="bold red" if tot_fail else "bold dim"),
            "",
            "",
        )

        # -- Overall progress bar ---------------------------------------------
        if grand_total > 0:
            pct = min(tot_i / grand_total, 1.0)
            bar = ProgressBar(total=grand_total, completed=min(tot_i, grand_total), width=50)
            bar_grid = Table.grid(padding=(0, 1))
            bar_grid.add_row(Text(" "), bar, Text(f" {tot_i:,} / {grand_total:,}  ({pct:.1%})"))
        else:
            bar_grid = Text("  Waiting for records...", style="dim")

        # -- Timing -----------------------------------------------------------
        rate_min = overall_rate * 60
        parts = [f"  Elapsed: [bold]{_fmt_dur(elapsed)}[/]"]
        if rate_min >= 1:
            parts.append(f"Rate: [bold]{rate_min:,.0f}/min[/]")
        elif rate_min > 0:
            parts.append(f"Rate: [bold]{rate_min:.1f}/min[/]")
        else:
            parts.append("Rate: [dim]--[/]")
        if overall_rate > 0 and grand_total > tot_i:
            parts.append(f"ETA: [bold]~{_fmt_dur((grand_total - tot_i) / overall_rate)}[/]")
        timing = Text.from_markup("  [dim]|[/]  ".join(parts))

        # -- Activity + error + footer (compact) --------------------------------
        act_dur = now - self._activity_t0
        if self._activity == "Complete":
            act_text = f"  [bold green]+ {self._activity}[/]"
        elif self._stop_requested:
            act_text = f"  [bold yellow]! {self._activity}[/]"
        elif "Resolving ACLs" in self._activity and self._last_acl_duration > 0:
            remaining = max(0, self._last_acl_duration - act_dur)
            acl_eta = f"  ETA ~{_fmt_dur(remaining)}" if remaining > 0 else "  (longer than usual)"
            act_text = f"  [bold]> {self._activity}[/]  [dim]({_fmt_dur(act_dur)}){acl_eta}[/]"
        else:
            act_text = f"  [bold]> {self._activity}[/]  [dim]({_fmt_dur(act_dur)})[/]"

        err_text = ""
        if self._last_error:
            # Truncate long errors to keep it on one line
            err = self._last_error if len(self._last_error) <= 90 else self._last_error[:87] + "..."
            err_text = f"\n  [red]Last error: {err}[/]"

        if self._stop_requested:
            hint = "  [bold yellow]Press Ctrl+X again to exit immediately[/]"
        else:
            hint = "  [dim]Ctrl+X = stop gracefully[/]"

        bottom = Text.from_markup(f"{act_text}{err_text}\n{hint}")

        # -- Assemble ---------------------------------------------------------
        elements: list = [header, Text(""), tbl, Text(""), bar_grid, Text(""), timing, bottom]
        return Group(*elements)
