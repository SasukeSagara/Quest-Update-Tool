### MetaQuestUpdater — Meta Quest headset firmware updater

Lightweight desktop utility built with Python/Tkinter for updating **Meta Quest** headset firmware  
(Quest, Quest 2, Quest 3, Quest 3S, Quest Pro) over ADB with a simple graphical interface.

Heavily inspired by the original [QuestUpdater](https://github.com/IvanProkshin/QuestUpdater_source) project.

---

### Table of contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Running and usage](#running-and-usage)
- [Project structure](#project-structure)
- [The `files` folder](#the-files-folder)
- [Building `.exe` (PyInstaller)](#building-exe-pyinstaller)
- [Warning](#warning)
- [Credits](#credits)
- [Feedback](#feedback)

---

### Features

- **Automatic ADB device detection**
  - Checks that the headset is connected.
  - Detects the model (Quest / Quest 2 / Quest 3 / Quest 3S / Quest Pro) via `ro.product.model`.
  - Shows connection status and hints directly in the UI.

- **Firmware management**
  - Fetches the list of available firmware builds from an online archive (see `firmware_archive.py`).
  - Filters and sorts versions.
  - Lets you pick the desired firmware from a dropdown list.
  - Allows selecting an already downloaded firmware file.

- **Firmware download**
  - Downloads firmware via `requests` with progress indication.
  - `DownloadManager` (`download_manager.py`) handles background downloads, cancellation and errors.

- **Firmware installation**
  - Starts firmware update on the connected headset via ADB.
  - Detailed log output in the text area at the bottom of the window.
  - Step-by-step flow: select ADB → connect device → select/download firmware → flash.

---

### Requirements

- **OS**: Windows 10/11.
- **Python**: 3.10+ (recommended; see `.python-version`).
- **ADB**: installed `adb.exe` (Android Platform Tools).
- **Internet**: required for automatic firmware download.

Python dependencies are listed in `pyproject.toml` (section `project.dependencies`).

---

### Installation

Recommended way is to use [`uv`](https://github.com/astral-sh/uv) and the `sync` command:

```bash
# install uv (if not installed yet)
pip install uv

# install dependencies from pyproject.toml
uv sync
```

> `uv sync` will create and manage a virtual environment automatically – no need  
> to call `python -m venv` / `pip install` manually.

Alternatively, classic `pip` workflow:

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

---

### Running and usage

1. Clone the repository:

```bash
git clone https://github.com/<USER>/<REPO>.git
cd <REPO>
```

2. Install dependencies (prefer `uv sync`, see [Requirements](#requirements)).

3. Run the application:

```bash
uv run main.py
```

4. In the application window:
   - Click the `adb.exe` button and select the path to ADB.
   - Click **Check ADB connection** and make sure the headset is detected.
   - Adjust the headset model in the combobox if necessary.
   - Pick a firmware version from the list or download the latest available one.
   - When the firmware is ready, start the update process.

All messages and errors are shown in the log output field.

---

### Project structure

- `main.py` — main Tkinter GUI, update flow logic, ADB integration.
- `src/quest_update_tool/firmware_archive.py` — fetching and sorting firmware links for Meta Quest and other devices.
- `src/quest_update_tool/download_manager.py` — background download manager (progress, cancel, error handling).
- `files/` — helper files used by the application (ADB binaries and icons).

---

### The `files` folder

By default the repository root contains a `files` directory used by the application:

- `adb.exe` — ADB binary for Windows.
- `AdbWinApi.dll` and `AdbWinUsbApi.dll` — DLLs required by `adb.exe` on Windows.
- `favicon.ico` and other icons in `files/img` — application icons.

If you distribute the built `.exe`, make sure the `files` folder is placed next to it, otherwise ADB may fail to start.

---

### Building `.exe` (PyInstaller)

You can build a Windows executable with `PyInstaller`:

```bash
uv add pyinstaller

uv run pyinstaller `
  --name MetaQuestUpdater `
  --onefile `
  --noconsole `
  --icon ./files/favicon.ico `
  --paths ./src `
  --add-data "files/favicon.ico;files/img" `
  main.py
```

After the build finishes, the executable will be at `dist/MetaQuestUpdater.exe`.  
Place the `files` folder next to `MetaQuestUpdater.exe`.

---

### Warning

- Any firmware operations are performed **at your own risk**.
- Before updating it is strongly recommended to:
  - Charge the headset to at least 50–60%.
  - Do not disconnect the cable while flashing.
  - Make a backup of important data if possible.

The author is not responsible for any possible device damage, data loss or other consequences of incorrect use of this program.

---

### Credits

- **Online firmware archive authors**:  
  The application uses the public firmware catalog provided by the [cocaine.trade](https://cocaine.trade) service  
  (see `firmware_archive.py`) to retrieve lists of available firmware builds for Meta Quest and other devices.  
  Many thanks to the project authors and the community for maintaining and improving the service.

- **QuestUpdater**:  
  This project reuses the main idea and some UX patterns from the original  
  [QuestUpdater](https://github.com/IvanProkshin/QuestUpdater_source) by Ivan Prokshin. Many thanks for the great foundation and inspiration.
---

### Feedback

- **Issues / bug reports**: please create them on GitHub.
- **Pull requests** with improvements and enhancements are very welcome.

