

Ollama, ComfyUI, voice-api and Blender launch from embedded runtimes: **always** set `cwd` to the service dir, **never** `start /min` or `cmd /c start` (both open windows). Full pattern: `.claude/rules/external-service-launch.md`.
