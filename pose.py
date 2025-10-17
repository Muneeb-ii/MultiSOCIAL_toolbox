'''
This is the script for extracting pose and embedding pose information from a video (single or multiperson)
using MediaPipe and YOLO

'''

import os
import cv2
import mediapipe as mp
import pandas as pd
import numpy as np
from scipy.optimize import linear_sum_assignment
from yolov5 import YOLOv5

def ensure_yolov5_weights():
    """Ensure yolov5s weights exist without triggering network calls at import in other modules."""
    weights_path = "yolov5s.pt"
    if not os.path.exists(weights_path):
        try:
            import requests
            url = "https://github.com/ultralytics/yolov5/releases/download/v6.0/yolov5s.pt"
            response = requests.get(url)
            response.raise_for_status()  # Raise exception for HTTP errors
            with open(weights_path, "wb") as f:
                f.write(response.content)
            print(f"Downloaded YOLOv5 weights to {weights_path}")
        except Exception as e:
            raise RuntimeError(f"Failed to download YOLOv5 weights: {e}")


# Core pose processor class
class PoseProcessor:
    def __init__(self, output_csv_folder, output_video_folder=None, status_callback=None, frame_threshold=10):
        self.output_csv_folder = output_csv_folder
        self.output_video_folder = output_video_folder
        self.status_callback = status_callback  
        self.enable_multi_person_pose = False  # Default to single person mode
        self.frame_threshold = frame_threshold  # Configurable threshold for bounding box recalibration
        # Frame counters for optional maintenance tasks
        self._frame_counter = 0
        # Disable periodic global reassignment to reduce jitter; set >0 to enable
        self._reassign_period = 0
        # Lightweight periodic spawn-only check to detect new entrants without moving existing ROIs
        self._spawn_period = 10
        # Light smoothing for ROI box updates to reduce micro jitter
        self.smooth_alpha = 0.5
        
        # Initialize Mediapipe Pose
        self.pose = mp.solutions.pose.Pose(static_image_mode=False, min_detection_confidence=0.7, min_tracking_confidence=0.7, model_complexity=1)
        self.drawing_utils = mp.solutions.drawing_utils
        
        # Initialize YOLO lazily (only when needed for multi-person mode)
        self.yolo = None

    def _roi_center(self, x1, y1, x2, y2):
        cx = 0.5 * (x1 + x2)
        cy = 0.5 * (y1 + y2)
        return cx, cy

    def _seed_rois_if_needed(self, image_rgb, image_width, image_height, locked_rois, margin_ratio=0.25):
        """Ensure YOLO is initialized and seed ROIs when none exist."""
        # Ensure YOLO is loaded
        self._ensure_yolo()
        if not locked_rois:
            results = self.yolo.predict(image_rgb, size=640)
            boxes = results.xyxy[0]
            person_boxes = [b[:4].int().tolist() for b in boxes if int(b[5]) == 0]
            for (x1, y1, x2, y2) in person_boxes:
                x1e, y1e, x2e, y2e = self._expand_and_clip_bbox(x1, y1, x2, y2, image_width, image_height, margin_ratio=margin_ratio)
                roi_pose = mp.solutions.pose.Pose(static_image_mode=False, min_detection_confidence=0.7, min_tracking_confidence=0.7, model_complexity=2)
                locked_rois.append({"x1": x1e, "y1": y1e, "x2": x2e, "y2": y2e, "lost": 0, "pose": roi_pose, "overlap_streak": 0})
        return locked_rois

    def _process_multiperson_frame(self, image_rgb, image_width, image_height, locked_rois):
        """Run pose on existing ROIs, handle reseed when lost, and return mapped landmarks per ROI.

        Returns a list of tuples (person_id, mp_landmarks_mapped) where landmarks are mapped to full frame.
        """
        outputs = []
        if not locked_rois:
            return outputs, locked_rois

        # Track which ROIs need reseed this frame
        rois_needing_reseed = []  # list of indices

        for person_id, roi in enumerate(locked_rois):
            x1e, y1e, x2e, y2e = roi["x1"], roi["y1"], roi["x2"], roi["y2"]
            cropped = image_rgb[y1e:y2e, x1e:x2e]
            result = roi["pose"].process(cropped)
            if result.pose_landmarks:
                roi["lost"] = 0
                # Map back to full frame coordinates
                try:
                    original_landmarks = result.pose_landmarks
                    for lmk in original_landmarks.landmark:
                        lmk.x = (lmk.x * (x2e - x1e) + x1e) / image_width
                        lmk.y = (lmk.y * (y2e - y1e) + y1e) / image_height
                    outputs.append((person_id, original_landmarks))
                except Exception:
                    pass
            else:
                roi["lost"] += 1
                if roi["lost"] >= self.frame_threshold:
                    rois_needing_reseed.append(person_id)

        # Perform a single reseed step using global one-to-one assignment
        if rois_needing_reseed:
            results = self.yolo.predict(image_rgb, size=640)
            boxes = results.xyxy[0]
            person_boxes = [b[:4].int().tolist() for b in boxes if int(b[5]) == 0]

            # Reserve detections that belong to healthy ROIs so lost ROIs can't take them
            healthy_indices = [i for i, r in enumerate(locked_rois) if r.get("lost", 0) == 0]
            healthy_boxes = [(locked_rois[i]["x1"], locked_rois[i]["y1"], locked_rois[i]["x2"], locked_rois[i]["y2"]) for i in healthy_indices]
            reserved_iou = 0.5
            filtered_boxes = []
            for (x1, y1, x2, y2) in person_boxes:
                keep = True
                for hb in healthy_boxes:
                    if self._iou((x1, y1, x2, y2), hb) >= reserved_iou:
                        keep = False
                        break
                if keep:
                    filtered_boxes.append((x1, y1, x2, y2))
            person_boxes = filtered_boxes

            if person_boxes:
                # Build blended cost matrix: normalized distance + lambda*(1 - IoU)
                roi_centers = []
                for idx in rois_needing_reseed:
                    r = locked_rois[idx]
                    roi_centers.append(self._roi_center(r["x1"], r["y1"], r["x2"], r["y2"]))

                det_centers = [self._roi_center(x1, y1, x2, y2) for (x1, y1, x2, y2) in person_boxes]

                diag = np.sqrt(image_width * image_width + image_height * image_height)
                lambda_iou = 0.5
                cost = np.zeros((len(rois_needing_reseed), len(person_boxes)), dtype=np.float32)
                for i, (cx, cy) in enumerate(roi_centers):
                    for j, (dcx, dcy) in enumerate(det_centers):
                        dx = dcx - cx
                        dy = dcy - cy
                        dist_norm = np.sqrt(dx * dx + dy * dy) / max(1e-6, diag)
                        # IoU between ROI box and detection box
                        rr = locked_rois[rois_needing_reseed[i]]
                        iou_val = self._iou((rr["x1"], rr["y1"], rr["x2"], rr["y2"]), tuple(person_boxes[j]))
                        cost[i, j] = dist_norm + lambda_iou * (1.0 - float(iou_val))

                # Hungarian assignment ensures one-to-one mapping
                row_ind, col_ind = linear_sum_assignment(cost)

                # Optional gating: ignore matches beyond a distance threshold (8% of image diagonal)
                max_dist = 0.08 * diag

                for r_i, c_j in zip(row_ind, col_ind):
                    if r_i < len(rois_needing_reseed) and c_j < len(person_boxes):
                        if cost[r_i, c_j] <= max_dist:
                            roi_index = rois_needing_reseed[r_i]
                            rx1, ry1, rx2, ry2 = person_boxes[c_j]
                            nx1, ny1, nx2, ny2 = self._expand_and_clip_bbox(rx1, ry1, rx2, ry2, image_width, image_height, margin_ratio=0.25)
                            # Final conflict check against healthy ROIs
                            conflict = False
                            for hb in healthy_boxes:
                                if self._iou((nx1, ny1, nx2, ny2), hb) >= reserved_iou:
                                    conflict = True
                                    break
                            if conflict:
                                continue
                            roi = locked_rois[roi_index]
                            try:
                                roi_pose = roi.get("pose")
                                if roi_pose is not None:
                                    roi_pose.close()
                            except Exception:
                                pass
                            sx1, sy1, sx2, sy2 = self._smooth_box((roi["x1"], roi["y1"], roi["x2"], roi["y2"]), (nx1, ny1, nx2, ny2), self.smooth_alpha)
                            roi["x1"], roi["y1"], roi["x2"], roi["y2"] = sx1, sy1, sx2, sy2
                            roi["pose"] = mp.solutions.pose.Pose(static_image_mode=False, min_detection_confidence=0.7, min_tracking_confidence=0.7, model_complexity=2)
                            roi["lost"] = 0

        # Increment frame counter for optional tasks (no global reassignment by default)
        self._frame_counter += 1

        # Post-assignment deduplication with short persistence using IoU
        try:
            n = len(locked_rois)
            to_remove = set()
            for i in range(n):
                if i in to_remove:
                    continue
                for j in range(i + 1, n):
                    if j in to_remove:
                        continue
                    iou_val = self._iou((locked_rois[i]["x1"], locked_rois[i]["y1"], locked_rois[i]["x2"], locked_rois[i]["y2"]),
                                        (locked_rois[j]["x1"], locked_rois[j]["y1"], locked_rois[j]["x2"], locked_rois[j]["y2"]))
                    if iou_val > 0.55:
                        locked_rois[i]["overlap_streak"] = locked_rois[i].get("overlap_streak", 0) + 1
                        locked_rois[j]["overlap_streak"] = locked_rois[j].get("overlap_streak", 0) + 1
                        if locked_rois[i]["overlap_streak"] >= 3 and locked_rois[j]["overlap_streak"] >= 3:
                            drop = i if locked_rois[i]["lost"] >= locked_rois[j]["lost"] else j
                            try:
                                roi_pose = locked_rois[drop].get("pose")
                                if roi_pose is not None:
                                    roi_pose.close()
                            except Exception:
                                pass
                            to_remove.add(drop)
                    else:
                        locked_rois[i]["overlap_streak"] = 0
                        locked_rois[j]["overlap_streak"] = 0

            if to_remove:
                locked_rois = [r for idx, r in enumerate(locked_rois) if idx not in to_remove]
        except Exception:
            pass

        return outputs, locked_rois

    def _cleanup_locked_rois(self, locked_rois):
        """Close Mediapipe Pose instances to free resources."""
        try:
            for roi in locked_rois:
                roi_pose = roi.get("pose")
                if roi_pose is not None:
                    roi_pose.close()
        except Exception:
            pass

    def _expand_and_clip_bbox(self, x1, y1, x2, y2, image_width, image_height, margin_ratio=0.12):
        """Expand bbox by margin_ratio on all sides and clip to image bounds.

        Returns clipped integer coordinates (nx1, ny1, nx2, ny2) with nx1 < nx2 and ny1 < ny2.
        """
        # Convert to float for precise expansion
        bx1, by1, bx2, by2 = float(x1), float(y1), float(x2), float(y2)
        bw = max(0.0, bx2 - bx1)
        bh = max(0.0, by2 - by1)
        if bw <= 0 or bh <= 0:
            return int(max(0, min(image_width - 1, x1))), int(max(0, min(image_height - 1, y1))), int(max(0, min(image_width, x2))), int(max(0, min(image_height, y2)))

        margin_w = bw * margin_ratio
        margin_h = bh * margin_ratio

        ex1 = bx1 - margin_w
        ey1 = by1 - margin_h
        ex2 = bx2 + margin_w
        ey2 = by2 + margin_h

        # Clip to image bounds
        ex1 = max(0.0, min(ex1, image_width - 1.0))
        ey1 = max(0.0, min(ey1, image_height - 1.0))
        ex2 = max(0.0, min(ex2, image_width - 0.0))
        ey2 = max(0.0, min(ey2, image_height - 0.0))

        # Ensure correct ordering after clipping
        if ex2 <= ex1:
            ex2 = min(image_width * 1.0, ex1 + 1.0)
        if ey2 <= ey1:
            ey2 = min(image_height * 1.0, ey1 + 1.0)

        return int(ex1), int(ey1), int(ex2), int(ey2)

    def _smooth_box(self, old_box, new_box, alpha):
        """EMA smoothing between old and new box; returns integer box."""
        ox1, oy1, ox2, oy2 = old_box
        nx1, ny1, nx2, ny2 = new_box
        sx1 = int(alpha * nx1 + (1.0 - alpha) * ox1)
        sy1 = int(alpha * ny1 + (1.0 - alpha) * oy1)
        sx2 = int(alpha * nx2 + (1.0 - alpha) * ox2)
        sy2 = int(alpha * ny2 + (1.0 - alpha) * oy2)
        return sx1, sy1, sx2, sy2

    def _iou(self, a, b):
        """Compute IoU between boxes a and b given as (x1,y1,x2,y2)."""
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        inter_x1 = max(ax1, bx1)
        inter_y1 = max(ay1, by1)
        inter_x2 = min(ax2, bx2)
        inter_y2 = min(ay2, by2)
        inter_w = max(0, inter_x2 - inter_x1)
        inter_h = max(0, inter_y2 - inter_y1)
        inter_area = inter_w * inter_h
        area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
        area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
        union = area_a + area_b - inter_area
        if union <= 0:
            return 0.0
        return float(inter_area) / float(union)

    def set_multi_person_mode(self, enabled: bool):
        """Enable or disable multi-person pose mode."""
        self.enable_multi_person_pose = enabled

    def _ensure_yolo(self):
        """Lazily initialize YOLO only when needed for multi-person detection."""
        if self.yolo is None:
            try:
                ensure_yolov5_weights()
                self.yolo = YOLOv5("yolov5s.pt")
                if self.status_callback:
                    self.status_callback("🤖 YOLOv5 model loaded for multi-person detection")
            except Exception as e:
                if self.status_callback:
                    self.status_callback(f"❌ Failed to load YOLOv5: {e}")
                raise RuntimeError(f"Failed to initialize YOLOv5: {e}")

    def extract_pose_features(self, video_path, progress_callback=None):
        """Extract pose features from video, saving one CSV per person."""
        cap = cv2.VideoCapture(video_path)
        frame_idx = 0
        keypoints_by_person = {} # Dictionary to store keypoints per person
        
        # Get video dimensions for coordinate normalization
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        
        # Get total frame count for progress tracking
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            # Fallback: estimate frames from FPS and duration
            fps = cap.get(cv2.CAP_PROP_FPS)
            if fps > 0:
                duration = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
                total_frames = int(fps * duration)

        # Maintain locked ROIs across frames for multi-person mode
        # Each ROI holds its own MediaPipe Pose instance; index in list is stable person_id
        locked_rois = []  # Each item: {"x1":int,"y1":int,"x2":int,"y2":int,"lost":int, "pose": Pose}

        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
              
            if self.status_callback:
                self.status_callback(f"📸 Extracting pose from: {os.path.basename(video_path)} (Frame {frame_idx + 1}/{total_frames})")

            image_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            if self.enable_multi_person_pose:
                # Seed ROIs using shared pipeline
                locked_rois = self._seed_rois_if_needed(image_rgb, w, h, locked_rois, margin_ratio=0.25)

                # If still none, skip frame
                if not locked_rois:
                    frame_idx += 1
                    if progress_callback and total_frames > 0:
                        progress_percent = int((frame_idx / total_frames) * 100)
                        progress_callback(progress_percent)
                    continue

                # Process using shared multiperson step
                mp_outputs, locked_rois = self._process_multiperson_frame(image_rgb, w, h, locked_rois)
                for person_id, mp_landmarks in mp_outputs:
                    row = [frame_idx, person_id]
                    for lmk in mp_landmarks.landmark:
                        row.extend([lmk.x, lmk.y, lmk.z, lmk.visibility])
                    if person_id not in keypoints_by_person:
                        keypoints_by_person[person_id] = []
                    keypoints_by_person[person_id].append(row)

            else:
                # Single-person mode
                result = self.pose.process(image_rgb)
                if result.pose_landmarks:
                    row = [frame_idx, 0]
                    for lmk in result.pose_landmarks.landmark:
                        row.extend([lmk.x, lmk.y, lmk.z, lmk.visibility])
                    keypoints_by_person[0] = keypoints_by_person.get(0, []) + [row]

            frame_idx += 1
            
            # Update progress if callback provided
            if progress_callback and total_frames > 0:
                progress_percent = int((frame_idx / total_frames) * 100)
                progress_callback(progress_percent)

        cap.release()

        # Cleanup ROI Pose instances to free resources (match embedding behavior)
        self._cleanup_locked_rois(locked_rois)
        
        
        # Define DataFrame column names
        columns = ['frame', 'person_id']
        names = [
            'Nose', 'Left_eye_inner', 'Left_eye', 'Left_eye_outer', 'Right_eye_inner', 
            'Right_eye', 'Right_eye_outer', 'Left_ear', 'Right_ear', 'Mouth_left', 
            'Mouth_right', 'Left_shoulder', 'Right_shoulder', 'Left_elbow', 'Right_elbow',
            'Left_wrist', 'Right_wrist', 'Left_pinky', 'Right_pinky', 'Left_index', 
            'Right_index', 'Left_thumb', 'Right_thumb', 'Left_hip', 'Right_hip',
            'Left_knee', 'Right_knee', 'Left_ankle', 'Right_ankle', 'Left_heel',
            'Right_heel', 'Left_foot_index', 'Right_foot_index'
        ]
        for n in names:
            columns.extend([f"{n}_x", f"{n}_y", f"{n}_z", f"{n}_confidence"])

        suffix = "_multi" if self.enable_multi_person_pose else ""
        base_filename = os.path.splitext(os.path.basename(video_path))[0] + suffix
        
        
        # Save separate CSV for each person
        for person_id, keypoints in keypoints_by_person.items():
            df = pd.DataFrame(keypoints, columns=columns)
            filename = f"{base_filename}_ID_{int(person_id)}.csv"
            csv_path = os.path.join(self.output_csv_folder, filename)
            df.to_csv(csv_path, index=False)

        return

    def embed_pose_video(self, video_path, progress_callback=None):
        """Overlay pose landmarks on the video and save output."""
        if not self.output_video_folder:
            return None

        cap = cv2.VideoCapture(video_path)
        fps = int(cap.get(cv2.CAP_PROP_FPS))
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        
        # Get total frame count for progress tracking
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            # Fallback: estimate frames from FPS and duration
            fps = cap.get(cv2.CAP_PROP_FPS)
            if fps > 0:
                duration = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
                total_frames = int(fps * duration)

        suffix = "_multi" if self.enable_multi_person_pose else ""
        filename = os.path.splitext(os.path.basename(video_path))[0] + f"{suffix}_pose.mp4"
        out_path = os.path.join(self.output_video_folder, filename)
        out = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(*'mp4v'), fps, (w, h))

        frame_idx = 0
        # Maintain locked ROIs across frames for multi-person mode
        # Each ROI holds its own MediaPipe Pose instance
        locked_rois = []  # Each item: {"x1":int,"y1":int,"x2":int,"y2":int,"lost":int, "pose": Pose}

        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break

            image_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            if self.enable_multi_person_pose:
                # Seed ROIs using shared pipeline
                locked_rois = self._seed_rois_if_needed(image_rgb, w, h, locked_rois, margin_ratio=0.25)

                # If no ROIs found, write frame and continue
                if not locked_rois:
                    out.write(frame)
                    frame_idx += 1
                    if progress_callback and total_frames > 0:
                        progress_percent = int((frame_idx / total_frames) * 100)
                        progress_callback(progress_percent)
                    continue

                # Process using shared multiperson step and draw
                mp_outputs, locked_rois = self._process_multiperson_frame(image_rgb, w, h, locked_rois)
                for _, mp_landmarks in mp_outputs:
                    try:
                        self.drawing_utils.draw_landmarks(
                            frame,
                            mp_landmarks,
                            mp.solutions.pose.POSE_CONNECTIONS,
                            landmark_drawing_spec=mp.solutions.drawing_utils.DrawingSpec(color=(0, 255, 0), thickness=3, circle_radius=3),
                            connection_drawing_spec=mp.solutions.drawing_utils.DrawingSpec(color=(255, 0, 0), thickness=2)
                        )
                    except Exception as e:
                        if self.status_callback:
                            self.status_callback(f"❌ Error drawing landmarks: {e}")
                        print(f"Error drawing landmarks: {e}")

            else:
                # Single-person mode
                result = self.pose.process(image_rgb)
                if result.pose_landmarks:
                    # Debug output to verify landmarks are being detected
                    if self.status_callback:
                        self.status_callback(f"🎯 Drawing {len(result.pose_landmarks.landmark)} landmarks (single-person mode)")
                    
                    # Draw landmarks with visible style
                    self.drawing_utils.draw_landmarks(
                        frame, 
                        result.pose_landmarks, 
                        mp.solutions.pose.POSE_CONNECTIONS,
                        landmark_drawing_spec=mp.solutions.drawing_utils.DrawingSpec(color=(0, 255, 0), thickness=3, circle_radius=3),
                        connection_drawing_spec=mp.solutions.drawing_utils.DrawingSpec(color=(255, 0, 0), thickness=2)
                    )
                else:
                    # Debug output when no landmarks detected in single-person mode
                    if self.status_callback:
                        self.status_callback("⚠️ No landmarks detected (single-person mode)")

            out.write(frame)
            
            frame_idx += 1
            
            # Update progress if callback provided
            if progress_callback and total_frames > 0:
                progress_percent = int((frame_idx / total_frames) * 100)
                progress_callback(progress_percent)

        cap.release()
        out.release()
        # Cleanup ROI Pose instances
        self._cleanup_locked_rois(locked_rois)
        return out_path
