"""
子进程驱动运行器。
由主程序以 --driver-host 参数启动，通过 stdin/stdout 二进制帧通信。

帧格式: type(1B) + json_len(4B LE) + bin_len(4B LE) + json_bytes + bin_bytes
"""
import os
import sys
import json
import struct
import time
import threading
import queue
import importlib.util

_HEADER_FMT = "<BII"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)
_MSG_JSON = 0
_MSG_BINARY = 1


def run_driver(driver_file: str, packages_dir: str = None):
    if packages_dir:
        sys.path.insert(0, packages_dir)
    
    drivers_pkg_dir = os.path.dirname(os.path.abspath(__file__))
    drivers_parent = os.path.dirname(drivers_pkg_dir)
    if drivers_parent not in sys.path:
        sys.path.insert(0, drivers_parent)
    
    from drivers.base import BaseDriver
    
    spec = importlib.util.spec_from_file_location("driver_module", driver_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    
    driver_classes = [
        obj for obj in vars(module).values()
        if isinstance(obj, type) and issubclass(obj, BaseDriver) and obj is not BaseDriver
    ]
    if not driver_classes:
        raise RuntimeError("No BaseDriver subclass found in driver module")
    
    driver = driver_classes[0]()
    driver.start()
    
    stdout = sys.stdout.buffer
    stdin = sys.stdin.buffer
    
    cmd_queue = queue.Queue()
    
    def _send(meta: dict, bin_data: bytes = None, mime: str = None):
        json_bytes = json.dumps(meta, ensure_ascii=False).encode("utf-8")
        body = bin_data or b""
        header = struct.pack(_HEADER_FMT, 0, len(json_bytes), len(body))
        stdout.write(header + json_bytes)
        if body:
            stdout.write(body)
        stdout.flush()
    
    def _stdin_reader():
        while True:
            header = stdin.read(_HEADER_SIZE)
            if not header:
                break
            _, json_len, bin_len = struct.unpack(_HEADER_FMT, header)
            json_bytes = stdin.read(json_len)
            msg = json.loads(json_bytes.decode("utf-8"))
            if bin_len > 0:
                msg["__data__"] = stdin.read(bin_len)
            cmd_queue.put(msg)
    
    reader_thread = threading.Thread(target=_stdin_reader, daemon=True)
    reader_thread.start()
    
    _send({"status": "ready"})
    
    try:
        while True:
            # 处理命令队列
            while not cmd_queue.empty():
                msg = cmd_queue.get_nowait()
                cmd = msg.get("command")
                if cmd == "operate":
                    try:
                        result = driver.operate(msg.get("cmd"), msg.get("params", {}))
                    except Exception as e:
                        result = {"status": "error", "message": str(e)}
                    _send(result if isinstance(result, dict) else {"status": "ok", "result": result})
                elif cmd == "stop":
                    driver.stop()
                    return
            
            if hasattr(driver, 'is_streaming') and driver.is_streaming():
                try:
                    frame_result = driver.capture_frame()
                    if isinstance(frame_result, dict) and frame_result.get("status") == "ok":
                        if "__data__" in frame_result:
                            mime = frame_result.pop("__mime__", "application/octet-stream")
                            bin_data = frame_result.pop("__data__")
                            _send({"status": "ok", "type": "stream", "result": frame_result.get("result")}, bin_data, mime)
                        else:
                            _send(frame_result)
                    else:
                        _send(frame_result)
                    
                    config = driver.get_stream_config()
                    target_fps = config.get("target_fps", 30)
                    time.sleep(1.0 / target_fps)
                except Exception as e:
                    _send({"status": "error", "message": str(e)})
                    time.sleep(0.1)
            else:
                # 非流模式，等待命令
                try:
                    msg = cmd_queue.get(timeout=0.1)
                    cmd = msg.get("command")
                    if cmd == "operate":
                        try:
                            result = driver.operate(msg.get("cmd"), msg.get("params", {}))
                        except Exception as e:
                            result = {"status": "error", "message": str(e)}
                        _send(result if isinstance(result, dict) else {"status": "ok", "result": result})
                    elif cmd == "stop":
                        driver.stop()
                        return
                except queue.Empty:
                    pass
    finally:
        driver.stop()


if __name__ == "__main__":
    driver_file = sys.argv[1]
    packages_dir = sys.argv[2] if len(sys.argv) > 2 else None
    run_driver(driver_file, packages_dir)
