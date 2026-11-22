"""Executed by Blender's embedded Python interpreter."""

import json
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def meshes():
    return [obj for obj in bpy.context.scene.objects if obj.type == "MESH" and obj.data.polygons]


def make_primitive(name):
    operators = {
        "cube": bpy.ops.mesh.primitive_cube_add,
        "icosphere": bpy.ops.mesh.primitive_ico_sphere_add,
        "torus": bpy.ops.mesh.primitive_torus_add,
    }
    if name not in operators:
        raise ValueError("Demo primitives are cube, icosphere and torus.")
    operators[name]()
    material = bpy.data.materials.new("Local3D cobalt")
    material.use_nodes = True
    shader = material.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = (0.045, 0.24, 0.75, 1.0)
    shader.inputs["Roughness"].default_value = 0.38
    bpy.context.object.data.materials.append(material)


def load(source):
    if Path(source).suffix.lower() == ".blend":
        bpy.ops.wm.open_mainfile(filepath=source, use_scripts=False)
    else:
        bpy.ops.import_scene.gltf(filepath=source)


def optimize(objects, ratio):
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        if obj.data.shape_keys is not None:
            raise ValueError("Optimization expects static meshes without shape keys.")
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        if obj.data.users > 1:
            obj.data = obj.data.copy()
        modifier = obj.modifiers.new("Local3D decimation", "DECIMATE")
        modifier.ratio = ratio
        modifier.use_collapse_triangulate = True
        bpy.ops.object.modifier_apply(modifier=modifier.name)
        obj.select_set(False)


def remove_floaters(objects, share=0.005):
    """Delete loose surface pieces smaller than `share` of the object's faces.

    Single-image reconstruction often leaves specks and thin shards around the main
    surface. Pieces are found by vertex position, since GLB vertices are split along UV
    seams and index connectivity would mistake texture charts for separate parts.
    """
    import bmesh

    for obj in objects:
        mesh = bmesh.new()
        mesh.from_mesh(obj.data)
        if not mesh.faces:
            mesh.free()
            continue
        lower = Vector(tuple(min(v.co[i] for v in mesh.verts) for i in range(3)))
        upper = Vector(tuple(max(v.co[i] for v in mesh.verts) for i in range(3)))
        step = max((upper - lower).length, 1e-9) * 1e-6
        welded = {}
        parent = []

        def find(index):
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        point = {}
        for vert in mesh.verts:
            key = tuple(round(value / step) for value in vert.co)
            if key not in welded:
                welded[key] = len(parent)
                parent.append(len(parent))
            point[vert] = welded[key]
        for face in mesh.faces:
            first = find(point[face.verts[0]])
            for vert in face.verts[1:]:
                other = find(point[vert])
                if other != first:
                    parent[other] = first
        sizes = {}
        owner = {}
        for face in mesh.faces:
            root = find(point[face.verts[0]])
            owner[face] = root
            sizes[root] = sizes.get(root, 0) + 1
        limit = share * len(mesh.faces)
        doomed = [face for face, root in owner.items() if sizes[root] < limit]
        if doomed and len(doomed) < len(mesh.faces):
            bmesh.ops.delete(mesh, geom=doomed, context="FACES")
            loose = [vert for vert in mesh.verts if not vert.link_faces]
            bmesh.ops.delete(mesh, geom=loose, context="VERTS")
            mesh.to_mesh(obj.data)
            obj.data.update()
            print(f"Removed {len(doomed)} floater faces from {obj.name}", flush=True)
        mesh.free()


def statistics():
    objects = meshes()
    if not objects:
        raise ValueError("No polygon mesh is present.")
    evaluated = [obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data for obj in objects]
    for mesh in evaluated:
        mesh.calc_loop_triangles()
    materials = {material for mesh in evaluated for material in mesh.materials if material}
    return {
        "vertices": sum(len(mesh.vertices) for mesh in evaluated),
        "triangles": sum(len(mesh.loop_triangles) for mesh in evaluated),
        "materials": len(materials),
    }


def aim(obj, center):
    obj.rotation_euler = (center - obj.location).to_track_quat("-Z", "Y").to_euler()


def render(objects, output, size):
    bounds = [obj.matrix_world @ Vector(corner) for obj in objects for corner in obj.bound_box]
    lower = Vector(tuple(min(point[i] for point in bounds) for i in range(3)))
    upper = Vector(tuple(max(point[i] for point in bounds) for i in range(3)))
    center = (lower + upper) * 0.5
    radius = max((upper - lower).length, 0.01)
    scene = bpy.context.scene
    for obj in tuple(scene.objects):
        if obj.type == "LIGHT":
            bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.object.camera_add(location=center + Vector((1.0, -1.6, 1.0)) * radius)
    scene.camera = bpy.context.object
    aim(scene.camera, center)
    scene.camera.data.type = "ORTHO"
    scene.camera.data.ortho_scale = radius * 1.15
    scene.camera.data.clip_end = max(100, radius * 10)
    for direction, power in [((1, -2, 3), 320), ((-2, -1, 1), 130), ((0, 2, 2), 230)]:
        bpy.ops.object.light_add(type="AREA", location=center + Vector(direction) * radius)
        light = bpy.context.object
        light.data.energy = power * radius**2
        light.data.shape = "DISK"
        light.data.size = radius * 2
        aim(light, center)
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 8
    scene.cycles.use_denoising = True
    scene.world = bpy.data.worlds.new("Local3D studio")
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs[0].default_value = (0.18, 0.18, 0.18, 1)
    scene.render.resolution_x = scene.render.resolution_y = size
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = output
    bpy.ops.render.render(write_still=True)


def main():
    action, source, output, metadata, ratio, size = sys.argv[sys.argv.index("--") + 1 :]
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if action == "demo":
        make_primitive(source)
    else:
        load(source)
    if action == "optimize":
        optimize(meshes(), float(ratio))
    elif action == "finalize":
        remove_floaters(meshes())
    stats = statistics()
    if action == "preview":
        render(meshes(), output, int(size))
    elif Path(output).suffix.lower() == ".blend":
        bpy.ops.file.pack_all()
        bpy.ops.wm.save_as_mainfile(filepath=output)
    else:
        bpy.ops.export_scene.gltf(
            filepath=output,
            export_format="GLB",
            export_animations=False,
            export_apply=True,
        )
    Path(metadata).write_text(json.dumps(stats), encoding="utf-8")


if __name__ == "__main__":
    main()
