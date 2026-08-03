import tkinter
import threading
import queue
import os
import json
import re
from tkinter import ttk, messagebox
from datetime import date
from typing import Optional
from dataclasses import dataclass

from src import constants
from src.configuration import write_configuration
from src.file_extractor import FileExtractor
from src.utils import retrieve_local_set_list, read_local_manifest, open_file
from src.ui.components import DynamicTreeviewManager, AutoScrollbar
from src.ui.styles import Theme


@dataclass
class DatasetArgs:
    draft_set: str
    draft: str
    start: str
    end: str
    user_group: str
    game_count: int
    color_ratings: Optional[dict] = None
    time_period: str = constants.TIME_PERIOD_DEFAULT


class DownloadWindow(ttk.Frame):
    def __init__(self, parent, limited_sets, configuration, on_update_callback):
        super().__init__(parent)
        self.sets_data = limited_sets.data
        self.latest_set_code = limited_sets.latest_set
        self.configuration = configuration
        self.on_update_callback = on_update_callback
        self.vars = {}
        self._download_thread = None
        self._download_events = queue.Queue()
        self._download_poll_id = None
        self._build_ui()

    def refresh(self):
        self._update_table()

    def _build_ui(self):
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        canvas = tkinter.Canvas(
            self,
            highlightthickness=0,
            borderwidth=0,
            background=Theme.BG_PRIMARY,
        )
        scrollbar = AutoScrollbar(self, orient="vertical", command=canvas.yview)

        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scrollbar.set)

        container = ttk.Frame(
            canvas,
            style="App.TFrame",
            padding=Theme.scaled_val((6, 10, 6, 18)),
        )
        canvas_window = canvas.create_window((0, 0), window=container, anchor="nw")

        def _on_content_resize(event):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_resize(event):
            canvas.itemconfig(canvas_window, width=event.width)

        container.bind("<Configure>", _on_content_resize)
        canvas.bind("<Configure>", _on_canvas_resize)

        from src.utils import bind_scroll

        bind_scroll(canvas, canvas.yview_scroll)
        bind_scroll(container, canvas.yview_scroll)

        self.vars["dataset_count"] = tkinter.StringVar(value="No downloads yet")
        self.vars["active_dataset"] = tkinter.StringVar(
            value="No active dataset selected"
        )

        header = ttk.Frame(
            container, style="Surface.TFrame", padding=Theme.scaled_val(16)
        )
        header.pack(fill="x", pady=(0, Theme.scaled_val(12)))
        header.columnconfigure(0, weight=1)

        title_stack = ttk.Frame(header, style="Surface.TFrame")
        title_stack.grid(row=0, column=0, sticky="ew")
        ttk.Label(
            title_stack, text="Downloaded datasets", style="SurfaceTitle.TLabel"
        ).pack(anchor="w")
        ttk.Label(
            title_stack,
            text="Every row below is stored on this computer. Choose one to power ratings and recommendations.",
            style="SurfaceMuted.TLabel",
        ).pack(anchor="w", pady=(Theme.scaled_val(2), 0))

        ttk.Label(
            header, textvariable=self.vars["dataset_count"], style="Badge.TLabel"
        ).grid(row=0, column=1, sticky="e", padx=(Theme.scaled_val(12), 0))

        ttk.Label(
            header,
            textvariable=self.vars["active_dataset"],
            style="Surface.TLabel",
            font=Theme.scaled_font(10, "bold"),
        ).grid(row=1, column=0, sticky="w", pady=(Theme.scaled_val(12), 0))
        ttk.Button(
            header,
            text="Open download folder",
            command=self._open_dataset_folder,
            bootstyle="secondary-outline",
        ).grid(
            row=1,
            column=1,
            sticky="e",
            padx=(Theme.scaled_val(12), 0),
            pady=(Theme.scaled_val(8), 0),
        )

        table_card = ttk.Frame(
            container, style="Surface.TFrame", padding=Theme.scaled_val(12)
        )
        table_card.pack(fill="x", pady=(0, Theme.scaled_val(12)))

        self.table_manager = DynamicTreeviewManager(
            table_card,
            view_id="dataset_manager",
            configuration=self.configuration,
            on_update_callback=lambda: None,
            static_columns=[
                "Status",
                "Set",
                "Event",
                "Group",
                "Start",
                "End",
                "Collected",
                "Games",
            ],
            height=6,
        )
        self.table_manager.pack(fill="x")
        self.table = self.table_manager.tree
        self.table.bind("<Double-1>", self._on_set_active)
        self.table.bind("<Button-3>", self._on_context_menu)
        self.table.bind("<Control-Button-1>", self._on_context_menu)
        self.table.bind("<<TreeviewSelect>>", self._on_dataset_selection)

        column_widths = {
            "Status": (78, False),
            "Set": (150, True),
            "Event": (125, True),
            "Group": (100, False),
            "Start": (105, False),
            "End": (105, False),
            "Collected": (155, True),
            "Games": (95, False),
        }
        for column, (width, stretch) in column_widths.items():
            self.table.column(
                column,
                width=Theme.scaled_val(width),
                minwidth=Theme.scaled_val(60),
                stretch=stretch,
            )

        table_actions = ttk.Frame(table_card, style="Surface.TFrame")
        table_actions.pack(fill="x", pady=(Theme.scaled_val(10), 0))
        ttk.Label(
            table_actions,
            text="Select a row to manage it. Double-click still activates a dataset.",
            style="SurfaceMuted.TLabel",
        ).pack(side="left", fill="x", expand=True)
        self.btn_delete_selected = ttk.Button(
            table_actions,
            text="Delete selected",
            command=self._delete_selected_dataset,
            bootstyle="danger-outline",
            state="disabled",
        )
        self.btn_delete_selected.pack(side="right", padx=(Theme.scaled_val(8), 0))
        self.btn_use_selected = ttk.Button(
            table_actions,
            text="Use selected",
            command=self._on_set_active,
            bootstyle="primary",
            state="disabled",
        )
        self.btn_use_selected.pack(side="right")

        form = ttk.Frame(
            container, style="Surface.TFrame", padding=Theme.scaled_val(16)
        )
        form.pack(fill="x")
        for column in range(4):
            form.columnconfigure(column, weight=1)

        ttk.Label(
            form, text="Download a dataset", style="SurfaceTitle.TLabel"
        ).grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(
            form,
            text="Create a local 17Lands snapshot for a set and draft format.",
            style="SurfaceMuted.TLabel",
        ).grid(
            row=1,
            column=0,
            columnspan=4,
            sticky="w",
            pady=(Theme.scaled_val(2), Theme.scaled_val(12)),
        )

        # --- DYNAMIC SET SORTING & SEPARATOR LOGIC ---
        set_options = list(self.sets_data.keys())
        active_set_codes = []

        active_set_codes = read_local_manifest().get("active_sets", [])

        active_options = []
        inactive_options = []

        for k in set_options:
            s_info = self.sets_data[k]
            is_active = False
            match_index = 999

            # Check if set_code or 17lands codes match the active calendar
            if s_info.set_code in active_set_codes:
                is_active = True
                match_index = min(match_index, active_set_codes.index(s_info.set_code))

            for sl_code in s_info.seventeenlands:
                if sl_code in active_set_codes:
                    is_active = True
                    match_index = min(match_index, active_set_codes.index(sl_code))

            if is_active:
                active_options.append((match_index, k))
            else:
                inactive_options.append(k)

        # Sort active sets in the exact order they appear in the calendar
        active_options.sort(key=lambda x: x[0])
        active_names = [x[1] for x in active_options]
        inactive_names = inactive_options

        # Fallback if no active sets found (e.g., manifest is missing/empty)
        if not active_names:
            latest_key = None
            for k, v in self.sets_data.items():
                if v.set_code == self.latest_set_code:
                    latest_key = k
                    break

            if latest_key and latest_key in inactive_names:
                inactive_names.remove(latest_key)
                active_names.append(latest_key)

        default_val = (
            active_names[0]
            if active_names
            else (inactive_names[0] if inactive_names else "")
        )
        self.vars["set"] = tkinter.StringVar(value=default_val)

        ttk.Label(form, text="Set", style="SurfaceMuted.TLabel").grid(
            row=2, column=0, sticky="w"
        )

        self.om_set = ttk.OptionMenu(form, self.vars["set"], default_val)
        menu = self.om_set["menu"]
        menu.delete(0, "end")

        for opt in active_names:
            menu.add_command(
                label=opt,
                command=tkinter._setit(self.vars["set"], opt, self._on_set_change),
            )

        if active_names and inactive_names:
            menu.add_separator()

        for opt in inactive_names:
            menu.add_command(
                label=opt,
                command=tkinter._setit(self.vars["set"], opt, self._on_set_change),
            )

        self.om_set.grid(
            row=3,
            column=0,
            sticky="ew",
            padx=(0, Theme.scaled_val(8)),
            pady=(Theme.scaled_val(3), Theme.scaled_val(10)),
        )
        # --- END DYNAMIC SET SORTING ---

        self.vars["event"] = tkinter.StringVar(value="PremierDraft")
        ttk.Label(form, text="Event", style="SurfaceMuted.TLabel").grid(
            row=2, column=1, sticky="w", padx=(Theme.scaled_val(4), 0)
        )
        self.om_event = ttk.OptionMenu(
            form,
            self.vars["event"],
            "PremierDraft",
            *sorted(constants.LIMITED_TYPE_LIST),
        )
        self.om_event.grid(
            row=3,
            column=1,
            sticky="ew",
            padx=Theme.scaled_val(4),
            pady=(Theme.scaled_val(3), Theme.scaled_val(10)),
        )

        self.vars["group"] = tkinter.StringVar(value="All")
        ttk.Label(form, text="Player group", style="SurfaceMuted.TLabel").grid(
            row=2, column=2, sticky="w", padx=(Theme.scaled_val(4), 0)
        )
        ttk.OptionMenu(
            form, self.vars["group"], "All", *constants.LIMITED_GROUPS_LIST
        ).grid(
            row=3,
            column=2,
            sticky="ew",
            padx=Theme.scaled_val(4),
            pady=(Theme.scaled_val(3), Theme.scaled_val(10)),
        )

        self.vars["threshold"] = tkinter.StringVar(value="500")
        ttk.Label(form, text="Minimum games", style="SurfaceMuted.TLabel").grid(
            row=2, column=3, sticky="w", padx=(Theme.scaled_val(4), 0)
        )
        ttk.Entry(form, textvariable=self.vars["threshold"]).grid(
            row=3,
            column=3,
            sticky="ew",
            padx=(Theme.scaled_val(4), 0),
            pady=(Theme.scaled_val(3), Theme.scaled_val(10)),
        )

        # 17Lands replaced custom start/end date ranges with time_period presets.
        # We still track start/end internally (set-start -> today) for the dataset
        # meta and the table, but the user now picks a preset instead.
        self.vars["start"] = tkinter.StringVar(value="2019-01-01")
        self.vars["end"] = tkinter.StringVar(value=str(date.today()))

        self.vars["period"] = tkinter.StringVar(
            value=constants.TIME_PERIOD_DEFAULT_LABEL
        )
        ttk.Label(form, text="Time period", style="SurfaceMuted.TLabel").grid(
            row=4, column=0, sticky="w"
        )
        ttk.OptionMenu(
            form,
            self.vars["period"],
            constants.TIME_PERIOD_DEFAULT_LABEL,
            *constants.TIME_PERIOD_LABELS,
        ).grid(
            row=5,
            column=0,
            sticky="ew",
            padx=(0, Theme.scaled_val(8)),
            pady=(Theme.scaled_val(3), 0),
        )

        ttk.Label(
            form,
            text="All Time is the most stable default. Minimum games only affects color-specific views.",
            style="SurfaceMuted.TLabel",
        ).grid(
            row=5,
            column=1,
            columnspan=2,
            sticky="w",
            padx=Theme.scaled_val(4),
        )

        self.btn_dl = ttk.Button(
            form,
            text="Download dataset",
            command=self._manual_download,
            bootstyle="primary",
        )
        self.btn_dl.grid(
            row=5,
            column=3,
            pady=0,
            padx=(Theme.scaled_val(8), 0),
            sticky="ew",
        )

        self.btn_clear = ttk.Button(
            form,
            text="Clear all downloads",
            command=self._clear_set_history,
            bootstyle="danger-outline",
        )
        self.btn_clear.grid(
            row=6,
            column=3,
            pady=(Theme.scaled_val(8), 0),
            padx=(Theme.scaled_val(8), 0),
            sticky="ew",
        )

        progress_card = ttk.Frame(
            container, style="Surface.TFrame", padding=Theme.scaled_val((16, 12))
        )
        progress_card.pack(fill="x", pady=(Theme.scaled_val(12), 0))

        self.progress = ttk.Progressbar(progress_card, mode="determinate")
        self.progress.pack(fill="x")

        self.vars["status"] = tkinter.StringVar(value="Ready")
        ttk.Label(
            progress_card,
            textvariable=self.vars["status"],
            style="SurfaceMuted.TLabel",
        ).pack(anchor="w", pady=(Theme.scaled_val(6), 0))

        self._update_table()

        # Trigger an immediate synchronization so the dynamic dropdowns reflect the correct set's formats
        self._on_set_change(self.vars["set"].get())

    def _open_dataset_folder(self):
        open_file(constants.SETS_FOLDER)

    def _on_dataset_selection(self, event=None):
        selection = self.table.selection()
        if not selection:
            self.btn_use_selected.configure(state="disabled")
            self.btn_delete_selected.configure(state="disabled")
            return

        filename = os.path.basename(selection[0])
        is_active = filename == self.configuration.card_data.latest_dataset
        self.btn_use_selected.configure(state="disabled" if is_active else "normal")
        self.btn_delete_selected.configure(
            state="disabled" if is_active else "normal"
        )

    def _delete_selected_dataset(self):
        selection = self.table.selection()
        if selection:
            self._delete_dataset(selection[0])

    def _on_set_active(self, event=None):
        """Switches the application to use the selected dataset."""
        selection = self.table.selection()
        if not selection:
            return

        filepath = selection[0]
        filename = os.path.basename(filepath)

        if self.configuration.card_data.latest_dataset != filename:
            self.configuration.card_data.latest_dataset = filename
            write_configuration(self.configuration)

            self._update_table()
            if self.on_update_callback:
                self.on_update_callback()

    def _on_context_menu(self, event):
        """Spawns a right-click menu to manage datasets."""
        region = self.table.identify_region(event.x, event.y)
        if region == "heading":
            return

        row_id = self.table.identify_row(event.y)
        if not row_id:
            return

        self.table.selection_set(row_id)

        menu = tkinter.Menu(self, tearoff=0)
        menu.add_command(label="Use as active dataset", command=self._on_set_active)
        menu.add_separator()
        menu.add_command(
            label="Delete dataset", command=lambda: self._delete_dataset(row_id)
        )

        menu.post(event.x_root, event.y_root)

    def _delete_dataset(self, filepath):
        """Safely deletes a downloaded dataset from the hard drive."""
        filename = os.path.basename(filepath)

        if self.configuration.card_data.latest_dataset == filename:
            messagebox.showwarning(
                "Cannot Delete",
                "You cannot delete the currently active dataset. Please double-click a different dataset to switch to it first.",
            )
            return

        if messagebox.askyesno(
            "Confirm Delete",
            f"Are you sure you want to permanently delete this dataset?\n\n{filename}",
        ):
            try:
                os.remove(filepath)
                from src.utils import invalidate_local_set_cache

                invalidate_local_set_cache()
                self._update_table()
            except Exception as e:
                messagebox.showerror("Error", f"Failed to delete file:\n{e}")

    def enter(self, args: DatasetArgs = None):
        if args:
            target_key = next(
                (
                    k
                    for k, v in self.sets_data.items()
                    if v.seventeenlands[0] == args.draft_set
                ),
                None,
            )
            if target_key:
                self.vars["set"].set(target_key)
                self._on_set_change(target_key)
            self.vars["event"].set(args.draft)
            self.vars["group"].set(args.user_group)
            self.vars["start"].set(args.start)
            self.vars["end"].set(args.end)
            self.vars["period"].set(constants.time_period_label(args.time_period))
            self.update_idletasks()
            self._start_download(args)

    def _manual_download(self):
        self._start_download()

    def _resolve_start_date(self, set_key: str) -> str:
        """Best-known start date for a set. The set list can be stuck on the
        placeholder default (stale cache, 17Lands feed gap), so fall back to
        the earliest start_date the synced dataset manifest records for it."""
        s_info = self.sets_data.get(set_key)
        if s_info and s_info.start_date != constants.START_DATE_DEFAULT:
            return s_info.start_date

        codes = {c.upper() for c in (s_info.seventeenlands if s_info else [])}
        manifest_dates = [
            entry["start_date"]
            for key, entry in read_local_manifest().get("datasets", {}).items()
            if key.split("_")[0].upper() in codes and entry.get("start_date")
        ]
        if manifest_dates:
            return min(manifest_dates)

        return self.vars["start"].get()

    def _clear_set_history(self):
        """Deletes all locally downloaded datasets and schedules a fresh refresh on
        the next launch's splash screen. Useful when accumulated old sets slow
        loading or after a data-format change."""
        if not messagebox.askyesno(
            "Clear Set History",
            "Delete all downloaded datasets?\n\nThe latest 17Lands data will be "
            "re-downloaded automatically the next time you start the app.",
        ):
            return

        from src.utils import clear_set_history

        removed = clear_set_history()

        # The active dataset file may now be gone, and resetting the version marker
        # makes the next launch perform the one-time corrected-data refresh.
        self.configuration.card_data.latest_dataset = ""
        self.configuration.settings.last_run_version = ""
        write_configuration(self.configuration)

        self._update_table()
        messagebox.showinfo(
            "Set History Cleared",
            f"Removed {removed} dataset(s). Restart the app to download fresh "
            "17Lands data.",
        )

    def _on_set_change(self, val):
        s_info = self.sets_data.get(val)
        if s_info:
            if s_info.start_date:
                self.vars["start"].set(s_info.start_date)
            try:
                menu = self.om_event["menu"]
                menu.delete(0, "end")

                formats_to_show = (
                    s_info.formats if s_info.formats else constants.LIMITED_TYPE_LIST
                )
                formats_to_show = sorted(formats_to_show)

                for f in formats_to_show:
                    menu.add_command(
                        label=f, command=lambda v=f: self.vars["event"].set(v)
                    )
                if formats_to_show:
                    self.vars["event"].set(formats_to_show[0])
            except:
                pass

    def _update_table(self):
        for i in self.table.get_children():
            self.table.delete(i)

        self.table.tag_configure(
            "active_dataset_card", background="#0ea5e9", foreground="#ffffff"
        )

        codes = [v.seventeenlands[0] for v in self.sets_data.values()]
        files, _ = retrieve_local_set_list(codes, list(self.sets_data.keys()))

        active_filename = self.configuration.card_data.latest_dataset
        active_label = "No active dataset selected"
        sorted_files = sorted(files, key=lambda x: x[7], reverse=True)

        for idx, row in enumerate(sorted_files):
            filepath = row[6]
            filename = os.path.basename(filepath)

            is_active = filename == active_filename
            status = "Active" if is_active else "Available"
            tag = (
                "active_dataset_card"
                if is_active
                else ("bw_odd" if idx % 2 == 0 else "bw_even")
            )

            if is_active:
                event_name = re.sub(r"(?<!^)(?=[A-Z])", " ", str(row[1]))
                active_label = (
                    f"Active dataset: {row[0]} / {event_name} / {row[2]}"
                )

            self.table.insert(
                "",
                "end",
                iid=filepath,
                values=(
                    status,
                    row[0],
                    row[1],
                    row[2],
                    row[3],
                    row[4],
                    row[7],
                    row[5],
                ),
                tags=(tag,),
            )

        count = len(sorted_files)
        self.vars["dataset_count"].set(
            f"{count} downloaded" if count else "No downloads yet"
        )
        self.vars["active_dataset"].set(active_label)
        self.btn_clear.configure(state="normal" if count else "disabled")
        self._on_dataset_selection()

    def _start_download(self, args: DatasetArgs = None):
        try:
            thr_str = self.vars["threshold"].get().strip() or "500"
            if not thr_str.isdigit():
                raise ValueError("Min Games must be numeric.")
            threshold = int(thr_str)
        except ValueError as e:
            messagebox.showerror("Download Error", str(e))
            return

        # CAPTURE DATA ON MAIN THREAD BEFORE ENTERING WORKER
        ctx = {
            "set_key": self.vars["set"].get(),
            "event": self.vars["event"].get(),
            "start": self._resolve_start_date(self.vars["set"].get()),
            "end": self.vars["end"].get(),
            "time_period": constants.time_period_value(self.vars["period"].get()),
            "group": self.vars["group"].get(),
            "db_loc": self.configuration.settings.database_location,
            "threshold": threshold,
        }

        if self._download_thread and self._download_thread.is_alive():
            return
        self.btn_dl.configure(state="disabled")

        self._download_thread = threading.Thread(
            target=self._run_download_process, args=(args, ctx), daemon=True
        )
        self._schedule_download_poll()
        self._download_thread.start()

    def _schedule_download_poll(self):
        """Start polling worker messages from the Tk main thread."""
        if self._download_poll_id is None:
            self._download_poll_id = self.after(50, self._poll_download_events)

    def _queue_progress_update(self, event_type, value):
        """Receive progress from a worker without touching Tk."""
        self._download_events.put((event_type, value))

    def _poll_download_events(self):
        """Apply queued worker messages. This method only runs on Tk's thread."""
        self._download_poll_id = None
        while True:
            try:
                event_type, value = self._download_events.get_nowait()
            except queue.Empty:
                break

            if event_type == "status":
                self.vars["status"].set(value)
            elif event_type == "progress":
                self.progress["value"] = value
            elif event_type == "success":
                self._finalize_download(value)
            elif event_type == "error":
                self._handle_error(value)

        if (
            self._download_thread is not None
            and self._download_thread.is_alive()
        ) or not self._download_events.empty():
            self._schedule_download_poll()

    def _run_download_process(self, args, ctx):
        try:
            ex = FileExtractor(
                ctx["db_loc"],
                None,
                None,
                None,
                threshold=ctx["threshold"],
                update_callback=self._queue_progress_update,
            )
            ex.clear_data()
            ex.select_sets(self.sets_data[ctx["set_key"]])
            ex.set_draft_type(ctx["event"])
            ex.set_start_date(ctx["start"])
            ex.set_end_date(ctx["end"])
            ex.set_time_period(ctx["time_period"])
            ex.set_user_group(ctx["group"])
            ex.set_version(3.0)
            suc = True
            if args and args.color_ratings:
                ex.set_game_count(args.game_count)
                ex.set_color_ratings(args.color_ratings)
            else:
                suc, _ = ex.retrieve_17lands_color_ratings()
            if suc:
                success, msg, _ = ex.download_card_data(0)
                if success:
                    self.configuration.card_data.latest_dataset = ex.export_card_data()
                    write_configuration(self.configuration)
                    self._safe_finalize(msg)
                else:
                    self._safe_error(msg)
            else:
                self._safe_error("17Lands Connection Failed")
        except Exception as e:
            self._safe_error(str(e))

    def _safe_finalize(self, msg):
        if threading.current_thread() is threading.main_thread():
            self._finalize_download(msg)
        else:
            self._download_events.put(("success", msg))

    def _safe_error(self, err):
        if threading.current_thread() is threading.main_thread():
            self._handle_error(err)
        else:
            self._download_events.put(("error", err))

    def _finalize_download(self, msg):
        self.btn_dl.configure(state="normal")
        self.progress["value"] = 0
        self._update_table()

        status_str = (
            "DOWNLOAD SUCCESSFUL"
            if "Min Games" not in msg
            else "DOWNLOADED (LIMITED DATA)"
        )
        self.vars["status"].set(status_str)

        # Force UI to fully redraw and settle BEFORE the blocking messagebox appears
        self.update_idletasks()

        messagebox.showinfo("Dataset Download Complete", msg)

        # Defer the main app UI refresh until after the user dismisses the dialog
        if self.on_update_callback:
            self.after(50, self.on_update_callback)

    def _handle_error(self, err):
        self.btn_dl.configure(state="normal")
        self.progress["value"] = 0
        self.vars["status"].set("DOWNLOAD FAILED")
        messagebox.showerror("Download Error", err)
