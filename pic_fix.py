from PIL import Image
import os

def force_match_image_parameters(ref_image_path, target_image_path, output_path):
    """
    强制将 target_image 的参数（尺寸、模式、EXIF、DPI）转换为与 ref_image 一致。
    """
    try:
        # 1. 打开图片
        ref_img = Image.open(ref_image_path)
        target_img = Image.open(target_image_path)

        print(f"参考图信息: 尺寸={ref_img.size}, 模式={ref_img.mode}, 格式={ref_img.format}")
        print(f"原目标图信息: 尺寸={target_img.size}, 模式={target_img.mode}")

        # 2. 转换色彩模式 (例如: RGBA -> RGB)
        if target_img.mode != ref_img.mode:
            print(f"正在转换色彩模式: {target_img.mode} -> {ref_img.mode}")
            target_img = target_img.convert(ref_img.mode)

        # 3. 强制调整尺寸 (分辨率)
        # 注意：这会忽略原图的宽高比，强制拉伸以匹配参考图
        if target_img.size != ref_img.size:
            print(f"正在调整尺寸: {target_img.size} -> {ref_img.size}")
            target_img = target_img.resize(ref_img.size, Image.Resampling.LANCZOS)

        # 4. 获取参考图的元数据 (EXIF)
        # 注意：EXIF 数据是二进制 blob，直接复制即可
        exif_data = ref_img.info.get('exif')
        
        # 获取 DPI 信息 (如果有)
        dpi = ref_img.info.get('dpi', (72, 72))

        # 5. 保存结果
        # 使用参考图的格式 (通常是 JPEG)，并注入 EXIF 数据
        save_kwargs = {
            "format": ref_img.format,
            "dpi": dpi,
            "quality": 95, # 默认保持较高质量
            "subsampling": 0 
        }
        
        if exif_data:
            save_kwargs["exif"] = exif_data
            print("已注入参考图的 EXIF 元数据。")
        else:
            print("参考图没有 EXIF 数据，跳过注入。")

        target_img.save(output_path, **save_kwargs)
        
        print(f"\n成功！新图片已保存至: {output_path}")
        print("该图片现在的分辨率、色彩空间和元数据与参考图完全一致。")

    except Exception as e:
        print(f"发生错误: {e}")

# --- 使用示例 ---
# 请根据实际文件名修改以下路径
reference_file = "00028.jpg"  # 第一张图（标准）
target_file = "00053.jpg"     # 第二张图（需要修改的）
output_file = "0005_modified.jpg" # 输出结果

# 检查文件是否存在以防报错
if os.path.exists(reference_file) and os.path.exists(target_file):
    force_match_image_parameters(reference_file, target_file, output_file)
else:
    print("请确保图片文件在当前目录下，或修改代码中的文件路径。")