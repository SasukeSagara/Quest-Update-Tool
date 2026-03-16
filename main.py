"""Tkinter GUI application for updating Meta Quest firmware via ADB and downloads."""

# pylint: disable=missing-function-docstring, invalid-name, global-statement, broad-exception-caught

import os
import queue
import shutil
import subprocess
import sys
import tempfile
import tkinter as tk
from datetime import datetime
from threading import Thread
from tkinter import ttk
from tkinter.filedialog import askopenfilename
from tkinter.messagebox import showerror, showinfo, showwarning

# Add src to sys.path to use src layout without installing the package
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(ROOT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from quest_update_tool.download_manager import (  # noqa: E402  # pylint: disable=wrong-import-position, import-error
    DownloadManager,
    DownloadStatus,
    DownloadTask,
)
from quest_update_tool.firmware_archive import (  # noqa: E402  # pylint: disable=wrong-import-position, import-error
    META_DEVICE_PAGES,
    FirmwareLink,
    fetch_firmware_links,
    get_firmware_page_for_device,
    sort_firmware_links_by_version,
)


class CustomOutput(tk.Text):
    """Text widget that mimics a file-like object for logging to the UI."""

    def write(self, prompt):
        self.insert(tk.END, f"{prompt}\n")
        self.see(tk.END)

    def flush(self):
        pass

    def fileno(self):
        return 1


def progress_update(downloaded, total, _width=None):
    """
    Update download progress callback, called from a background thread.
    Pushes an event to the queue so that the UI can be updated in the main thread.
    """
    if total is None or total <= 0:
        return
    progress_queue.put(("progress", {"current": downloaded, "total": total}))


def select_adb():
    """Ask the user for adb.exe path and update UI state accordingly."""
    global file
    file = askopenfilename(
        filetypes=[
            (
                "Executable files",
                "*.exe",
            )
        ]
    )
    if file:
        adb_path_var.set(file)
        adb_status_var.set('ADB selected, click "Check ADB connection".')
        button_check["state"] = "normal"
        update_step_label(1)
        text_out.insert(tk.END, f"[INFO] Selected ADB path: {file}\n")
    else:
        showwarning("ADB not selected", "Please choose adb.exe first.")
        text_out.insert(tk.END, "[WARN] Attempt to select ADB without a file.\n")


def check_connect_func():
    """Run adb checks, detect connected headset and update UI state."""
    global adb_ok
    # check that there is a connected device
    text_out.insert(tk.END, "[INFO] Checking ADB connection...\n")
    if not os.path.isfile(file):
        showerror("ADB not found", "The specified adb.exe does not exist.")
        adb_status_var.set("ADB not found at the specified path.")
        text_out.insert(tk.END, "[ERROR] The specified adb.exe does not exist.\n")
        return

    try:
        with subprocess.Popen(
            [file, "devices"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        ) as connected_to_adb:
            devices_output, _ = connected_to_adb.communicate()
    except FileNotFoundError:
        showerror("ADB not found", "Failed to start adb. Please check the path.")
        adb_status_var.set("Failed to start ADB.")
        text_out.insert(tk.END, "[ERROR] Failed to start adb. Please check the path.\n")
        return

    lines = [line.strip() for line in devices_output.splitlines() if line.strip()]
    # Expected output format:
    # List of devices attached
    # SERIAL_NUMBER    device
    has_device = any(
        "device" in line and not line.lower().startswith("list of") for line in lines
    )
    if not has_device:
        showerror("Headset not detected", "No headset was detected over ADB.")
        device_status_var.set("Headset not detected over ADB.")
        text_out.insert(tk.END, "[ERROR] adb does not see any connected devices.\n")
        return

    # if the device is present, try to get its model
    try:
        with subprocess.Popen(
            [file, "shell", "getprop", "ro.product.model"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as model_proc:
            model_raw, _model_err = model_proc.communicate()
    except Exception:
        model_raw = ""

    model = (model_raw or "").strip().lower()
    if model:
        text_out.insert(tk.END, f"[INFO] Device model reported by ADB: {model}\n")

    # simple mapping from model string to our known options
    if "quest 2" in model or "quest_2" in model:
        helmet_var.set("Quest 2")
    elif "quest 3s" in model or "quest_3s" in model:
        helmet_var.set("Quest 3S")
    elif "quest 3" in model or "quest_3" in model:
        helmet_var.set("Quest 3")
    elif "quest pro" in model or "quest_pro" in model:
        helmet_var.set("Quest Pro")
    elif "quest" in model:
        helmet_var.set("Quest")

    adb_ok = True
    adb_status_var.set("ADB check succeeded.")
    device_status_var.set(f"Detected device: {helmet_var.get()}")
    status_text_var.set("Step 2: device connected. Choose or download firmware.")
    button_download_fw["state"] = "normal"
    button_choose_fw["state"] = "normal"
    update_step_label(2)

    button_run["state"] = "normal"
    text_out.insert(tk.END, "[OK] Device detected, ADB is working.\n")


def _try_detect_device_silent() -> bool:
    """
    Background device check without showing any dialogs.
    Updates labels/combobox but does not touch the current step.
    Returns True if a device is detected.
    """
    global adb_ok
    if not os.path.isfile(file):
        return False

    try:
        with subprocess.Popen(
            [file, "devices"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        ) as proc:
            devices_output, _ = proc.communicate()
    except Exception:
        return False

    lines = [line.strip() for line in devices_output.splitlines() if line.strip()]
    has_device = any(
        "device" in line and not line.lower().startswith("list of") for line in lines
    )
    if not has_device:
        if device_status_var.get() != "Device not detected":
            text_out.insert(tk.END, "[INFO] Device disconnected.\n")
        device_status_var.set("Device not detected")
        return False

    # device is present, get its model
    try:
        with subprocess.Popen(
            [file, "shell", "getprop", "ro.product.model"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as model_proc:
            model_raw, _ = model_proc.communicate()
    except Exception:
        model_raw = ""

    model = (model_raw or "").strip().lower()
    prev_status = device_status_var.get()

    if "quest 2" in model or "quest_2" in model:
        helmet_var.set("Quest 2")
    elif "quest 3s" in model or "quest_3s" in model:
        helmet_var.set("Quest 3S")
    elif "quest 3" in model or "quest_3" in model:
        helmet_var.set("Quest 3")
    elif "quest pro" in model or "quest_pro" in model:
        helmet_var.set("Quest Pro")
    elif "quest" in model:
        helmet_var.set("Quest")

    adb_ok = True
    device_status_var.set(f"Detected device: {helmet_var.get()}")
    adb_status_var.set("ADB connected (device detected).")
    button_download_fw["state"] = "normal"
    button_choose_fw["state"] = "normal"
    if firmware_ready:
        button_run["state"] = "normal"

    # Move to step 2 if previously the device was "not detected"
    if prev_status != device_status_var.get():
        update_step_label(2)
        status_text_var.set("Step 2: device connected. Choose or download firmware.")
        text_out.insert(
            tk.END,
            f"[INFO] Auto-detected device: {helmet_var.get()} (via adb devices).\n",
        )
    return True


def poll_device_status():
    """
    Periodically poll adb to auto-update device status.
    Does not show dialogs, only updates labels.
    """
    try:
        _try_detect_device_silent()
    finally:
        # repeat the check every 5 seconds
        root.after(5000, poll_device_status)


def run_update(adb_path: str, firmware_path: str):
    """Run `adb sideload` in a background process and stream output to the UI queue."""
    err = ""
    progress_queue.put(
        (
            "log",
            {
                "text": f'{adb_path}\n"{adb_path}" sideload "{firmware_path}"\n',
            },
        )
    )
    try:
        with subprocess.Popen(
            [adb_path, "sideload", firmware_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            universal_newlines=True,
        ) as updater:
            if updater.stdout:
                for line in updater.stdout:
                    progress_queue.put(("log", {"text": line}))
            if updater.stderr:
                err = updater.stderr.read()
    except FileNotFoundError:
        progress_queue.put(
            (
                "dialog_error",
                {
                    "title": "ADB not found",
                    "message": "Failed to start adb for flashing.",
                },
            )
        )
        return

    if err:
        progress_queue.put(
            (
                "log",
                {
                    "text": f"[ERROR] {err}\n",
                },
            )
        )


adb_pth: str
file = "./files/adb.exe"
firmware_filename = None
progress_queue: "queue.Queue[tuple]" = queue.Queue()
adb_ok = False
firmware_ready = False

# Default base directory for downloaded firmware — OS temporary directory
FIRMWARE_BASE_DIR = os.path.join(tempfile.gettempdir(), "MetaQuestUpdater")

# Downloads
downloads_tree: "ttk.Treeview"
downloads: dict[int, DownloadTask] = {}
active_download_manager: DownloadManager | None = None


def find_existing_firmware():
    """Search the firmware directory for an existing ZIP that looks like a Quest firmware."""
    if not os.path.isdir(FIRMWARE_BASE_DIR):
        return None
    for name in os.listdir(FIRMWARE_BASE_DIR):
        lowered = name.lower()
        if lowered.endswith(".zip") and ("quest" in lowered or "firmware" in lowered):
            return os.path.join(FIRMWARE_BASE_DIR, name)
    return None


def select_firmware_link_ui(links: list[FirmwareLink]) -> str | None:
    """
    Show a window to choose a firmware version and return the href of the selected link.
    If the user closes the window / cancels selection, return None.
    """
    # simple shortcut: if there is only one link, just use it
    if len(links) == 1:
        return links[0].href

    # sort by version (newest on top)
    sorted_links = sort_firmware_links_by_version(links)

    win = tk.Toplevel(root)
    win.title("Choose firmware version")
    win.transient(root)
    win.geometry("950x420+640+320")

    container = ttk.Frame(win, padding=10)
    container.pack(fill="both", expand=True)
    container.rowconfigure(1, weight=1)
    container.columnconfigure(0, weight=1)

    header_text = (
        "Select a firmware version to download.\n"
        'Sort by date or version, then double-click a row or press "OK".'
    )
    ttk.Label(container, text=header_text, justify="left").grid(
        row=0, column=0, sticky="w", pady=(0, 8)
    )

    # Table with columns mirroring the website (plus a hidden href column)
    fw_columns = ("incremental", "version", "runtime", "build_date", "href")
    tree = ttk.Treeview(
        container,
        columns=fw_columns,
        show="headings",
        height=min(len(sorted_links), 14),
    )

    # Configure headers
    tree.heading("incremental", text="Incremental")
    tree.heading("version", text="Version")
    tree.heading("runtime", text="Runtime")
    tree.heading("build_date", text="Build date")

    # Hidden href column without a header
    tree.heading("href", text="")

    tree.column("incremental", width=150, anchor="w")
    tree.column("version", width=190, anchor="w")
    tree.column("runtime", width=190, anchor="w")
    tree.column("build_date", width=220, anchor="w")
    tree.column("href", width=0, stretch=False)

    # Scrollbar
    scrollbar = ttk.Scrollbar(container, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)

    tree.grid(row=1, column=0, sticky="nsew")
    scrollbar.grid(row=1, column=1, sticky="ns")

    for link in sorted_links:
        tree.insert(
            "",
            tk.END,
            values=(
                link.incremental,
                link.version,
                link.runtime_version,
                link.build_date,
                link.href,
            ),
        )

    # Column sorting on header click
    sort_directions = {
        "incremental": False,
        "version": False,
        "runtime": False,
        "build_date": False,
    }

    def sort_by(col: str):
        reverse = sort_directions[col]
        sort_directions[col] = not reverse

        data = [(tree.set(item, col), item) for item in tree.get_children("")]

        if col == "incremental":
            # Try to sort as numbers, otherwise as strings
            def key_func(x):
                val = x[0]
                try:
                    return int(val)
                except ValueError:
                    return val

            data.sort(key=key_func, reverse=reverse)
        elif col == "build_date":
            # Parse date text like "Wed Mar 4 01:31:17 PST 2026" into a sortable key
            def parse_date(val: str) -> datetime | str:
                parts = val.split()
                if len(parts) >= 6:
                    # Strip timezone to avoid issues with PST/PDT names
                    no_tz = " ".join(parts[0:4] + parts[5:6])
                    fmt = "%a %b %d %H:%M:%S %Y"
                    try:
                        return datetime.strptime(no_tz, fmt)
                    except Exception:
                        return val
                return val

            data.sort(key=lambda x: parse_date(x[0]), reverse=reverse)
        else:
            data.sort(key=lambda x: x[0], reverse=reverse)

        for index, (_, item) in enumerate(data):
            tree.move(item, "", index)

    tree.heading("incremental", command=lambda: sort_by("incremental"))
    tree.heading("version", command=lambda: sort_by("version"))
    tree.heading("runtime", command=lambda: sort_by("runtime"))
    tree.heading("build_date", command=lambda: sort_by("build_date"))

    selected = {"href": None}

    def on_ok():
        sel = tree.selection()
        if not sel:
            showwarning(
                "No version selected", "Please select a firmware version first."
            )
            return
        item_id = sel[0]
        href_value = tree.set(item_id, "href")
        selected["href"] = href_value
        win.destroy()

    def on_cancel():
        selected["href"] = None
        win.destroy()

    def on_double_click(_event):
        if tree.selection():
            on_ok()

    tree.bind("<Double-1>", on_double_click)

    btn_frame = ttk.Frame(container)
    btn_frame.grid(row=2, column=0, columnspan=2, sticky="e", pady=(8, 0))

    ttk.Button(btn_frame, text="Cancel", command=on_cancel, width=12).pack(
        side="right", padx=(5, 0)
    )
    ttk.Button(btn_frame, text="OK", command=on_ok, width=12).pack(
        side="right", padx=(5, 0)
    )

    win.grab_set()
    root.wait_window(win)
    return selected["href"]


def resource_path(relative: str) -> str:
    """
    Resolve resource paths correctly both in dev mode and inside a PyInstaller executable.
    """
    if getattr(sys, "frozen", False):
        base = getattr(
            sys,
            "_MEIPASS",
            os.path.dirname(sys.executable),
        )
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative)


def auto_select_adb() -> None:
    """
    Try to find a usable adb in this order:
    1) adb from PATH
    2) bundled adb.exe inside the PyInstaller bundle (files/adb.exe)
    3) local files/adb.exe next to the script
    """
    global file

    if "adb_path_var" not in globals():
        return

    candidates: list[str] = []

    adb_from_path = shutil.which("adb")
    if adb_from_path:
        candidates.append(adb_from_path)

    candidates.append(resource_path("files/adb.exe"))
    candidates.append(os.path.join(ROOT_DIR, "files", "adb.exe"))

    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            file = candidate
            adb_path_var.set(candidate)
            if "button_check" in globals():
                button_check["state"] = "normal"
            if "text_out" in globals():
                text_out.insert(tk.END, f"[INFO] Using ADB at: {candidate}\n")
            return


root = tk.Tk()
root.title("Meta Quest Updater")
try:
    root.iconbitmap(resource_path("files/favicon.ico"))
except Exception as e:
    # Print the real reason why the icon cannot be set (visible in dev mode console).
    import traceback

    print("ICON ERROR:", e, file=sys.stderr)
    traceback.print_exc()
# Slightly wider window for the new layout
root.geometry("800x540+640+320")
root.minsize(780, 520)
root.columnconfigure(0, weight=1)
root.rowconfigure(2, weight=1)

style = ttk.Style(root)
try:
    # Try to use a modern theme if available
    style.theme_use("vista")
except Exception:
    pass

default_font = ("Segoe UI", 9)
root.option_add("*Font", default_font)

HELMET_VERSIONS = dict(META_DEVICE_PAGES)

helmet_var = tk.StringVar(value="Quest 3")
firmware_path_var = tk.StringVar(value="")


def start_update():
    """Validate that firmware is ready and start flashing in a background thread."""
    if not firmware_filename:
        showwarning(
            "Firmware not found", "Please download or choose a firmware file first."
        )
        return
    status_text_var.set("Step 4: flashing the device...")
    update_step_label(4)
    text_out.insert(
        tk.END,
        f"[INFO] Starting ADB sideload for file: {firmware_filename}\n",
    )
    Thread(
        target=lambda: run_update(str(file), str(firmware_filename)), daemon=True
    ).start()


def download_firmware():
    """Fetch the firmware list for the selected device and enqueue a download task."""
    device = helmet_var.get()
    page_slug = get_firmware_page_for_device(device)
    if not page_slug:
        showerror("Error", f"Unknown device: {device}")
        return
    text_out.insert(tk.END, "Fetching firmware list...\n")
    status_text_var.set("Fetching list of available firmware builds...")
    try:
        links = fetch_firmware_links(page_slug)
    except Exception as e:
        showerror("Network error", f"Failed to fetch firmware list:\n{e}")
        return

    if not links:
        showerror("Error", "Failed to find any firmware links.")
        return

    href = select_firmware_link_ui(links)
    if not href:
        showinfo("Cancelled", "Firmware download was cancelled by the user.")
        return

    res = href
    text_out.insert(tk.END, f"Selected firmware link: {res}\n")
    text_out.insert(tk.END, "Firmware download added to the queue...\n")

    # Hand off the task to the download manager
    def _enqueue():
        if active_download_manager is None:
            return
        active_download_manager.add_download(res)

    Thread(target=_enqueue, daemon=True).start()


def choose_firmware_file():
    """Let the user pick a local firmware ZIP and update selection state."""
    global firmware_filename, firmware_ready
    path = askopenfilename(
        filetypes=[("ZIP archives", "*.zip"), ("All files", "*.*")],
        title="Choose firmware file",
    )
    if path:
        firmware_filename = path
        firmware_ready = True
        selected_firmware_label["text"] = os.path.basename(path)
        firmware_path_var.set(os.path.abspath(path))
        status_text_var.set("Local firmware file selected.")
        update_step_label(3)
        if adb_ok:
            button_run["state"] = "normal"


def process_queue():
    """Process all pending events from the worker queue and reschedule itself."""
    while True:
        try:
            event, payload = progress_queue.get_nowait()
        except queue.Empty:
            break

        if event == "log":
            text = payload["text"]
            text_out.insert(tk.END, text)
        elif event == "dialog_error":
            showerror(payload["title"], payload["message"])
        elif event == "download_event":
            handle_download_event(payload)

    root.after(100, process_queue)


step_var = tk.StringVar(value="1")


step_text_var = tk.StringVar(value="Step 1 of 4")


def update_step_label(step: int):
    step_var.set(str(step))
    step_text_var.set(f"Step {step} of 4")


# ---- Top panel: title and compact step indicator ----
steps_frame = ttk.Frame(root, padding=(10, 10, 10, 0))
steps_frame.grid(row=0, column=0, sticky="ew")
steps_frame.columnconfigure(0, weight=1)

title_label = ttk.Label(
    steps_frame,
    text="Meta Quest Updater",
    font=("Segoe UI", 11, "bold"),
)
title_label.grid(row=0, column=0, sticky="w")

current_step_label = ttk.Label(
    steps_frame,
    textvariable=step_text_var,
)
current_step_label.grid(row=0, column=1, sticky="e")

# ---- Center area: ADB/device on the left, firmware on the right ----
center_frame = ttk.Frame(root, padding=(10, 10, 10, 5))
center_frame.grid(row=1, column=0, sticky="ew")
center_frame.columnconfigure(0, weight=1)
center_frame.columnconfigure(1, weight=1)

# ADB and device block
adb_frame = ttk.LabelFrame(center_frame, text="ADB and device", padding=10)
adb_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
adb_frame.columnconfigure(1, weight=1)

adb_path_var = tk.StringVar(value=file)

ttk.Label(adb_frame, text="ADB path:").grid(row=0, column=0, sticky="w")
adb_entry = ttk.Entry(adb_frame, textvariable=adb_path_var)
adb_entry.grid(row=0, column=1, sticky="ew", padx=(5, 0))
button_select = ttk.Button(adb_frame, text="Browse...", command=select_adb)
button_select.grid(row=0, column=2, padx=(5, 0))

adb_status_var = tk.StringVar(value="ADB not checked yet")
adb_status_label = ttk.Label(adb_frame, textvariable=adb_status_var)
adb_status_label.grid(row=1, column=0, columnspan=3, sticky="w", pady=(5, 0))

button_check = ttk.Button(
    adb_frame,
    text="Check ADB connection",
    command=check_connect_func,
)
button_check.grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))

# Initially allow the check button only if the ADB path points to an existing file
if os.path.isfile(adb_path_var.get()):
    button_check["state"] = "normal"
else:
    button_check["state"] = "disabled"

device_status_var = tk.StringVar(value="Device not detected")
device_status_label = ttk.Label(adb_frame, textvariable=device_status_var)
device_status_label.grid(row=3, column=0, columnspan=3, sticky="w", pady=(5, 0))

ttk.Label(adb_frame, text="Device:").grid(row=4, column=0, sticky="w", pady=(8, 0))
helmet_selector = ttk.Combobox(
    adb_frame,
    textvariable=helmet_var,
    values=list(HELMET_VERSIONS.keys()),
    state="readonly",
    width=18,
)
helmet_selector.grid(
    row=4, column=1, columnspan=2, sticky="w", padx=(5, 0), pady=(8, 0)
)

# Firmware block
firmware_frame = ttk.LabelFrame(center_frame, text="Firmware", padding=10)
firmware_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
firmware_frame.columnconfigure(0, weight=1)
firmware_frame.columnconfigure(1, weight=1)
firmware_frame.columnconfigure(2, weight=0)

button_download_fw = ttk.Button(
    firmware_frame, text="Download", command=download_firmware
)
button_choose_fw = ttk.Button(
    firmware_frame, text="Choose file…", command=choose_firmware_file
)
button_cancel_download: "ttk.Button"
button_cancel_download = ttk.Button(
    firmware_frame,
    text="Cancel",
    command=lambda: cancel_selected_download(),  # pylint: disable=unnecessary-lambda
    state="disabled",
)

button_download_fw.grid(row=0, column=0, sticky="w")
button_choose_fw.grid(row=0, column=1, sticky="w", padx=(5, 0))
button_cancel_download.grid(row=0, column=2, sticky="e", padx=(10, 0))

selected_firmware_label = ttk.Label(firmware_frame, text="No firmware file selected")
selected_firmware_label.grid(row=1, column=0, columnspan=3, sticky="w", pady=(10, 0))

# Downloads table
downloads_frame = ttk.Frame(firmware_frame)
downloads_frame.grid(row=2, column=0, columnspan=3, sticky="nsew", pady=(5, 0))
firmware_frame.rowconfigure(2, weight=1)

columns = ("id", "filename", "status", "progress")
downloads_tree = ttk.Treeview(
    downloads_frame,
    columns=columns,
    show="headings",
    height=5,
)
downloads_tree.heading("id", text="ID")
downloads_tree.heading("filename", text="File")
downloads_tree.heading("status", text="Status")
downloads_tree.heading("progress", text="Progress")

downloads_tree.column("id", width=40, anchor="center")
downloads_tree.column("filename", width=180, anchor="w")
downloads_tree.column("status", width=90, anchor="center")
downloads_tree.column("progress", width=80, anchor="center")

downloads_tree.grid(row=0, column=0, sticky="nsew")
downloads_scroll = ttk.Scrollbar(
    downloads_frame, orient="vertical", command=downloads_tree.yview
)
downloads_scroll.grid(row=0, column=1, sticky="ns")
downloads_tree.configure(yscrollcommand=downloads_scroll.set)

downloads_frame.rowconfigure(0, weight=1)
downloads_frame.columnconfigure(0, weight=1)


def on_downloads_double_click(_event):
    """Treeview double-click handler to select a completed download as firmware."""
    select_download_for_firmware()


downloads_tree.bind("<Double-1>", on_downloads_double_click)

auto_select_adb()

# Show full path to the selected firmware
ttk.Label(firmware_frame, text="Firmware path:").grid(
    row=3, column=0, sticky="w", pady=(5, 0)
)
firmware_path_entry = ttk.Entry(
    firmware_frame, textvariable=firmware_path_var, state="readonly"
)
firmware_path_entry.grid(row=3, column=1, columnspan=2, sticky="ew", pady=(5, 0))

firmware_filename = find_existing_firmware()

if firmware_filename:
    selected_firmware_label["text"] = os.path.basename(firmware_filename)
    firmware_path_var.set(os.path.abspath(firmware_filename))

# ---- Bottom section: log and overall progress ----
bottom_frame = ttk.Frame(root, padding=(10, 0, 10, 10))
bottom_frame.grid(row=2, column=0, sticky="nsew")
bottom_frame.rowconfigure(0, weight=1)
bottom_frame.columnconfigure(0, weight=1)

log_frame = ttk.LabelFrame(bottom_frame, text="Log", padding=5)
log_frame.grid(row=0, column=0, sticky="nsew")
log_frame.rowconfigure(0, weight=1)
log_frame.columnconfigure(0, weight=1)

text_out = CustomOutput(log_frame, background="#111111", foreground="#d0ffd0")
text_out.grid(row=0, column=0, sticky="nsew")
log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=text_out.yview)
log_scroll.grid(row=0, column=1, sticky="ns")
text_out.configure(yscrollcommand=log_scroll.set)

status_frame = ttk.Frame(bottom_frame)
status_frame.grid(row=1, column=0, sticky="ew", pady=(5, 0))
status_frame.columnconfigure(0, weight=1)

global_progress = ttk.Progressbar(
    status_frame, orient="horizontal", mode="determinate", maximum=100
)
global_progress.grid(row=0, column=0, sticky="ew")

status_text_var = tk.StringVar(value="Ready")
status_label = ttk.Label(status_frame, textvariable=status_text_var)
status_label.grid(row=1, column=0, sticky="w", pady=(2, 0))

button_run = ttk.Button(
    status_frame,
    text="Start flashing",
    command=start_update,
    state="disabled",
)
button_run.grid(row=0, column=1, rowspan=2, padx=(10, 0))


def download_manager_event(event: str, payload: dict) -> None:
    """
    Callback for DownloadManager: put events into the shared queue
    so they can be processed in the Tkinter main thread.
    """
    progress_queue.put(("download_event", {"event": event, "payload": payload}))


def _status_to_text(status: DownloadStatus) -> str:
    mapping = {
        DownloadStatus.QUEUED: "Queued",
        DownloadStatus.DOWNLOADING: "Downloading",
        DownloadStatus.COMPLETED: "Completed",
        DownloadStatus.CANCELLED: "Cancelled",
        DownloadStatus.ERROR: "Error",
    }
    return mapping.get(status, str(status))


def handle_download_event(message: dict) -> None:
    event = message["event"]
    payload = message["payload"]
    task: DownloadTask | None = payload.get("task")

    if task is None:
        return

    downloads[task.id] = task

    # Update or create a row in the Treeview
    item_id = f"task-{task.id}"
    values = (
        task.id,
        task.filename,
        _status_to_text(task.status),
        f"{task.progress}%",
    )
    if item_id in downloads_tree.get_children(""):
        downloads_tree.item(item_id, values=values)
    else:
        downloads_tree.insert("", "end", iid=item_id, values=values)

    # Per-event logic
    if event == "task_started":
        text_out.insert(tk.END, f"[INFO] Download started: {task.filename}\n")
        status_text_var.set("Downloading firmware...")
    elif event == "task_progress":
        # Show progress in the table; keep the global indicator untouched
        # so it does not jump when multiple downloads run in parallel.
        pass
    elif event == "task_completed":
        text_out.insert(tk.END, f"[OK] Download completed: {task.filename}\n")
        status_text_var.set(
            "Firmware downloaded. Double-click the row to select it for flashing."
        )
    elif event == "task_cancelled_finished":
        text_out.insert(tk.END, f"[INFO] Download cancelled: {task.filename}\n")
    elif event == "task_error":
        text_out.insert(
            tk.END, f"[ERROR] Failed to download {task.filename}: {task.error}\n"
        )

    # After each event, recalculate whether there are any active downloads
    has_active = any(t.status == DownloadStatus.DOWNLOADING for t in downloads.values())
    button_cancel_download["state"] = "normal" if has_active else "disabled"


def cancel_selected_download() -> None:
    """Cancel the currently selected download task, if any."""
    if active_download_manager is None:
        return
    sel = downloads_tree.selection()
    if not sel:
        return
    item_id = sel[0]
    task_id = int(downloads_tree.set(item_id, "id"))
    active_download_manager.cancel(task_id)


def select_download_for_firmware() -> None:
    """Use the selected completed download as the active firmware file."""
    global firmware_filename, firmware_ready

    sel = downloads_tree.selection()
    if not sel:
        return
    item_id = sel[0]
    task_id = int(downloads_tree.set(item_id, "id"))
    task = downloads.get(task_id)
    if not task or task.status is not DownloadStatus.COMPLETED:
        return

    firmware_filename = task.path
    firmware_ready = True
    selected_firmware_label["text"] = task.filename
    firmware_path_var.set(os.path.abspath(task.path))
    status_text_var.set("Firmware selected from the downloads list.")
    update_step_label(3)
    if adb_ok:
        button_run["state"] = "normal"


active_download_manager = DownloadManager(FIRMWARE_BASE_DIR, download_manager_event)

root.after(100, process_queue)
root.after(3000, poll_device_status)

if __name__ == "__main__":
    root.mainloop()
