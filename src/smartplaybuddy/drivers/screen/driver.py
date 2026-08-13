import io
from typing import Any, Dict, Tuple

import cv2
import dxcam
import numpy as np
from PIL import Image
from drivers.base import BaseDriver


class ScreenDriver(BaseDriver):
    name = "screen"
    description = "High-performance screen capture driver (dxcam)"

    def __init__(self):
        self._cameras: Dict[Tuple[int, int], Any] = {}
        self._streaming = False
        self._stream_config: dict = {}

    def start(self):
        pass

    def stop(self):
        self._streaming = False
        for camera in self._cameras.values():
            del camera
        self._cameras.clear()

    def operate(self, command: str, params: dict):
        op = params.get("operate", "capture")
        if op == "capture":
            return self._capture(params)
        elif op == "list_monitors":
            return self._list_monitors()
        elif op == "start_stream":
            return self._start_stream(params)
        elif op == "stop_stream":
            return self._stop_stream()
        else:
            raise ValueError(f"Unknown operate: {op}")

    def _capture(self, params: dict) -> dict:
        device_idx = int(params.get("device_idx", 0))
        output_idx = int(params.get("output_idx", 0))
        region = self._parse_region(params.get("region"))
        fmt = params.get("format", "raw")
        quality = int(params.get("quality", 80))
        scale = float(params.get("scale", 1.0))
        resolution = params.get("resolution")

        camera = self._get_camera(device_idx, output_idx)
        frame = camera.grab(region=region, new_frame_only=False)

        if frame is None:
            raise RuntimeError("Failed to capture frame")

        if resolution:
            if isinstance(resolution, str):
                target_w, target_h = (int(v) for v in resolution.lower().split("x", 1))
            else:
                target_w, target_h = int(resolution[0]), int(resolution[1])
            h, w = frame.shape[:2]
            if w > target_w or h > target_h:
                s = min(target_w / w, target_h / h)
                frame = cv2.resize(frame, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        elif scale != 1.0:
            h, w = frame.shape[:2]
            frame = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

        h, w = frame.shape[:2]

        if fmt == "raw":
            return {
                "format": "raw",
                "width": w,
                "height": h,
                "__data__": frame.tobytes(),
                "__mime__": "image/raw-rgb",
            }

        if fmt == "jpeg":
            bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            if not ok:
                raise RuntimeError("JPEG encode failed")
            data = buf.tobytes()
        elif fmt == "webp":
            method = int(params.get("method", 0))
            buf = io.BytesIO()
            Image.fromarray(frame).save(buf, format="WebP", quality=quality, method=method)
            data = buf.getvalue()
        else:
            buf = io.BytesIO()
            Image.fromarray(frame).save(buf, format="PNG")
            data = buf.getvalue()

        return {
            "format": fmt,
            "width": w,
            "height": h,
            "__data__": data,
            "__mime__": f"image/{fmt}",
        }

    def _get_camera(self, device_idx: int, output_idx: int):
        key = (device_idx, output_idx)
        if key not in self._cameras:
            self._cameras[key] = dxcam.create(device_idx=device_idx, output_idx=output_idx)
        return self._cameras[key]

    def _parse_region(self, region) -> tuple | None:
        if not region:
            return None
        return (
            int(region["left"]),
            int(region["top"]),
            int(region["right"]),
            int(region["bottom"]),
        )

    def _list_monitors(self) -> list:
        import dxcam.device
        devices = dxcam.device.list_devices()
        result = []
        for dev_idx, (adapter, outputs) in enumerate(devices):
            for out_idx, output in enumerate(outputs):
                result.append({
                    "device_idx": dev_idx,
                    "output_idx": out_idx,
                    "name": output.description,
                    "resolution": [output.width, output.height],
                    "refresh_rate": output.refresh_rate,
                })
        return result

    def _operate(self, cmd: str, params: dict) -> dict:
        operation = params.get("operate", cmd)
        if operation == "capture":
            return self._capture(params)
        elif operation == "start_stream":
            return self._start_stream(params)
        elif operation == "stop_stream":
            return self._stop_stream()
        else:
            return {"status": "error", "message": f"Unknown command: {operation}"}

    def _start_stream(self, params: dict) -> dict:
        if self._streaming:
            return {"status": "error", "message": "Already streaming"}
        
        device_idx = int(params.get("device_idx", 0))
        output_idx = int(params.get("output_idx", 0))
        target_fps = int(params.get("target_fps", 30))
        
        camera = self._get_camera(device_idx, output_idx)
        camera.start(target_fps=target_fps, video_mode=True)
        
        resolution = params.get("resolution")
        if isinstance(resolution, str):
            resolution = [int(v) for v in resolution.lower().split("x", 1)]
        
        self._streaming = True
        self._stream_config = {
            "device_idx": device_idx,
            "output_idx": output_idx,
            "target_fps": target_fps,
            "format": params.get("format", "jpeg"),
            "quality": int(params.get("quality", 80)),
            "resolution": resolution,
            "scale": float(params.get("scale", 1.0)),
        }
        
        return {"status": "ok", "result": {"message": "Stream started", "target_fps": target_fps}}

    def _stop_stream(self) -> dict:
        if not self._streaming:
            return {"status": "error", "message": "Not streaming"}
        
        self._streaming = False
        device_idx = self._stream_config.get("device_idx", 0)
        output_idx = self._stream_config.get("output_idx", 0)
        camera = self._get_camera(device_idx, output_idx)
        camera.stop()
        
        return {"status": "ok", "result": {"message": "Stream stopped"}}

    def is_streaming(self) -> bool:
        return self._streaming

    def get_stream_config(self) -> dict:
        return self._stream_config

    def capture_frame(self) -> dict:
        if not self._streaming:
            return {"status": "error", "message": "Not streaming"}
        
        config = self._stream_config
        device_idx = config["device_idx"]
        output_idx = config["output_idx"]
        camera = self._get_camera(device_idx, output_idx)
        frame = camera.get_latest_frame()
        
        if frame is None:
            raise RuntimeError("Failed to capture frame")
        
        fmt = config["format"]
        quality = config["quality"]
        resolution = config.get("resolution")
        scale = config["scale"]
        
        if resolution:
            target_w, target_h = int(resolution[0]), int(resolution[1])
            h, w = frame.shape[:2]
            if w > target_w or h > target_h:
                s = min(target_w / w, target_h / h)
                frame = cv2.resize(frame, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        elif scale != 1.0:
            h, w = frame.shape[:2]
            frame = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        
        h, w = frame.shape[:2]
        
        if fmt == "raw":
            return {"status": "ok", "result": {"format": "raw", "width": w, "height": h},
                    "__data__": frame.tobytes(), "__mime__": "image/raw-rgb"}
        
        if fmt == "jpeg":
            bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            if not ok:
                raise RuntimeError("JPEG encode failed")
            data = buf.tobytes()
        elif fmt == "webp":
            method = int(config.get("method", 0))
            buf = io.BytesIO()
            Image.fromarray(frame).save(buf, format="WebP", quality=quality, method=method)
            data = buf.getvalue()
        else:
            buf = io.BytesIO()
            Image.fromarray(frame).save(buf, format="PNG")
            data = buf.getvalue()
        
        return {"status": "ok", "result": {"format": fmt, "width": w, "height": h},
                "__data__": data, "__mime__": f"image/{fmt}"}
