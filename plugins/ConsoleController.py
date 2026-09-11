"""Console state owner: the bounded, per-printer command transcript and
the send lane.

Sends ride the console's OWN request path (channel "console"): the
endpoint replies only after Klipper processes the script, and that
reply's result is the execution verdict. Klipper's OUTPUT streams back
through Moonraker's gcode store, which MonitorData polls while the
console is expanded (the author's ruling: expanded-only, with a
backfill seed on expand) and deduplicates — the store pairs responses
to recent commands by recency only (there are no correlation ids), so
the pane is a terminal FEED, not a per-command echo: our typed lines
appear as sent, Klipper's lines arrive as they land, and no line claims
an attribution the store cannot support.

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


class ConsoleController(QObject):
    changed = pyqtSignal()

    def __init__(self, data, commands, config, apply_config, identity=None, parent=None):
        super().__init__(parent)
        self._data, self._commands = data, commands
        self._config, self._apply_config = config, apply_config
        self._identity = identity
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
        self._revisions = 0
        self._last_connection = None
        # In-flight sends by entry identity. Emergency stop and session
        # invalidation clear the set, so late completions from dead
        # requests can never drain the NEXT session's sends (the
        # empirical drift: 3 sends → es → 2 fresh → 3 stale completions
        # → 0, should be 2).
        self._in_flight = set()
        # The machine identity the in-memory transcript belongs to. The
        # console constructs before Cura's active machine exists, so it
        # resolves on the first successful load.
        self._transcript_identity = None
        commands.emergencyStopped.connect(self._emergency_stopped)
        # Every connection transition writes a "#" note into the feed
        # (the author's request: the console says when it lost or
        # regained the printer).
        data.connectionStateChanged.connect(self._connection_note)
        # A printer switch must not leave phantom pending sends or a
        # "sent" status bleeding across sessions; the transcript is
        # per-printer, so it swaps to the incoming machine's record.
        data.invalidated.connect(self._session_invalidated)

    @property
    def values(self):
        return {
            "consoleHistory": [entry["text"] for entry in self._transcript if entry["kind"] == "command"],
            "consoleLines": [dict(entry) for entry in self._transcript],
            "consoleDropped": self._dropped,
            # Bumps whenever an existing line's flags change (a send
            # verdict landing): the pane's spans are baked at render,
            # so a recolour needs a rebuild.
            "consoleRevisions": self._revisions,
            "consolePending": len(self._in_flight),
        }

    def _connection_note(self, connected) -> None:
        # Only genuine TRANSITIONS write a note: a flapping link re-emits
        # the same state on every failed reconnect attempt, and the feed
        # must not fill with repeats (the author's live report).
        connected = bool(connected)
        if connected == self._last_connection:
            return
        self._last_connection = connected
        self._note("Connected to Moonraker." if connected else "Disconnected from Moonraker.")

    def note(self, text) -> None:
        """The public entry for local notes (the toolhead's rejected
        moves and other plugin-side explanations)."""
        self._note(text)

    def _note(self, text) -> None:
        """A plugin-side note rendered as its own feed line: the pane
        draws kind "note" with a "#" prefix in amber, unmistakably the
        plugin's voice, never Klipper's (the author's ruling — anything
        the plugin adds to the feed must be obviously its own).
        Session-transient: notes never persist."""
        self._append_entries([{"kind": "note", "text": str(text),
                               "error": False, "success": False, "restored": False}])
        self.changed.emit()

    def _append_entries(self, additions) -> None:
        """Append transcript entries, trimming to the session ring cap.
        Once the ring is full every addition rotates one entry out of
        the HEAD; the count of rotated lines feeds the pane's renderer
        (a full ring never grows, so an incremental sync would otherwise
        stop appending forever). The newest commands are pinned through
        the rotation: a chatty Klipper floods the ring with a response
        per second and the typed requests rotated out entirely, so
        restores came back requestless (the author's report)."""
        merged = self._transcript + additions
        dropped_head, self._transcript = merged[:-MAX_HISTORY], merged[-MAX_HISTORY:]
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
            # Terminal semantics: an empty Enter is simply nothing — no
            # note, no banner (the author's ruling: the feed is the
            # console's information surface, and nothing sent carries no
            # information). Only an oversized paste explains itself.
            if len(str(text or "").strip()) > MAX_LINE:
                self._note("Command too long — ignored.")
            return False
        if len(self._in_flight) >= MAX_PENDING:
            self._note("Too many commands waiting — try again in a moment.")
            return False
        # The entry is captured BEFORE the request: the finished callback
        # closes over exactly this entry, so identical commands in flight
        # can never swap verdicts (the old text+recency scan
        # misattributed on duplicates, and the wrong colour persisted).
        entry = {"kind": "command", "text": line, "error": False, "success": False, "restored": False,
                 "saved": False}
        token = id(entry)
        def finished(payload, error):
            # The send's OWN result is the execution verdict: the
            # endpoint returns "ok" when the command completed (the
            # reply arrives only after Klipper processes the script),
            # and this callback belongs to THIS request.
            if error and payload is not None:
                # The server ANSWERED with an error body: a real refusal.
                entry["error"] = True
            elif error:
                # Transport-level failure (timeout, network, abort): the
                # command may still be executing — no verdict. A client
                # timeout must never paint a running command red (the
                # domain panel: Moonraker imposes no script timeout, and
                # blocking commands legitimately outlast ours).
                self._note("No response from the printer — the command may still be running.")
            # The layering pin keeps ConsoleController on ConsolePolicy
            # only, so the MonitorFormatting result() helper is inlined
            # here.
            verdict = payload.get("result", payload) if isinstance(payload, Mapping) else {}
            if not error:
                entry["success"] = verdict == "ok"
            if entry["error"] or entry["success"]:
                self._revisions += 1
                self._persist()
            if token in self._in_flight:
                self._in_flight.discard(token)
            self.changed.emit()
        # The console posts its own request instead of riding the shared
        # one-shot lane: the lane's card ticker is off-limits for console
        # traffic (the panel UX ruling), and the lane's completion signal
        # carries no verdict to colour the line with.
        started = self._data.request("console", "POST", "printer/gcode/script", finished,
                                     body={"script": line}, category="command", timeout_ms=30000)
        if not started:
            self._note("Command queue full or Moonraker unavailable — try again.")
            return False
        self._append_entries([entry])
        self._in_flight.add(token)
        # No "sent to Klipper" caption (the author's ruling): the typed
        # line's own verdict colouring carries the feedback, and the
        # feed's "!!" lines are their own red signal — no status banner
        # anywhere outside the feed.
        self._persist()
        self.changed.emit()
        return True

    def append_responses(self, entries) -> None:
        """Klipper's gcode-store output, newest last. Entries are already
        formatted ({text, error}) and deduplicated by MonitorData (its
        seen-set and the expand seed own the backfill skip — the
        author's "stale responses without requests" report); the
        transcript keeps the last MAX_TRANSCRIPT lines of the combined
        feed."""
        if not entries:
            return
        fresh = [{"kind": "response", "text": str(entry.get("text") or "")[:MAX_LINE],
                  "error": bool(entry.get("error")),
                  "success": bool(entry.get("success")),
                  "restored": False}
                 for entry in entries
                 if str(entry.get("text") or "")]
        if not fresh:
            return
        # The stamp advances monotonically and persists with the
        # transcript: the next session's expand skip is seeded with it.
        self._store_time = max(self._store_time, max(float(entry.get("time") or 0.0) for entry in entries))
        self._append_entries(fresh)
        # A live "!!" line is its own red signal in the feed — no
        # banner, no status line outside it (the author's ruling).
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

    def reload_if_empty(self) -> None:
        """Keep the transcript aligned with the ACTIVE machine's record.

        The console constructs before Cura's active machine exists, so
        the early read hits the 'unknown' machine's EMPTY record and the
        restored lines never appear (the author's 'console is completely
        empty' report; the writes land under the real machine once the
        identity arrives). Re-load per poll heartbeat until the identity
        resolves. Once loaded, a reconnect (same machine) must NOT
        re-stamp a working session — but a MACHINE SWITCH swaps the
        transcript to the incoming machine's per-printer record instead
        of letting A's lines bleed into B's pane and B's record (the
        panel security contamination: an empty A once adopted B's
        transcript AND B's store stamp, silencing A's feed forever)."""
        identity = None
        if self._identity is not None:
            identity = self._identity()
            if not identity or str(identity[0] or "") in ("", "unknown"):
                identity = None  # Cura's machine isn't resolved yet: retry later
            else:
                identity = str(identity[0])
        if self._transcript and (identity is None or self._transcript_identity is None
                                 or identity == self._transcript_identity):
            return  # live session, same machine, or identity untracked (harness)
        self._load_transcript(identity)

    def _load_transcript(self, identity) -> None:
        stored = getattr(self._config(), "console_transcript", None)
        if isinstance(stored, (list, tuple)) and stored:
            transcript = [dict(entry) for entry in [{
                "kind": str(entry.get("kind") or "command"),
                "text": str(entry.get("text") or ""),
                "error": bool(entry.get("error")),
                "success": bool(entry.get("success")),
                "restored": True,
            } for entry in stored][-(MAX_TRANSCRIPT + self.MAX_PERSIST_COMMANDS):]]
        else:
            # The incoming machine has no record (fresh install, or a
            # Clear): an empty pane, never the previous machine's lines.
            transcript = []
        if not transcript and not self._transcript and identity == self._transcript_identity:
            return  # nothing to load and nothing changed: stay quiet
        self._transcript = transcript
        self._transcript_identity = identity
        self._dropped = 0
        self._store_time = float(getattr(self._config(), "console_store_time", 0.0) or 0.0)
        self._revisions += 1
        self.changed.emit()

    def mark_saved(self) -> None:
        """The preference file flushed: the sent lines are on disk, so
        their blue pending colour can settle (the author's ruling — the
        API verdict flips too fast to read). Only entries inside the
        persisted window may claim "on disk": lines beyond it were never
        written and die with the session (the engineering panel's
        over-promise)."""
        window = {id(entry) for entry in self._persist_window()}
        changed_any = False
        for entry in self._transcript:
            if (entry["kind"] == "command" and not entry["restored"] and not entry.get("saved")
                    and id(entry) in window):
                entry["saved"] = True
                changed_any = True
        if changed_any:
            self._revisions += 1
            self.changed.emit()

    def _session_invalidated(self) -> None:
        if self._in_flight:
            self._in_flight.clear()
            self.changed.emit()
        self.reload_if_empty()

    def _emergency_stopped(self) -> None:
        if self._in_flight:
            self._in_flight.clear()
            self._note("Pending console commands dropped by the emergency stop.")

    # The author's ruling: persist the last ~50 lines per printer —
    # BOTH our requests and its responses. A chatty Klipper fills the
    # 50-line window with responses and the typed requests age out of
    # it entirely, so the newest commands are pulled back in (bounded).
    MAX_PERSIST_COMMANDS = 10

    def _persist_window(self) -> list:
        """The entries the persisted record will hold: the last
        MAX_TRANSCRIPT lines plus the newest commands pinned at the
        front. mark_saved and _persist share this so the "on disk"
        colour never claims a line the record dropped."""
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
        return transcript

    def _persist(self) -> None:
        config = self._config()
        # The stored record is the canonical schema (kind/text/error/
        # success): session-transient keys (restored, saved) must not
        # enter it — they made the equality guard compare unequal
        # forever, so every persist rewrote the full multi-machine
        # preference map (the architecture panel's dead guard).
        transcript = [{"kind": entry["kind"], "text": entry["text"],
                       "error": entry["error"], "success": entry["success"]}
                      for entry in self._persist_window()]
        # Only the last 50 lines persist (the author's ruling); the
        # session keeps up to MAX_HISTORY in the pane. The store stamp
        # persists with them so the next session's expand skip is
        # seeded with everything already seen.
        if getattr(config, "console_transcript", None) != transcript \
                or getattr(config, "console_store_time", 0.0) != self._store_time:
            self._apply_config(replace(config, console_transcript=transcript,
                                       console_store_time=self._store_time))
