"""Blender background: --python this.py -- --output ABS_DIR [--render].

Self-contained teaching scenes; no downloaded assets, handlers or user startup file.
Saved shape keys make the paper animation work after reopening the .blend.
"""
import argparse
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

args = argparse.ArgumentParser()
args.add_argument('--output', required=True)
args.add_argument('--render', action='store_true')
opt = args.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
out = Path(opt.output).resolve()
out.mkdir(parents=True, exist_ok=True)
FPS = 24


def material(name, color, roughness=.65):
    m = bpy.data.materials.new(name)
    m.diffuse_color = (*color, 1)
    m.use_nodes = True
    bs = m.node_tree.nodes.get('Principled BSDF')
    bs.inputs['Base Color'].default_value = (*color, 1)
    bs.inputs['Roughness'].default_value = roughness
    return m


def setup(name, end, camera):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    s = bpy.context.scene
    s.name = name
    s.render.engine = 'CYCLES'
    s.cycles.device = 'CPU'
    s.cycles.samples = 8
    s.cycles.use_denoising = True
    s.render.resolution_x, s.render.resolution_y = 640, 360
    s.render.resolution_percentage = 100
    s.render.fps = FPS
    s.frame_start, s.frame_end = 1, end
    s.render.image_settings.file_format = 'PNG'
    s.view_settings.view_transform = 'AgX'
    s.world = bpy.data.worlds.new('World')
    s.world.use_nodes = True
    s.world.node_tree.nodes['Background'].inputs[0].default_value = (.13, .17, .22, 1)
    s.world.node_tree.nodes['Background'].inputs[1].default_value = .5
    bpy.ops.object.camera_add(location=camera)
    cam = bpy.context.object
    cam.rotation_euler = (Vector((0, 0, .1)) - cam.location).to_track_quat('-Z', 'Y').to_euler()
    cam.data.type = 'ORTHO'
    cam.data.ortho_scale = 8.2
    s.camera = cam
    bpy.ops.object.light_add(type='AREA', location=(-3, -2, 7))
    bpy.context.object.data.energy = 700
    bpy.context.object.data.shape = 'DISK'
    bpy.context.object.data.size = 4
    bpy.ops.mesh.primitive_plane_add(size=200)
    bpy.context.object.name = 'Receiving surface'
    bpy.context.object.data.materials.append(material('Slate', (.055, .09, .105)))
    return s


def smooth(v):
    return v * v * (3 - 2 * v)


def paper(name, center, color, start, enter_end, release, end):
    # One segmented printed sheet, with two dark ink stripes deforming in-place.
    # Stripes intentionally stand in for glyphs so the sample needs no font assets.
    nx, ny, width, height = 72, 12, 2.75, 1.2
    verts = [((i/nx-.5)*width, (j/ny-.5)*height, 0) for j in range(ny+1) for i in range(nx+1)]
    faces = [(j*(nx+1)+i, j*(nx+1)+i+1, (j+1)*(nx+1)+i+1, (j+1)*(nx+1)+i)
             for j in range(ny) for i in range(nx)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.location = (*center, .025)
    front, ink = material('Paper '+name, color), material('Ink '+name, (.025,.035,.04))
    for m in (front, ink):
        ns, lk = m.node_tree.nodes, m.node_tree.links
        original = ns.get('Principled BSDF')
        back = ns.new('ShaderNodeBsdfPrincipled')
        back.inputs['Base Color'].default_value = (.79,.73,.60,1)
        back.inputs['Roughness'].default_value = .85
        noise = ns.new('ShaderNodeTexNoise'); noise.inputs['Scale'].default_value = 180
        bump = ns.new('ShaderNodeBump'); bump.inputs['Strength'].default_value = .12
        bump.inputs['Distance'].default_value = .015
        lk.new(noise.outputs['Fac'], bump.inputs['Height'])
        lk.new(bump.outputs['Normal'], back.inputs['Normal'])
        geo = ns.new('ShaderNodeNewGeometry'); mix = ns.new('ShaderNodeMixShader')
        lk.new(geo.outputs['Backfacing'], mix.inputs[0])
        lk.new(original.outputs[0], mix.inputs[1]); lk.new(back.outputs[0], mix.inputs[2])
        lk.new(mix.outputs[0], ns.get('Material Output').inputs['Surface'])
        mesh.materials.append(m)
    for j in range(ny):
        for i in range(nx):
            mesh.polygons[j*nx+i].material_index = int(j in (4,7) and 9 < i < (59 if j==7 else 44))
            mesh.polygons[j*nx+i].use_smooth = True
    obj.shape_key_add(name='Flat')
    mesh.shape_keys.use_relative = False
    for k in range(1,25):
        p = k/24
        key = obj.shape_key_add(name=f'Peel {p:.3f}')
        radius = .28
        crease = width/2 - p*(width+math.pi*radius)
        for v, (x,y,z) in zip(key.data, verts):
            d = max(0,x-crease)
            if d == 0: xx, zz = x,0
            elif d < math.pi*radius:
                xx,zz = crease+radius*math.sin(d/radius),radius*(1-math.cos(d/radius))
            else: xx,zz = crease-(d-math.pi*radius),2*radius
            v.co = (xx,y,zz)
    keys = mesh.shape_keys
    # Bake deterministic geometry/timing into normal animation data, no runtime handler.
    for frame in range(1,145):
        if frame < start: p=1
        elif frame < enter_end: p=.72*(1-smooth((frame-start)/(enter_end-start)))
        elif frame < release: p=0
        else: p=smooth(min(1,(frame-release)/(end-release)))
        keys.eval_time = p*240
        keys.keyframe_insert(data_path='eval_time', frame=frame)
        # Edge flutter is a proposed extension; attached center never translates.
        breath = math.sin(math.pi*max(0,min(1,(frame-enter_end)/max(1,release-enter_end))))
        obj.scale = (1+.012*breath,1+.012*breath,1)
        obj.keyframe_insert(data_path='scale',frame=frame)
        obj.hide_render = frame < start or frame >= end
        obj.keyframe_insert(data_path='hide_render',frame=frame)
    return obj


def save_render(scene, name):
    folder = out/name
    folder.mkdir(exist_ok=True)
    scene.render.filepath = str(folder/'frame-')
    scene.frame_set(1)
    bpy.ops.wm.save_as_mainfile(filepath=str(out/(name+'.blend')))
    if opt.render:
        bpy.ops.render.render(animation=True)


s = setup('Paper contact and handoff',144,(0,-3.5,10))
paper('A',(-1.4,.4),(.85,.46,.16),1,20,67,101)
paper('B',(1.35,-.45),(.27,.67,.63),77,99,122,144)
for f,label in [(1,'A attach'),(20,'A readable'),(67,'A peel'),(77,'B attach / overlap'),(99,'B readable'),(122,'B peel')]:
    s.timeline_markers.new(label,frame=f)
save_render(s,'01-paper-handoff')

s = setup('Jump blocking proxy',96,(0,-9,5))
bpy.ops.mesh.primitive_uv_sphere_add(segments=32,ring_count=16,radius=.38)
ball = bpy.context.object
ball.name='ACTION PROXY - not a finished character'
ball.data.materials.append(material('Ochre',(.9,.39,.08),.4))
for poly in ball.data.polygons: poly.use_smooth=True
for f in range(1,97):
    if f < 17:
        x=-2; z=.38; squash=1-.18*smooth((f-1)/15)
    elif f <= 65:
        u=(f-17)/48; x=-2+4*u; z=.38+1.7*4*u*(1-u); squash=1
    else:
        x=2; squash=1-.22*math.sin(math.pi*min(1,(f-65)/16)); z=.38
    ball.location=(x,0,z if 17 <= f <=65 else .38*squash)
    ball.scale=(1/math.sqrt(squash),1/math.sqrt(squash),squash)
    ball.keyframe_insert(data_path='location',frame=f)
    ball.keyframe_insert(data_path='scale',frame=f)
for f,label in [(1,'Prepare'),(17,'Takeoff'),(41,'Apex'),(65,'Contact'),(81,'Settled')]:
    s.timeline_markers.new(label,frame=f)
save_render(s,'02-jump-blocking')
(out/'manifest.json').write_text(json.dumps({'blender':bpy.app.version_string,'fps':FPS,'size':[640,360],
    'examples':[{'name':'01-paper-handoff','frames':144,'purpose':'contact, peel, breathing, overlap; stripes are typography placeholders'},
                {'name':'02-jump-blocking','frames':96,'purpose':'action blocking only; left to right, ground to air to ground'}],
    'audio':None,'external_assets':False},indent=2),encoding='utf-8')
