"""Console state owner: the bounded, per-printer command transcript and
the send lane.

Commands go through MonitorCommands' untracked one-shot path — HTTP
acknowledgement means Klipper received the script (printer/gcode/script
returns after Klipper processes it), and Klipper's OUTPUT streams back
through Moonraker's gcode store, which MonitorData polls at 1 s while
the console is on screen (the author's ruling: expanded-only polling
with a backfill on expand). The store pairs responses to the most
recent command by recency — there are no correlation ids — so the pane
is a terminal FEED, not a per-command echo: our typed lines appear as
sent, Klipper's lines arrive as they land, and no line claims an
attribution the store cannot support.

The transcript persists per printer (sensor/command vocabulary differs
between machines), bounded by ConsolePolicy; lines restored from a
previous session render greyed in the pane.
"""
from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import QObject, pyqtSignal

from .ConsolePolicy import MAX_HISTORY, MAX_LINE, MAX_PENDING, MAX_TRANSCRIPT, normalise_line
from collections.abc import Mapping

def _transcript_from_history(lines) -> list:
    """Legacy console_history (typed lines) becomes a command transcript
    with every line marked restored — they all predate this session."""
    return [{"kind": "command", "text": str(line), "error": False, "success": False, "restored": True}
            for line in lines][-MAX_TRANSCRIPT:]


def _trim_transcript(entries: list) -> list:
    return entries[-MAX_TRANSCRIPT:]


# The console-local status when a live "!!" response lands: the pane's
# own status line is where Klipper's verdicts live, and it must not
# keep claiming "sent" while the feed shows an error (panel UX ruling).
KLIPPER_ERROR_STATUS = "Klipper reported an error — see the console output."


class ConsoleController(QObject):
    changed = pyqtSignal()

    def __init__(self, data, commands, config, apply_config, parent=None):
        super().__init__(parent)
        self._data, self._commands = data, commands
        self._config, self._apply_config = config, apply_config
        stored = getattr(self._config(), "console_transcript", None)
        if isinstance(stored, (list, tuple)):
            # EVERY loaded line predates this session: restored stays
            # out of the persisted record and is stamped on load so the
            # pane greys the previous session's lines. An EMPTY list is
            # a genuine Clear — it must not fall through to the legacy
            # history re-migration (the author's Clear-doesn't-stick
            # report).
            transcript = [{
                "kind": str(entry.get("kind") or "command"),
                "text": str(entry.get("text") or ""),
                "error": bool(entry.get("error")),
                "success": bool(entry.get("success")),
                "restored": True,
            } for entry in stored][-(MAX_TRANSCRIPT + self.MAX_PERSIST_COMMANDS):]
            # The load must keep the retained commands: the persist
            # stores MAX_TRANSCRIPT entries plus up to
            # MAX_PERSIST_COMMANDS newest commands at the FRONT, and a
            # plain MAX_TRANSCRIPT trim here cut exactly those (the
            # author's "my requests are missing from the restore").
        else:
            # Legacy migration: the typed-only history becomes the
            # transcript; everything in it predates this session.
            transcript = _transcript_from_history(getattr(self._config(), "console_history", ()))
        self._transcript = [dict(entry) for entry in transcript]
        self._store_time = float(getattr(self._config(), "console_store_time", 0.0) or 0.0)
        # Lines rotated out of the session ring's HEAD once it hits
        # MAX_HISTORY. The pane renders the transcript incrementally and
        # the ring's length stops growing at the cap, so the count alone
        # can no longer signal new content: the pane drops this many
        # lines from its own head (or rebuilds) to stay aligned.
        self._dropped = 0
        self._status = ""
        # Accepted-but-unacknowledged sends, counted per completion of a
        # console-labelled lane cycle. Per-idle-epoch decrements drifted
        # under bursts (an intermediate completion pumps the next queued
        # command and the lane never looks idle), accumulating phantom
        # pending toward the cap.
        self._pending = 0
        commands.completed.connect(self._lane_completed)
        commands.emergencyStopped.connect(self._emergency_stopped)
        # A printer switch must not leave phantom pending sends or a
        # "sent" status bleeding across sessions; the transcript is
        # per-printer and persists, so it stays.
        data.invalidated.connect(self._session_invalidated)

    @property
    def values(self):
        return {
            "consoleHistory": [entry["text"] for entry in self._transcript if entry["kind"] == "command"],
            "consoleLines": [dict(entry) for entry in self._transcript],
            "consoleDropped": self._dropped,
            "consolePending": self._pending,
            "consoleStatus": self._status,
        }

    def _append_entries(self, additions) -> None:
        """Append transcript entries, trimming to the session ring cap.
        Once the ring is full every addition rotates one entry out of
        the HEAD; the count of rotated lines feeds the pane's renderer
        (a full ring never grows, so an incremental sync would otherwise
        stop appending forever). The newest commands are pinned through
        the rotation: a chatty Klipper floods the ring with a response
        per second and the typed requests rotated out entirely, so
        restores came back requestless (the author's report)."""
        dropped_head, self._transcript =             (self._transcript + additions)[:-MAX_HISTORY],             (self._transcript + additions)[-MAX_HISTORY:]
        if len(dropped_head) > 0:
            keep = [entry for entry in dropped_head
                    if entry["kind"] == "command"][-self.MAX_PERSIST_COMMANDS:]
            self._transcript = keep + self._transcript
            self._dropped += len(dropped_head) - len(keep)

    def send(self, text) -> bool:
        """Accept a console line; True only when it actually entered the
        lane, so the UI can keep the draft on a refusal."""
        line = normalise_line(text)
        if not line:
            self._status = ("Command too long — ignored." if len(str(text or "").strip()) > MAX_LINE
                            else "Empty command ignored.")
            self.changed.emit()
            return False
        if self._pending >= MAX_PENDING:
            self._status = "Too many commands waiting — try again in a moment."
            self.changed.emit()
            return False
        def finished(payload, error):
            # The send's OWN result is the execution verdict: the
            # endpoint returns "ok" when the command completed and an
            # error when it failed (Moonraker's docs), and this callback
            # belongs to THIS request — the pairing the store feed
            # cannot offer (its entries carry no correlation id). The
            # verdict colours the typed line: green for "ok", red for a
            # failure; the match is by recency among this session's
            # unverdict-command entries with the same text.
            for entry in reversed(self._transcript):
                if (entry["kind"] == "command" and entry["text"] == line
                        and not entry["success"] and not entry["error"]
                        and not entry["restored"]):
                    entry["error"] = bool(error)
                    # The layering pin keeps ConsoleController on
                    # ConsolePolicy only, so the MonitorFormatting
                    # result() helper is inlined here.
                    verdict = payload.get("result", payload) if isinstance(payload, Mapping) else {}
                    entry["success"] = not error and verdict == "ok"
                    self._persist()
                    self.changed.emit()
                    break
            if self._pending:
                self._pending -= 1
                self.changed.emit()
        # The console posts its own request instead of riding the shared
        # one-shot lane: the lane's card ticker is off-limits for console
        # traffic (the panel UX ruling), and the lane's completion signal
        # carries no verdict to colour the line with.
        started = self._data.request("console", "POST", "printer/gcode/script", finished,
                                     body={"script": line}, category="command", timeout_ms=30000)
        if not started:
            self._status = "Command queue full or Moonraker unavailable — try again."
            self.changed.emit()
            return False
        entry = {"kind": "command", "text": line, "error": False, "success": False, "restored": False}
        self._append_entries([entry])
        self._pending += 1
        # No "sent to Klipper" caption (the author's ruling): the typed
        # line's own verdict colouring carries the feedback. Refusals
        # and live "!!" errors still set the status.
        self._status = ""
        self._persist()
        self.changed.emit()
        return True

    def append_responses(self, entries) -> None:
        """Klipper's gcode-store output, newest last. Entries are already
        formatted ({text, error}) and deduplicated by MonitorData; the
        transcript keeps the last MAX_TRANSCRIPT lines of the combined
        feed."""
        if not entries:
            return
        fresh = [{"kind": "response", "text": str(entry.get("text") or ""),
                  "error": bool(entry.get("error")),
                  "success": bool(entry.get("success")),
                  "restored": False}
                 for entry in entries
                 if str(entry.get("text") or "") and float(entry.get("time") or 0.0) > self._store_time]
        if not fresh:
            return
        # The store stamp advances with the newest entry so re-polls and
        # the expand-backfill never repeat a line (the author's
        # "stale responses without requests" report was the backfill
        # re-adding the server's whole buffer).
        self._store_time = max(float(entry.get("time") or 0.0) for entry in entries)
        self._append_entries(fresh)
        # A live "!!" anywhere in the batch flips the console-local
        # status to the error notice; it holds until the next send
        # replaces it. Restored lines never flip it (load is not a
        # verdict).
        if any(entry["error"] for entry in fresh):
            self._status = KLIPPER_ERROR_STATUS
        self._persist()
        self.changed.emit()

    def clear(self) -> None:
        if not self._transcript:
            return
        self._transcript = []
        # Clear must clear the PERSISTED record too — both the new
        # transcript and the legacy typed history, so nothing survives
        # a restart (the author's ruling). The store stamp stays: the
        # cleared pane must not refill from the server's buffer.
        config = self._config()
        self._apply_config(replace(config, console_transcript=[], console_history=[]))
        self.changed.emit()

    def _lane_completed(self, label) -> None:
        # Each completion of a console-labelled lane cycle drains one
        # pending line. Macro sends share the lane but carry their own
        # labels, so they never touch the console counter, and the
        # guard keeps the counter from going negative when an emergency
        # stop already zeroed it.
        if label == "Console" and self._pending:
            self._pending -= 1
            self.changed.emit()

    def reload_if_empty(self) -> None:
        """The console loads its transcript ONCE, at construction — and
        the plugin constructs before Cura's active machine exists, so
        the early read hits the 'unknown' machine's EMPTY record and the
        restored lines never appear (the author's 'console is completely
        empty' report; the writes land under the real machine once the
        identity arrives). Re-load once the pane attaches / the session
        comes alive, but only while the transcript is still empty: a
        working session must not be re-stamped by a reconnect."""
        if self._transcript:
            return
        stored = getattr(self._config(), "console_transcript", None)
        if not isinstance(stored, (list, tuple)) or not stored:
            return
        self._transcript = [dict(entry) for entry in [{
            "kind": str(entry.get("kind") or "command"),
            "text": str(entry.get("text") or ""),
            "error": bool(entry.get("error")),
            "success": bool(entry.get("success")),
            "restored": True,
        } for entry in stored][-(MAX_TRANSCRIPT + self.MAX_PERSIST_COMMANDS):]]
        self._dropped = 0
        self._store_time = float(getattr(self._config(), "console_store_time", 0.0) or 0.0)
        self.changed.emit()

    def _session_invalidated(self) -> None:
        if self._pending or self._status:
            self._pending = 0
            self._status = ""
            self.changed.emit()
        self.reload_if_empty()

    def _emergency_stopped(self) -> None:
        if self._pending:
            self._pending = 0
            self._status = "Pending console commands dropped by the emergency stop."
            self.changed.emit()

    # The author's ruling: persist the last ~50 lines per printer —
    # BOTH our requests and its responses. A chatty Klipper fills the
    # 50-line window with responses and the typed requests age out of
    # it entirely, so the newest commands are pulled back in (bounded).
    MAX_PERSIST_COMMANDS = 10

    def _persist(self) -> None:
        config = self._config()
        entries = self._transcript
        transcript = list(entries[-MAX_TRANSCRIPT:])
        have = sum(1 for entry in transcript if entry["kind"] == "command")
        if have < self.MAX_PERSIST_COMMANDS:
            for entry in reversed(entries[:-len(transcript)]):
                if have >= self.MAX_PERSIST_COMMANDS:
                    break
                if entry["kind"] == "command":
                    transcript.insert(0, entry)
                    have += 1
        # Only the last 50 lines persist (the author's ruling); the
        # session keeps up to MAX_HISTORY in the pane. The store stamp
        # persists with them so the next session's backfill skips
        # everything already seen.
        if getattr(config, "console_transcript", None) != transcript                 or getattr(config, "console_store_time", 0.0) != self._store_time:
            self._apply_config(replace(config, console_transcript=transcript,
                                       console_store_time=self._store_time))
