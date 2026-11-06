"""批量图片压缩窗口。

这个模块独立于原有的裁剪流程，作为一个附加功能使用。压缩档位使用
JPEG quality 70/50/30 表示高/中/低质量，图片像素尺寸保持不变。
"""

import os
import threading
import tkinter as tk
from io import BytesIO
from tkinter import filedialog, messagebox, ttk
from typing import Tuple

from PIL import Image, ImageOps
from dnd_support import install_file_drop, parse_dropped_files


COMPRESSION_PRESETS = (
    ("高像素 / 高质量（70%）", 70),
    ("中像素 / 中质量（50%）", 50),
    ("低像素 / 低质量（30%）", 30),
)
IMAGE_FILETYPES = [
    ("图片文件", "*.png;*.jpg;*.jpeg;*.bmp;*.tiff;*.webp"),
    ("所有文件", "*.*"),
]
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp"}


def _unique_output_path(output_dir: str, stem: str, quality: int) -> str:
    """生成不会覆盖已有文件的 JPEG 输出路径。"""
    base = os.path.join(output_dir, f"{stem}_{quality}pct")
    output_path = f"{base}.jpg"
    index = 1
    while os.path.exists(output_path):
        output_path = f"{base}_{index}.jpg"
        index += 1
    return output_path


def compress_image(input_path: str, output_path: str, quality: int) -> Tuple[int, int]:
    """将一张图片压缩为 JPEG，并返回 (原始字节数, 输出字节数)。"""
    quality = max(30, min(70, int(quality)))
    original_bytes = os.path.getsize(input_path)

    with Image.open(input_path) as source:
        # 修正手机照片的 EXIF 方向，避免压缩后方向发生变化。
        image = ImageOps.exif_transpose(source).convert("RGBA")
        compress_pil_image(image, output_path, quality)
    return original_bytes, os.path.getsize(output_path)


def compress_pil_image(image: Image.Image, output_path: str, quality: int) -> int:
    """将内存中的图片压缩为 JPEG，返回输出字节数。"""
    quality = max(30, min(70, int(quality)))
    rgba_image = image.convert("RGBA")
    background = Image.new("RGB", rgba_image.size, (255, 255, 255))
    background.paste(rgba_image, mask=rgba_image.getchannel("A"))

    buffer = BytesIO()
    background.save(
        buffer,
        format="JPEG",
        quality=quality,
        optimize=True,
        progressive=True,
        subsampling=2,
    )
    with open(output_path, "wb") as output_file:
        output_file.write(buffer.getvalue())
    return os.path.getsize(output_path)


def compress_pil_image_to_target_size(
    image: Image.Image, output_path: str, target_kb: int, quality_start: int = 70
) -> int:
    """尽量将图片压缩到目标 KB 以内，返回实际输出字节数。"""
    target_bytes = max(1, int(target_kb)) * 1024
    quality_start = max(30, min(70, int(quality_start)))
    rgba_image = image.convert("RGBA")
    rgb_image = Image.new("RGB", rgba_image.size, (255, 255, 255))
    rgb_image.paste(rgba_image, mask=rgba_image.getchannel("A"))

    best_data = None
    current = rgb_image
    # 先用 1 个质量值的步进寻找最接近目标的结果；仍超出目标时再缩小像素尺寸。
    for _ in range(12):
        closest_under_data = None
        closest_under_size = 0
        for quality in range(quality_start, 9, -1):
            buffer = BytesIO()
            current.save(
                buffer,
                format="JPEG",
                quality=quality,
                optimize=True,
                progressive=True,
                subsampling=2,
            )
            data = buffer.getvalue()
            if best_data is None or len(data) < len(best_data):
                best_data = data
            if len(data) <= target_bytes:
                if len(data) > closest_under_size:
                    closest_under_data = data
                    closest_under_size = len(data)

        if closest_under_data is not None:
            with open(output_path, "wb") as output_file:
                output_file.write(closest_under_data)
            return len(closest_under_data)

        width, height = current.size
        if width <= 64 or height <= 64:
            break
        scale = 0.85
        current = current.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))),
            Image.LANCZOS,
        )

    # 极小目标可能无法严格满足，输出尝试过程中的最小结果。
    if best_data is None:
        raise ValueError("无法生成压缩图片")
    with open(output_path, "wb") as output_file:
        output_file.write(best_data)
    return len(best_data)


class CompressionWindow:
    """批量压缩图片的独立窗口。"""

    def __init__(self, master):
        self.master = master
        self.window = tk.Toplevel(master)
        self.window.title("批量压缩图片")
        self.window.geometry("780x530")
        self.window.minsize(700, 460)
        self.window.transient(master)
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        self.file_list = []
        self.processing = False

        self._build_ui()

    def _build_ui(self):
        top_frame = tk.Frame(self.window)
        top_frame.pack(pady=8)
        self.btn_select = tk.Button(
            top_frame, text="批量导入图片...", command=self.select_images
        )
        self.btn_select.pack(side="left", padx=6)
        self.btn_clear = tk.Button(
            top_frame, text="清空列表", command=self.clear_list
        )
        self.btn_clear.pack(side="left", padx=6)

        list_frame = tk.Frame(self.window)
        list_frame.pack(fill="both", expand=True, padx=10)
        self.listbox = tk.Listbox(list_frame, height=12)
        self.listbox.pack(side="left", fill="both", expand=True)
        self.drop_enabled = install_file_drop(self.listbox, self.handle_dropped_files)
        scrollbar = tk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        scrollbar.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=scrollbar.set)
        drop_hint = "支持将图片直接拖入列表" if self.drop_enabled else "可点击按钮导入图片"
        tk.Label(self.window, text=drop_hint, fg="#666666").pack(pady=(2, 0))

        output_frame = tk.Frame(self.window)
        output_frame.pack(fill="x", padx=10, pady=8)
        tk.Label(output_frame, text="导出文件夹:").pack(side="left")
        self.output_entry = tk.Entry(output_frame)
        self.output_entry.pack(side="left", fill="x", expand=True, padx=6)
        tk.Button(output_frame, text="浏览", command=self.select_output_dir).pack(side="left")

        preset_frame = tk.LabelFrame(self.window, text="压缩档位")
        preset_frame.pack(fill="x", padx=10, pady=4)
        self.quality_var = tk.IntVar(value=70)
        for label, quality in COMPRESSION_PRESETS:
            tk.Radiobutton(
                preset_frame,
                text=label,
                variable=self.quality_var,
                value=quality,
            ).pack(side="left", padx=12, pady=6)
        tk.Label(
            preset_frame,
            text="保持原始像素尺寸，导出为 JPEG；透明图片会以白色背景导出",
            fg="#666666",
        ).pack(side="left", padx=8)

        run_frame = tk.Frame(self.window)
        run_frame.pack(fill="x", padx=10, pady=8)
        self.btn_start = tk.Button(
            run_frame, text="批量导出压缩图片", command=self.start_processing
        )
        self.btn_start.pack(side="left", padx=6)
        self.progress = ttk.Progressbar(run_frame, length=400, mode="determinate")
        self.progress.pack(side="left", padx=8, fill="x", expand=True)

        self.log_text = tk.Text(self.window, height=7, state="disabled")
        self.log_text.pack(fill="both", padx=10, pady=4)
        self.status_label = tk.Label(self.window, text="等待导入图片...", anchor="w")
        self.status_label.pack(fill="x", padx=10, pady=(0, 8))

    def _set_log(self, text):
        def update():
            self.log_text.config(state="normal")
            self.log_text.insert(tk.END, text + "\n")
            self.log_text.see(tk.END)
            self.log_text.config(state="disabled")

        self.window.after(0, update)

    def _set_status(self, text):
        self.window.after(0, lambda: self.status_label.config(text=text))

    def _set_progress(self, value):
        self.window.after(0, lambda: self.progress.config(value=value))

    def select_images(self):
        if self.processing:
            messagebox.showwarning("处理中", "当前正在压缩图片，请等待完成。", parent=self.window)
            return
        paths = filedialog.askopenfilenames(
            parent=self.window, title="批量导入图片", filetypes=IMAGE_FILETYPES
        )
        if not paths:
            return
        self._add_image_paths(paths)

    def handle_dropped_files(self, data):
        if self.processing:
            return
        paths = [
            path for path in parse_dropped_files(self.listbox, data)
            if os.path.isfile(path)
            and os.path.splitext(path)[1].lower() in IMAGE_EXTENSIONS
        ]
        self._add_image_paths(paths)

    def _add_image_paths(self, paths):
        existing = set(self.file_list)
        added = 0
        for path in paths:
            if path not in existing:
                self.file_list.append(path)
                existing.add(path)
                self.listbox.insert(tk.END, path)
                added += 1
        if added:
            self.status_label.config(text=f"已导入 {len(self.file_list)} 张图片。")

    def clear_list(self):
        if self.processing:
            messagebox.showwarning("处理中", "当前正在压缩图片，不能清空列表。", parent=self.window)
            return
        self.file_list.clear()
        self.listbox.delete(0, tk.END)
        self.progress.config(value=0, maximum=0)
        self.status_label.config(text="已清空列表。")
        self._clear_log()

    def _clear_log(self):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.config(state="disabled")

    def select_output_dir(self):
        output_dir = filedialog.askdirectory(parent=self.window, title="选择导出文件夹")
        if output_dir:
            self.output_entry.delete(0, tk.END)
            self.output_entry.insert(0, output_dir)

    def _set_controls(self, enabled):
        state = "normal" if enabled else "disabled"
        self.btn_select.config(state=state)
        self.btn_clear.config(state=state)
        self.btn_start.config(state=state)

    def start_processing(self):
        if not self.file_list:
            messagebox.showwarning("未选择文件", "请先批量导入至少一张图片。", parent=self.window)
            return
        output_dir = self.output_entry.get().strip()
        if not output_dir:
            messagebox.showwarning("未设置导出目录", "请先选择导出文件夹。", parent=self.window)
            return
        try:
            os.makedirs(output_dir, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("无法创建文件夹", str(exc), parent=self.window)
            return

        self.processing = True
        self._set_controls(False)
        self._clear_log()
        self.progress.config(value=0, maximum=len(self.file_list))
        quality = int(self.quality_var.get())
        paths = list(self.file_list)
        thread = threading.Thread(
            target=self._process_thread,
            args=(paths, output_dir, quality),
            daemon=True,
        )
        thread.start()

    def _process_thread(self, paths, output_dir, quality):
        success_count = 0
        total = len(paths)
        for index, input_path in enumerate(paths, start=1):
            filename = os.path.basename(input_path)
            self._set_status(f"正在压缩 {index}/{total}: {filename}")
            try:
                stem = os.path.splitext(filename)[0]
                output_path = _unique_output_path(output_dir, stem, quality)
                original_bytes, output_bytes = compress_image(input_path, output_path, quality)
                saved_name = os.path.basename(output_path)
                self._set_log(
                    f"{filename} -> {saved_name} | "
                    f"{original_bytes / 1024:.1f} KB -> {output_bytes / 1024:.1f} KB"
                )
                success_count += 1
            except Exception as exc:
                self._set_log(f"失败：{filename} -> {exc}")
            self._set_progress(index)

        def finished():
            self.processing = False
            self._set_controls(True)
            self.status_label.config(text=f"处理完成：成功 {success_count}/{total} 张。")
            if success_count:
                messagebox.showinfo(
                    "压缩完成",
                    f"已完成 {success_count}/{total} 张图片的压缩导出。",
                    parent=self.window,
                )

        self.window.after(0, finished)

    def close(self):
        if self.processing:
            messagebox.showwarning("处理中", "图片正在压缩，请完成后再关闭窗口。", parent=self.window)
            return
        self.window.destroy()
