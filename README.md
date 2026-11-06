# 批量图片处理工具

一个基于 Python 和 Pillow 的 Windows 批量图片处理工具，支持图片尺寸处理、像素压缩、目标文件大小压缩和拖动导入。

## 功能介绍

### 1. 尺寸处理

- 支持预设尺寸：`1242 × 2208`、`1242 × 2688`
- 支持自定义宽度和高度
- 保持图片比例进行居中裁剪，不会拉伸变形
- 图片较小时可选择：
  - 放大并裁剪
  - 仅填充白边
  - 只缩小，不放大

### 2. 像素压缩

尺寸处理和像素压缩位于同一个页面，可以：

- 只进行尺寸处理
- 只进行像素压缩
- 同时进行尺寸处理和像素压缩

像素压缩提供三个档位：

| 档位 | 初始 JPEG 质量 |
| --- | ---: |
| 高像素 | 70% |
| 中像素 | 50% |
| 低像素 | 30% |

还可以填写目标占用大小，例如输入 `60`，单位固定为 KB。程序会逐步调整 JPEG 质量，在不超过目标大小的前提下尽量接近目标；如果仅降低质量仍无法达到目标，会继续按比例缩小图片尺寸。

由于 JPEG 编码结果不是连续可控的，目标大小不一定正好等于输入值。例如输入 `60KB`，实际可能输出 `58.4KB`，但不会无故跳到很小的文件。

### 3. 批量处理

- 文件选择框支持一次选择多张图片
- 支持直接将图片拖入列表
- 支持 PNG、JPG、JPEG、BMP、TIFF、WEBP
- 自动创建输出目录
- 输出文件重名时自动添加序号，避免覆盖原文件
- PNG 透明区域转 JPEG 时使用白色背景
- 显示处理进度和处理状态

## 使用方法

1. 点击“选择图片”，或将图片直接拖入列表。
2. 选择输出文件夹。
3. 在“处理方式”中至少勾选“尺寸处理”或“像素压缩”一项。
4. 如果选择像素压缩，选择 70%、50% 或 30%，并填写目标占用大小（KB）。
5. 点击“开始处理”。

尺寸处理默认开启，因此不改变选项时仍保持原有裁剪工具的使用方式。

## 安装依赖

建议使用 Python 3.8 或更高版本：

```powershell
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

## 运行源码

```powershell
python batch_image.py
```

`batch_image.py` 是当前打包使用的主入口；`app.py` 保留了带有原有高级输出压缩选项的增强版本。

## 打包 exe

```powershell
pyinstaller --clean --noconfirm BatchImage.spec
```

打包完成后，程序位于：

```text
dist/BatchImage.exe
```

`BatchImage.spec` 会自动收集 `tkinterdnd2` 所需的拖放组件。

## 实现原理

- `Pillow` 负责图片读取、格式转换、裁剪、缩放和 JPEG 编码。
- 目标大小压缩使用循环试探质量值的方式，选择最接近目标且不超过目标的结果。
- 如果质量调整仍无法达到目标，再逐步缩小宽高后重新编码。
- `tkinter` 负责桌面界面。
- `tkinterdnd2` 为 Tkinter 增加 Windows 文件拖放能力。
- `threading` 负责后台批量处理，避免界面卡顿。
- `PyInstaller` 用于生成独立 exe。

## 项目结构

```text
batch_image.py          主界面和批量处理流程
app.py                  增强版入口，保留原有高级压缩选项
image_compression.py    JPEG 压缩和目标大小压缩算法
dnd_support.py          文件拖放支持
BatchImage.spec         PyInstaller 打包配置
requirements.txt        Python 依赖
app.ico                 程序图标
```
