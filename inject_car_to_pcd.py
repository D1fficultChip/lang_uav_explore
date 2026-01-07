import open3d as o3d
import numpy as np
import cv2
import copy # 引入copy库防止引用问题

# ================= 配置区域 =================
MAP_PATH = "classical_office.pcd"   # 原始地图路径
IMAGE_PATH = "00050.jpg"              # 你的汽车图片
OUTPUT_PATH = "map_with_car_1.pcd"    # 输出的新地图名字

POS_X = 5.0    
POS_Y = 0.0    
POS_Z = 1.5    

SCALE_WIDTH = 2.0   
DENSITY = 0.02      # 建议稍微调大一点点，0.01可能会让文件过大
# ===========================================

def create_point_cloud_from_image(image_path, width_m, density):
    img = cv2.imread(image_path)
    if img is None:
        print(f"❌ 错误: 找不到图片 {image_path}")
        return None
    
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    h, w, _ = img.shape
    
    aspect_ratio = h / w
    height_m = width_m * aspect_ratio
    
    cols = int(width_m / density)
    rows = int(height_m / density)
    
    img_resized = cv2.resize(img, (cols, rows))
    
    points = []
    colors = []
    
    print(f"🔄 正在生成 {cols*rows} 个点来模拟图片...")
    
    for r in range(rows):
        for c in range(cols):
            color = img_resized[rows-1-r, c] / 255.0
            
            # 背景剔除 (稍微放宽一点阈值)
            if np.all(color > 0.90): 
                continue

            # 这里的坐标逻辑我没动，假设你的墙是YZ平面或XZ平面
            # 默认生成在 X-Z 平面 (y=0)
            x = 2 
            y = (c - cols/2) * density+2
            z = r * density
            
            points.append([x, y, z])
            colors.append(color)
            
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(np.array(points))
    pcd.colors = o3d.utility.Vector3dVector(np.array(colors))
    
    return pcd

def main():
    print(f"📂 正在读取地图: {MAP_PATH} ...")
    try:
        map_pcd = o3d.io.read_point_cloud(MAP_PATH)
        print(f"✅ 地图加载成功，包含 {len(map_pcd.points)} 个点")
    except Exception as e:
        print(f"❌ 地图读取失败: {e}")
        return

    # =========== 🔥 核心修复代码开始 🔥 ===========
    # 检查原地图是否有颜色
    if not map_pcd.has_colors():
        print("⚠️ 检测到原地图没有颜色信息！")
        print("🎨 正在给原地图涂上灰色，以防止合并时丢失汽车颜色...")
        # 给地图涂上灰色 [0.5, 0.5, 0.5]
        map_pcd.paint_uniform_color([0.5, 0.5, 0.5])
    else:
        print("✅ 原地图已有颜色信息。")
    # =========== 🔥 核心修复代码结束 🔥 ===========

    car_pcd = create_point_cloud_from_image(IMAGE_PATH, SCALE_WIDTH, DENSITY)
    if car_pcd is None: return

    # 移动汽车
    # 注意：这里我们让 car_pcd 先旋转一下，防止它是平躺的（Open3D默认平面如果不旋转可能是躺在地上的）
    # 如果你觉得车是立着的就不需要下面这行
    # R = car_pcd.get_rotation_matrix_from_xyz((0, 0, 0)) 
    # car_pcd.rotate(R, center=(0, 0, 0))
    
    car_pcd.translate((POS_X, POS_Y, POS_Z))
    
    print(f"🚗 汽车点云生成完毕，准备合并...")

    # 合并
    merged_pcd = map_pcd + car_pcd

    # 再次确认颜色存在
    if not merged_pcd.has_colors():
        print("❌ 警告：合并后的点云依然没有颜色！Open3D 可能出 Bug 了。")
    else:
        print("✅ 合并成功，颜色已保留。")

    # 保存
    o3d.io.write_point_cloud(OUTPUT_PATH, merged_pcd) # 默认保存为二进制
    print(f"🎉 成功！新地图已保存为: {OUTPUT_PATH}")

if __name__ == "__main__":
    main()