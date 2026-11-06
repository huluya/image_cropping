"""
批量图片裁剪工具（支持自定义尺寸与预设尺寸）
- 依赖: Pillow
    pip install pillow
- 功能：
    1. 选择一张或多张图片（支持批量）
    2. 选择输出文件夹
    3. 支持尺寸预设（例如 1242x2208, 1242x2688）或自定义输入宽/高
    4. 可选择是否对小于目标尺寸的图片放大（保持长宽比）或仅居中填充白边
    5. 可选的文件命名模式：默认保留原名，或使用模式，例如 "prefix_{index}.jpg" 或 "img_{orig}" 等
    6. 批量处理并显示进度

说明：裁剪是严格得到目标像素（例如 1242x2208），不会把图片拉伸变形，会先按比例缩放（如选择放大），然后居中裁剪；若选择“仅填充”，则不会放大，只会在白色背景上居中放置原图并填充到目标尺寸。
打包：
# 1. 建议建虚拟环境
python -m venv venv
.\venv\Scripts\activate

# 2. 安装依赖
pip install --upgrade pip
pip install pillow tkinterdnd2 pyinstaller
打包单文件 exe
pyinstaller --onefile --noconsole --name BatchImage --icon=app.ico batch_image.py
"""

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PIL import Image
from image_compression import compress_pil_image_to_target_size
from dnd_support import create_root, install_file_drop, parse_dropped_files


DEFAULT_PRESETS = [
    (1242, 2208),
    (1242, 2688),
]


def center_crop_to_target(img: Image.Image, target_w: int, target_h: int, strategy: str) -> Image.Image:
    """将 PIL Image 处理为 target 大小。
    strategy: 'upscale' -> 如需放大则按比例放大到覆盖后裁剪
              'pad' -> 不放大，原图居中并在白色背景上填充到目标大小
              'downscale_only' -> 只在图片比目标大时缩小并裁剪；若比目标小则 pad
    """
    w, h = img.size

    if w == target_w and h == target_h:
        return img.copy()

    # 需要按比例的缩放系数（使图片至少覆盖目标）
    scale_cover = max(target_w / w, target_h / h)
    scale_fit = min(target_w / w, target_h / h)

    if strategy == 'pad':
        # 不放大，直接居中放在背景上（如果比目标大，会裁剪）
        if w >= target_w and h >= target_h:
            # 如果原图已经足够大，做居中裁剪
            left = (w - target_w) // 2
            top = (h - target_h) // 2
            return img.crop((left, top, left + target_w, top + target_h))
        else:
            # 原图较小，不放大 -> 在白背景上居中
            background = Image.new('RGB', (target_w, target_h), (255, 255, 255))
            left = (target_w - w) // 2
            top = (target_h - h) // 2
            background.paste(img, (left, top))
            return background

    if strategy == 'downscale_only':
        # 如果图片更大则缩小到覆盖后裁剪；如果更小则 pad
        if w < target_w or h < target_h:
            background = Image.new('RGB', (target_w, target_h), (255, 255, 255))
            left = (target_w - w) // 2
            top = (target_h - h) // 2
            background.paste(img, (left, top))
            return background
        # 否则按 cover 缩放然后裁剪
        new_w = int(round(w * scale_cover))
        new_h = int(round(h * scale_cover))
        resized = img.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - target_w) // 2
        top = (new_h - target_h) // 2
        return resized.crop((left, top, left + target_w, top + target_h))

    # 默认 'upscale'
    new_w = int(round(w * scale_cover))
    new_h = int(round(h * scale_cover))
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


class App:
    def __init__(self, master):
        self.master = master
        master.title("批量图片处理 - 尺寸与像素压缩")
        master.geometry("820x600")

        # 顶部按钮（选择、清空）
        frame_top = tk.Frame(master)
        frame_top.pack(pady=6)
        self.btn_select = tk.Button(frame_top, text="选择图片...", command=self.select_images)
        self.btn_select.pack(side='left', padx=6)
        self.btn_clear = tk.Button(frame_top, text="清空列表", command=self.clear_list)
        self.btn_clear.pack(side='left', padx=6)

        # 列表显示
        self.listbox = tk.Listbox(master, width=110, height=8)
        self.listbox.pack(padx=10)
        self.drop_enabled = install_file_drop(self.listbox, self.handle_dropped_files)
        if self.drop_enabled:
            tk.Label(master, text='提示：可将图片文件直接拖入上方列表', fg='#666666').pack()

        # 输出文件夹选择
        frame_out = tk.Frame(master)
        frame_out.pack(pady=6, fill='x', padx=10)
        tk.Label(frame_out, text="输出文件夹:").pack(side='left')
        self.out_entry = tk.Entry(frame_out)
        self.out_entry.pack(side='left', fill='x', expand=True, padx=6)
        tk.Button(frame_out, text="浏览", command=self.select_out_dir).pack(side='left')

        # 处理方式：默认勾选尺寸处理，保持原有行为
        frame_mode = tk.LabelFrame(master, text='处理方式（至少选择一项）')
        frame_mode.pack(fill='x', padx=10, pady=6)
        self.resize_var = tk.BooleanVar(value=True)
        self.pixel_compress_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            frame_mode, text='尺寸处理（裁剪/填充）', variable=self.resize_var
        ).pack(side='left', padx=10)
        tk.Checkbutton(
            frame_mode, text='像素压缩', variable=self.pixel_compress_var,
            command=self.on_pixel_compress_toggle
        ).pack(side='left', padx=10)
        tk.Label(frame_mode, text='可单独选择，也可同时处理', fg='#666666').pack(side='left', padx=10)

        frame_pixel = tk.Frame(master)
        frame_pixel.pack(fill='x', padx=10)
        tk.Label(frame_pixel, text='压缩档位:').pack(side='left')
        self.pixel_quality_var = tk.IntVar(value=70)
        self.pixel_quality_buttons = []
        for label, quality in (
            ('高像素（70%）', 70), ('中像素（50%）', 50), ('低像素（30%）', 30)
        ):
            button = tk.Radiobutton(
                frame_pixel, text=label, variable=self.pixel_quality_var,
                value=quality
            )
            button.pack(side='left', padx=8)
            self.pixel_quality_buttons.append(button)
        tk.Label(frame_pixel, text='目标占用大小（KB，固定单位）:').pack(side='left', padx=(12, 2))
        self.pixel_target_entry = tk.Entry(frame_pixel, width=12, fg='#777777')
        self.pixel_target_entry.insert(0, '输入目标占用大小')
        self.pixel_target_entry.pack(side='left', padx=4)
        self.pixel_target_entry.bind('<FocusIn>', self._clear_pixel_target_hint)
        self.pixel_target_entry.bind('<FocusOut>', self._restore_pixel_target_hint)
        tk.Label(frame_pixel, text='像素压缩输出为 JPEG', fg='#666666').pack(side='left', padx=6)

        # 预设与自定义尺寸
        frame_size = tk.Frame(master)
        frame_size.pack(fill='x', padx=10, pady=6)

        tk.Label(frame_size, text="尺寸预设:").pack(side='left')
        self.preset_var = tk.StringVar()
        preset_names = [f"{w} x {h}" for w, h in DEFAULT_PRESETS] + ["自定义"]
        self.combobox = ttk.Combobox(frame_size, values=preset_names, state='readonly', width=20, textvariable=self.preset_var)
        self.combobox.current(0)
        self.combobox.pack(side='left', padx=6)
        self.combobox.bind('<<ComboboxSelected>>', self.on_preset_change)

        tk.Label(frame_size, text=" 宽:").pack(side='left')
        self.width_entry = tk.Entry(frame_size, width=8)
        self.width_entry.pack(side='left')
        tk.Label(frame_size, text=" 高:").pack(side='left')
        self.height_entry = tk.Entry(frame_size, width=8)
        self.height_entry.pack(side='left')

        # 设置默认尺寸为第一个预设
        self.width_entry.insert(0, str(DEFAULT_PRESETS[0][0]))
        self.height_entry.insert(0, str(DEFAULT_PRESETS[0][1]))

        # 策略选择
        frame_strategy = tk.Frame(master)
        frame_strategy.pack(fill='x', padx=10)
        tk.Label(frame_strategy, text="当图片小于目标尺寸时:").pack(side='left')
        self.strategy_var = tk.StringVar(value='upscale')
        tk.Radiobutton(frame_strategy, text='放大并裁剪（upscale）', variable=self.strategy_var, value='upscale').pack(side='left', padx=6)
        tk.Radiobutton(frame_strategy, text='仅填充白边（pad）', variable=self.strategy_var, value='pad').pack(side='left', padx=6)
        tk.Radiobutton(frame_strategy, text='只缩小，不放大（downscale_only）', variable=self.strategy_var, value='downscale_only').pack(side='left', padx=6)

        # 命名模式
        frame_name = tk.Frame(master)
        frame_name.pack(fill='x', padx=10, pady=6)
        tk.Label(frame_name, text="命名模式（可选）:").pack(side='left')
        self.name_pattern = tk.Entry(frame_name)
        self.name_pattern.pack(side='left', fill='x', expand=True, padx=6)
        self.name_pattern.insert(0, "{orig}")
        tk.Label(frame_name, text="示例：prefix_{index}.jpg 或 {orig}（保留原名）").pack(side='left')

        # 开始按钮和进度
        frame_run = tk.Frame(master)
        frame_run.pack(pady=8)
        self.btn_start = tk.Button(frame_run, text="开始处理", command=self.start_processing)
        self.btn_start.pack(side='left', padx=6)
        self.progress = ttk.Progressbar(frame_run, length=480)
        self.progress.pack(side='left', padx=6)

        self.status_label = tk.Label(master, text="等待操作...", anchor='w')
        self.status_label.pack(fill='x', padx=10)

        # 内部
        self.file_list = []
        self.processing = False
        self.on_pixel_compress_toggle()

    def on_preset_change(self, _evt=None):
        sel = self.combobox.get()
        if sel == '自定义':
            # 允许编辑
            self.width_entry.config(state='normal')
            self.height_entry.config(state='normal')
        else:
            # 填入预设并禁用编辑
            parts = sel.split('x')
            w = parts[0].strip()
            h = parts[1].strip()
            self.width_entry.config(state='normal')
            self.height_entry.config(state='normal')
            self.width_entry.delete(0, 'end')
            self.height_entry.delete(0, 'end')
            self.width_entry.insert(0, w)
            self.height_entry.insert(0, h)
            self.width_entry.config(state='disabled')
            self.height_entry.config(state='disabled')

    def select_images(self):
        if self.processing:
            messagebox.showwarning('处理中', '当前正在处理图片，请等待完成后再选择或清空。')
            return
        paths = filedialog.askopenfilenames(title='选择图片', filetypes=[('Image files', '*.png;*.jpg;*.jpeg;*.bmp;*.tiff;*.webp'), ('All files', '*.*')])
        if not paths:
            return
        # 支持追加或覆盖（这里采用覆盖：每次选择替换列表）
        self.file_list = list(paths)
        self.listbox.delete(0, tk.END)
        for p in self.file_list:
            self.listbox.insert(tk.END, p)

    def handle_dropped_files(self, data):
        if self.processing:
            return
        allowed = {'.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.webp'}
        paths = [
            p for p in parse_dropped_files(self.listbox, data)
            if os.path.isfile(p) and os.path.splitext(p)[1].lower() in allowed
        ]
        existing = set(self.file_list)
        for p in paths:
            if p not in existing:
                self.file_list.append(p)
                existing.add(p)
                self.listbox.insert(tk.END, p)
        if paths:
            self.status_label.config(text=f'已导入 {len(self.file_list)} 张图片。')

    def clear_list(self):
        if self.processing:
            messagebox.showwarning('处理中', '当前正在处理图片，不能清空列表。')
            return
        self.file_list = []
        self.listbox.delete(0, tk.END)
        self.progress['value'] = 0
        self.progress['maximum'] = 0
        self.status_label.config(text='已清空列表。')
        self.btn_start.config(state='normal')
        self.btn_select.config(state='normal')

    def on_pixel_compress_toggle(self):
        state = 'normal' if self.pixel_compress_var.get() else 'disabled'
        for button in self.pixel_quality_buttons:
            button.config(state=state)
        self.pixel_target_entry.config(state=state)

    def _clear_pixel_target_hint(self, _event=None):
        if self.pixel_target_entry.get() == '输入目标占用大小':
            self.pixel_target_entry.delete(0, tk.END)
            self.pixel_target_entry.config(fg='black')

    def _restore_pixel_target_hint(self, _event=None):
        if not self.pixel_target_entry.get().strip():
            self.pixel_target_entry.insert(0, '输入目标占用大小')
            self.pixel_target_entry.config(fg='#777777')

    def select_out_dir(self):
        d = filedialog.askdirectory(title='选择输出文件夹')
        if d:
            self.out_entry.delete(0, tk.END)
            self.out_entry.insert(0, d)

    def start_processing(self):
        if not self.file_list:
            messagebox.showwarning('未选择文件', '请先选择至少一张图片。')
            return
        if self.processing:
            messagebox.showwarning('处理中', '当前已有处理任务在运行，请等待。')
            return
        out_dir = self.out_entry.get().strip()
        if not out_dir:
            messagebox.showwarning('未设置输出', '请先选择输出文件夹。')
            return
        resize_enabled = bool(self.resize_var.get())
        pixel_compress_enabled = bool(self.pixel_compress_var.get())
        if not resize_enabled and not pixel_compress_enabled:
            messagebox.showwarning('未选择处理方式', '尺寸处理和像素压缩至少选择一项。')
            return

        target_w = target_h = None
        if resize_enabled:
            try:
                target_w = int(self.width_entry.get())
                target_h = int(self.height_entry.get())
                if target_w <= 0 or target_h <= 0:
                    raise ValueError()
            except Exception:
                messagebox.showerror('尺寸错误', '请输入有效的正整数宽度和高度。')
                return

        if not os.path.exists(out_dir):
            try:
                os.makedirs(out_dir)
            except Exception as e:
                messagebox.showerror('无法创建文件夹', str(e))
                return

        self.btn_start.config(state='disabled')
        self.btn_select.config(state='disabled')
        self.processing = True
        self.progress['value'] = 0
        self.progress['maximum'] = len(self.file_list)

        pixel_quality = int(self.pixel_quality_var.get())
        pixel_target_kb = None
        if pixel_compress_enabled:
            target_value = self.pixel_target_entry.get().strip()
            if target_value == '输入目标占用大小' or not target_value:
                messagebox.showerror('目标大小错误', '请输入目标占用大小（KB）。')
                return
            try:
                pixel_target_kb = int(target_value)
                if pixel_target_kb <= 0:
                    raise ValueError()
            except Exception:
                messagebox.showerror('目标大小错误', '请输入有效的正整数目标占用大小（KB）。')
                return
        t = threading.Thread(
            target=self._process_thread,
            args=(out_dir, target_w, target_h, resize_enabled,
                  pixel_compress_enabled, pixel_quality, pixel_target_kb)
        )
        t.daemon = True
        t.start()

    def _process_thread(self, out_dir, target_w, target_h, resize_enabled,
                        pixel_compress_enabled, pixel_quality, pixel_target_kb):
        total = len(self.file_list)
        pattern = self.name_pattern.get().strip() or "{orig}"
        strategy = self.strategy_var.get()

        for i, path in enumerate(self.file_list, start=1):
            try:
                self._set_status(f"处理 {i}/{total}: {os.path.basename(path)}")
                with Image.open(path) as img:
                    # 保证为 RGB 或保留 alpha
                    has_alpha = img.mode in ('RGBA', 'LA')
                    if has_alpha:
                        img = img.convert('RGBA')
                    else:
                        img = img.convert('RGB')

                    if resize_enabled:
                        out_img = center_crop_to_target(img, target_w, target_h, strategy)
                    else:
                        out_img = img.copy()

                    # 组织输出文件名
                    orig_name = os.path.basename(path)
                    orig_noext, orig_ext = os.path.splitext(orig_name)
                    try:
                        target_name = pattern.format(orig=orig_noext, index=i, ext=orig_ext.lstrip('.'))
                    except Exception:
                        target_name = orig_name

                    if pixel_compress_enabled:
                        target_stem = os.path.splitext(target_name)[0]
                        target_name = f"{target_stem}_{pixel_quality}pct.jpg"
                    elif not os.path.splitext(target_name)[1]:
                        target_name = f"{target_name}{orig_ext}"

                    out_path = os.path.join(out_dir, target_name)
                    base, ext = os.path.splitext(out_path)
                    k = 1
                    while os.path.exists(out_path):
                        out_path = f"{base}_{k}{ext}"
                        k += 1

                    if pixel_compress_enabled:
                        compress_pil_image_to_target_size(
                            out_img, out_path, pixel_target_kb, pixel_quality
                        )
                    else:
                        save_params = {}
                        save_format = None
                        if img.mode == 'RGBA' and out_path.lower().endswith('.png'):
                            save_format = 'PNG'
                        else:
                            if out_path.lower().endswith(('.jpg', '.jpeg')):
                                save_format = 'JPEG'
                                save_params['quality'] = 92
                            elif out_path.lower().endswith('.png'):
                                save_format = 'PNG'

                        if save_format in ('JPEG', None) and out_img.mode == 'RGBA':
                            out_img = out_img.convert('RGB')

                        if save_format:
                            out_img.save(out_path, save_format, **save_params)
                        else:
                            out_img.save(out_path)

                self.progress['value'] = i
            except Exception as e:
                print(f"处理失败: {path} -> {e}")
            finally:
                pass

        self.processing = False
        self._set_status('全部处理完成。')
        self.btn_start.config(state='normal')
        self.btn_select.config(state='normal')

    def _set_status(self, text):
        def inner():
            self.status_label.config(text=text)
        self.master.after(0, inner)


if __name__ == '__main__':
    root = create_root()
    app = App(root)
    root.mainloop()
