import os
import queue
import subprocess
from threading import Thread
from tkinter import *
from tkinter import ttk
from datetime import datetime
from tkinter.filedialog import askopenfilename
from tkinter.messagebox import askyesno, showerror, showinfo, showwarning

import wget

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
        button_check["state"] = "normal"
        label_path["text"] = file
    else:
        showwarning("Драйвер не выбран!", "Сначала выберете драйвер!")


def check_connect_func():
    # проверяем наличие подключённого устройства
    if not os.path.isfile(file):
        showerror("ADB не найден", "Указанный adb.exe не существует.")
        return

    err = ""
    try:
        with subprocess.Popen(
            [file, "devices"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        ) as connected_to_adb:
            devices_output, devices_err = connected_to_adb.communicate()
    except FileNotFoundError:
        showerror("ADB не найден", "Не удалось запустить adb. Проверьте путь к файлу.")
        return

    if "device" not in devices_output.splitlines()[-1]:
        showerror("Шлем не обнаружен!", "Шлем не обнаружен!")
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

    button_run["state"] = "normal"


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


def find_existing_firmware():
    for name in os.listdir("./"):
        lowered = name.lower()
        if lowered.endswith(".zip") and ("quest" in lowered or "firmware" in lowered):
            return name
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
    win.geometry("950x400+640+320")

    Label(win, text="Выберите версию прошивки:").pack(anchor="w", padx=10, pady=5)

    # Таблица с колонками как на сайте (доп. скрытая колонка для href)
    columns = ("incremental", "version", "runtime", "build_date", "href")
    tree = ttk.Treeview(
        win,
        columns=columns,
        show="headings",
        height=min(len(sorted_links), 12),
    )

    # Настраиваем заголовки
    tree.heading("incremental", text="Incremental")
    tree.heading("version", text="Version")
    tree.heading("runtime", text="Runtime Version")
    tree.heading("build_date", text="Build Date")

    # Скрытая колонка с href, без заголовка
    tree.heading("href", text="")

    tree.column("incremental", width=150, anchor="w")
    tree.column("version", width=180, anchor="w")
    tree.column("runtime", width=180, anchor="w")
    tree.column("build_date", width=220, anchor="w")
    tree.column("href", width=0, stretch=False)

    # Скроллбар
    scrollbar = ttk.Scrollbar(win, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)

    tree.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=(0, 10))
    scrollbar.pack(side="right", fill="y", padx=(0, 10), pady=(0, 10))

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
        win.destroy()

    btn_frame = Frame(win)
    btn_frame.pack(pady=5)

    Button(btn_frame, text="OK", command=on_ok, width=10).pack(side="left", padx=5)
    Button(btn_frame, text="Отмена", command=on_cancel, width=10).pack(side="left", padx=5)

    win.grab_set()
    root.wait_window(win)
    return selected["href"]


root = Tk()
root.title("Quest updater")
root.iconbitmap("./files/favicon.ico")
root.geometry("520x520+710+290")

HELMET_VERSIONS = dict(META_DEVICE_PAGES)

helmet_var = StringVar(value="Quest 3")


def start_update():
    global firmware_filename
    if not firmware_filename:
        showwarning("Прошивка не найдена!", "Сначала скачайте или выберите прошивку.")
        return
    Thread(target=lambda: run_update(str(file), str(firmware_filename)), daemon=True).start()


def download_firmware():
    global firmware_filename
    device = helmet_var.get()
    page_slug = get_firmware_page_for_device(device)
    if not page_slug:
        showerror("Ошибка", f"Неизвестное устройство: {device}")
        return
    text_out.insert(END, "Получение списка прошивок...\n")
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
    downloaded_file = wget.detect_filename(res)
    firmware_filename = downloaded_file
    Thread(
        target=wget.download,
        args=(res,),
        kwargs={"out": downloaded_file, "bar": progress_update},
        daemon=True,
    ).start()
    text_out.insert(END, f"Загрузка начата: {downloaded_file}\n")
    selected_firmware_label["text"] = downloaded_file


def choose_firmware_file():
    global firmware_filename
    path = askopenfilename(
        filetypes=[("ZIP archives", "*.zip"), ("All files", "*.*")],
        title="Выбор файла прошивки",
    )
    if path:
        firmware_filename = path
        selected_firmware_label["text"] = path


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
            percent["text"] = f"{int(value)}%"
            if current >= total:
                showinfo("Прошивка скачана.", "Прошивка скачана.")
        elif event == "log":
            text = payload["text"]
            text_out.insert(END, text)
        elif event == "dialog_error":
            showerror(payload["title"], payload["message"])

    root.after(100, process_queue)


button_select = ttk.Button(root, text="Путь к adb драйверу", command=select_adb)
button_check = ttk.Button(
    root,
    text="Проверить подключение по adb",
    command=check_connect_func,
    state="disabled",
)
button_run = ttk.Button(
    root,
    text="Запустить прошивку",
    command=start_update,
    state="disabled",
)
button_download_fw = ttk.Button(root, text="Скачать прошивку", command=download_firmware)
button_choose_fw = ttk.Button(
    root, text="Выбрать файл прошивки", command=choose_firmware_file
)
label_path = ttk.Label(root)
progress = ttk.Progressbar(root, orient="horizontal", length=430, mode="determinate")
firmware_filename = find_existing_firmware()
percent = ttk.Label(root, text="100%" if firmware_filename else "0%")
text_out = CustomOutput(
    root, width=63, height=20, background="black", foreground="green"
)
selected_firmware_label = ttk.Label(root, text=firmware_filename or "Файл не выбран")

button_select.place(x=10, y=10)
button_check.place(x=10, y=40)
button_run.place(x=10, y=70)
button_download_fw.place(x=160, y=40)
button_choose_fw.place(x=160, y=70)
text_out.place(x=10, y=130)
label_path.place(x=135, y=11)
helmet_selector = ttk.Combobox(
    root,
    textvariable=helmet_var,
    values=list(HELMET_VERSIONS.keys()),
    state="readonly",
    width=15,
)
helmet_selector.place(x=360, y=10)
selected_firmware_label.place(x=10, y=100)
progress.place(x=10, y=470)
percent.place(x=450, y=472)

label_path["text"] = file
if firmware_filename:
    selected_firmware_label["text"] = firmware_filename

root.after(100, process_queue)

if __name__ == "__main__":
    root.mainloop()
