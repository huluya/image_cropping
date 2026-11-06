"""
批量图片裁剪工具（支持自定义尺寸与预设尺寸 + 输出压缩 + 按目标文件大小压缩）
- 依赖: Pillow
    pip install pillow tkinterdnd2
- 功能：
    1. 选择一张或多张图片（支持批量）
    2. 选择输出文件夹
    3. 支持尺寸预设（例如 1242x2208, 1242x2688）或自定义输入宽/高
    4. 可选择是否对小于目标尺寸的图片放大（保持长宽比）或仅居中填充白边
    5. 可选的文件命名模式：默认保留原名，或使用模式，例如 "prefix_{index}.jpg" 或 "img_{orig}" 等
    6. 批量处理并显示进度
    7. 输出压缩：可启用压缩并设置 JPEG 质量（30-95）以及最长边缩放限制（可选）
    8. 新增：可勾选“按目标文件大小压缩（KB）”按钮 —— 无视原始后缀，尽力把输出文件控制在指定大小以内（会尝试 WebP/JPEG/PNG 策略，若需要会改扩展名，最终会在界面上显示实际保存文件名与大小）

说明：默认保存逻辑保持绝大部分原行为不变；只有当你勾选“按目标文件大小压缩”并填写 KB 值时，程序会额外尝试按目标大小压缩输出文件。
"""

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from io import BytesIO
from PIL import Image
from image_compression import compress_pil_image_to_target_size
from dnd_support import create_root, install_file_drop, parse_dropped_files

DEFAULT_PRESETS = [
    (1242, 2208),
    (1242, 2688),
]


def center_crop_to_target(img: Image.Image, target_w: int, target_h: int, strategy: str) -> Image.Image:
    w, h = img.size
    if w == target_w and h == target_h:
        return img.copy()
    scale_cover = max(target_w / w, target_h / h)
    if strategy == 'pad':
        if w >= target_w and h >= target_h:
            left = (w - target_w) // 2
            top = (h - target_h) // 2
            return img.crop((left, top, left + target_w, top + target_h))
        else:
            background = Image.new('RGB', (target_w, target_h), (255, 255, 255))
            left = (target_w - w) // 2
            top = (target_h - h) // 2
            background.paste(img, (left, top))
            return background
    if strategy == 'downscale_only':
        if w < target_w or h < target_h:
            background = Image.new('RGB', (target_w, target_h), (255, 255, 255))
            left = (target_w - w) // 2
            top = (target_h - h) // 2
            background.paste(img, (left, top))
            return background
        new_w = int(round(w * scale_cover))
        new_h = int(round(h * scale_cover))
        resized = img.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - target_w) // 2
        top = (new_h - target_h) // 2
        return resized.crop((left, top, left + target_w, top + target_h))
    new_w = int(round(w * scale_cover))
    new_h = int(round(h * scale_cover))
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


# 原有稳健保存逻辑（保留）
def _save_with_params(out_img: Image.Image, out_path: str, compress_enabled: bool, quality: int):
    try:
        out_img_clean = out_img.copy()
        out_img_clean.info.pop('exif', None)
        out_img_clean.info.pop('icc_profile', None)
        out_img_clean.info.pop('dpi', None)
    except Exception:
        out_img_clean = out_img

    ext_lower = out_path.lower()
    if ext_lower.endswith('.png'):
        save_format = 'PNG'
    elif ext_lower.endswith(('.jpg', '.jpeg')):
        save_format = 'JPEG'
    elif ext_lower.endswith('.webp'):
        save_format = 'WEBP'
    else:
        save_format = 'JPEG' if compress_enabled else 'PNG'

    save_params = {}
    if save_format == 'JPEG':
        if out_img_clean.mode == 'RGBA':
            background = Image.new('RGB', out_img_clean.size, (255, 255, 255))
            background.paste(out_img_clean, mask=out_img_clean.split()[3])
            out_img_clean = background
        else:
            out_img_clean = out_img_clean.convert('RGB')
        if compress_enabled:
            save_params = {
                'quality': max(30, min(95, int(quality))),
                'optimize': True,
                'progressive': True,
                'subsampling': 2
            }
        else:
            save_params = {'quality': max(30, min(95, int(quality)))}
    elif save_format == 'PNG':
        if compress_enabled:
            save_params = {'optimize': True}
        else:
            save_params = {}
    elif save_format == 'WEBP':
        if compress_enabled:
            save_params = {'quality': max(30, min(95, int(quality))), 'method': 6}
        else:
            save_params = {'quality': max(30, min(95, int(quality)))}
    else:
        save_params = {}
    try:
        out_img_clean.save(out_path, save_format, **save_params)
    except TypeError:
        save_params.pop('subsampling', None)
        out_img_clean.save(out_path, save_format, **save_params)
    except Exception as e:
        try:
            out_img_clean.save(out_path)
        except Exception as e2:
            raise e2


# 新增：按目标文件大小压缩（尽力，不受原始后缀限制）
def _save_to_target_size(out_img: Image.Image, out_path: str, target_kb: int, quality_start: int=92, min_quality: int=30, step: int=5, prefer_webp: bool=True):
    """
    尝试把图片保存到 <= target_kb（KB）。
    - 优先尝试 WebP（通常最省空间）
    - 然后尝试迭代 JPEG（从 quality_start 向下）
    - 对 PNG 先尝试 palette (P) 减色并 optimize
    - 若最终使用了不同的格式，会把文件保存为新的扩展（并返回新的路径）
    返回 (实际保存路径, 保存格式, 最终字节数, 使用的 quality 或 None)
    """
    target_bytes = int(target_kb) * 1024
    # 准备干净副本
    try:
        img = out_img.copy()
        img.info.pop('exif', None)
        img.info.pop('icc_profile', None)
        img.info.pop('dpi', None)
    except Exception:
        img = out_img

    base, orig_ext = os.path.splitext(out_path)
    orig_ext = orig_ext.lower()

    # Helper: write buffer to file
    def _write_buf(buf, path):
        with open(path, 'wb') as f:
            f.write(buf.getvalue())
        return os.path.getsize(path)

    # 1) Try WebP if preferred
    if prefer_webp:
        try:
            buf = BytesIO()
            img.save(buf, format='WEBP', quality=quality_start, method=6)
            size = buf.tell()
            if size <= target_bytes:
                new_path = base + '.webp'
                final = _write_buf(buf, new_path)
                print(f"[target] saved webp {final} bytes")
                return new_path, 'WEBP', final, quality_start
            else:
                print(f"[target] webp too big {size} bytes")
        except Exception as e:
            print(f"[target] webp fail {e}")

    # 2) If original ext is png, try palette reduce + optimize
    if orig_ext == '.png':
        try:
            buf = BytesIO()
            # 尝试自适应调色板
            reduced = img.convert('P', palette=Image.ADAPTIVE)
            reduced.save(buf, format='PNG', optimize=True)
            size = buf.tell()
            if size <= target_bytes:
                new_path = base + '.png'
                final = _write_buf(buf, new_path)
                print(f"[target] saved png(palette) {final} bytes")
                return new_path, 'PNG', final, None
            else:
                print(f"[target] png palette too big {size} bytes")
        except Exception as e:
            print(f"[target] png palette fail {e}")

    # 3) Iterative JPEG attempts (从 quality_start 降低)
    q = quality_start
    last_buf = None
    last_size = None
    while q >= min_quality:
        try:
            buf = BytesIO()
            tmp = img
            if tmp.mode == 'RGBA':
                bg = Image.new('RGB', tmp.size, (255,255,255))
                bg.paste(tmp, mask=tmp.split()[3])
                tmp = bg
            else:
                tmp = tmp.convert('RGB')
            tmp.save(buf, format='JPEG', quality=q, optimize=True, progressive=True, subsampling=2)
            size = buf.tell()
            print(f"[target] JPEG try q={q} size={size}")
            if size <= target_bytes:
                new_path = base + '.jpg'
                final = _write_buf(buf, new_path)
                return new_path, 'JPEG', final, q
            last_buf = buf
            last_size = size
            q -= step
        except Exception as e:
            print(f"[target] JPEG fail q={q} {e}")
            q -= step
            continue

    # 4) If none met target, but we have last_buf, write it (best effort)
    if last_buf is not None:
        new_path = base + '.jpg'
        final = _write_buf(last_buf, new_path)
        print(f"[target] JPEG final used size={final}")
        return new_path, 'JPEG', final, q + step

    # 5) 最后兜底：保存 PNG optimize
    try:
        buf = BytesIO()
        img.save(buf, format='PNG', optimize=True)
        new_path = base + ('.png' if orig_ext != '' else '.png')
        final = _write_buf(buf, new_path)
        print(f"[target] PNG fallback size={final}")
        return new_path, 'PNG', final, None
    except Exception as e:
        # 最后再直接保存原路径
        img.save(out_path)
        final = os.path.getsize(out_path)
        print(f"[target] fallback direct save size={final}")
        return out_path, 'UNKNOWN', final, None


class App:
    def __init__(self, master):
        self.master = master
        master.title("批量图片处理 - 尺寸与像素压缩")
        master.geometry("920x760")

        # 顶部按钮（选择、清空）
        frame_top = tk.Frame(master)
        frame_top.pack(pady=6)
        self.btn_select = tk.Button(frame_top, text="选择图片...", command=self.select_images)
        self.btn_select.pack(side='left', padx=6)
        self.btn_clear = tk.Button(frame_top, text="清空列表", command=self.clear_list)
        self.btn_clear.pack(side='left', padx=6)

        # 列表显示
        self.listbox = tk.Listbox(master, width=140, height=12)
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

        # 压缩选项
        frame_compress = tk.LabelFrame(master, text='压缩设置（针对输出）')
        frame_compress.pack(fill='x', padx=10, pady=6)
        self.compress_var = tk.BooleanVar(value=False)
        tk.Checkbutton(frame_compress, text='启用压缩', variable=self.compress_var, command=self.on_compress_toggle).pack(side='left', padx=6)

        tk.Label(frame_compress, text='JPEG质量:').pack(side='left')
        self.quality_scale = tk.Scale(frame_compress, from_=30, to=95, orient='horizontal', length=200)
        self.quality_scale.set(92)
        self.quality_scale.pack(side='left', padx=6)

        tk.Label(frame_compress, text='最长边限制(像素，可选):').pack(side='left')
        self.max_side_entry = tk.Entry(frame_compress, width=8)
        self.max_side_entry.pack(side='left', padx=6)
        tk.Label(frame_compress, text='（留空表示不缩放）').pack(side='left')

        # 新增：按目标大小压缩
        tk.Label(frame_compress, text='  ').pack(side='left')
        self.target_size_var = tk.BooleanVar(value=False)
        tk.Checkbutton(frame_compress, text='按目标大小压缩（KB）', variable=self.target_size_var).pack(side='left', padx=6)
        self.target_kb_entry = tk.Entry(frame_compress, width=8)
        self.target_kb_entry.pack(side='left', padx=6)
        tk.Label(frame_compress, text='（填写整数 KB，例：200）').pack(side='left')

        # 开始按钮和进度
        frame_run = tk.Frame(master)
        frame_run.pack(pady=8)
        self.btn_start = tk.Button(frame_run, text="开始处理", command=self.start_processing)
        self.btn_start.pack(side='left', padx=6)
        self.progress = ttk.Progressbar(frame_run, length=520)
        self.progress.pack(side='left', padx=6)

        # 处理日志文本（可选，显示每张输出大小/格式）
        self.log_text = tk.Text(master, height=6)
        self.log_text.pack(fill='both', padx=10, pady=6)

        self.status_label = tk.Label(master, text="等待操作...", anchor='w')
        self.status_label.pack(fill='x', padx=10)

        # 内部
        self.file_list = []
        self.processing = False
        self.on_compress_toggle()
        self.on_pixel_compress_toggle()

    def on_compress_toggle(self):
        enabled = bool(self.compress_var.get())
        state = 'normal' if enabled else 'disabled'
        self.quality_scale.config(state=state)
        self.max_side_entry.config(state=state)
        # target size always enabled separately
        # target_size checkbox and entry controlled separately

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

    def on_preset_change(self, _evt=None):
        sel = self.combobox.get()
        if sel == '自定义':
            self.width_entry.config(state='normal')
            self.height_entry.config(state='normal')
        else:
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
        self.log_text.delete('1.0', tk.END)

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

        compress_enabled = bool(self.compress_var.get())
        try:
            quality = int(self.quality_scale.get())
        except Exception:
            quality = 92
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
        max_side = None
        if compress_enabled:
            v = self.max_side_entry.get().strip()
            if v:
                try:
                    max_side = int(v)
                    if max_side <= 0:
                        max_side = None
                except Exception:
                    max_side = None

        # 目标大小设置
        target_size_enabled = bool(self.target_size_var.get())
        target_kb = None
        if target_size_enabled and not pixel_compress_enabled:
            v = self.target_kb_entry.get().strip()
            if not v:
                messagebox.showerror('目标大小错误', '已勾选按目标大小压缩，请输入目标 KB（整数）。')
                return
            try:
                target_kb = int(v)
                if target_kb <= 0:
                    raise ValueError()
            except Exception:
                messagebox.showerror('目标大小错误', '请输入有效的目标 KB（正整数）。')
                return

        self.btn_start.config(state='disabled')
        self.btn_select.config(state='disabled')
        self.processing = True
        self.progress['value'] = 0
        self.progress['maximum'] = len(self.file_list)
        self.log_text.delete('1.0', tk.END)

        t = threading.Thread(
            target=self._process_thread,
            args=(out_dir, target_w, target_h, resize_enabled, pixel_compress_enabled,
                  pixel_quality, pixel_target_kb, compress_enabled, quality, max_side,
                  target_size_enabled, target_kb)
        )
        t.daemon = True
        t.start()

    def _process_thread(self, out_dir, target_w, target_h, resize_enabled,
                        pixel_compress_enabled, pixel_quality, pixel_target_kb,
                        compress_enabled, quality, max_side, target_size_enabled,
                        target_kb):
        total = len(self.file_list)
        pattern = self.name_pattern.get().strip() or "{orig}"
        strategy = self.strategy_var.get()

        for i, path in enumerate(self.file_list, start=1):
            try:
                self._set_status(f"处理 {i}/{total}: {os.path.basename(path)}")
                with Image.open(path) as img:
                    has_alpha = img.mode in ('RGBA', 'LA')
                    if has_alpha:
                        img = img.convert('RGBA')
                    else:
                        img = img.convert('RGB')

                    if resize_enabled:
                        out_img = center_crop_to_target(img, target_w, target_h, strategy)
                    else:
                        out_img = img.copy()

                    if compress_enabled and max_side:
                        w, h = out_img.size
                        longest = max(w, h)
                        if longest > max_side:
                            scale = max_side / longest
                            new_w = int(round(w * scale))
                            new_h = int(round(h * scale))
                            out_img = out_img.resize((new_w, new_h), Image.LANCZOS)

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

                    # 目标大小设置优先；否则使用新增加的像素压缩档位。
                    if target_size_enabled and target_kb and not pixel_compress_enabled:
                        try:
                            saved_path, fmt, final_bytes, used_q = _save_to_target_size(out_img, out_path, target_kb, quality_start=quality)
                            self.log_text.insert(tk.END, f"{os.path.basename(saved_path)} -> {fmt}, {final_bytes} bytes, q={used_q}")
                        except Exception as e:
                            print(f"目标大小保存失败: {out_path} -> {e}")
                            try:
                                _save_with_params(out_img, out_path, compress_enabled, quality)
                                final_bytes = os.path.getsize(out_path)
                                self.log_text.insert(tk.END, f"{os.path.basename(out_path)} -> fallback, {final_bytes} bytes")
                            except Exception as e2:
                                print(f"回退保存失败: {out_path} -> {e2}")
                    elif pixel_compress_enabled:
                        try:
                            final_bytes = compress_pil_image_to_target_size(
                                out_img, out_path, pixel_target_kb, pixel_quality
                            )
                            self.log_text.insert(
                                tk.END,
                                f"{os.path.basename(out_path)} -> 目标 {pixel_target_kb} KB, "
                                f"实际 {final_bytes / 1024:.1f} KB"
                            )
                        except Exception as e:
                            print(f"像素压缩保存失败: {out_path} -> {e}")
                    else:
                        try:
                            _save_with_params(out_img, out_path, compress_enabled, quality)
                            final_bytes = os.path.getsize(out_path)
                            self.log_text.insert(tk.END, f"{os.path.basename(out_path)} -> saved, {final_bytes} bytes")
                        except Exception as e:
                            print(f"保存失败: {out_path} -> {e}")

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
