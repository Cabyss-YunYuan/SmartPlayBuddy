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
        self._streams: Dict[str, dict] = {}
        self._camera_stream_count: Dict[Tuple[int, int], int] = {}

    def start(self):
        pass

    def stop(self):
        for stream_id in list(self._streams.keys()):
            self._stop_stream_by_id(stream_id)
        self._cameras.clear()
        self._camera_stream_count.clear()

    def operate(self, command: str, params: dict):
        op = params.get("operate", "capture")
        if op == "capture":
            return self._capture(params)
        elif op == "list_monitors":
            return self._list_monitors()
        elif op == "start_stream":
            return self._start_stream(params)
        elif op == "stop_stream":
            return self._stop_stream(params)
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
                "status": "ok",
                "result": {"format": "raw", "width": w, "height": h},
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
            "status": "ok",
            "result": {"format": fmt, "width": w, "height": h},
            "__data__": data,
            "__mime__": f"image/{fmt}",
        }

    def _start_stream(self, params: dict) -> dict:
        stream_id = params.get("stream_id")
        if not stream_id:
            return {"status": "error", "message": "stream_id is required"}
        if stream_id in self._streams:
            return {"status": "error", "message": f"Stream {stream_id} already exists"}

        device_idx = int(params.get("device_idx", 0))
        output_idx = int(params.get("output_idx", 0))
        target_fps = int(params.get("target_fps", 30))

        camera = self._get_camera(device_idx, output_idx)
        key = (device_idx, output_idx)
        count = self._camera_stream_count.get(key, 0)
        if count == 0:
            camera.start(target_fps=target_fps, video_mode=True)
        self._camera_stream_count[key] = count + 1

        resolution = params.get("resolution")
        if isinstance(resolution, str):
            resolution = [int(v) for v in resolution.lower().split("x", 1)]

        self._streams[stream_id] = {
            "device_idx": device_idx,
            "output_idx": output_idx,
            "target_fps": target_fps,
            "format": params.get("format", "jpeg"),
            "quality": int(params.get("quality", 80)),
            "resolution": resolution,
            "scale": float(params.get("scale", 1.0)),
        }

        return {"status": "ok", "result": {"stream_id": stream_id, "target_fps": target_fps}}

    def _stop_stream(self, params: dict) -> dict:
        stream_id = params.get("stream_id")
        if not stream_id or stream_id not in self._streams:
            return {"status": "error", "message": f"Stream {stream_id} not found"}

        self._stop_stream_by_id(stream_id)
        return {"status": "ok", "result": {"stream_id": stream_id, "message": "Stream stopped"}}

    def _stop_stream_by_id(self, stream_id: str):
        config = self._streams.pop(stream_id, None)
        if not config:
            return
        key = (config.get("device_idx", 0), config.get("output_idx", 0))
        count = self._camera_stream_count.get(key, 1) - 1
        self._camera_stream_count[key] = count
        if count <= 0:
            camera = self._get_camera(*key)
            camera.stop()
            self._camera_stream_count.pop(key, None)

    def is_streaming(self) -> bool:
        return bool(self._streams)

    def get_active_streams(self) -> Dict[str, dict]:
        return dict(self._streams)

    def capture_frames(self) -> list:
        if not self._streams:
            return []

        frames = []
        device_frames = {}

        for stream_id, config in self._streams.items():
            key = (config["device_idx"], config["output_idx"])
            if key not in device_frames:
                camera = self._get_camera(*key)
                raw = camera.get_latest_frame()
                device_frames[key] = raw

        raw_frame = None
        for stream_id, config in self._streams.items():
            key = (config["device_idx"], config["output_idx"])
            frame = device_frames.get(key)
            if frame is None:
                continue

            encoded = self._encode_frame(frame, config)
            if encoded:
                encoded["stream_id"] = stream_id
                frames.append(encoded)

        return frames

    def _encode_frame(self, frame, config: dict) -> dict | None:
        import copy
        f = frame
        fmt = config["format"]
        quality = config["quality"]
        resolution = config.get("resolution")
        scale = config.get("scale", 1.0)

        if resolution:
            target_w, target_h = int(resolution[0]), int(resolution[1])
            h, w = f.shape[:2]
            if w > target_w or h > target_h:
                s = min(target_w / w, target_h / h)
                f = cv2.resize(f, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        elif scale != 1.0:
            h, w = f.shape[:2]
            f = cv2.resize(f, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

        h, w = f.shape[:2]

        if fmt == "raw":
            return {"status": "ok", "result": {"format": "raw", "width": w, "height": h},
                    "__data__": f.tobytes(), "__mime__": "image/raw-rgb"}

        if fmt == "jpeg":
            bgr = cv2.cvtColor(f, cv2.COLOR_RGB2BGR)
            ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            if not ok:
                return None
            data = buf.tobytes()
        elif fmt == "webp":
            method = int(config.get("method", 0))
            buf = io.BytesIO()
            Image.fromarray(f).save(buf, format="WebP", quality=quality, method=method)
            data = buf.getvalue()
        else:
            buf = io.BytesIO()
            Image.fromarray(f).save(buf, format="PNG")
            data = buf.getvalue()

        return {"status": "ok", "result": {"format": fmt, "width": w, "height": h},
                "__data__": data, "__mime__": f"image/{fmt}"}

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
