# Core Runtime Scripts

These scripts are used for the main model workflow:

- `train.py`: train the YOLO PCB defect model.
- `evaluate.py`: evaluate a trained model on train/val/test splits.
- `predict.py`: batch inference for image folders.
- `detect.py`: single-image inference with JSON output.
- `camera_detect.py`: live camera inference.
- `camera_capture.py`: capture static camera images.
- `camera_capture_board.py`: board-side manual or timed PCB capture utility.
- `detect_onnx.py`: optional ONNX Runtime single-image diagnostic tool.

`camera_capture_board.py` only accesses the camera when explicitly run. `detect_onnx.py` is a development diagnostic; the production Loongson runtime remains the existing OpenCV DNN path.
