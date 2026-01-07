import numpy as np
import open3d as o3d

vis = o3d.visualization.Visualizer()
assert vis.create_window("t", 640, 480, visible=True)

mesh = o3d.geometry.TriangleMesh.create_sphere(radius=1.0)
mesh.compute_vertex_normals()
vis.add_geometry(mesh)

opt = vis.get_render_option()
opt.background_color = np.asarray([1.0, 1.0, 1.0])

vis.reset_view_point(True)
for _ in range(5):
    vis.poll_events()
    vis.update_renderer()

img = np.asarray(vis.capture_screen_float_buffer(do_render=True))
print("mean", float(img.mean()), "max", float(img.max()), "min", float(img.min()))
vis.destroy_window()
