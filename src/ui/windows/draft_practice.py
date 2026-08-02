"""Safe, local draft replay for validating the live drafting UI."""

import json
import os
import random
import uuid
import tkinter
from tkinter import messagebox

import ttkbootstrap as ttk

from src import constants
from src.log_scanner import ArenaScanner
from src.ui.styles import Theme


class DraftPracticeWindow(tkinter.Toplevel):
    """Drive the normal log pipeline with a synthetic, isolated Player.log."""

    PICKS_PER_PACK = 14
    PACK_COUNT = 3

    def __init__(self, parent, app_context):
        super().__init__(parent)
        self.app_context = app_context
        self.orchestrator = app_context.orchestrator
        self._ended = False
        self._poll_after_id = None
        self._pack = 1
        self._pick = 1
        self._draft_id = f"practice-{uuid.uuid4().hex[:12]}"
        self._rng = random.Random(self._draft_id)
        self._current_pack_ids = []
        self._choice_to_id = {}
        self._practice_scanner = None
        self._log_path = ""
        self._state_path = ""

        self.title("Practice Draft — No Arena Entry")
        self.geometry(f"{Theme.scaled_val(480)}x{Theme.scaled_val(360)}")
        self.minsize(Theme.scaled_val(440), Theme.scaled_val(330))
        Theme.apply(self, self.app_context.configuration.settings.theme)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self.end_practice)

        if not self._prepare_session():
            self.after_idle(self.destroy)
            return

        self._build_ui()
        self._start_session()

    def _prepare_session(self):
        if self.orchestrator.is_practice_mode:
            messagebox.showinfo(
                "Practice Draft",
                "A practice draft is already running.",
                parent=self,
            )
            return False

        live_scanner = self.orchestrator.scanner
        live_pack, live_pick = live_scanner.retrieve_current_pack_and_pick()
        live_log_path = getattr(self.orchestrator, "live_log_path", "")
        is_reading_live_log = bool(
            live_log_path
            and os.path.abspath(live_scanner.arena_file)
            == os.path.abspath(live_log_path)
        )
        draft_is_incomplete = live_pack and (live_pack < 3 or live_pick < 14)
        if is_reading_live_log and draft_is_incomplete:
            messagebox.showwarning(
                "Active draft detected",
                "Practice mode is unavailable while a draft is active. This protects your live pick state.",
                parent=self,
            )
            return False

        dataset_name = self.app_context.configuration.card_data.latest_dataset
        dataset_path = os.path.join(constants.SETS_FOLDER, dataset_name)
        if not dataset_name or not os.path.exists(dataset_path):
            messagebox.showwarning(
                "Dataset required",
                "Download and activate a dataset first, then start the dummy draft again.",
                parent=self,
            )
            return False

        self._set_code = dataset_name.split("_", 1)[0].upper()
        self._event_name = f"PremierDraft_{self._set_code}_Practice"

        os.makedirs(constants.TEMP_FOLDER, exist_ok=True)
        self._log_path = os.path.join(
            constants.TEMP_FOLDER, f"PracticeDraft_{self._draft_id}.log"
        )
        self._state_path = os.path.join(
            constants.TEMP_FOLDER, f"PracticeDraft_{self._draft_id}.json"
        )

        self._practice_scanner = ArenaScanner(
            filename=self._log_path,
            set_list=live_scanner.set_list,
            sets_location=getattr(live_scanner, "sets_location", constants.SETS_FOLDER),
            retrieve_unknown=False,
            db_path=getattr(live_scanner.set_data, "db_path", None),
        )
        # ArenaScanner normally shares the live recovery file. Give the sandbox
        # its own state before clearing the inherited in-memory snapshot.
        self._practice_scanner.state_file = self._state_path
        self._practice_scanner.clear_draft(True)
        self._practice_scanner.retrieve_set_data(dataset_path)

        ratings = self._practice_scanner.set_data.get_card_ratings()
        self._eligible_ids = [
            str(card_id)
            for card_id, card in ratings.items()
            if card.get("name")
            and card.get("name") not in constants.BASIC_LANDS
            and "Basic" not in card.get("types", [])
        ]
        if len(self._eligible_ids) < self.PICKS_PER_PACK:
            messagebox.showwarning(
                "Dataset incomplete",
                "The active dataset does not contain enough cards for a practice pack.",
                parent=self,
            )
            return False
        return True

    def _build_ui(self):
        shell = ttk.Frame(self, padding=Theme.scaled_val(20))
        shell.pack(fill="both", expand=True)

        ttk.Label(
            shell,
            text="SAFE DUMMY DRAFT",
            font=Theme.scaled_font(10, "bold"),
            bootstyle="success",
        ).pack(anchor="w")
        ttk.Label(
            shell,
            text="No Arena event is joined and no gems or gold can be spent.",
            font=Theme.scaled_font(14, "bold"),
            wraplength=Theme.scaled_val(420),
            justify="left",
        ).pack(anchor="w", pady=Theme.scaled_val((4, 6)))
        ttk.Label(
            shell,
            text=(
                "This window writes synthetic events to an isolated log. The main "
                "window uses its normal detector, card ratings, recommendation, and pool-update path."
            ),
            wraplength=Theme.scaled_val(420),
            justify="left",
        ).pack(anchor="w", pady=Theme.scaled_val((0, 14)))

        self.progress_var = tkinter.StringVar()
        ttk.Label(
            shell,
            textvariable=self.progress_var,
            font=Theme.scaled_font(12, "bold"),
        ).pack(anchor="w")

        self.choice_var = tkinter.StringVar()
        self.choice_box = ttk.Combobox(
            shell,
            textvariable=self.choice_var,
            state="readonly",
        )
        self.choice_box.pack(fill="x", pady=Theme.scaled_val((8, 12)))

        button_row = ttk.Frame(shell)
        button_row.pack(fill="x")
        self.pick_button = ttk.Button(
            button_row,
            text="Pick selected card",
            bootstyle="primary",
            command=self.pick_selected,
        )
        self.pick_button.pack(side="left", padx=Theme.scaled_val((0, 8)))
        self.recommended_button = ttk.Button(
            button_row,
            text="Pick recommendation",
            bootstyle="success",
            command=self.pick_recommended,
        )
        self.recommended_button.pack(side="left")

        footer = ttk.Frame(shell)
        footer.pack(fill="x", side="bottom", pady=Theme.scaled_val((16, 0)))
        ttk.Label(
            footer,
            text="Closing this window restores the original live scanner.",
            font=Theme.scaled_font(9),
        ).pack(side="left")
        ttk.Button(
            footer,
            text="End practice",
            bootstyle="secondary",
            command=self.end_practice,
        ).pack(side="right")

    def _start_session(self):
        self._current_pack_ids = self._make_pack()
        with open(self._log_path, "w", encoding="utf-8") as practice_log:
            practice_log.write("DETAILED LOGS: ENABLED\n")
            practice_log.write(self._event_join_line())
            practice_log.write(self._pack_line())
            practice_log.flush()

        if not self.orchestrator.begin_practice_session(self._practice_scanner):
            messagebox.showwarning(
                "Practice Draft",
                "Practice mode could not be started.",
                parent=self,
            )
            self._ended = True
            self.destroy()
            return

        self._refresh_choices()
        self._poll_after_id = self.after(500, self._poll_session)

    def _make_pack(self):
        size = self.PICKS_PER_PACK - self._pick + 1
        return self._rng.sample(self._eligible_ids, size)

    def _refresh_choices(self):
        cards = self._practice_scanner.set_data.get_data_by_id(self._current_pack_ids)
        self._choice_to_id = {}
        labels = []
        for card_id, card in zip(self._current_pack_ids, cards):
            name = card.get("name", card_id)
            label = f"{name}  [{card_id}]"
            labels.append(label)
            self._choice_to_id[label] = card_id

        self.choice_box.configure(values=labels)
        if labels:
            self.choice_var.set(labels[0])
        self.progress_var.set(
            f"{self._set_code} · Pack {self._pack} · Pick {self._pick} · {len(labels)} cards"
        )

    def pick_selected(self):
        card_id = self._choice_to_id.get(self.choice_var.get())
        if card_id:
            self._pick_card(card_id)

    def pick_recommended(self):
        card_id = None
        advisor_panel = getattr(self.app_context.dashboard, "advisor_panel", None)
        recommendations = getattr(advisor_panel, "last_recs", [])
        if recommendations:
            recommended_name = recommendations[0].card_name
            for candidate_id in self._current_pack_ids:
                cards = self._practice_scanner.set_data.get_data_by_id([candidate_id])
                if cards and cards[0].get("name") == recommended_name:
                    card_id = candidate_id
                    break
        self._pick_card(card_id or self._current_pack_ids[0])

    def _pick_card(self, card_id):
        if self._ended or not self.orchestrator.is_practice_mode:
            return

        with open(self._log_path, "a", encoding="utf-8") as practice_log:
            practice_log.write(self._pick_line(card_id))

            self._pick += 1
            if self._pick > self.PICKS_PER_PACK:
                self._pack += 1
                self._pick = 1

            if self._pack <= self.PACK_COUNT:
                self._current_pack_ids = self._make_pack()
                practice_log.write(self._pack_line())
            practice_log.flush()

        if self._pack > self.PACK_COUNT:
            self.progress_var.set("Practice draft complete — 42 picks processed")
            self.choice_box.configure(values=[], state="disabled")
            self.pick_button.configure(state="disabled")
            self.recommended_button.configure(state="disabled")
        else:
            self._refresh_choices()

    def _event_join_line(self):
        request = json.dumps({"EventName": self._event_name})
        payload = json.dumps({"id": self._draft_id, "request": request})
        return f"{constants.DRAFT_START_STRING_PREMIER}{payload}\n"

    def _pack_line(self):
        payload = json.dumps(
            {
                "draftId": self._draft_id,
                "SelfPick": self._pick,
                "SelfPack": self._pack,
                "PackCards": ",".join(self._current_pack_ids),
            }
        )
        return f"{constants.DRAFT_PACK_STRING_PREMIER}{payload}\n"

    def _pick_line(self, card_id):
        request = json.dumps(
            {
                "DraftId": self._draft_id,
                "Pack": self._pack,
                "Pick": self._pick,
                "GrpId": int(card_id) if str(card_id).isdigit() else str(card_id),
            }
        )
        payload = json.dumps({"id": uuid.uuid4().hex, "request": request})
        return f"{constants.DRAFT_PICK_STRING_PREMIER}{payload}\n"

    def _poll_session(self):
        if self._ended:
            return
        if not self.orchestrator.is_practice_mode:
            self._ended = True
            messagebox.showwarning(
                "Practice stopped",
                "Live Arena draft activity was detected. The app restored live monitoring automatically.",
                parent=self,
            )
            self._clear_app_reference()
            self.destroy()
            return
        self._poll_after_id = self.after(500, self._poll_session)

    def end_practice(self):
        if self._ended:
            return
        self._ended = True
        if self._poll_after_id is not None:
            try:
                self.after_cancel(self._poll_after_id)
            except tkinter.TclError:
                pass
        self.orchestrator.end_practice_session()
        self._clear_app_reference()
        self.destroy()

    def _clear_app_reference(self):
        if getattr(self.app_context, "draft_practice_window", None) is self:
            self.app_context.draft_practice_window = None
