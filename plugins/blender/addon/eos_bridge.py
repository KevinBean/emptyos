"""EmptyOS Blender Addon — JSON-RPC server inside Blender.

Install: Blender → Edit → Preferences → Add-ons → Install from Disk → select this file.
Once enabled, Blender listens on port 8400 for JSON-RPC calls from EmptyOS.
"""

bl_info = {
    "name": "EmptyOS Bridge",
    "author": "EmptyOS",
    "version": (1, 0, 0),
    "blender": (4, 0, 0),
    "location": "None (runs as background server)",
    "description": "JSON-RPC server for EmptyOS integration",
    "category": "System",
}

import bpy
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from io import BytesIO

# --- Config ---
EOS_PORT = 8400

# --- RPC Handlers ---

def handle_ping(params):
    return "pong"


def handle_scene_info(params):
    scene = bpy.context.scene
    return {
        "name": scene.name,
        "frame_current": scene.frame_current,
        "frame_start": scene.frame_start,
        "frame_end": scene.frame_end,
        "fps": scene.render.fps,
        "engine": scene.render.engine,
        "resolution": [scene.render.resolution_x, scene.render.resolution_y],
        "object_count": len(scene.objects),
    }


def handle_list_objects(params):
    objects = []
    for obj in bpy.context.scene.objects:
        objects.append({
            "name": obj.name,
            "type": obj.type,
            "location": list(obj.location),
            "rotation": list(obj.rotation_euler),
            "scale": list(obj.scale),
            "visible": obj.visible_get(),
        })
    return {"objects": objects}


def handle_execute(params):
    """Execute arbitrary Python code in Blender context."""
    code = params.get("code", "")
    if not code:
        raise ValueError("No code provided")

    # Capture output
    import io, sys
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        exec(code, {"bpy": bpy, "__builtins__": __builtins__})
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout

    return {"output": output}


def handle_import_model(params):
    path = params.get("path", "")
    fmt = params.get("format", "auto")
    if fmt == "auto":
        ext = path.rsplit(".", 1)[-1].lower()
        fmt = ext

    importers = {
        "obj": lambda p: bpy.ops.wm.obj_import(filepath=p),
        "fbx": lambda p: bpy.ops.import_scene.fbx(filepath=p),
        "glb": lambda p: bpy.ops.import_scene.gltf(filepath=p),
        "gltf": lambda p: bpy.ops.import_scene.gltf(filepath=p),
        "stl": lambda p: bpy.ops.wm.stl_import(filepath=p),
        "ply": lambda p: bpy.ops.wm.ply_import(filepath=p),
    }

    importer = importers.get(fmt)
    if not importer:
        raise ValueError(f"Unsupported format: {fmt}")

    importer(path)
    return {"imported": path, "format": fmt}


def handle_export_model(params):
    path = params.get("path", "")
    fmt = params.get("format", "glb")

    exporters = {
        "obj": lambda p: bpy.ops.wm.obj_export(filepath=p),
        "fbx": lambda p: bpy.ops.export_scene.fbx(filepath=p),
        "glb": lambda p: bpy.ops.export_scene.gltf(filepath=p, export_format="GLB"),
        "gltf": lambda p: bpy.ops.export_scene.gltf(filepath=p, export_format="GLTF_SEPARATE"),
        "stl": lambda p: bpy.ops.wm.stl_export(filepath=p),
    }

    exporter = exporters.get(fmt)
    if not exporter:
        raise ValueError(f"Unsupported format: {fmt}")

    exporter(path)
    return {"exported": path, "format": fmt}


def handle_set_material(params):
    obj_name = params.get("object", "")
    mat_props = params.get("material", {})

    obj = bpy.data.objects.get(obj_name)
    if not obj:
        raise ValueError(f"Object not found: {obj_name}")

    # Create or get material
    mat_name = mat_props.get("name", f"eos_{obj_name}")
    mat = bpy.data.materials.get(mat_name)
    if not mat:
        mat = bpy.data.materials.new(name=mat_name)
        mat.use_nodes = True

    # Assign to object
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)

    # Set basic properties
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        if "color" in mat_props:
            c = mat_props["color"]
            bsdf.inputs["Base Color"].default_value = (*c[:3], 1.0) if len(c) == 3 else tuple(c)
        if "metallic" in mat_props:
            bsdf.inputs["Metallic"].default_value = mat_props["metallic"]
        if "roughness" in mat_props:
            bsdf.inputs["Roughness"].default_value = mat_props["roughness"]

    return {"material": mat_name, "object": obj_name}


def handle_render_from_prompt(params):
    """Render current scene to image. Prompt metadata is logged but scene must be set up."""
    import tempfile, os

    width = params.get("width", 1024)
    height = params.get("height", 1024)

    scene = bpy.context.scene
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = 100

    output = os.path.join(tempfile.gettempdir(), f"eos_render_{os.getpid()}.png")
    scene.render.filepath = output
    scene.render.image_settings.file_format = "PNG"

    bpy.ops.render.render(write_still=True)
    return {"image_path": output}


# --- RPC dispatch ---

RPC_METHODS = {
    "ping": handle_ping,
    "scene_info": handle_scene_info,
    "list_objects": handle_list_objects,
    "execute": handle_execute,
    "import_model": handle_import_model,
    "export_model": handle_export_model,
    "set_material": handle_set_material,
    "render_from_prompt": handle_render_from_prompt,
}


class RPCHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress HTTP logs

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            request = json.loads(body)
        except json.JSONDecodeError:
            self._respond(400, {"error": "Invalid JSON"})
            return

        method = request.get("method", "")
        params = request.get("params", {})
        req_id = request.get("id")

        handler = RPC_METHODS.get(method)
        if not handler:
            self._respond(200, {
                "jsonrpc": "2.0",
                "error": {"code": -32601, "message": f"Method not found: {method}"},
                "id": req_id,
            })
            return

        try:
            # Execute in main thread via timer for Blender thread safety
            import queue
            result_queue = queue.Queue()

            def run_in_main():
                try:
                    result = handler(params)
                    result_queue.put(("ok", result))
                except Exception as e:
                    result_queue.put(("error", str(e)))
                return None  # Unregister timer

            bpy.app.timers.register(run_in_main, first_interval=0)
            result_type, result_value = result_queue.get(timeout=120)

            if result_type == "error":
                self._respond(200, {
                    "jsonrpc": "2.0",
                    "error": {"code": -32000, "message": result_value},
                    "id": req_id,
                })
            else:
                self._respond(200, {
                    "jsonrpc": "2.0",
                    "result": result_value,
                    "id": req_id,
                })

        except Exception as e:
            self._respond(200, {
                "jsonrpc": "2.0",
                "error": {"code": -32000, "message": str(e)},
                "id": req_id,
            })

    def _respond(self, status, data):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())


# --- Blender registration ---

_server = None
_thread = None


def start_server():
    global _server, _thread
    if _server:
        return

    _server = HTTPServer(("0.0.0.0", EOS_PORT), RPCHandler)
    _thread = threading.Thread(target=_server.serve_forever, daemon=True)
    _thread.start()
    print(f"[EmptyOS Bridge] Listening on port {EOS_PORT}")


def stop_server():
    global _server, _thread
    if _server:
        _server.shutdown()
        _server = None
        _thread = None
        print("[EmptyOS Bridge] Stopped")


def register():
    start_server()


def unregister():
    stop_server()


if __name__ == "__main__":
    register()
