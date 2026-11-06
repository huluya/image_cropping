"""Tkinter 文件拖放支持。

依赖未安装时，程序仍可使用原有的文件选择按钮，不影响其他功能。
"""

import tkinter as tk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    DND_AVAILABLE = True
except ImportError:
    DND_FILES = None
    TkinterDnD = None
    DND_AVAILABLE = False


def create_root():
    """创建支持文件拖放的根窗口，依赖不可用时回退到普通 Tk。"""
    if DND_AVAILABLE:
        try:
            return TkinterDnD.Tk()
        except Exception:
            pass
    return tk.Tk()


def parse_dropped_files(widget, data):
    """解析拖放事件中的文件列表，兼容带空格的路径。"""
    try:
        return list(widget.tk.splitlist(data))
    except Exception:
        return [data] if data else []


def install_file_drop(widget, callback):
    """让 widget 接收文件拖放，并将原始路径数据交给 callback。"""
    if not DND_AVAILABLE or not hasattr(widget, "drop_target_register"):
        return False
    try:
        widget.drop_target_register(DND_FILES)

        def on_drop(event):
            callback(event.data)
            return "copy"

        widget.dnd_bind("<<Drop>>", on_drop)
        return True
    except Exception:
        return False
