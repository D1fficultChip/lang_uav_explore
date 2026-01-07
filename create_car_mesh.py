import os

# ================= 配置 =================
IMAGE_NAME = "car.png"          # 你的图片文件名
OBJ_NAME = "car_plane.obj"      # 输出的模型名
WIDTH = 2.0                     # 宽度 (米)
HEIGHT = 1.5                    # 高度 (米) - 根据图片比例调整
# =======================================

def create_obj():
    # 1. 创建 MTL 材质文件
    mtl_content = f"""
newmtl car_material
Ns 96.078431
Ka 1.000000 1.000000 1.000000
Kd 0.640000 0.640000 0.640000
Ks 0.500000 0.500000 0.500000
Ke 0.000000 0.000000 0.000000
Ni 1.000000
d 1.000000
illum 2
map_Kd {IMAGE_NAME}
"""
    with open(OBJ_NAME.replace(".obj", ".mtl"), "w") as f:
        f.write(mtl_content)

    # 2. 创建 OBJ 模型文件 (一个简单的立着的矩形面片)
    # 顶点坐标 (x, y, z)
    # 假设放在原点，稍后在 yaml 里配置位置
    v1 = f"v 0 {-WIDTH/2} 0"
    v2 = f"v 0 {WIDTH/2} 0"
    v3 = f"v 0 {WIDTH/2} {HEIGHT}"
    v4 = f"v 0 {-WIDTH/2} {HEIGHT}"
    
    # 纹理坐标 (u, v)
    vt1 = "vt 1.0 0.0" # 右下
    vt2 = "vt 0.0 0.0" # 左下
    vt3 = "vt 0.0 1.0" # 左上
    vt4 = "vt 1.0 1.0" # 右上

    obj_content = f"""
mtllib {OBJ_NAME.replace(".obj", ".mtl")}
o CarPlane
{v1}
{v2}
{v3}
{v4}
{vt1}
{vt2}
{vt3}
{vt4}
vn 1.0000 0.0000 0.0000
usemtl car_material
s off
f 1/1/1 2/2/1 3/3/1 4/4/1
"""
    with open(OBJ_NAME, "w") as f:
        f.write(obj_content)
    
    print(f"✅ 生成成功！请把 {OBJ_NAME}, {OBJ_NAME.replace('.obj', '.mtl')} 和 {IMAGE_NAME} 放到 resource 文件夹。")

if __name__ == "__main__":
    create_obj()