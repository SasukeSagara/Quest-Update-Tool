import os
import queue
import subprocess
from threading import Thread
from tkinter import *
from tkinter import ttk
from datetime import datetime
from tkinter.filedialog import askopenfilename
from tkinter.messagebox import askyesno, showerror, showinfo, showwarning

import requests

from firmware_archive import (
    META_DEVICE_PAGES,
    FirmwareLink,
    fetch_firmware_links,
    get_firmware_page_for_device,
    sort_firmware_links_by_version,
)


class CustomOutput(Text):
    def write(self, prompt):
        self.insert(END, f"{prompt}\n")
        self.see(END)

    def flush(self):
        pass

    def fileno(self):
        return 1


def progress_update(downloaded, total, width=None):
    """
    Обновление прогресса скачивания, вызывается из фонового потока wget.
    Передаём событие в очередь, а UI обновляем в главном потоке.
    """
    if total is None or total <= 0:
        return
    progress_queue.put(("progress", {"current": downloaded, "total": total}))


def select_adb():
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
        adb_status_var.set("ADB выбран, нажмите «Проверить подключение по ADB».")
        button_check["state"] = "normal"
        update_step_label(1)
        text_out.insert(END, f"[INFO] Выбран путь к ADB: {file}\n")
    else:
        showwarning("Драйвер не выбран!", "Сначала выберете драйвер!")
        text_out.insert(END, "[WARN] Попытка выбора ADB без указания файла.\n")


def check_connect_func():
    global adb_ok
    # проверяем наличие подключённого устройства
    text_out.insert(END, "[INFO] Проверка подключения по ADB...\n")
    if not os.path.isfile(file):
        showerror("ADB не найден", "Указанный adb.exe не существует.")
        adb_status_var.set("ADB не найден по указанному пути.")
        text_out.insert(END, "[ERROR] Указанный adb.exe не существует.\n")
        return

    err = ""
    try:
        with subprocess.Popen(
            [file, "devices"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        ) as connected_to_adb:
            devices_output, devices_err = connected_to_adb.communicate()
    except FileNotFoundError:
        showerror("ADB не найден", "Не удалось запустить adb. Проверьте путь к файлу.")
        adb_status_var.set("Не удалось запустить ADB.")
        text_out.insert(END, "[ERROR] Не удалось запустить adb. Проверьте путь к файлу.\n")
        return

    lines = [line.strip() for line in devices_output.splitlines() if line.strip()]
    # Ожидаемый формат:
    # List of devices attached
    # SERIAL_NUMBER    device
    has_device = any("device" in line and not line.lower().startswith("list of") for line in lines)
    if not has_device:
        showerror("Шлем не обнаружен!", "Шлем не обнаружен!")
        device_status_var.set("Шлем не обнаружен по ADB.")
        text_out.insert(END, "[ERROR] adb не видит ни одного подключённого устройства.\n")
        return

    # если устройство есть — пробуем узнать модель
    try:
        with subprocess.Popen(
            [file, "shell", "getprop", "ro.product.model"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as model_proc:
            model_raw, model_err = model_proc.communicate()
    except Exception:
        model_raw = ""

    model = (model_raw or "").strip().lower()
    if model:
        text_out.insert(END, f"[INFO] Модель устройства по ADB: {model}\n")

    # простое сопоставление модели к нашим вариантам
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
    adb_status_var.set("ADB успешно проверен.")
    device_status_var.set(f"Обнаружено устройство: {helmet_var.get()}")
    status_text_var.set("Шаг 2: устройство подключено. Выберите или скачайте прошивку.")
    button_download_fw["state"] = "normal"
    button_choose_fw["state"] = "normal"
    update_step_label(2)

    button_run["state"] = "normal"
    text_out.insert(END, "[OK] Устройство обнаружено, ADB работает.\n")


def _try_detect_device_silent() -> bool:
    """
    Фоновая проверка наличия устройства без показa диалогов.
    Обновляет надписи/combobox, но не трогает шаги.
    Возвращает True, если устройство найдено.
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
    has_device = any("device" in line and not line.lower().startswith("list of") for line in lines)
    if not has_device:
        if device_status_var.get() != "Устройство не обнаружено":
            text_out.insert(END, "[INFO] Устройство отключено.\n")
        device_status_var.set("Устройство не обнаружено")
        return False

    # есть устройство, узнаем модель
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
    device_status_var.set(f"Обнаружено устройство: {helmet_var.get()}")
    adb_status_var.set("ADB подключен (обнаружено устройство).")
    button_download_fw["state"] = "normal"
    button_choose_fw["state"] = "normal"
    if firmware_ready:
        button_run["state"] = "normal"

    # Переходим к шагу 2, если раньше устройство было "не обнаружено"
    if prev_status != device_status_var.get():
        update_step_label(2)
        status_text_var.set("Шаг 2: устройство подключено. Выберите или скачайте прошивку.")
        text_out.insert(
            END,
            f"[INFO] Автоопределение устройства: {helmet_var.get()} (через adb devices).\n",
        )
    return True


def poll_device_status():
    """
    Периодический опрос adb для авто-обновления статуса устройства.
    Не показывает диалогов, только обновляет подписи.
    """
    try:
        _try_detect_device_silent()
    finally:
        # повторяем опрос раз в 5 секунд
        root.after(5000, poll_device_status)


def download_file_with_progress(url: str, out_path: str):
    """
    Скачивание файла с отображением прогресса и поддержкой отмены.
    Работает в отдельном потоке.
    """
    global download_in_progress, download_cancelled
    try:
        with requests.get(url, stream=True, timeout=30) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length", "0") or "0")
            downloaded = 0

            with open(out_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if download_cancelled:
                        break
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        progress_queue.put(
                            (
                                "progress",
                                {
                                    "current": downloaded,
                                    "total": total,
                                },
                            )
                        )

        if download_cancelled:
            # Удаляем недокачанный файл
            try:
                if os.path.exists(out_path):
                    os.remove(out_path)
            except OSError:
                pass
            progress_queue.put(
                (
                    "log",
                    {"text": "[INFO] Загрузка прошивки отменена пользователем.\n"},
                )
            )
            progress_queue.put(
                (
                    "download_finished",
                    {"reason": "cancelled"},
                )
            )
        else:
            progress_queue.put(
                (
                    "log",
                    {"text": "[OK] Загрузка прошивки завершена.\n"},
                )
            )
            # Основной обработчик прогресса уже покажет 100% и сообщение
            progress_queue.put(
                (
                    "download_finished",
                    {"reason": "completed"},
                )
            )
    except Exception as e:
        progress_queue.put(
            (
                "dialog_error",
                {
                    "title": "Ошибка сети",
                    "message": f"Ошибка при скачивании прошивки:\n{e}",
                },
            )
        )
        progress_queue.put(
            (
                "download_finished",
                {"reason": "error"},
            )
        )
    finally:
        download_in_progress = False
        download_cancelled = False


def cancel_download():
    """
    Обработчик кнопки отмены загрузки.
    """
    global download_cancelled
    if not download_in_progress:
        return
    download_cancelled = True
    status_text_var.set("Отмена загрузки прошивки...")
    text_out.insert(END, "[INFO] Отмена загрузки по запросу пользователя...\n")


def run_update(adb_path: str, firmware_path: str):
    progress_queue.put(
        (
            "log",
            {
                "text": f"{adb_path}\n\"{adb_path}\" sideload \"{firmware_path}\"\n",
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
            ("dialog_error", {"title": "ADB не найден", "message": "Не удалось запустить adb для прошивки."})
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
download_in_progress = False
download_cancelled = False


def find_existing_firmware():
    firm_dir = "./files"
    if not os.path.isdir(firm_dir):
        return None
    for name in os.listdir(firm_dir):
        lowered = name.lower()
        if lowered.endswith(".zip") and ("quest" in lowered or "firmware" in lowered):
            return os.path.join(firm_dir, name)
    return None


def select_firmware_link_ui(links: list[FirmwareLink]) -> str | None:
    """
    Показываем окно с выбором версии прошивки и возвращаем href выбранной ссылки.
    Если пользователь закрыл окно/отменил выбор — возвращаем None.
    """
    # подстрахуемся: если всего одна ссылка, просто берём её
    if len(links) == 1:
        return links[0].href

    # сортируем по версии (новые сверху)
    sorted_links = sort_firmware_links_by_version(links)

    win = Toplevel(root)
    win.title("Выбор версии прошивки")
    win.transient(root)
    win.geometry("950x420+640+320")

    container = ttk.Frame(win, padding=10)
    container.pack(fill="both", expand=True)
    container.rowconfigure(1, weight=1)
    container.columnconfigure(0, weight=1)

    header_text = (
        "Выберите версию прошивки для загрузки.\n"
        "Отсортируйте по дате или версии, затем дважды кликните по строке или нажмите «OK»."
    )
    ttk.Label(container, text=header_text, justify="left").grid(
        row=0, column=0, sticky="w", pady=(0, 8)
    )

    # Таблица с колонками как на сайте (доп. скрытая колонка для href)
    columns = ("incremental", "version", "runtime", "build_date", "href")
    tree = ttk.Treeview(
        container,
        columns=columns,
        show="headings",
        height=min(len(sorted_links), 14),
    )

    # Настраиваем заголовки
    tree.heading("incremental", text="Incremental")
    tree.heading("version", text="Версия")
    tree.heading("runtime", text="Runtime")
    tree.heading("build_date", text="Дата сборки")

    # Скрытая колонка с href, без заголовка
    tree.heading("href", text="")

    tree.column("incremental", width=150, anchor="w")
    tree.column("version", width=190, anchor="w")
    tree.column("runtime", width=190, anchor="w")
    tree.column("build_date", width=220, anchor="w")
    tree.column("href", width=0, stretch=False)

    # Скроллбар
    scrollbar = ttk.Scrollbar(container, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)

    tree.grid(row=1, column=0, sticky="nsew")
    scrollbar.grid(row=1, column=1, sticky="ns")

    for link in sorted_links:
        tree.insert(
            "",
            END,
            values=(
                link.incremental,
                link.version,
                link.runtime_version,
                link.build_date,
                link.href,
            ),
        )

    # Сортировка по клику на заголовок
    sort_directions = {"incremental": False, "version": False, "runtime": False, "build_date": False}

    def sort_by(col: str):
        reverse = sort_directions[col]
        sort_directions[col] = not reverse

        data = [
            (tree.set(item, col), item)
            for item in tree.get_children("")
        ]

        if col == "incremental":
            # Пытаемся сортировать как числа, иначе как строки
            def key_func(x):
                val = x[0]
                try:
                    return int(val)
                except ValueError:
                    return val

            data.sort(key=key_func, reverse=reverse)
        elif col == "build_date":
            # Преобразуем текст даты вида "Wed Mar 4 01:31:17 PST 2026" в сортируемый ключ
            def parse_date(val: str) -> datetime | str:
                parts = val.split()
                if len(parts) >= 6:
                    # Убираем таймзону, чтобы не споткнуться о PST/PDT
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
            showwarning("Не выбрана версия", "Сначала выберите версию прошивки.")
            return
        item_id = sel[0]
        href_value = tree.set(item_id, "href")
        selected["href"] = href_value
        win.destroy()

    def on_cancel():
        selected["href"] = None
        win.destroy()

    def on_double_click(event):
        if tree.selection():
            on_ok()

    tree.bind("<Double-1>", on_double_click)

    btn_frame = ttk.Frame(container)
    btn_frame.grid(row=2, column=0, columnspan=2, sticky="e", pady=(8, 0))

    ttk.Button(btn_frame, text="Отмена", command=on_cancel, width=12).pack(
        side="right", padx=(5, 0)
    )
    ttk.Button(btn_frame, text="OK", command=on_ok, width=12).pack(
        side="right", padx=(5, 0)
    )

    win.grab_set()
    root.wait_window(win)
    return selected["href"]


root = Tk()
root.title("Meta Quest Updater")
try:
    root.iconbitmap("./files/favicon.ico")
except Exception:
    pass
# Чуть более широкое окно под новую компоновку
root.geometry("800x540+640+320")
root.minsize(780, 520)
root.columnconfigure(0, weight=1)
root.rowconfigure(2, weight=1)

style = ttk.Style(root)
try:
    # Попробуем использовать современную тему, если доступна
    style.theme_use("vista")
except Exception:
    pass

default_font = ("Segoe UI", 9)
root.option_add("*Font", default_font)

HELMET_VERSIONS = dict(META_DEVICE_PAGES)

helmet_var = StringVar(value="Quest 3")


def start_update():
    global firmware_filename
    if not firmware_filename:
        showwarning("Прошивка не найдена!", "Сначала скачайте или выберите прошивку.")
        return
    status_text_var.set("Шаг 4: идёт прошивка устройства...")
    update_step_label(4)
    text_out.insert(END, f"[INFO] Запуск прошивки через ADB sideload для файла: {firmware_filename}\n")
    Thread(target=lambda: run_update(str(file), str(firmware_filename)), daemon=True).start()


def download_firmware():
    global firmware_filename, firmware_ready, download_in_progress, download_cancelled
    device = helmet_var.get()
    page_slug = get_firmware_page_for_device(device)
    if not page_slug:
        showerror("Ошибка", f"Неизвестное устройство: {device}")
        return
    text_out.insert(END, "Получение списка прошивок...\n")
    status_text_var.set("Получение списка доступных прошивок...")
    try:
        links = fetch_firmware_links(page_slug)
    except Exception as e:
        showerror("Ошибка сети", f"Не удалось получить список прошивок:\n{e}")
        return

    if not links:
        showerror("Ошибка", "Не удалось найти ссылки на прошивки.")
        return

    href = select_firmware_link_ui(links)
    if not href:
        showinfo("Отмена", "Загрузка прошивки отменена пользователем.")
        return

    res = href
    text_out.insert(END, f"Выбрана ссылка прошивки: {res}\n")
    text_out.insert(END, "Скачивание прошивки...\n")

    # Имя файла берём из URL (последний сегмент пути)
    filename = os.path.basename(res.split("?")[0]) or "firmware.zip"
    firm_dir = "./files"
    os.makedirs(firm_dir, exist_ok=True)
    downloaded_file = os.path.join(firm_dir, filename)

    firmware_filename = downloaded_file
    firmware_ready = False
    status_text_var.set("Идёт скачивание прошивки...")
    download_in_progress = True
    download_cancelled = False
    button_cancel_download["state"] = "normal"

    Thread(
        target=download_file_with_progress,
        args=(res, downloaded_file),
        daemon=True,
    ).start()

    text_out.insert(END, f"Загрузка начата: {downloaded_file}\n")
    selected_firmware_label["text"] = filename


def choose_firmware_file():
    global firmware_filename, firmware_ready
    path = askopenfilename(
        filetypes=[("ZIP archives", "*.zip"), ("All files", "*.*")],
        title="Выбор файла прошивки",
    )
    if path:
        firmware_filename = path
        selected_firmware_label["text"] = os.path.basename(path)
        firmware_ready = True
        status_text_var.set("Выбран локальный файл прошивки.")
        update_step_label(3)
        if adb_ok:
            button_run["state"] = "normal"


def process_queue():
    while True:
        try:
            event, payload = progress_queue.get_nowait()
        except queue.Empty:
            break

        if event == "progress":
            current = payload["current"]
            total = payload["total"]
            value = current / total * 100
            progress["value"] = value
            global_progress["value"] = value
            percent["text"] = f"{int(value)}%"
            if current >= total:
                showinfo("Прошивка скачана.", "Прошивка скачана.")
                status_text_var.set("Шаг 3: прошивка скачана, можно запускать прошивку устройства.")
                update_step_label(3)
                globals()["firmware_ready"] = True
                if globals().get("adb_ok"):
                    button_run["state"] = "normal"
        elif event == "log":
            text = payload["text"]
            text_out.insert(END, text)
        elif event == "dialog_error":
            showerror(payload["title"], payload["message"])
        elif event == "download_finished":
            # Завершение/отмена загрузки: сбрасываем кнопку и при необходимости файл
            reason = payload.get("reason")
            if reason == "cancelled":
                status_text_var.set("Загрузка прошивки отменена.")
                firmware_filename = None
                selected_firmware_label["text"] = "Файл прошивки не выбран"
                progress["value"] = 0
                global_progress["value"] = 0
                percent["text"] = "0%"
            button_cancel_download["state"] = "disabled"

    root.after(100, process_queue)


step_var = StringVar(value="1")


step_text_var = StringVar(value="Шаг 1 из 4")


def update_step_label(step: int):
    step_var.set(str(step))
    step_text_var.set(f"Шаг {step} из 4")


# ---- Верхняя панель: заголовок и компактный шаг ----
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

# ---- Центральная часть: слева ADB/устройство, справа прошивка ----
center_frame = ttk.Frame(root, padding=(10, 10, 10, 5))
center_frame.grid(row=1, column=0, sticky="ew")
center_frame.columnconfigure(0, weight=1)
center_frame.columnconfigure(1, weight=1)

# Блок ADB и устройство
adb_frame = ttk.LabelFrame(center_frame, text="ADB и устройство", padding=10)
adb_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
adb_frame.columnconfigure(1, weight=1)

adb_path_var = StringVar(value=file)

ttk.Label(adb_frame, text="Путь к ADB:").grid(row=0, column=0, sticky="w")
adb_entry = ttk.Entry(adb_frame, textvariable=adb_path_var)
adb_entry.grid(row=0, column=1, sticky="ew", padx=(5, 0))
button_select = ttk.Button(adb_frame, text="Обзор...", command=select_adb)
button_select.grid(row=0, column=2, padx=(5, 0))

adb_status_var = StringVar(value="ADB не проверен")
adb_status_label = ttk.Label(adb_frame, textvariable=adb_status_var)
adb_status_label.grid(row=1, column=0, columnspan=3, sticky="w", pady=(5, 0))

button_check = ttk.Button(
    adb_frame,
    text="Проверить подключение по ADB",
    command=check_connect_func,
    )
button_check.grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))

# Изначально разрешим проверку, если путь к adb указывает на существующий файл
if os.path.isfile(adb_path_var.get()):
    button_check["state"] = "normal"
else:
    button_check["state"] = "disabled"

device_status_var = StringVar(value="Устройство не обнаружено")
device_status_label = ttk.Label(adb_frame, textvariable=device_status_var)
device_status_label.grid(row=3, column=0, columnspan=3, sticky="w", pady=(5, 0))

ttk.Label(adb_frame, text="Устройство:").grid(row=4, column=0, sticky="w", pady=(8, 0))
helmet_selector = ttk.Combobox(
    adb_frame,
    textvariable=helmet_var,
    values=list(HELMET_VERSIONS.keys()),
    state="readonly",
    width=18,
)
helmet_selector.grid(row=4, column=1, columnspan=2, sticky="w", padx=(5, 0), pady=(8, 0))

# Блок прошивки
firmware_frame = ttk.LabelFrame(center_frame, text="Прошивка", padding=10)
firmware_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
firmware_frame.columnconfigure(0, weight=1)
firmware_frame.columnconfigure(1, weight=1)
firmware_frame.columnconfigure(2, weight=0)

button_download_fw = ttk.Button(
    firmware_frame, text="Скачать", command=download_firmware
)
button_choose_fw = ttk.Button(
    firmware_frame, text="Выбрать файл…", command=choose_firmware_file
)
button_cancel_download = ttk.Button(
    firmware_frame,
    text="Отменить",
    command=cancel_download,
    state="disabled",
)

button_download_fw.grid(row=0, column=0, sticky="w")
button_choose_fw.grid(row=0, column=1, sticky="w", padx=(5, 0))
button_cancel_download.grid(row=0, column=2, sticky="e", padx=(10, 0))

selected_firmware_label = ttk.Label(
    firmware_frame, text="Файл прошивки не выбран"
)
selected_firmware_label.grid(row=1, column=0, columnspan=3, sticky="w", pady=(10, 0))

progress = ttk.Progressbar(
    firmware_frame, orient="horizontal", mode="determinate"
)
progress.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
firmware_filename = find_existing_firmware()
percent = ttk.Label(firmware_frame, text="100%" if firmware_filename else "0%")
percent.grid(row=2, column=2, sticky="e", pady=(10, 0))

if firmware_filename:
    selected_firmware_label["text"] = firmware_filename

# ---- Нижняя часть: лог и общий прогресс ----
bottom_frame = ttk.Frame(root, padding=(10, 0, 10, 10))
bottom_frame.grid(row=2, column=0, sticky="nsew")
bottom_frame.rowconfigure(0, weight=1)
bottom_frame.columnconfigure(0, weight=1)

log_frame = ttk.LabelFrame(bottom_frame, text="Лог", padding=5)
log_frame.grid(row=0, column=0, sticky="nsew")
log_frame.rowconfigure(0, weight=1)
log_frame.columnconfigure(0, weight=1)

text_out = CustomOutput(
    log_frame, background="#111111", foreground="#d0ffd0"
)
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

status_text_var = StringVar(value="Готов к работе")
status_label = ttk.Label(status_frame, textvariable=status_text_var)
status_label.grid(row=1, column=0, sticky="w", pady=(2, 0))

button_run = ttk.Button(
    status_frame,
    text="Запустить прошивку",
    command=start_update,
    state="disabled",
)
button_run.grid(row=0, column=1, rowspan=2, padx=(10, 0))

root.after(100, process_queue)
root.after(3000, poll_device_status)

if __name__ == "__main__":
    root.mainloop()
